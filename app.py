#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy2API 控制面板 —— 单文件桌面应用
=========================================
一个进程内跑起全套，双击即用，没有命令行窗口、不依赖外部浏览器：

  1. 启动网关 bin\\wb2api.exe（127.0.0.1:7863，无窗口子进程）
  2. 启动面板 HTTP 服务（127.0.0.1:7864，进程内线程）
  3. pywebview 打开原生窗口（系统 WebView2 内核）加载面板页
  4. 关闭窗口 → Job Object 回收网关及其全部子进程 → 进程退出

与 `启动面板.bat`（launcher.py）的关系：
  - launcher.py：开发态用，Chrome/Edge --app 打开页面，带控制台窗口
  - app.py    ：打包成 exe 用，页面渲染在应用窗口内，全程无控制台

打包（见 build_exe.py）：
  pyinstaller --noconsole --onefile --add-binary "bin\\wb2api.exe;bin" ...
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

# ---------------------------------------------------------------------------
# 路径：打包后资源在 _MEIPASS；配置 / 日志 / 运行时数据放 exe 同目录
# ---------------------------------------------------------------------------
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else BUNDLE_DIR
LOGS_DIR = os.path.join(APP_DIR, "logs")
_APP_LOG = None


def _ensure_streams():
    """--noconsole 打包后 stdout/stderr 是 None，任何 print 都会抛异常；先接管。"""
    global _APP_LOG
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        _APP_LOG = open(os.path.join(LOGS_DIR, "app.log"), "a",
                        encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        _APP_LOG = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _APP_LOG
    if sys.stderr is None:
        sys.stderr = _APP_LOG


_ensure_streams()

CREATE_NO_WINDOW = 0x08000000

import launcher as L  # noqa: E402  （复用 Job Object / 配置生成 / 健康检查）
import server  # noqa: E402  （复用面板 HTTP 服务）

PANEL_PORT = int(os.environ.get("WB2API_PANEL_PORT") or L.PANEL_PORT)
GATEWAY_PORT = L.GATEWAY_PORT


def _alert(msg: str, title: str = "WorkBuddy2API", icon: int = 0x00000010):
    """无控制台时用系统弹窗把错误告知用户，避免"双击没反应"。

    icon：0x10 = 错误图标，0x40 = 信息图标。弹窗会阻塞到用户点「确定」为止。
    """
    print(msg, flush=True)
    if FROZEN:
        try:
            ctypes.windll.user32.MessageBoxW(None, msg, title, icon)
        except Exception:
            pass


def _webview2_version():
    """探测系统 WebView2 运行时版本；未安装返回 None（应用窗口依赖它）。"""
    try:
        import winreg
        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    ver, _ = winreg.QueryValueEx(k, "pv")
                    if ver:
                        return str(ver)
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    base = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                        "Microsoft", "EdgeWebView", "Application")
    try:
        versions = sorted(os.listdir(base), reverse=True)
        if versions:
            return versions[0]
    except OSError:
        pass
    return None


def _log_env():
    """把关键环境信息写进 app.log —— 远程排查时用户只要把日志发过来就够。"""
    try:
        import platform
        print("[环境] 系统=%s | 架构=%s" % (platform.platform(), platform.machine()), flush=True)
        print("[环境] 程序=%s" % (sys.executable if FROZEN else os.path.abspath(__file__)), flush=True)
        print("[环境] 资源目录=%s | 数据目录=%s" % (BUNDLE_DIR, APP_DIR), flush=True)
        print("[环境] WebView2=%s" % (_webview2_version()
              or "未检测到（应用窗口无法显示，将自动回退浏览器模式）"), flush=True)
    except Exception:  # noqa: BLE001
        pass


