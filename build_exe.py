#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把面板打包成单个 WorkBuddy2API.exe（内嵌网关二进制 + 页面资源）。

用法：
    <venv>/Scripts/python.exe build_exe.py

产物：
    dist/WorkBuddy2API.exe      —— 双击即用：网关 + 面板服务 + 原生窗口（WebView2）

说明：
  * --noconsole  关键：不弹命令行窗口
  * --onefile    单文件（启动时自解包到临时目录）
  * bin/*.exe    网关与子工具以二进制方式内嵌，运行时从 _MEIPASS/bin 调用
  * index.html / tasks_all.py 作为数据打入
"""
from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# 任务脚本来源仓库（workbuddy2api）。仅打包时用于取材，运行时不再依赖。
def _default_repo() -> str:
    """网关仓库 workbuddy2api 的默认位置：../workbuddy2api → ~/workbuddy2api。

    可用环境变量 WB2API_DIR 显式覆盖。
    """
    parent = os.path.dirname(BASE)
    for cand in (os.path.join(parent, "workbuddy2api"),
                 os.path.join(os.path.expanduser("~"), "workbuddy2api")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(parent, "workbuddy2api")


REPO = os.environ.get("WB2API_DIR") or _default_repo()
REPO_SCRIPTS = os.path.join(REPO, "scripts")

TASK_MODULES = ["task_common.py", "task_chat5.py", "task_first_buddy.py",
                "task_model_chat.py", "task_richmeow.py", "probe_active.py"]

BINARIES = ["wb2api.exe", "signin_bin.exe", "login.exe", "credit.exe"]
DATAS = ["index.html", "tasks_all.py"]


def build_tasks_exe() -> str:
    """先把 tasks_all.py 打成 tasks_all.exe（内嵌 scripts/*），供主 exe 内嵌。

    这样目标机器**无需安装 Python** 也能用「一键接取 / 一键领奖」。
    """
    from PyInstaller.__main__ import run

    args = [
        "--noconfirm", "--clean",
        "--console",                  # 需要 stdout 供面板捕获
        "--onefile",
        "--name", "tasks_all",
        "--distpath", os.path.join(BASE, "dist"),
        "--workpath", os.path.join(BASE, "build", "tasks"),
        "--specpath", os.path.join(BASE, "build"),
        "--paths", REPO_SCRIPTS,
        "--hidden-import", "task_common",
    ]
    for name in TASK_MODULES:
        src = os.path.join(REPO_SCRIPTS, name)
        if os.path.isfile(src):
            args += ["--add-data", "%s;scripts" % src]
        else:
            print("[警告] 缺少任务脚本：%s" % src)
    args.append(os.path.join(BASE, "tasks_all.py"))
    print("---- 打包 tasks_all.exe ----")
    run(args)
    out = os.path.join(BASE, "dist", "tasks_all.exe")
    print("[tasks_all] %s" % ("已生成 %.1f MB" % (os.path.getsize(out) / 1048576.0)
                              if os.path.isfile(out) else "生成失败"))
    return out


def build_args(tasks_exe: str = "") -> list:
    args = [
        "--noconfirm", "--clean",
        "--noconsole",
        "--onefile",
        "--name", "WorkBuddy2API",
        "--distpath", os.path.join(BASE, "dist"),
        "--workpath", os.path.join(BASE, "build"),
        "--specpath", os.path.join(BASE, "build"),
        # pywebview 的 Windows 后端是 EdgeChromium(WebView2) + pythonnet
        "--hidden-import", "webview.platforms.edgechromium",
        "--collect-all", "webview",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
    ]
    for name in DATAS:
        src = os.path.join(BASE, name)
        if os.path.isfile(src):
            args += ["--add-data", "%s;." % src]
        else:
            print("[警告] 缺少数据文件：%s" % name)
    for name in BINARIES:
        src = os.path.join(BASE, "bin", name)
        if os.path.isfile(src):
            args += ["--add-binary", "%s;bin" % src]
        else:
            print("[警告] 缺少二进制：bin\\%s" % name)
    if tasks_exe and os.path.isfile(tasks_exe):
        args += ["--add-binary", "%s;bin" % tasks_exe]
    else:
        print("[警告] 未内嵌 tasks_all.exe（目标机器需要自备 Python 才能跑成长计划）")
    if os.path.isdir(REPO_SCRIPTS):
        args += ["--add-data", "%s;scripts" % REPO_SCRIPTS]
    else:
        print("[警告] 缺少任务脚本目录：%s" % REPO_SCRIPTS)
    args.append(os.path.join(BASE, "app.py"))
    return args


def main() -> int:
    try:
        import PyInstaller.__main__  # noqa: F401
    except ImportError:
        print("缺少 pyinstaller：pip install pyinstaller")
        return 1
    try:
        tasks_exe = build_tasks_exe()
    except Exception as exc:  # noqa: BLE001
        print("[警告] tasks_all.exe 打包失败（%r），改为仅内嵌脚本" % exc)
        tasks_exe = ""
    args = build_args(tasks_exe)
    print("---- 打包主程序 ----")
    print("pyinstaller " + " ".join(args))
    from PyInstaller.__main__ import run
    run(args)
    out = os.path.join(BASE, "dist", "WorkBuddy2API.exe")
    if os.path.isfile(out):
        print("\n[完成] %s  (%.1f MB)" % (out, os.path.getsize(out) / 1048576.0))
        return 0
    print("\n[失败] 未生成 %s" % out)
    return 1


if __name__ == "__main__":
    sys.exit(main())
