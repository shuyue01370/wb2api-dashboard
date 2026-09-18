#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：「日志跟随线程」必须在**所有**面板入口里被启动。

## 为什么需要这个测试

面板有**两个**入口，它们各自实现了一份 main()：

  1. `server.py` 的 main()  —— bat / 直接 `python server.py` 的形态
  2. `app.py`    的 main()  —— **打包后的 exe 形态**（build_exe.py 指定的入口）

`preload_logs` / `log_follower` 这两个线程原本只写在 `server.py` 的 main() 里启动，
而 exe 走的是 `app.py` 的 main()、**从不调用 `server.main()`**，于是：

  → 线程压根没起 → `LOG_TAIL` / `EVENTS` 恒为空
  → 「自动切换」页的「实时日志」与「事件时间线」永远空白
  → 但 `gateway.log` 本身在正常增长（网关没问题）

用户侧看到的是「实时日志怎么又没输出了」，而且**开发环境（bat）怎么试都是好的**——
这正是它反复复发的原因。

修法：抽成 `server.start_log_threads()`，两个入口都调它。本测试就是钉住这条不变量。

## 这个测试怎么跑

    python tests/test-log-threads.py

不依赖网络、不起网关：静态部分扫源码，运行时部分用一个临时 gateway.log 走一遍
preload + 增量跟随。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def read(name: str) -> str:
    with open(os.path.join(BASE, name), "r", encoding="utf-8") as fh:
        return fh.read()


def extract_func(src: str, name: str) -> str:
    """粗切出顶层函数的函数体（到下一个顶层 def 或 __main__ 守卫为止）。"""
    try:
        i = src.index("def %s(" % name)
    except ValueError:
        return ""
    m = re.search(r"\n(?=(?:def |if __name__))", src[i + 1:])
    return src[i: i + 1 + m.start()] if m else src[i:]


def main() -> int:
    failures = []

    def check(cond, label):
        print("  %-4s %s" % ("✓" if cond else "✗", label))
        if not cond:
            failures.append(label)

    server_src = read("server.py")
    app_src = read("app.py")

    # ------------------------------------------------------------------
    # 一、静态：两个入口都必须启动日志线程，且只允许经由 start_log_threads()
    # ------------------------------------------------------------------
    print("\n[一] 静态检查：两个入口都调用了 start_log_threads()")

    sfn = extract_func(server_src, "start_log_threads")
    check(bool(sfn), "server.py 定义了 start_log_threads()")
    check("preload_logs" in sfn, "start_log_threads() 里启动了 preload_logs")
    check("log_follower" in sfn, "start_log_threads() 里启动了 log_follower")

    s_main = extract_func(server_src, "main")
    a_main = extract_func(app_src, "main")
    check(bool(s_main), "能取到 server.py 的 main()")
    check(bool(a_main), "能取到 app.py 的 main()")
    check("start_log_threads()" in s_main, "server.py 的 main() 调用 start_log_threads()")
    check("server.start_log_threads()" in a_main,
          "app.py 的 main() 调用 server.start_log_threads()  ← exe 形态的关键一句")

    # 反例守卫：谁再回到「在 main() 里裸起线程」，只有一处生效，另一处静默失效
    for fname, body in (("server.py", s_main), ("app.py", a_main)):
        bare = "Thread(target=preload_logs" in body or "Thread(target=log_follower" in body
        check(not bare, "%s 的 main() 里没有裸起日志线程（必须走 start_log_threads）" % fname)

    # ------------------------------------------------------------------
    # 二、静态：日志轮转不能无条件执行
    # ------------------------------------------------------------------
    print("\n[二] 静态检查：rotate_log 只在「本次真新起网关」时执行")

    i_ready = app_src.find("gateway_ready = L.wait_healthz")
    i_rot = app_src.find("L.rotate_log(gateway_log)")
    check(i_ready != -1 and i_rot != -1, "app.py 里能定位 gateway_ready 与 rotate_log")
    check(i_rot > i_ready,
          "rotate_log 在 gateway_ready 判定之后（复用网关时不再轮转，避免改名后新文件永远空）")
    if i_ready != -1 and i_rot != -1:
        seg = app_src[i_ready:i_rot]
        check("if gateway_ready:" in seg and "else:" in seg,
              "rotate_log 落在 if gateway_ready / else 分支内（不是无条件调用）")

    # ------------------------------------------------------------------
    # 三、运行时：真跑一遍 preload + 增量跟随
    # ------------------------------------------------------------------
    print("\n[三] 运行时：临时 gateway.log 走一遍 preload + 增量跟随")

    import server  # noqa: E402

    tmp = tempfile.mkdtemp(prefix="wb2api-logtest-")
    log_path = os.path.join(tmp, "gateway.log")
    boot = [
        "2026/09/18 10:00:00 loaded 6 account(s) from %s" % tmp,
        "2026/09/18 10:00:01 workbuddy2api listening on 0.0.0.0:7863 (api_key=true)",
    ]
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(boot) + "\n")

    server.NATIVE = True                  # 模拟 exe 形态（app.py 里就是这么设的）
    server.GATEWAY_LOG = log_path         # 指向我们自己的临时文件

    n0 = len(server.LOG_TAIL)
    server.start_log_threads()

    # preload 是同步跑完 200 行的；等它落地
    for _ in range(40):
        if len(server.LOG_TAIL) > n0:
            break
        time.sleep(0.1)
    n1 = len(server.LOG_TAIL)
    check(n1 > n0, "start_log_threads() 后 LOG_TAIL 有内容（%d → %d 行）" % (n0, n1))

    # 增量：追加一行 failover 日志，看跟随线程是否 1 秒内捡到
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write("2026/09/18 10:00:05 pool: fallback_earliest_expiry uid=abc, "
                 "所有 healthy 账号不可用，回落到最早到期账号\n")
        fh.flush()
        os.fsync(fh.fileno())

    for _ in range(50):
        if len(server.LOG_TAIL) > n1:
            break
        time.sleep(0.1)
    n2 = len(server.LOG_TAIL)
    check(n2 > n1, "追加一行后被增量跟随到（%d → %d 行）" % (n1, n2))

    # 「自动切换」页的两条数据通路都要有内容
    check(len(server.EVENTS) > 0, "/api/events 的数据源 EVENTS 非空")
    check(any("fallback_earliest_expiry" in (r.get("text") or "")
              for r in server.LOG_TAIL),
          "/api/logs 的数据源 LOG_TAIL 里能看到切换事件")

    # classify_event：failover 归类正确（「自动切换」页据此渲染）
    kind, level, uid, text, ts = server.classify_event(
        "2026/09/18 10:00:06 pool: fallback_earliest_expiry")
    check(kind == "failover" and level == "warn",
          "classify_event 把 fallback_earliest_expiry 归为 failover/warn")

    # 清理（进程内的缓冲 + 临时目录）
    try:
        del server.LOG_TAIL[:]
        del server.EVENTS[:]
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print("  ✗ 失败 %d 项：" % len(failures))
        for f in failures:
            print("      - %s" % f)
        return 1
    print("  ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
