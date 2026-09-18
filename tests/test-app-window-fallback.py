#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：应用窗口起不来时，必须走「浏览器兜底」而不是把网关一起带走。

## 为什么需要这个测试

`app.py` 的 `main()` 在 `_run_window()` **之后**紧接着就是关停逻辑（杀网关 → 关 Job）。
如果 `_run_window()` 抛异常或直接 return，整个进程就会收摊退出，
把刚拉起的网关一起杀掉 —— 「窗口起不来」被放大成「网关也起不来」。

2026-09-18 排查「别人下载 exe 打开后网关起不来」时发现这条链路同样致命：
窗口依赖 WebView2，没装 WebView2 的机器会走到这里。

## 断言

1. `webview` 导入失败（等价于没装 pywebview）→ `_run_window()` 返回 False，且已弹窗 + 打开浏览器
2. `webview.create_window` 抛异常（等价于 WebView2 缺失/渲染器起不来）→ 同样返回 False
3. 两种情况都**不得**向上抛异常

    python tests/test-app-window-fallback.py
"""

from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    import app  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    # app.py 会导入 launcher，而后者顶层依赖 Windows 专属的 ctypes.wintypes
    print("  SKIP：无法导入 app（%r）—— 本测试只在 Windows 上有意义" % _exc)
    sys.exit(0)


class Recorder:
    def __init__(self):
        self.alerts = []
        self.opened = []


def patch_side_effects(rec: Recorder):
    """把弹窗与打开浏览器换成记录器，避免测试时真的弹窗/开浏览器。"""
    app._alert = lambda msg, title="WorkBuddy2API", icon=0x00000010: rec.alerts.append(msg)
    app.webbrowser = types.SimpleNamespace(open=lambda url, *a, **k: rec.opened.append(url))


def case_import_error() -> list:
    bad = []
    rec = Recorder()
    patch_side_effects(rec)
    sys.modules["webview"] = None          # 让 `import webview` 抛 ImportError
    try:
        ret = app._run_window()
    except Exception as exc:               # noqa: BLE001
        bad.append("导入失败场景仍然抛了异常：%r" % exc)
        ret = None
    finally:
        sys.modules.pop("webview", None)
    if ret is not False:
        bad.append("导入失败场景应返回 False，实际 %r" % (ret,))
    if not rec.alerts:
        bad.append("导入失败场景没有弹窗告知用户")
    if not rec.opened:
        bad.append("导入失败场景没有打开浏览器兜底")
    elif str(app.PANEL_PORT) not in rec.opened[0]:
        bad.append("兜底打开的地址不对：%s" % rec.opened[0])
    print("  用例1  webview 导入失败 → 返回值=%-5s 弹窗=%d 开浏览器=%d"
          % (ret, len(rec.alerts), len(rec.opened)))
    return bad


def case_create_window_raises() -> list:
    bad = []
    rec = Recorder()
    patch_side_effects(rec)

    def boom(*a, **k):
        raise RuntimeError("WebView2 runtime not found")

    sys.modules["webview"] = types.SimpleNamespace(create_window=boom, start=lambda **k: None)
    try:
        ret = app._run_window()
    except Exception as exc:               # noqa: BLE001
        bad.append("create_window 失败场景仍然抛了异常：%r" % exc)
        ret = None
    finally:
        sys.modules.pop("webview", None)
    if ret is not False:
        bad.append("create_window 失败场景应返回 False，实际 %r" % (ret,))
    if not rec.alerts:
        bad.append("create_window 失败场景没有弹窗告知用户")
    if not rec.opened:
        bad.append("create_window 失败场景没有打开浏览器兜底")
    print("  用例2  create_window 抛错 → 返回值=%-5s 弹窗=%d 开浏览器=%d"
          % (ret, len(rec.alerts), len(rec.opened)))
    return bad


def main() -> int:
    failures = case_import_error() + case_create_window_raises()
    print()
    if failures:
        print("  ✗ 失败 %d 项：" % len(failures))
        for f in failures:
            print("      - %s" % f)
        return 1
    print("  ✓ 全部通过：窗口失败会兜底到浏览器，不会连带杀掉网关")
    return 0


if __name__ == "__main__":
    sys.exit(main())