def _find_python() -> str:
    """tasks_all.py 等脚本需要真正的 Python 解释器。

    打包后 sys.executable 指向本 exe（GUI 程序，不能当解释器用），
    所以按「环境变量 → 随包 python → PATH → WorkBuddy 自带运行时」依次探测。
    """
    if not FROZEN:
        return sys.executable or "python"

    env_py = os.environ.get("WB2API_PYTHON")
    if env_py and os.path.isfile(env_py):
        return env_py

    bundled = os.path.join(BUNDLE_DIR, "bin", "python.exe")
    if os.path.isfile(bundled):
        return bundled

    for name in ("python.exe", "python3.exe"):
        found = shutil.which(name)
        if found:
            return found

    home = os.path.expanduser("~")
    for root in (os.path.join(home, ".workbuddy", "binaries", "python", "versions"),
                 os.path.join(home, ".workbuddy", "binaries", "python")):
        if not os.path.isdir(root):
            continue
        try:
            versions = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for ver in versions:
            cand = os.path.join(root, ver, "python.exe")
            if os.path.isfile(cand):
                return cand
    return "python"


# 首次生成 config.json 的默认值。
# **刻意不写死「本机那套配置」**：
#   listen 默认只监听 127.0.0.1 —— 写 0.0.0.0 等于把网关开放给同一局域网里的所有人；
#   api_key 每次现场随机生成 —— 写死 test_key 等于没有鉴权。
# 想局域网共享、想固定 key，改生成出来的 config.json 即可。
DEFAULT_CONFIG = {
    "listen": "127.0.0.1:7863",
    "api_key": "",              # 由 build_default_config() 现场填随机值
    "auth_dir": "auths",
    "state_file": "data/state.json",
    "server": {"max_body_mb": 8},
    "cooldown": {"soft_rate": "600s", "soft_rate_max": "2h"},
    "schedule": {
        "checkin_hours": [9, 21], "travel_hours": [9, 21],
        "activity_hours": [10], "keepalive_hours": [22],
        "checkin_enabled": True, "travel_enabled": True,
        "activity_enabled": True, "keepalive_enabled": True,
    },
    "upstream": {"timeout_seconds": 120, "header_timeout_seconds": 120,
                 "idle_timeout_seconds": 300, "user_agent": ""},
    "features": {"sanitize_blacklist_fingerprints": True},
    "prompt": {"mode": "custom", "file": ""},
    "upstash": {"url": "", "token": ""},
    "pool": {"max_in_flight": 3, "breaker_threshold": 3, "breaker_cooldown": "30m",
             "breaker_cooldown_max": "6h", "idle_weight_per_hour": 0.5,
             "idle_weight_max": 5.0},
    "session_sticky": {"enabled": True, "ttl": "30m", "gc_interval": "5m"},
}


