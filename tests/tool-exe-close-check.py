# -*- coding: utf-8 -*-
"""验证单文件 exe 的真实行为：双击启动 → 点窗口关闭 → 是否全部停止、无残留。

用 os.startfile 启动（等价双击，不继承句柄），再向窗口发 WM_CLOSE（等价点 X）。
"""
import ctypes
import os
import subprocess
import time

EXE = r"D:\utils\wb2api-dashboard\dist\WorkBuddy2API.exe"
LOG = r"D:\utils\wb2api-dashboard\logs\app.log"
TITLE = "WorkBuddy2API 控制面板"
WM_CLOSE = 0x0010


def count(image):
    out = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq %s" % image],
        capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    return len([ln for ln in out.strip().splitlines() if ln.strip()])


def listening(port):
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="gbk", errors="replace").stdout
    return any((":%d " % port) in ln and "LISTENING" in ln for ln in out.splitlines())


def find_window(title, timeout=25):
    u32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = u32.FindWindowW(None, title)
        if hwnd:
            return hwnd
        time.sleep(1)
    return 0


try:
    os.remove(LOG)
except OSError:
    pass

os.environ["WB2API_PANEL_PORT"] = "7865"
before = count("msedgewebview2.exe")
print("测试前：WebView2=%d" % before)

os.startfile(EXE)          # 等价双击
print("已启动（等待解包与窗口…）")
time.sleep(15)

print("运行中：exe=%d, WebView2=%d, 7865监听=%s, 7863监听=%s"
      % (count("WorkBuddy2API.exe"), count("msedgewebview2.exe"),
         listening(7865), listening(7863)))

hwnd = find_window(TITLE)
print("窗口句柄：%s" % hwnd)
if hwnd:
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    print("已发送 WM_CLOSE（等价点击窗口关闭按钮）")
else:
    print("未找到窗口！")

time.sleep(12)
after_exe = count("WorkBuddy2API.exe")
after_wv = count("msedgewebview2.exe")
print("关窗后：exe=%d, WebView2=%d, 7865监听=%s" % (after_exe, after_wv, listening(7865)))
ok = (after_exe == 0 and after_wv <= before and not listening(7865))
print("结论：", "关窗即全部停止，无残留 ✓" if ok else "有残留 ✗")

print("--- app.log ---")
try:
    with open(LOG, "r", encoding="utf-8", errors="replace") as fh:
        print(fh.read()[-1500:])
except Exception as exc:  # noqa: BLE001
    print("(无日志)", exc)
