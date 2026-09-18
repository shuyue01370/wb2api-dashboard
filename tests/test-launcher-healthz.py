#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：wait_healthz 必须把 503 认作「就绪」。

## 为什么需要这个测试

`urllib.request.urlopen` 对**非 2xx 会直接抛 HTTPError**，不会正常返回。
所以 `wait_healthz` 里 `r.status in (200, 503)` 是一句**不可达的分支**。

网关在「账号池为空」时正是返回 503（活着，但没有可服务账号）。
2026-09-18 的 bug：503 被当成「网关没起来」→ 等待 25 秒超时 →
刚拉起的网关被自己 kill 掉。全新环境 100% 复现，
用户侧表现为「从 GitHub 下载的 exe 双击打开，网关起不来」。

## 这个测试怎么跑

自起一个只返回 503 的本地 HTTP 服务，把 launcher.GATEWAY_PORT 指过去，
断言 wait_healthz 在首次请求就返回 True。不需要真的起网关。

    python tests/test-launcher-healthz.py
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import launcher  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    # launcher.py 顶层依赖 Windows 专属的 ctypes.wintypes，非 Windows 无法导入
    print("  SKIP：无法导入 launcher（%r）—— 本测试只在 Windows 上有意义" % _exc)
    sys.exit(0)


class Handler503(http.server.BaseHTTPRequestHandler):
    """模拟「账号池为空」的网关：/healthz 返回 503。"""

    def do_GET(self):  # noqa: N802
        body = b'{"healthy":0,"service":"workbuddy2api","total":0}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


class Handler200(http.server.BaseHTTPRequestHandler):
    """模拟「有可用账号」的网关：/healthz 返回 200。"""

    def do_GET(self):  # noqa: N802
        body = b'{"healthy":1,"service":"workbuddy2api","total":1}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(handler):
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def main() -> int:
    failures = []

    # --- 用例 1：503（账号池为空）必须算就绪，且要立刻返回 ---
    srv, port = _serve(Handler503)
    launcher.GATEWAY_PORT = port
    t0 = time.time()
    ok = launcher.wait_healthz(3)
    dt = time.time() - t0
    srv.shutdown()
    print("  用例1  503 → %-5s (%.2fs)  期望 True" % (ok, dt))
    if not ok:
        failures.append("503 被误判为「未就绪」（这正是 2026-09-18 的 bug）")
    elif dt > 2:
        failures.append("503 判定太慢（%.2fs），应在首次请求即返回" % dt)

    # --- 用例 2：200（有可用账号）仍要正常返回 True ---
    srv, port = _serve(Handler200)
    launcher.GATEWAY_PORT = port
    t0 = time.time()
    ok = launcher.wait_healthz(3)
    dt = time.time() - t0
    srv.shutdown()
    print("  用例2  200 → %-5s (%.2fs)  期望 True" % (ok, dt))
    if not ok:
        failures.append("200 被判为「未就绪」")

    # --- 用例 3：端口没人监听时必须老老实实超时返回 False ---
    launcher.GATEWAY_PORT = 59999  # 基本不可能被占用
    t0 = time.time()
    ok = launcher.wait_healthz(1.5)
    dt = time.time() - t0
    print("  用例3  无监听 → %-5s (%.2fs)  期望 False" % (ok, dt))
    if ok:
        failures.append("端口无人监听却返回 True（会把「网关没起来」放过去）")
    elif dt < 1.0:
        failures.append("未监听时过早放弃（%.2fs），应重试到超时" % dt)

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
