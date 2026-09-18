#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy2API 一键启动器（原生模式，无需 Docker）
==================================================
双击 启动.bat → 本脚本：

  1. 生成 config-native.json（复用仓库 config.json，账号/状态路径绝对化）
  2. 启动网关 bin\\wb2api.exe（127.0.0.1:7863，反代理 + 调度器）
  3. 启动面板 server.py（127.0.0.1:7864）
  4. 打开 Edge/Chrome 「--app」应用窗口（无地址栏，观感即桌面应用）
  5. 任意一处退出（关窗口 / 关控制台 / Ctrl+C）→ 全部停止

进程树管理：Windows Job Object（JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）。
本进程无论以何种方式死亡（包括任务管理器强杀），同 Job 的所有子进程
（网关 / 面板 / 应用窗口及其全部子进程）都会被内核立即终止——这就是
"停 bat = 反代理和 web 服务全停"的实现机制。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_repo() -> str:
    """网关仓库 workbuddy2api 的默认位置：../workbuddy2api → ~/workbuddy2api。

    可用环境变量 WB2API_DIR 显式覆盖。
    """
    parent = os.path.dirname(BASE_DIR)
    for cand in (os.path.join(parent, "workbuddy2api"),
                 os.path.join(os.path.expanduser("~"), "workbuddy2api")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(parent, "workbuddy2api")


REPO = os.environ.get("WB2API_DIR") or _default_repo()
BIN_DIR = os.path.join(BASE_DIR, "bin")
LOG_DIR = os.path.join(BASE_DIR, "logs")
RUNTIME_FILE = os.path.join(BASE_DIR, ".runtime.json")
NATIVE_CONFIG = os.path.join(BASE_DIR, "config-native.json")
PANEL_PORT = 7864
GATEWAY_PORT = 7863

CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004

# ---------------------------------------------------------------------------
# Windows Job Object（ctypes，零第三方依赖）
# ---------------------------------------------------------------------------

kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


def create_kill_on_close_job():
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError("SetInformationJobObject failed")
    return job


def spawn_in_job(job, argv, **kwargs):
    """以 CREATE_SUSPENDED 启动 → 挂入 Job → 恢复运行。

    先挂后跑，保证子进程（及其随后创建的全部子孙）都落在 Job 里，
    不会出现"窗口关了但浏览器残进程还活着"的逃逸。
    kwargs 里可再带 creationflags（如 CREATE_NO_WINDOW），会与 SUSPENDED 合并。
    """
    flags = kwargs.pop("creationflags", 0) | CREATE_SUSPENDED
    proc = subprocess.Popen(argv, creationflags=flags, **kwargs)
    if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
        proc.kill()
        raise OSError("AssignProcessToJobObject failed (err=%d)"
                      % kernel32.GetLastError())
    ntdll.NtResumeProcess(int(proc._handle))
    return proc


def say(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 配置生成：仓库 config.json → config-native.json
# ---------------------------------------------------------------------------

def build_native_config() -> dict:
    src = os.path.join(REPO, "config.json")
    try:
        with open(src, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as exc:
        say("[警告] 读取 %s 失败（%s），使用纯默认配置" % (src, exc))
        cfg = {}

    def abspath(v, default):
        v = str(v or default)
        if not os.path.isabs(v):
            v = os.path.join(REPO, v)
        return os.path.normpath(v)

    cfg["auth_dir"] = abspath(cfg.get("auth_dir"), "auths")
    cfg["state_file"] = abspath(cfg.get("state_file"), os.path.join("data", "state.json"))
    # listen 保持与仓库配置一致的端口；确保是明确的 host:port 形式
    listen = str(cfg.get("listen") or ":%d" % GATEWAY_PORT)
    if listen.startswith(":"):
        listen = "0.0.0.0" + listen
    cfg["listen"] = listen
    with open(NATIVE_CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    return cfg


def rotate_log(path: str, max_bytes: int = 5 * 1024 * 1024):
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            old = path + ".old"
            if os.path.exists(old):
                os.remove(old)
            os.replace(path, old)
    except OSError:
        pass


def wait_healthz(timeout: float = 20.0) -> bool:
    """等待网关就绪。200 = 有可用账号；503 = 活着但账号池为空 —— 两者都算就绪。

    注意：`urlopen` 对非 2xx 会**直接抛 HTTPError**，所以 503 只能在 except 分支里认，
    光靠 `r.status in (200, 503)` 是**不可达的分支**。

    历史 bug（2026-09-18）：503 被当成「网关没起来」→ 刚拉起的网关被自己 kill 掉，
    全新环境（账号池为空）100% 复现，表现为「下载后打开不可用、网关起不来」。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = False
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/healthz" % GATEWAY_PORT, timeout=2) as r:
                ready = r.status in (200, 503)
        except urllib.error.HTTPError as exc:
            ready = exc.code in (200, 503)
        except Exception:
            ready = False
        if ready:
            return True
        time.sleep(0.5)
    return False


def _app_paths_lookup(exe_name: str) -> str:
    """从注册表 App Paths 取可执行文件全路径（浏览器装在自定义目录时靠它）。"""
    try:
        import winreg
    except Exception:
        return ""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (r"Software\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe_name,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe_name):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    path = str(winreg.QueryValueEx(key, "")[0] or "")
                if path:
                    return path
            except Exception:
                continue
    return ""


def find_app_browser():
    """优先 Chrome，其次 Edge，返回 (exe路径, 可否等待) 或 (None, False)。

    探测顺序（**不写死 C:\\Program Files** —— Chrome 默认是按用户安装的）：
      1. 常见安装位置：%LOCALAPPDATA%（按用户）→ %ProgramFiles% → %ProgramFiles(x86)%
      2. 注册表 App Paths（装到自定义目录）
      3. PATH 里能找到的 chrome.exe / msedge.exe

    Chrome --app + 独立 user-data-dir：新实例稳定存活，进程退出=窗口关闭，可精确等待。
    Edge --app：若机器上已有 Edge 在跑（含 startup boost 后台实例），会把窗口移交给
    现有实例并立即退出（实测 returncode=0）——进程退出不等于窗口关闭，不可等待。
    """
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    candidates = [
        # Chrome：按用户安装最常见，排最前
        (os.path.join(local, r"Google\Chrome\Application\chrome.exe"), True),
        (os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), True),
        (os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"), True),
        # Edge
        (os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), False),
        (os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"), False),
        (os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"), False),
    ]
    for path, waitable in candidates:
        if os.path.isfile(path):
            return path, waitable
    for exe_name, waitable in (("chrome.exe", True), ("msedge.exe", False)):
        path = _app_paths_lookup(exe_name)
        if path and os.path.isfile(path):
            return path, waitable
    for exe_name, waitable in (("chrome.exe", True), ("msedge.exe", False)):
        path = shutil.which(exe_name)
        if path:
            return path, waitable
    return None, False


# ---------------------------------------------------------------------------

def main() -> int:
    if os.name != "nt":
        say("[错误] 本启动器仅支持 Windows（Job Object 机制）。")
        return 1

    gateway_exe = os.path.join(BIN_DIR, "wb2api.exe")
    if not os.path.isfile(gateway_exe):
        say("[错误] 找不到 %s" % gateway_exe)
        say("       请先把 4 个 exe 放入 bin\\ 目录（见 README）。")
        return 1
    server_py = os.path.join(BASE_DIR, "server.py")
    if not os.path.isfile(server_py):
        say("[错误] 找不到 %s" % server_py)
        return 1

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    os.makedirs(LOG_DIR, exist_ok=True)
    # login.exe 把登录状态写到「当前盘:\tmp\...」（源码写死 /tmp，Windows 解析为当前盘根）
    drive_tmp = os.path.join(os.path.splitdrive(BASE_DIR)[0] + "\\", "tmp")
    os.makedirs(drive_tmp, exist_ok=True)

    cfg = build_native_config()
    gateway_log = os.path.join(LOG_DIR, "gateway.log")
    rotate_log(gateway_log)

    say("=" * 68)
    say(" WorkBuddy2API 原生启动器（无 Docker）")
    say("=" * 68)
    say(" 网关     : %s  ← %s" % (cfg.get("listen"), gateway_exe))
    say(" 账号目录 : %s" % cfg.get("auth_dir"))
    say(" 面板     : http://127.0.0.1:%d" % PANEL_PORT)
    say("-" * 68)

    # 端口预检：网关或面板已在跑（比如重复双击），直接把窗口拉起来即可
    gateway_already = wait_healthz(1.5)

    job = create_kill_on_close_job()
    procs = {}

    if gateway_already:
        say(" [跳过] 网关 7863 已在运行（可能上次未完全停止），直接复用。")
    else:
        log_fh = open(gateway_log, "ab")
        procs["gateway"] = spawn_in_job(
            job, [gateway_exe, "-config", NATIVE_CONFIG],
            cwd=REPO, stdout=log_fh, stderr=subprocess.STDOUT)
        say(" [启动] 网关 wb2api.exe (pid=%d)" % procs["gateway"].pid)
        if not wait_healthz():
            say("[错误] 网关 %d 秒内未就绪，查看日志：%s" % (20, gateway_log))
            for p in procs.values():
                p.kill()
            return 1
        say(" [就绪] healthz OK")

    # runtime.json：面板据此显示进程信息 / 执行「重启网关」
    with open(RUNTIME_FILE, "w", encoding="utf-8") as fh:
        json.dump({
            "mode": "native",
            "gateway_pid": procs["gateway"].pid if "gateway" in procs else None,
            "gateway_reused": gateway_already,
            "panel_port": PANEL_PORT,
            "started_at": time.time(),
        }, fh, ensure_ascii=False, indent=2)

    env = os.environ.copy()
    env["WB2API_NATIVE"] = "1"
    procs["panel"] = spawn_in_job(
        job, [sys.executable, "-u", server_py, "--no-browser", "--port", str(PANEL_PORT)],
        cwd=BASE_DIR, env=env)
    say(" [启动] 面板 server.py (pid=%d)" % procs["panel"].pid)

    # 网关守护：面板「重启网关」= kill 网关进程，这里检测到退出后自动拉起
    import threading

    def gateway_keeper():
        while True:
            p = procs.get("gateway")
            if p is None:
                time.sleep(1)
                continue
            code = p.wait()
            if stopping.is_set():
                return
            say(" [守护] 网关退出(code=%s)，3 秒后自动重启…" % code)
            time.sleep(3)
            log_fh2 = open(gateway_log, "ab")
            procs["gateway"] = spawn_in_job(
                job, [gateway_exe, "-config", NATIVE_CONFIG],
                cwd=REPO, stdout=log_fh2, stderr=subprocess.STDOUT)
            say(" [守护] 网关已重启 (pid=%d)" % procs["gateway"].pid)
            with open(RUNTIME_FILE, "w", encoding="utf-8") as fh:
                json.dump({
                    "mode": "native",
                    "gateway_pid": procs["gateway"].pid,
                    "panel_port": PANEL_PORT,
                    "started_at": time.time(),
                }, fh, ensure_ascii=False, indent=2)

    stopping = threading.Event()
    threading.Thread(target=gateway_keeper, daemon=True).start()

    # ---- 打开应用窗口（Chrome/Edge --app：无地址栏，观感即桌面应用）----
    browser, waitable = find_app_browser()
    win_proc = None
    if browser:
        profile = os.path.join(BASE_DIR, ".app-profile")
        os.makedirs(profile, exist_ok=True)
        name = "Edge" if "edge" in browser.lower() else "Chrome"
        say(" [窗口] %s 应用窗口 → http://127.0.0.1:%d" % (name, PANEL_PORT))
        try:
            win_proc = spawn_in_job(job, [
                browser,
                "--app=http://127.0.0.1:%d" % PANEL_PORT,
                "--user-data-dir=" + profile,   # 独立 profile：独立进程实例
                "--window-size=1380,920",
                "--no-first-run",
                "--no-default-browser-check",
            ])
            if waitable:
                time.sleep(1.5)
                if win_proc.poll() is not None:   # 理论上 Chrome 不会走到这
                    win_proc = None
            else:
                # Edge 单实例移交：进程秒退但窗口已打开，进程退出≠窗口关闭 → 不可等待
                time.sleep(1.5)
                if win_proc.poll() is None:
                    say(" [提示] 该浏览器本次保持了独立实例，关闭窗口即全部停止。")
                else:
                    say(" [提示] 浏览器把窗口移交给了已运行的实例（Edge 单实例行为）。")
                    say("        窗口应已打开；停止服务请关闭本控制台窗口或按 Ctrl+C。")
                    win_proc = None
        except Exception as exc:
            say(" [警告] 应用窗口启动失败（%s），回退默认浏览器。" % exc)
            win_proc = None
    if not browser:
        # 一个都没探到：不是"退出"，而是回退到系统默认浏览器，别让用户面对空白
        webbrowser.open("http://127.0.0.1:%d" % PANEL_PORT)
        say(" [窗口] 未找到 Chrome / Edge，已用系统默认浏览器打开。")
        say("        关闭本控制台窗口或按 Ctrl+C 即可停止全部服务。")
    elif win_proc is None:
        # 找到了浏览器但启动失败（异常分支已置 None）→ 同样回退，别只留一条警告
        webbrowser.open("http://127.0.0.1:%d" % PANEL_PORT)
        say(" [窗口] 应用窗口启动失败，已用系统默认浏览器打开面板。")
        say("        关闭本控制台窗口或按 Ctrl+C 即可停止全部服务。")

    say("-" * 68)
    say(" 已全部启动。关闭 应用窗口 / 本控制台 任一个，即全部停止。")
    say("")

    try:
        if win_proc is not None:
            win_proc.wait()          # 用户关掉应用窗口 → 触发全停
            say(" [退出] 应用窗口已关闭。")
        else:
            while True:
                time.sleep(3600)     # 浏览器模式：等待 Ctrl+C / 控制台关闭
    except KeyboardInterrupt:
        say(" [退出] Ctrl+C。")

    say(" [停止] 正在终止网关 / 面板 / 窗口…")
    stopping.set()
    for p in procs.values():
        try:
            p.kill()
        except Exception:
            pass
    try:
        kernel32.TerminateJobObject(job, 0)   # 兜底：Job 内所有进程（含窗口子树）全灭
    except Exception:
        pass
    try:
        os.remove(RUNTIME_FILE)
    except OSError:
        pass
    say(" [停止] 已全部停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