def build_default_config() -> dict:
    """首次生成 config.json 用：深拷贝默认值，并现场生成随机 api_key。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["api_key"] = "wb2_" + secrets.token_hex(16)
    return cfg


def _resolve_data_root() -> str:
    """确定数据根（账号池 auths / data / config.json 的家）。

    顺序（保证既有部署不受影响，同时让 exe 可独立分发）：
      1. 环境变量 WB2API_DIR（显式指定，最高优先 —— 目录不存在就建出来，
         否则「我明明指定了却没用上」极难排查）
      2. exe 同目录已有 config.json 或 auths/ → 便携模式（复制给别人后的运行形态）
      3. 本机既有仓库（开发机场景，向后兼容）
      4. exe 同目录（首次运行的便携形态：会自动生成 config.json / auths / data）
    """
    env_dir = os.environ.get("WB2API_DIR")
    if env_dir:
        try:
            os.makedirs(env_dir, exist_ok=True)
            return env_dir
        except OSError as exc:
            print("[警告] WB2API_DIR=%s 不可用（%r），改用自动探测" % (env_dir, exc), flush=True)
    if (os.path.isfile(os.path.join(APP_DIR, "config.json"))
            or os.path.isdir(os.path.join(APP_DIR, "auths"))):
        return APP_DIR
    if os.path.isdir(L.REPO):
        return L.REPO
    return APP_DIR


def _ensure_data_layout(root: str) -> str:
    """确保数据根下存在 config.json / auths / data，返回 config 路径。"""
    for sub in ("auths", "data"):
        try:
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        except OSError:
            pass
    cfg_path = os.path.join(root, "config.json")
    if not os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(build_default_config(), fh, ensure_ascii=False, indent=2)
            print("[初始化] 已生成默认配置：%s" % cfg_path, flush=True)
            print("[初始化] 网关 api_key 已随机生成；默认只监听 127.0.0.1（要局域网访问改 config.json）",
                  flush=True)
        except OSError as exc:  # noqa: BLE001
            print("[警告] 生成默认配置失败：%r" % exc, flush=True)
    return cfg_path


def _apply_paths():
    """把 launcher / server 的路径常量重定向到「打包资源 + 数据根」。"""
    bin_dir = os.path.join(BUNDLE_DIR, "bin")
    runtime_file = os.path.join(APP_DIR, ".runtime.json")
    gateway_log = os.path.join(LOGS_DIR, "gateway.log")

    # 数据根：便携优先（exe 同目录），开发机回退到既有仓库
    data_root = _resolve_data_root()
    config_path = _ensure_data_layout(data_root)
    print("[就绪] 数据根：%s" % data_root, flush=True)

    # 任务脚本目录：优先用打包进 exe 的副本，其次数据根下的 scripts/
    scripts_dir = os.path.join(BUNDLE_DIR, "scripts")
    if not os.path.isdir(scripts_dir):
        scripts_dir = os.path.join(data_root, "scripts")

    L.BASE_DIR = APP_DIR
    L.LOG_DIR = LOGS_DIR
    L.BIN_DIR = bin_dir
    L.REPO = data_root
    L.NATIVE_CONFIG = os.path.join(APP_DIR, "config-native.json")
    L.RUNTIME_FILE = runtime_file

    server.BASE_DIR = APP_DIR
    server.BIN_DIR = bin_dir
    server.REPO = data_root
    server.SCRIPTS_DIR = scripts_dir
    server.AUTHS_DIR = os.path.join(data_root, "auths")
    server.STATE_FILE = os.path.join(data_root, "data", "state.json")
    server.RUNTIME_FILE = runtime_file
    server.GATEWAY_LOG = gateway_log
    server.INDEX_HTML = os.path.join(BUNDLE_DIR, "index.html")
    server.NATIVE = True
    server.python_exe = _find_python
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            server.REPO_CFG = json.load(fh)
    except Exception:  # noqa: BLE001
        pass
    # 关键：换了 REPO_CFG 就必须重算 API_KEY / API_BASE。否则面板会拿着导入期算出的
    # 空 key 去请求网关 → 401 → 页面顶部出现一条 [object Object]（错误对象被当字符串渲染）。
    server.refresh_api_config()
    print("[就绪] 网关鉴权：api_key %s" % ("已设置" if server.API_KEY else "未设置"), flush=True)

    os.makedirs(LOGS_DIR, exist_ok=True)
    # login.exe 把登录状态写到「当前盘:\tmp\...」（源码写死 /tmp）
    drive_tmp = os.path.join(os.path.splitdrive(APP_DIR)[0] + "\\", "tmp")
    try:
        os.makedirs(drive_tmp, exist_ok=True)
    except OSError:
        pass
    return runtime_file, gateway_log


def _write_runtime(runtime_file: str, pid):
    try:
        with open(runtime_file, "w", encoding="utf-8") as fh:
            json.dump({
                "mode": "native",
                "gateway_pid": pid,
                "panel_port": PANEL_PORT,
                "started_at": time.time(),
                "launcher": "app.py",
            }, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _join_self_to_job():
    """把当前进程自己放进 Job（KILL_ON_JOB_CLOSE）。

    为什么要这样：pywebview 的 WebView2 子进程（msedgewebview2.exe）不是我们
    spawn 的，直接挂不上 Job；但子进程会**继承父进程所属的 Job**。把自己加入 Job 后，
    网关、WebView2 及它们的全部子孙都自动成为 Job 成员，进程退出（含被强杀）时
    句柄关闭 → 内核一并回收，不留孤儿进程。

    返回 job 句柄（需保持打开直到退出）；失败返回 None（调用方回退到逐个挂载）。
    """
    try:
        job = L.create_kill_on_close_job()
    except OSError:
        return None
    try:
        if not L.kernel32.AssignProcessToJobObject(job, L.kernel32.GetCurrentProcess()):
            L.kernel32.CloseHandle(job)
            return None
    except Exception:  # noqa: BLE001
        return None
    return job


def _gateway_keeper(job, gateway_exe, config_path, gateway_log, state, stopping):
    """网关守护：面板的「重载账号」会 taskkill 网关，这里检测到退出后自动拉起。"""
    spawn = state["spawn"]
    while not stopping.is_set():
        proc = state.get("proc")
        if proc is None or proc.poll() is None:
            time.sleep(1.5)
            continue
        if stopping.is_set():
            return
        time.sleep(3)
        try:
            fh = open(gateway_log, "ab", buffering=0)
            newp = spawn([gateway_exe, "-config", config_path],
                         cwd=L.REPO, stdout=fh, stderr=subprocess.STDOUT)
            state["proc"] = newp
            _write_runtime(state["runtime_file"], newp.pid)
            print("[守护] 网关已重启 (pid=%d)" % newp.pid, flush=True)
        except Exception as exc:  # noqa: BLE001
            print("[守护] 网关重启失败：%r" % exc, flush=True)
            time.sleep(5)


def main() -> int:
    if os.name != "nt":
        _alert("本应用仅支持 Windows。")
        return 1

    runtime_file, gateway_log = _apply_paths()
    _log_env()

    gateway_exe = os.path.join(BUNDLE_DIR, "bin", "wb2api.exe")
    if not os.path.isfile(gateway_exe):
        _alert("缺少网关程序：\n%s\n\n请确认打包时已包含 bin\\wb2api.exe。" % gateway_exe)
        return 1

    # ---- 端口预检：面板已在跑就把窗口直接拉起来，不重复起服务 ----
    try:
        httpd = server.Server(("127.0.0.1", PANEL_PORT), server.Handler)
    except OSError:
        _open_window_only()
        return 0

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # 日志跟随线程必须在这里起：exe 走的是本函数，而**不会**走 server.main()。
    # 漏了这一句（2026-09-18 修），「自动切换」页的实时日志与事件时间线会永远空白。
    server.start_log_threads()

    cfg = L.build_native_config()

    # 先把自己放进 Job：之后创建的网关 / WebView2 自动继承成员身份，
    # 本进程退出时内核一并回收，避免 WebView2 孤儿残留。
    self_job = _join_self_to_job()
    if self_job is not None:
        job = self_job

        def _spawn(argv, **kw):
            return subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW, **kw)
        print("[就绪] 已加入作业对象：子进程随本进程退出被回收", flush=True)
    else:
        try:
            job = L.create_kill_on_close_job()
        except OSError as exc:
            _alert("无法创建进程作业对象：%r" % exc)
            return 1

        def _spawn(argv, **kw):
            return L.spawn_in_job(job, argv, creationflags=CREATE_NO_WINDOW, **kw)
        print("[就绪] 逐个挂载模式（网关随本进程退出被回收）", flush=True)

    gateway_ready = L.wait_healthz(1.5)
    state = {"proc": None, "runtime_file": runtime_file, "spawn": _spawn}
    if gateway_ready:
        print("[启动] 网关 7863 已在运行，直接复用。", flush=True)
        _write_runtime(runtime_file, None)
    else:
        # 只在**本次真的新起网关**时轮转。复用已有网关时轮转会把 gateway.log 改名，
        # 而复用中的那个网关句柄仍指着改名后的文件 → 面板跟随的新文件会永远是空的。
        L.rotate_log(gateway_log)
        log_fh = open(gateway_log, "ab", buffering=0)
        try:
            proc = _spawn([gateway_exe, "-config", L.NATIVE_CONFIG],
                          cwd=L.REPO, stdout=log_fh, stderr=subprocess.STDOUT)
        except Exception as exc:  # noqa: BLE001
            _alert("启动网关失败：%r\n\n日志：%s" % (exc, gateway_log))
            return 1
        state["proc"] = proc
        print("[启动] 网关 wb2api.exe (pid=%d)" % proc.pid, flush=True)
        if not L.wait_healthz(25):
            _alert("网关 25 秒内未就绪，请查看日志：\n%s" % gateway_log)
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return 1
        print("[就绪] 网关 healthz OK", flush=True)
        _write_runtime(runtime_file, proc.pid)

    stopping = threading.Event()
    threading.Thread(target=_gateway_keeper,
                     args=(job, gateway_exe, L.NATIVE_CONFIG, gateway_log, state, stopping),
                     daemon=True).start()

    print("[就绪] 面板 http://127.0.0.1:%d" % PANEL_PORT, flush=True)
    if _run_window():
        print("[退出] 应用窗口已关闭。", flush=True)
    else:
        print("[退出] 浏览器模式结束（用户已确认）。", flush=True)

    # ---- 窗口关闭：停守护 → 杀网关 → 停面板服务 → 关 Job（内核回收全部后代）----
    stopping.set()
    try:
        if state.get("proc") is not None:
            state["proc"].kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        httpd.shutdown()
    except Exception:  # noqa: BLE001
        pass
    print("[退出] 面板与网关已停止。", flush=True)
    # 关 Job 句柄：KILL_ON_JOB_CLOSE 会把 WebView2 等全部后代一并回收。
    # self_job 模式下这一步也会终止本进程，所以放在最后。
    try:
        L.kernel32.CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _window_fallback(reason: str) -> bool:
    """窗口不可用时的兜底：把面板交给系统默认浏览器，服务继续运行。

    关键：**不能一失败就直接返回** —— main() 里 _run_window() 之后紧接着就是关停逻辑，
    直接返回会把刚拉起的网关一起杀掉，把「窗口起不来」放大成「网关也起不来」。
    （2026-09-18 排查：窗口依赖 WebView2，"没装 WebView2" 的机器症状与健康检查误判
      完全一样，都表现为「双击打开不可用、网关起不来」。）
    """
    wv = _webview2_version()
    msg = ["无法显示应用窗口，已改为用浏览器打开面板。", "", "原因：%s" % reason]
    if not wv:
        msg += ["",
                "本机没有检测到 WebView2 运行时，应用窗口依赖它渲染界面。",
                "装一次即可（装完重新打开本程序）：",
                "https://developer.microsoft.com/microsoft-edge/webview2/"]
    msg += ["", "面板地址：http://127.0.0.1:%d" % PANEL_PORT,
            "", "点击「确定」后用默认浏览器打开它。"]
    _alert("\n".join(msg))
    try:
        webbrowser.open("http://127.0.0.1:%d" % PANEL_PORT)
    except Exception:  # noqa: BLE001
        pass
    _alert("网关与面板正在运行，可继续在浏览器里使用。\n\n"
           "用完后点击「确定」，服务停止并退出本程序。",
           title="WorkBuddy2API 运行中", icon=0x00000040)
    return False


def _run_window() -> bool:
    """显示应用窗口。

    返回 True  = 窗口正常显示并已关闭（可以收摊退出）
    返回 False = 窗口不可用，已回退为浏览器模式（用户已确认用完）
    """
    try:
        import webview
    except ImportError as exc:
        return _window_fallback("缺少 pywebview：%r" % exc)
    try:
        webview.create_window(
            "WorkBuddy2API 控制面板",
            "http://127.0.0.1:%d" % PANEL_PORT,
            width=1320, height=880, min_size=(980, 640),
            text_select=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _window_fallback("创建窗口失败：%r" % exc)
    try:
        try:
            webview.start(gui="edgechromium", debug=False)
        except Exception:  # noqa: BLE001
            webview.start(debug=False)
    except Exception as exc:  # noqa: BLE001
        return _window_fallback("窗口启动失败：%r" % exc)
    return True


def _open_window_only():
    """已有实例在跑：只把窗口打开，不再重复起服务。"""
    print("[提示] 面板已在运行，直接打开窗口。", flush=True)
    _run_window()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        # 兜底：任何未预料的异常都要落在日志里并弹窗告知，不再「双击没反应」
        import traceback
        tb = traceback.format_exc()
        print(tb, flush=True)
        last = tb.strip().splitlines()[-1] if tb.strip() else "未知错误"
        _alert("程序异常退出：\n\n%s\n\n详细日志：%s"
               % (last, os.path.join(LOGS_DIR, "app.log")))
        sys.exit(1)
