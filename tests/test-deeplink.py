#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：cc-switch 深链的探测/唤起，以及「禁止写死本机配置」的守卫。

背景（2026-09-18 用户实测）：面板上曾把协议状态**写死**成
`本机协议已注册：HKEY_CLASSES_ROOT\\ccswitch → D:\\soft\\cc_switch\\cc-switch.exe`，
在别人电脑上这是假信息（还会暴露开发者路径）；导入按钮也只是网页内跳转，
拿不到处理程序时静默无反应。现在改为：后端实测注册表 + 白名单唤起 + 失败弹框。

本测试**不会真的唤起 cc-switch**（把 os.startfile 打桩），也不会修改任何注册表。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server  # noqa: E402

FAILS = []
PASSED = [0]


def ok(cond, label):
    if cond:
        PASSED[0] += 1
        print("  OK   " + label)
    else:
        FAILS.append(label)
        print("  FAIL " + label)


def main():
    print("---- 1. 从协议命令里解析处理程序路径 ----")
    cases = [
        ('"C:\\soft\\cc-switch\\cc-switch.exe" "%1"', "C:\\soft\\cc-switch\\cc-switch.exe"),
        ("C:\\app\\cc-switch.exe %1", "C:\\app\\cc-switch.exe"),
        ('"C:\\Program Files\\x y\\a.exe" --flag "%1"', "C:\\Program Files\\x y\\a.exe"),
        ("", ""),
    ]
    for raw, want in cases:
        got = server._exe_from_command(raw)
        ok(got == want, "_exe_from_command(%r) → %r" % (raw, got))

    print("---- 2. ccswitch_protocol() 实测（只读注册表）----")
    info = server.ccswitch_protocol()
    for key in ("supported", "registered", "command", "exe", "exe_exists", "hive", "note"):
        ok(key in info, "返回结构含 %s" % key)
    if os.name == "nt":
        if info["registered"]:
            ok(bool(info["exe"]), "已注册时给出处理程序路径：%s" % info["exe"])
            ok(info["hive"] in ("HKCR", "HKCU", "HKLM"), "标出注册所在 hive：%s" % info["hive"])
            ok(os.path.isfile(info["exe"]) == info["exe_exists"],
               "exe_exists 与实际文件存在性一致")
        else:
            ok(bool(info["note"]), "未注册时给出可读的 note：%s" % info["note"])
            ok("cc-switch" in info["note"], "note 里点明 cc-switch 未安装")
    else:
        ok(info["supported"] is False, "非 Windows 平台 supported=False")
        ok(bool(info["note"]), "非 Windows 平台给出说明")
    ok(not info["registered"] or info["command"],
       "注册状态与 command 一致（不会出现「注册了但命令为空」）")

    print("---- 3. open_deeplink 白名单（绝不能变成通用协议启动器）----")
    for bad in ("", "   ", "http://x/y", "https://x", "file:///C:/Windows/System32/calc.exe",
                "javascript:alert(1)", "vbscript:msgbox(1)", "ccswitchx://a", "data:text/html,x"):
        r = server.open_deeplink(bad)
        ok(r.get("ok") is False, "拒绝 %r（%s）" % (bad, r.get("error", "")))
    ok("ccswitch" in server.open_deeplink("ms-settings://x").get("error", ""),
       "拒绝时的错误信息指明只允许 ccswitch:")

    print("---- 4. 未注册协议时应给出明确错误（而不是静默）----")
    real_probe = server.ccswitch_protocol
    server.ccswitch_protocol = lambda: {"supported": True, "registered": False, "exe": "",
                                        "exe_exists": False, "hive": "", "command": "",
                                        "note": "未检测到 ccswitch:// 协议：这台电脑没有安装 cc-switch"}
    try:
        r = server.open_deeplink("ccswitch://v1/import?app=claude")
        ok(r.get("ok") is False, "未注册 → ok=False")
        ok("cc-switch" in (r.get("error") or ""), "错误文案点明原因：%s" % r.get("error"))
    finally:
        server.ccswitch_protocol = real_probe

    print("---- 5. 已注册时调用系统处理程序（os.startfile 打桩，不会真唤起）----")
    called = []
    real_startfile = getattr(os, "startfile", None)
    url = "ccswitch://v1/import?resource=provider&app=claude&model=%E6%B5%8B%E8%AF%95"
    if os.name == "nt":
        os.startfile = lambda u, *a, **k: called.append(u)
        try:
            r = server.open_deeplink("  " + url + "  ")     # 带空白也要被 strip 掉
            ok(r.get("ok") is True, "已注册 → ok=True")
            ok(called == [url], "传给系统的是 strip 后的原样 URL（%r）" % (called[:1]))
        finally:
            if real_startfile:
                os.startfile = real_startfile
    else:
        ok(True, "非 Windows 跳过 os.startfile 打桩（走 open/xdg-open）")

    print("---- 6. 路由与前端接线（源码级）----")
    srv = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    ok('/api/open-deeplink' in srv, "后端注册了 POST /api/open-deeplink")
    ok('"ccswitch": ccswitch_protocol(),' in srv, "/api/meta 里带上 ccswitch 实测状态")
    ok('CCSWITCH_SCHEME = "ccswitch:"' in srv, "白名单常量存在")
    ok('/api/open-deeplink' in html, "前端按钮改走本机后端唤起")
    ok('id="dlgMask"' in html and "data-dlg-close" in html, "存在通用提示弹窗（失败时能弹框）")
    ok("function dlgShow" in html and "function dlgClose" in html, "弹窗开关函数存在")
    ok("function pickModel" in html, "模型名改由 pickModel 从实时列表挑")
    ok('if(!main)return "";' in html, "拿不到实时模型列表时不给导入（不塞写死的模型名）")
    ok("已注册 ✓" in html and "未检测到 ccswitch:// 协议" in html, "面板按实测结果分支渲染协议状态")

    print("---- 7. 守卫：代码里不得再出现「本机写死配置」----")
    forbidden = [
        (r"D:\\soft", "开发者本机的 cc-switch 路径"),
        (r"Administrator", "本机用户名"),
        (r"HKEY_CLASSES_ROOT..ccswitch", "写死的注册表位置文案"),
        (r"0\.0\.0\.0:7863", "把网关暴露到局域网的默认 listen"),
        (r"['\"]test_key['\"]", "写死的 api_key 默认值"),
    ]
    files = ["server.py", "app.py", "launcher.py", "index.html"]
    for name in files:
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        for pat, why in forbidden:
            hits = [ln.strip()[:70] for ln in text.split("\n") if re.search(pat, ln)]
            ok(not hits, "%s 不含 %s（%s）" % (name, why, hits[:1] or ""))

    print("\n通过 %d 项，失败 %d 项" % (PASSED[0], len(FAILS)))
    for f in FAILS:
        print("  FAILED: " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
