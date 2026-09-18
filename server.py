#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy2API 本地控制面板 —— 后端服务
=======================================

零依赖（仅 Python 标准库）的本地 HTTP 服务，为 workbuddy2api 提供可直接访问的
可视化控制台。**不修改 workbuddy2api 仓库里的任何文件。**

它做三件事：

1. 同源托管 dashboard 页面，并反向代理 7863 的只读接口
   （/status、/healthz、/v1/models），绕开浏览器 CORS 限制——页面上点按钮
   就等于执行 `curl.exe -s http://localhost:7863/status -H "Authorization: Bearer <key>"`。

2. 一键执行运维动作（白名单，不接受任意命令）：
   批量签到 / 成长计划积分任务 / 积分日报 / 重载账号 / 登录新账号 / 连通性探测。

3. 跟随容器日志，解析成结构化事件流（其中 `fallback_earliest_expiry` 就是
   "账号不可用时自动切换"的真实事件），供页面渲染时间线。

启动：
    python server.py [--port 7864] [--no-browser]
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# 基本路径与配置
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE_DIR, "index.html")

def _default_repo() -> str:
    """网关仓库 workbuddy2api 的默认位置。

    依次尝试：与本项目并列的 ../workbuddy2api → ~/workbuddy2api。
    两者都不存在时返回前者（首次运行会由上层创建所需目录）。
    可用环境变量 WB2API_DIR 显式覆盖。
    """
    parent = os.path.dirname(BASE_DIR)
    for cand in (os.path.join(parent, "workbuddy2api"),
                 os.path.join(os.path.expanduser("~"), "workbuddy2api")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(parent, "workbuddy2api")


# workbuddy2api 仓库位置（可用环境变量覆盖）
REPO = os.environ.get("WB2API_DIR") or _default_repo()
DOCKER = os.environ.get("WB2API_DOCKER", "docker")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _load_repo_config() -> dict:
    path = os.path.join(REPO, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


REPO_CFG = _load_repo_config()

def refresh_api_config():
    """按当前 REPO_CFG 重算 API_PORT / API_BASE / API_KEY。

    **外部改过 REPO_CFG 之后必须调用它。** 典型场景：app.py 先解析出便携数据根，
    再把该目录下的 config.json 赋给 server.REPO_CFG —— 此时不重算的话，面板会一直
    拿着「导入期」算出的空 key 去请求网关，被 401 拒绝（网关错误体是
    `{"error":{...}}` 对象），页面顶部就出现一条 `[object Object]`。
    """
    global API_PORT, API_BASE, API_KEY
    listen = str((REPO_CFG or {}).get("listen") or ":7863")
    try:
        API_PORT = int(listen.rsplit(":", 1)[-1])
    except ValueError:
        API_PORT = 7863
    API_BASE = "http://127.0.0.1:%d" % API_PORT
    API_KEY = str((REPO_CFG or {}).get("api_key") or "")


refresh_api_config()

_auth_dir = str(REPO_CFG.get("auth_dir") or "auths")
AUTHS_DIR = os.path.normpath(os.path.join(REPO, _auth_dir)) if not os.path.isabs(_auth_dir) else _auth_dir
SCRIPTS_DIR = os.path.join(REPO, "scripts")
STATE_FILE = os.path.normpath(os.path.join(REPO, str(REPO_CFG.get("state_file") or "data/state.json")))

# 本机 WorkBuddy 客户端数据目录（跨平台：~/.workbuddy）。
# 面板只读取其中的「明文身份信息」，不触碰任何加密凭据。
WORKBUDDY_HOME = os.path.join(os.path.expanduser("~"), ".workbuddy")

# 本机客户端「登录态」目录（CodeBuddyExtension 共享 auth 目录，Cockpit Tools 同款定位方式）。
#   Windows : %LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\
#   macOS   : ~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/
#   文件    : workbuddy-desktop.info（当前登录态，明文 JSON）
#             workbuddy-desktop.<时间戳>...info（客户端每次写入前自动留的备份）
#   结构    : account{uid,nickname,...} + auth{accessToken,refreshToken,domain,expiresAt}
#   注意    : 顶层 accessToken/domain 属于 CodeBuddy 体系（codebuddy.cn），与本模块无关；
#             本模块只读该目录，不写入、不解密。
def _default_wb_auth_dir() -> str:
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                            "CodeBuddyExtension", "Data", "Public", "auth")
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(local, "CodeBuddyExtension", "Data", "Public", "auth")
    return os.path.join(os.path.expanduser("~"), ".local", "share",
                        "CodeBuddyExtension", "Data", "Public", "auth")


WB_AUTH_DIR = os.environ.get("WB2API_CLIENT_AUTH_DIR") or _default_wb_auth_dir()
WB_DESKTOP_AUTH_FILE = "workbuddy-desktop.info"

CONTAINER = os.environ.get("WB2API_CONTAINER", "workbuddy2api")
DEFAULT_IMAGE = "workbuddy2api-wb2api:latest"

# ---------------------------------------------------------------------------
# 运行模式：native（launcher 直启 wb2api.exe）/ docker（容器）
# launcher 启动面板时设置 WB2API_NATIVE=1；单独跑面板时若 bin\wb2api.exe 存在也按 native。
# ---------------------------------------------------------------------------

BIN_DIR = os.path.join(BASE_DIR, "bin")
RUNTIME_FILE = os.path.join(BASE_DIR, ".runtime.json")
GATEWAY_LOG = os.path.join(BASE_DIR, "logs", "gateway.log")

NATIVE = bool(os.environ.get("WB2API_NATIVE")) or os.path.isfile(os.path.join(BIN_DIR, "wb2api.exe"))


def read_runtime() -> dict:
    try:
        with open(RUNTIME_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def python_exe() -> str:
    return sys.executable or "python"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ---------------------------------------------------------------------------
# 动作白名单定义（页面展示用；实际命令在 build_action 里构造）
# ---------------------------------------------------------------------------

TASKS = {
    "first_buddy": {
        "label": "领取首只 Buddy",
        "file": "task_first_buddy.py",
        "reward": "+300 积分 / +8 能量",
        "note": "上报 1 条活跃事件 → 领养协议 → 领取 Buddy。实测可行。",
        "experimental": False,
    },
    "chat_5": {
        "label": "与 AI 聊天 5 次",
        "file": "task_chat5.py",
        "reward": "+100 积分 / +5 能量",
        "note": "向 /v2/report 补发 chat_request_send 事件补齐到 5/5。",
        "experimental": False,
    },
    "model_chat": {
        "label": "体验 GLM-5.2 对话",
        "file": "task_model_chat.py",
        "reward": "+100 积分 / +5 能量",
        "note": "accept → 真实调用 glm-5.2 → 上报 1 条事件。",
        "experimental": False,
    },
    "richmeow": {
        "label": "桌面端对话（实验）",
        "file": "task_richmeow.py",
        "reward": "+100 积分 / +5 能量 / Buddy 盲盒",
        "note": "上游判定疑似走桌面端专属通道，脚本作者实测多次上报进度不动，多半无效。",
        "experimental": True,
    },
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

_cache: dict = {}
_cache_lock = threading.Lock()


def cached(key: str, ttl: float, producer):
    """极简 TTL 缓存，避免每次刷新都去问 docker。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = producer()
    with _cache_lock:
        _cache[key] = (now, val)
    return val


def detect_image() -> str:
    def probe():
        try:
            out = run_capture([DOCKER, "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=25)
            for line in out.splitlines():
                line = line.strip()
                if "wb2api" in line and "<none>" not in line:
                    return line
        except Exception:
            pass
        return DEFAULT_IMAGE

    return cached("image", 60, probe)


def detect_container() -> str:
    def probe():
        try:
            out = run_capture([DOCKER, "ps", "-a", "--format", "{{.Names}}"], timeout=25)
            names = [n.strip() for n in out.splitlines() if n.strip()]
            for n in names:
                if CONTAINER in n:
                    return n
            for n in names:
                if "wb2api" in n:
                    return n
        except Exception:
            pass
        return CONTAINER

    return cached("container", 30, probe)


def docker_available() -> bool:
    def probe():
        try:
            out = run_capture([DOCKER, "version", "--format", "{{.Server.Version}}"], timeout=25)
            return bool(out.strip())
        except Exception:
            return False

    return cached("docker", 30, probe)


def run_capture(argv, timeout=60, cwd=None) -> str:
    """同步执行命令并返回合并后的输出（用于短命令，如 docker inspect）。"""
    proc = subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    return ANSI_RE.sub("", proc.stdout or "")


def api_call(path: str, method: str = "GET", body: bytes | None = None, timeout: float = 15.0):
    """调用 7863 上的网关接口，自动带上 Bearer。返回 (status, parsed_or_text)。"""
    req = Request(API_BASE + path, data=body, method=method)
    if API_KEY:
        req.add_header("Authorization", "Bearer " + API_KEY)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except (URLError, OSError) as exc:
        return 0, {"error": "无法连接 %s：%s" % (API_BASE, exc)}
    try:
        return status, json.loads(raw)
    except Exception:
        return status, raw


def read_auth_files() -> list:
    """读取 auths/ 目录，脱敏返回每个账号的元信息（不返回 token）。"""
    out = []
    if not os.path.isdir(AUTHS_DIR):
        return out
    for name in sorted(os.listdir(AUTHS_DIR)):
        if not name.startswith("workbuddy-") or not name.endswith(".json"):
            continue
        path = os.path.join(AUTHS_DIR, name)
        item = {"file": name, "bytes": 0, "mtime": None, "uid": "", "nickname": ""}
        try:
            st = os.stat(path)
            item["bytes"] = st.st_size
            item["mtime"] = st.st_mtime
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            acc = data.get("account") or {}
            item["uid"] = acc.get("uid") or ""
            item["nickname"] = acc.get("nickname") or ""
            item["has_token"] = bool((data.get("auth") or {}).get("accessToken"))
            item["has_refresh"] = bool((data.get("auth") or {}).get("refreshToken"))
            item["expires_at"] = (data.get("auth") or {}).get("expiresAt")
        except Exception as exc:
            item["error"] = str(exc)
        out.append(item)
    return out


def panel_dir() -> str:
    """面板自身的数据目录（与 .runtime.json 同级）。

    **必须是函数而非模块常量**：打包成 exe 后 app.py 会在导入之后把 BASE_DIR 改写成
    exe 同目录，导入期算出的常量会落在 PyInstaller 的 _MEIPASS 临时目录里
    （每次启动都变、进程退出即删）。同类坑此前在 API_KEY 上踩过一次。
    """
    return BASE_DIR


def removed_dir() -> str:
    """被移除账号的备份目录（**放在面板侧**，不进账号池）。

    刻意不放进 auths/：网关会扫该目录加载账号，多一个子目录属于无谓风险；
    放这里既不影响网关，也便于用户自己翻回来。
    """
    return os.path.join(panel_dir(), "removed-auths")


def remove_pool_account(file: str = "", uid: str = "") -> dict:
    """把账号池里的账号移出：文件先备份到面板的 removed-auths/，再从 auths/ 删除。

    安全约束：只接受 AUTHS_DIR 下形如 `workbuddy-<uid>.json` 的文件名
    （basename 归一 + 目录越界校验），杜绝路径穿越。
    """
    name = os.path.basename(str(file or "").strip())
    uid = str(uid or "").strip()
    if not name and uid:
        name = "workbuddy-%s.json" % uid
    if not (name.startswith("workbuddy-") and name.endswith(".json")):
        return {"ok": False, "error": "非法的账号文件名：%s" % (name or "(空)")}
    path = os.path.normpath(os.path.join(AUTHS_DIR, name))
    if os.path.dirname(path) != os.path.normpath(AUTHS_DIR):
        return {"ok": False, "error": "拒绝操作账号池以外的路径"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "账号文件不存在：%s" % name}

    nickname = ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            nickname = ((json.load(fh) or {}).get("account") or {}).get("nickname") or ""
    except Exception:
        pass

    backup = ""
    try:
        os.makedirs(removed_dir(), exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(removed_dir(), "%s.%s" % (stamp, name))
        seq = 1
        while os.path.exists(backup):          # 同秒内多次移除不覆盖
            backup = os.path.join(removed_dir(), "%s.%d.%s" % (stamp, seq, name))
            seq += 1
        os.replace(path, backup)
    except Exception as exc:
        return {"ok": False, "error": "移除失败（备份阶段）：%r" % exc}

    return {
        "ok": True,
        "uid": uid or name[len("workbuddy-"):-len(".json")],
        "nickname": nickname,
        "file": name,
        "backup": backup,
        "remaining": len([a for a in read_auth_files() if a.get("uid")]),
    }


def read_local_accounts() -> dict:
    """扫描本机 WorkBuddy 客户端，列出它登录过的账号（只读明文身份，不触碰加密凭据）。

    数据源（2026-09 实测确认，均为明文）：
      ~/.workbuddy/storage/skeleton/account-snapshot.json
          当前主账号「非敏感字段」快照（客户端源码注释明确：不含 token）
      ~/.workbuddy/storage/user-<uid>[-personal]/
          客户端登录过的账号目录；同一账号的 -personal 为附加数据目录
    结果与账号池 auths/ 比对：已在池中的回填昵称与 token 到期时间。

    说明：客户端把 accessToken/refreshToken 加密落盘（AES-256-GCM，
    密钥由本地 masterKey + userId 经 HKDF 派生），面板不读取、不解密。
    """
    out = {
        "home": WORKBUDDY_HOME,
        "available": False,
        "current_uid": "",
        "current_nickname": "",
        "accounts": [],
        "note": "",
    }
    if not os.path.isdir(WORKBUDDY_HOME):
        out["note"] = "未找到本机 WorkBuddy 数据目录：%s" % WORKBUDDY_HOME
        return out

    pool = {}
    for acc in read_auth_files():
        if acc.get("uid"):
            pool[acc["uid"]] = acc

    # 1) 当前主账号（客户端明文快照）
    cur_uid = cur_nick = ""
    snap_path = os.path.join(WORKBUDDY_HOME, "storage", "skeleton", "account-snapshot.json")
    try:
        with open(snap_path, "r", encoding="utf-8") as fh:
            primary = (json.load(fh) or {}).get("primary") or {}
        cur_uid = str(primary.get("uid") or "")
        cur_nick = str(primary.get("nickname") or "")
    except Exception:
        pass

    # 2) 登录过的账号目录（user-<uid> / user-<uid>-personal 归一）
    uids = []
    storage = os.path.join(WORKBUDDY_HOME, "storage")
    if os.path.isdir(storage):
        for name in sorted(os.listdir(storage)):
            if not name.startswith("user-"):
                continue
            uid = name[len("user-"):]
            if uid.endswith("-personal"):
                uid = uid[: -len("-personal")]
            if uid and uid not in uids:
                uids.append(uid)
    if cur_uid and cur_uid not in uids:
        uids.insert(0, cur_uid)

    for uid in uids:
        home_pool = pool.get(uid) or {}
        nick = cur_nick if (uid == cur_uid and cur_nick) else (home_pool.get("nickname") or "")
        out["accounts"].append({
            "uid": uid,
            "short": uid[:8],
            "nickname": nick,
            "is_current": uid == cur_uid,
            "in_pool": uid in pool,
            "pool_file": home_pool.get("file") or "",
            "expires_at": home_pool.get("expires_at"),
            "pool_mtime": home_pool.get("mtime"),
        })
    # 排序：当前登录 → 已入池 → 未入池
    out["accounts"].sort(key=lambda x: (not x["is_current"], not x["in_pool"], x["uid"]))
    out["current_uid"] = cur_uid
    out["current_nickname"] = cur_nick
    out["available"] = bool(out["accounts"])
    if not out["accounts"]:
        out["note"] = "客户端数据目录存在，但未发现已登录账号记录"
    return out


# ---------------------------------------------------------------------------
# 本机客户端登录态（明文 JSON）读取与导入
# ---------------------------------------------------------------------------

def _jwt_claims(token: str) -> dict:
    """解出 JWT 的 payload（不验签，仅用于取 sub / exp 等元信息）。"""
    try:
        parts = str(token).split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _desktop_login_from_file(path: str) -> dict | None:
    """从单个登录态文件提取账号摘要；非 JSON / 无 auth 段 / 无 token 时返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    auth = data.get("auth") or {}
    acc = data.get("account") or {}
    token = str(auth.get("accessToken") or "")
    if not token:
        return None
    claims = _jwt_claims(token)
    uid = str(acc.get("uid") or "") or str(claims.get("sub") or "")
    if not uid:
        return None
    # 客户端把 expiresAt 存成毫秒；账号池用秒
    exp_ms = auth.get("expiresAt")
    expires_at = None
    if isinstance(exp_ms, (int, float)) and exp_ms > 0:
        expires_at = int(exp_ms / 1000)
    elif claims.get("exp"):
        try:
            expires_at = int(claims["exp"])
        except Exception:
            expires_at = None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    # 登录态指纹（用于「拉黑后重新登录自动解除」）：
    #   以 refreshToken 为准 —— 实测同一账号多次写入（客户端备份间仅 lastRefreshTime 变化）
    #   refreshToken / accessToken 均不变，只有真正重新登录才换发新 token。
    #   没有 refreshToken 时退回 accessToken；两者都无则留空（仅身份条目）。
    #   不返回 token 本身，只返回截断哈希。
    fp_src = str(auth.get("refreshToken") or "") or str(auth.get("accessToken") or "")
    fingerprint = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:16] if fp_src else ""
    return {
        "uid": uid,
        "nickname": str(acc.get("nickname") or ""),
        "domain": str(auth.get("domain") or ""),
        "expires_at": expires_at,
        "has_refresh": bool(auth.get("refreshToken")),
        "token_sub": str(claims.get("sub") or ""),
        "fingerprint": fingerprint,
        "file": os.path.basename(path),
        "mtime": mtime,
    }


# ---------------------------------------------------------------------------
# 本机账号拉黑名单（面板侧状态；不写客户端、不写账号池）
#
# 语义：拉黑后该账号不再出现在「本机 WorkBuddy 客户端」列表里；
#       一旦它在客户端**重新登录**（登录态 token 换发 → 指纹变化）就自动解除。
# 依据：实测同一账号的多次客户端备份，refreshToken / accessToken 均不变
#       （只有 lastRefreshTime 在变），所以「token 指纹」能区分"重新登录"与"自动续期"。
# ---------------------------------------------------------------------------

PANEL_STATE_LOCK = threading.Lock()
BLOCKLIST_KEY = "blocked_local_logins"


def panel_state_file() -> str:
    return os.path.join(panel_dir(), "panel-state.json")


def _load_panel_state() -> dict:
    try:
        with open(panel_state_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_panel_state(state: dict) -> None:
    path = panel_state_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_blocklist() -> dict:
    """当前拉黑名单：{uid: {uid, nickname, fingerprint, blocked_at, source}}。"""
    with PANEL_STATE_LOCK:
        blocked = _load_panel_state().get(BLOCKLIST_KEY)
    return blocked if isinstance(blocked, dict) else {}


def blocklist_items() -> list:
    items = [dict(v) for v in read_blocklist().values() if isinstance(v, dict)]
    items.sort(key=lambda x: -(x.get("blocked_at") or 0))
    return items


def apply_blocklist(accounts: list) -> tuple:
    """按拉黑名单过滤本机账号；指纹变化（= 客户端重新登录）时自动解除。

    返回 (保留的账号, 本次自动解除的账号)。
    """
    blocked = read_blocklist()
    if not blocked:
        return list(accounts), []
    kept, unblocked, changed = [], [], False
    for a in accounts:
        uid = str(a.get("uid") or "")
        rec = blocked.get(uid)
        if not rec:
            kept.append(a)
            continue
        if str(rec.get("fingerprint") or "") != str(a.get("fingerprint") or ""):
            unblocked.append({"uid": uid, "nickname": a.get("nickname") or ""})
            blocked.pop(uid, None)
            changed = True
            kept.append(a)            # 重新登录过 → 恢复显示
        # else：仍在拉黑中，跳过（不显示）
    if changed:
        with PANEL_STATE_LOCK:
            state = _load_panel_state()
            state[BLOCKLIST_KEY] = blocked
            state["updated_at"] = int(time.time())
            _save_panel_state(state)
    return kept, unblocked


def block_local_login(uid: str) -> dict:
    """拉黑一个本机客户端账号：从列表隐藏，直到它在客户端重新登录。"""
    uid = str(uid or "").strip()
    if not uid:
        return {"ok": False, "error": "缺少 uid"}
    hit = None
    for a in scan_desktop_logins(unfiltered=True).get("accounts") or []:
        if str(a.get("uid")) == uid:
            hit = a
            break
    if hit is None:
        # 「仅身份 · 无凭据」的条目（客户端有账号目录、但 auth 目录里没有它的登录态）
        # 也要能拉黑：指纹留空，等它重新登录产生凭据后指纹变非空 → 自动解除。
        for a in read_local_accounts().get("accounts") or []:
            if str(a.get("uid")) == uid:
                hit = {"uid": uid, "nickname": a.get("nickname") or "",
                       "fingerprint": "", "file": ""}
                break
    if hit is None:
        return {"ok": False, "error": "该账号不在本机客户端的账号记录里"}
    with PANEL_STATE_LOCK:
        state = _load_panel_state()
        blocked = state.get(BLOCKLIST_KEY)
        if not isinstance(blocked, dict):
            blocked = {}
        blocked[uid] = {
            "uid": uid,
            "nickname": hit.get("nickname") or "",
            "fingerprint": hit.get("fingerprint") or "",
            "blocked_at": int(time.time()),
            "source": hit.get("file") or "",
        }
        state[BLOCKLIST_KEY] = blocked
        state["updated_at"] = int(time.time())
        _save_panel_state(state)
    return {"ok": True, "uid": uid, "nickname": hit.get("nickname") or "",
            "blocked_count": len(blocked)}


def unblock_local_login(uid: str) -> dict:
    """手动解除拉黑（用户反悔时用；重新登录则无需手动，会自动解除）。"""
    uid = str(uid or "").strip()
    if not uid:
        return {"ok": False, "error": "缺少 uid"}
    with PANEL_STATE_LOCK:
        state = _load_panel_state()
        blocked = state.get(BLOCKLIST_KEY)
        if not isinstance(blocked, dict):
            blocked = {}
        rec = blocked.pop(uid, None)
        state[BLOCKLIST_KEY] = blocked
        state["updated_at"] = int(time.time())
        _save_panel_state(state)
    if not rec:
        return {"ok": False, "error": "该账号不在拉黑名单中"}
    return {"ok": True, "uid": uid, "nickname": (rec or {}).get("nickname") or "",
            "blocked_count": len(blocked)}


def scan_desktop_logins(unfiltered: bool = False) -> dict:
    """扫描本机客户端登录态（当前文件 + 客户端自动备份），按 uid 去重取最新。

    只读。返回脱敏摘要（不含 token 明文）；token 是否过期用 expiresAt 判断。
    默认会套用拉黑名单（被拉黑的账号不出现在 accounts 里，重新登录后自动恢复）；
    unfiltered=True 时返回未过滤的全量（供拉黑动作自身核对账号身份）。
    """
    out = {
        "dir": WB_AUTH_DIR,
        "available": False,
        "logged_out": False,
        "accounts": [],
        "blocked": [],
        "unblocked": [],
        "note": "",
    }
    if not os.path.isdir(WB_AUTH_DIR):
        out["note"] = "未找到客户端登录态目录：%s" % WB_AUTH_DIR
        return out

    current_path = os.path.join(WB_AUTH_DIR, WB_DESKTOP_AUTH_FILE)
    out["logged_out"] = os.path.exists(current_path + ".logged-out")

    pool = {}
    for acc in read_auth_files():
        if acc.get("uid"):
            pool[acc["uid"]] = acc

    by_uid = {}
    for name in sorted(os.listdir(WB_AUTH_DIR)):
        if not name.startswith("workbuddy-desktop") or name.endswith(".logged-out"):
            continue
        path = os.path.join(WB_AUTH_DIR, name)
        if not os.path.isfile(path):
            continue
        info = _desktop_login_from_file(path)
        if not info:
            continue
        info["is_current"] = (name == WB_DESKTOP_AUTH_FILE)
        old = by_uid.get(info["uid"])
        # 当前登录文件优先；同为备份时取较新的
        if (old is None
                or (info["is_current"] and not old["is_current"])
                or (info["is_current"] == old["is_current"] and info["mtime"] > old["mtime"])):
            by_uid[info["uid"]] = info

    now = int(time.time())
    accounts = []
    for uid, info in by_uid.items():
        home_pool = pool.get(uid) or {}
        info["in_pool"] = uid in pool
        info["pool_file"] = home_pool.get("file") or ""
        info["pool_expires_at"] = home_pool.get("expires_at")
        info["expired"] = bool(info["expires_at"] and info["expires_at"] < now)
        accounts.append(info)
    # 排序：当前登录 → 未入池 → uid
    accounts.sort(key=lambda x: (not x["is_current"], x["in_pool"], x["uid"]))

    if unfiltered:
        out["accounts"] = accounts
        out["blocked"] = blocklist_items()
        out["available"] = bool(accounts)
        return out

    kept, unblocked = apply_blocklist(accounts)
    blocked = blocklist_items()
    out["accounts"] = kept
    out["unblocked"] = unblocked
    out["blocked"] = blocked
    out["available"] = bool(kept or blocked)
    if not kept:
        out["note"] = ("可扫描到的账号均已被拉黑（共 %d 个）" % len(blocked)) if blocked \
            else "目录存在，但未发现可解析的登录态文件"
    return out


def import_desktop_logins(uids=None, dry_run: bool = False) -> dict:
    """把本机客户端登录态导入账号池 auths/（只读来源文件，只写账号池目录）。

    uids 为空表示导入全部；dry_run=True 时只返回将要执行的动作，不落盘。
    """
    scan = scan_desktop_logins()
    if not scan["available"]:
        return {"ok": False, "error": scan.get("note") or "未发现可导入的客户端登录态"}

    want = set(uids or [])
    targets = [a for a in scan["accounts"] if (not want or a["uid"] in want)]
    if not targets:
        return {"ok": False, "error": "没有匹配的账号（uid：%s）" % ", ".join(sorted(want))}

    results = []
    for info in targets:
        src = os.path.join(WB_AUTH_DIR, info["file"])
        try:
            with open(src, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            results.append({"uid": info["uid"], "nickname": info["nickname"],
                            "ok": False, "error": repr(exc)})
            continue
        auth = data.get("auth") or {}
        acc = data.get("account") or {}
        payload = {
            "account": {
                "uid": info["uid"],
                "enterpriseId": str(acc.get("enterpriseId") or ""),
                "nickname": info["nickname"],
            },
            "auth": {
                "accessToken": str(auth.get("accessToken") or ""),
                "refreshToken": str(auth.get("refreshToken") or ""),
                "expiresAt": info["expires_at"] or 0,
                "domain": info["domain"],
            },
        }
        dest = os.path.join(AUTHS_DIR, "workbuddy-%s.json" % info["uid"])
        exists = os.path.exists(dest)
        row = {
            "uid": info["uid"],
            "nickname": info["nickname"],
            "domain": info["domain"],
            "expires_at": info["expires_at"],
            "expired": info["expired"],
            "action": "覆盖更新" if exists else "新增",
            "file": os.path.basename(dest),
            "source": info["file"],
            "ok": True,
        }
        if dry_run:
            row["dry_run"] = True
            results.append(row)
            continue
        try:
            os.makedirs(AUTHS_DIR, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
        except Exception as exc:
            row["ok"] = False
            row["error"] = repr(exc)
        results.append(row)

    return {
        "ok": True,
        "dry_run": dry_run,
        "count": len(results),
        "imported": [r for r in results if r.get("ok")],
        "failed": [r for r in results if not r.get("ok")],
        "note": "已写入账号池；点「重载账号」让网关加载" if not dry_run else "仅预览，未写入",
    }


def read_state_file():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 6:
        return key[0] + "*" * (len(key) - 1)
    return key[:3] + "*" * max(4, len(key) - 6) + key[-3:]


def next_fire(hours, now=None):
    """复刻 Go 端 scheduler.nextFire：返回 now 之后最近的整点。"""
    import datetime

    now = now or datetime.datetime.now()
    best = None
    for h in hours or []:
        try:
            h = int(h)
        except Exception:
            continue
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t <= now:
            t = t + datetime.timedelta(days=1)
        if best is None or t < best:
            best = t
    return best


# ---------------------------------------------------------------------------
# cc-switch 深链协议：本机探测 + 唤起
#
# 为什么必须运行时探测：`ccswitch://` 是**安装 cc-switch 时才写进注册表**的自定义协议，
# 而且只写 HKCU（按用户）——换台机器 / 换个 Windows 账户就没了。
# 面板上曾经把「协议已注册 + 处理程序路径」整句**写死**在页面里，在别人的电脑上是假信息，
# 还把开发者的本机路径暴露了出去。这里一律实测（tests/test-deeplink.py 有守卫断言）。
# ---------------------------------------------------------------------------

CCSWITCH_SCHEME = "ccswitch:"
CCSWITCH_PROGID = "ccswitch"


def _exe_from_command(cmd: str) -> str:
    """从协议命令串里取出 exe 路径：'"C:\\...\\x.exe" "%1"' → 'C:\\...\\x.exe'。"""
    cmd = str(cmd or "").strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 1:
            return cmd[1:end]
    return cmd.split(" ")[0] if cmd else ""


def ccswitch_protocol() -> dict:
    """探测本机 ccswitch:// 的处理程序（只读注册表，不启动任何进程）。"""
    out = {
        "supported": os.name == "nt",
        "registered": False,
        "command": "",
        "exe": "",
        "exe_exists": False,
        "hive": "",
        "note": "",
    }
    if os.name != "nt":
        out["note"] = "当前平台不是 Windows，深链由系统自行解析"
        return out
    try:
        import winreg
    except Exception as exc:  # pragma: no cover
        out["note"] = "无法读取注册表：%r" % exc
        return out

    # HKCR 是合并视图（HKLM + HKCU），先查它拿到 Windows 实际会用到的那个；
    # 再单独查两个 hive 便于把「谁注册的」说清楚。
    probes = (
        ("HKCR", winreg.HKEY_CLASSES_ROOT, r"%s\shell\open\command" % CCSWITCH_PROGID),
        ("HKCU", winreg.HKEY_CURRENT_USER,
         r"Software\Classes\%s\shell\open\command" % CCSWITCH_PROGID),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Classes\%s\shell\open\command" % CCSWITCH_PROGID),
    )
    for hive, root, path in probes:
        try:
            with winreg.OpenKey(root, path) as key:
                cmd = str(winreg.QueryValueEx(key, "")[0] or "")
        except FileNotFoundError:
            continue
        except Exception:
            continue
        if not cmd:
            continue
        exe = _exe_from_command(cmd)
        out.update({
            "registered": True,
            "command": cmd,
            "exe": exe,
            "exe_exists": bool(exe) and os.path.isfile(exe),
            "hive": hive,
        })
        break

    if not out["registered"]:
        out["note"] = ("未检测到 ccswitch:// 协议：这台电脑没有安装 cc-switch，"
                       "或者用的是没跑过安装程序的绿色版")
    elif not out["exe_exists"]:
        out["note"] = ("注册表里指向的 cc-switch.exe 不存在：%s（安装被移动或删除，建议重装）"
                       % (out["exe"] or "?"))
    return out


def open_deeplink(url: str) -> dict:
    """用系统默认处理程序唤起自定义协议链接。

    **白名单只允许 `ccswitch:`** —— 这个接口在本机 7864 上，绝不能变成通用协议启动器。
    Windows 走 os.startfile（ShellExecute，与浏览器/WebView2 无关，两种模式都可用）；
    macOS 用 open，Linux 用 xdg-open。
    """
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "缺少 url"}
    if not url.lower().startswith(CCSWITCH_SCHEME):
        return {"ok": False, "error": "只允许唤起 %s 深链" % CCSWITCH_SCHEME}

    info = ccswitch_protocol()
    if info["supported"] and not info["registered"]:
        return {"ok": False, "error": info["note"] or "本机未注册 ccswitch:// 协议",
                "protocol": info}
    try:
        if os.name == "nt":
            os.startfile(url)          # noqa: S606 —— 上面已做协议白名单校验
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "唤起失败：%r" % exc, "protocol": info}
    return {"ok": True, "url": url, "protocol": info}


def first_model_id() -> str:
    """取网关 /v1/models 的第一个模型名。

    写死的模型名（曾经是 deepseek-v4-flash）换台机器/换个上游就可能不存在，
    所以默认值一律取实时列表。
    """
    try:
        status, data = api_call("/v1/models", "GET", None, timeout=15)
        for item in (data or {}).get("data") or []:
            mid = str((item or {}).get("id") or "").strip()
            if mid:
                return mid
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 作业（后台命令）管理
# ---------------------------------------------------------------------------

JOBS: dict = {}
JOB_ORDER: list = []
JOBS_LOCK = threading.Lock()
MAX_JOBS = 40


def oem_encoding() -> str:
    """系统 OEM 代码页（中文 Windows 为 cp936）——Windows 原生命令的管道输出编码。"""
    if os.name == "nt":
        try:
            import ctypes
            return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
        except Exception:  # noqa: BLE001
            pass
    try:
        import locale
        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:  # noqa: BLE001
        return "utf-8"


def decode_output(raw: bytes) -> str:
    """子进程输出逐行自适应解码。

    同一个面板要同时面对两类子进程：
      * Go 程序（wb2api / login / credit / signin_bin）与已强制 PYTHONIOENCODING=utf-8
        的 Python 脚本 → 输出 UTF-8；
      * Windows 原生命令（taskkill / cmd / docker）→ 输出系统 OEM 代码页（中文为 cp936）。
    先按 UTF-8 严格解码，失败再退到 OEM 代码页，避免中文变成方块。
    """
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode(oem_encoding())
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", "replace")


def start_job(title: str, argv: list, cwd=None, display: str | None = None,
              on_finish=None, env=None) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "title": title,
        "cmd": display or " ".join(argv),
        "status": "running",
        "exit_code": None,
        "started_at": time.time(),
        "ended_at": None,
        "lines": [],
        "proc": None,
    }
    with JOBS_LOCK:
        JOBS[jid] = job
        JOB_ORDER.append(jid)
        while len(JOB_ORDER) > MAX_JOBS:
            JOBS.pop(JOB_ORDER.pop(0), None)

    def worker():
        try:
            # Windows 下 Python 子进程 stdout 接管道时默认按系统 locale（GBK）编码，
            # 与下方按 UTF-8 解码不一致 → 面板输出乱码。统一强制子进程 stdio 为 UTF-8。
            # 这两个环境变量对非 Python 子进程（wb2api/login/credit 等 Go exe）无副作用。
            run_env = dict(env) if env is not None else os.environ.copy()
            run_env.setdefault("PYTHONIOENCODING", "utf-8")
            run_env.setdefault("PYTHONUTF8", "1")
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # 按字节读、逐行自适应解码（见 decode_output）：
                # UTF-8 的 Go/Python 输出与 cp936 的 Windows 原生命令都能正确显示。
                bufsize=0,
                creationflags=CREATE_NO_WINDOW,
            )
            job["proc"] = proc
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                line = ANSI_RE.sub("", decode_output(raw).rstrip("\r\n"))
                with JOBS_LOCK:
                    job["lines"].append(line)
            proc.wait()
            job["exit_code"] = proc.returncode
            job["status"] = "done" if proc.returncode == 0 else "failed"
        except FileNotFoundError:
            job["status"] = "failed"
            job["lines"].append("[错误] 找不到可执行文件：%s（请确认 Docker Desktop 已启动且在 PATH 中）" % argv[0])
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["lines"].append("[错误] " + repr(exc))
        finally:
            job["ended_at"] = time.time()
            job["proc"] = None
            job["lines"].append("__EXIT__%s" % job.get("exit_code"))
            if on_finish:
                try:
                    on_finish(job)
                except Exception as exc:  # noqa: BLE001
                    job["lines"].append("[on_finish 错误] " + repr(exc))

    threading.Thread(target=worker, daemon=True).start()
    return job


def public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "title": job["title"],
        "cmd": job["cmd"],
        "status": job["status"],
        "exit_code": job["exit_code"],
        "started_at": job["started_at"],
        "ended_at": job["ended_at"],
        "lines": list(job["lines"]),
    }


# ---------------------------------------------------------------------------
# 容器日志跟随 → 事件流
# ---------------------------------------------------------------------------

EVENTS: list = []
EVENTS_LOCK = threading.Lock()
EVENT_SEQ = 0
LOG_TAIL = []          # 原始日志环形缓冲（页面"实时日志"用）
LOG_TAIL_MAX = 500

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\s+(.*)$")
# native 模式 wb2api.exe 用 Go 标准日志格式：2026/09/12 10:40:06 …
_TS_RE2 = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$")


def to_local_ts(s: str) -> str:
    """docker logs -t 给的是 UTC RFC3339；转成本地时间，避免与日志正文里的本地时间对不上。"""
    try:
        if "/" in s[:10]:  # Go log 格式本身就是本地时间，直接规范化
            return s.replace("/", "-")
        raw = s.rstrip("Z")
        if "." in raw:
            head, frac = raw.split(".", 1)
            raw = head + "." + frac[:6]
        dt = datetime.datetime.fromisoformat(raw)
        if s.endswith("Z"):
            dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s


def classify_event(line: str):
    """把一行容器日志解析成 (kind, level, uid, text, ts)；认不出就按普通日志归类。"""
    if not line.strip():
        return None
    ts = None
    m = _TS_RE.match(line)
    if m:
        ts = to_local_ts(m.group(1))
        line = m.group(2)
    else:
        m2 = _TS_RE2.match(line)  # native 网关的 Go 标准日志时间戳
        if m2:
            ts = to_local_ts(m2.group(1))
            line = m2.group(2)
    line = line.strip()

    uid = ""
    mu = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", line)
    if mu:
        uid = mu.group(1)

    low = line.lower()
    if "fallback_earliest_expiry" in line:
        return ("failover", "warn", uid, line, ts)
    if low.startswith("checkin ") or " checkin " in low:
        return ("checkin", "info", uid, line, ts)
    if low.startswith("travel ") or " travel " in low:
        return ("travel", "info", uid, line, ts)
    if low.startswith("activity ") or " activity " in low:
        return ("activity", "info", uid, line, ts)
    if low.startswith("keepalive ") or " keepalive " in low:
        return ("keepalive", "info", uid, line, ts)
    if "session dead" in line or "禁用" in line:
        return ("disable", "error", uid, line, ts)
    if "no_healthy_account" in line or "all accounts unavailable" in line:
        return ("exhausted", "error", uid, line, ts)
    if "listening on" in low or "loaded " in low or "已启用" in line or "已禁用" in line:
        return ("boot", "muted", uid, line, ts)
    if low.startswith("bye") or "shutdown" in low:
        return ("boot", "muted", uid, line, ts)
    if "chat" in low or "ttfb" in low or "tokens" in low:
        return ("chat", "muted", uid, line, ts)
    if "err" in low or "fail" in low or "error" in low:
        return ("error", "error", uid, line, ts)
    return ("log", "muted", uid, line, ts)


def push_event(kind, level, uid, text, ts_raw=None):
    global EVENT_SEQ
    with EVENTS_LOCK:
        EVENT_SEQ += 1
        ev = {
            "seq": EVENT_SEQ,
            "ts": ts_raw or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "recv": time.time(),
            "kind": kind,
            "level": level,
            "uid": uid,
            "text": text,
        }
        EVENTS.append(ev)
        if len(EVENTS) > 800:
            del EVENTS[: len(EVENTS) - 800]
    with JOBS_LOCK:
        LOG_TAIL.append({"seq": EVENT_SEQ, "level": level, "kind": kind, "text": text, "uid": uid})
        if len(LOG_TAIL) > LOG_TAIL_MAX:
            del LOG_TAIL[: len(LOG_TAIL) - LOG_TAIL_MAX]


def log_follower():
    """常驻线程：native 模式跟随 gateway.log 文件增量；docker 模式跟随容器日志。"""
    if NATIVE:
        offset = 0
        while True:
            try:
                if os.path.exists(GATEWAY_LOG):
                    size = os.path.getsize(GATEWAY_LOG)
                    if size < offset:      # 日志被轮转/重启，从头再读
                        offset = 0
                    if size > offset:
                        with open(GATEWAY_LOG, "r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(offset)
                            chunk = fh.read()
                            offset = fh.tell()
                        for raw in chunk.splitlines():
                            parsed = classify_event(ANSI_RE.sub("", raw.rstrip("\r\n")))
                            if parsed:
                                push_event(*parsed)
                            else:
                                push_event("log", "muted", "", raw)
                time.sleep(1)
            except Exception:
                time.sleep(3)
        # 不可达（defensive）：native 分支永不跳出
    while True:
        container = detect_container()
        if not docker_available():
            time.sleep(8)
            continue
        try:
            proc = subprocess.Popen(
                [DOCKER, "logs", "-f", "--tail", "0", "-t", container],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            assert proc.stdout is not None
            for raw in proc.stdout:
                raw = ANSI_RE.sub("", raw.rstrip("\r\n"))
                # docker 可能给每行加容器名前缀（compose）——统一剥掉
                if "|" in raw[:40]:
                    head, _, tail = raw.partition("|")
                    if head.strip().lower().startswith(container[:12].lower()):
                        raw = tail.strip()
                parsed = classify_event(raw)
                if parsed:
                    push_event(*parsed)
                else:
                    push_event("log", "muted", "", raw)
            proc.wait()
        except Exception:
            pass
        time.sleep(5)


def preload_logs(lines=200):
    """启动时先灌一批历史日志，避免页面一开始空白。"""
    if NATIVE:
        try:
            with open(GATEWAY_LOG, "r", encoding="utf-8", errors="replace") as fh:
                out = fh.readlines()[-lines:]
        except Exception:
            return
        for raw in out:
            parsed = classify_event(ANSI_RE.sub("", raw.rstrip("\r\n")))
            if parsed:
                push_event(*parsed)
        return
    try:
        out = run_capture([DOCKER, "logs", "--tail", str(lines), "-t", detect_container()], timeout=30)
    except Exception:
        return
    for raw in out.splitlines():
        if "|" in raw[:40]:
            head, _, tail = raw.partition("|")
            if head.strip().lower().startswith(detect_container()[:12].lower()):
                raw = tail.strip()
        parsed = classify_event(raw)
        if parsed:
            push_event(*parsed)


def start_log_threads():
    """启动「日志跟随」的两个常驻线程：历史日志预灌 + 增量跟随。

    **所有面板入口都必须调用它** —— server.main()（bat / 直接跑 server.py 的形态）
    与 app.py 的 main()（打包后的 exe 形态）。

    历史 bug（2026-09-18）：这两行原来直接写在 main() 里，而 exe 的入口是 app.py、
    从不调用 server.main()，于是线程压根没起 —— 「自动切换」页的实时日志与事件时间线
    永远空白，但 gateway.log 本身在正常增长，看起来像"面板坏了"，其实是没人去读它。
    抽成函数就是为了让两个入口共用同一处，避免以后只改一边再漏。
    """
    threading.Thread(target=preload_logs, daemon=True).start()
    threading.Thread(target=log_follower, daemon=True).start()


# ---------------------------------------------------------------------------
# 动作构造
# ---------------------------------------------------------------------------

def mount(p: str) -> str:
    return p.replace("\\", "/")


def build_action(action: str, params: dict):
    """统一返回 6 元组 (title, argv, cwd, display, on_finish, env)；**不支持时 argv=None**。

    on_finish：可选回调 (job_dict)，native 登录动作用它解析 poll 输出并落盘 auth 文件。
    env：可选环境变量（native 的 tasks_all 需要注入 WB2API_SCRIPTS / WB2API_AUTHS）。

    归一化的原因：native 分支过去直接把 build_action_native() 的返回值透传，
    而它在「缺二进制 / 不支持的动作」时返回的是 (None, error) 两元组 —— 调用方按
    六元组拆包会抛 ValueError，连接被直接掐断（无任何响应），比报个错更难排查。
    """
    r = build_action_native(action, params) if NATIVE else build_action_docker(action, params)
    if len(r) == 6:
        return r
    if len(r) == 4:                       # docker 分支：(title, argv, cwd, display)
        return (r[0], r[1], r[2], r[3], None, None)
    err = r[1] if len(r) > 1 and r[1] else "不支持的动作：%s" % action   # (None, error)
    return (err, None, None, None, None, None)


def build_action_native(action: str, params: dict):
    """native 模式：直接调用 bin\\ 下的 exe 与本机 Python，不经 Docker。"""
    gw = os.path.join(BIN_DIR, "wb2api.exe")
    signin = os.path.join(BIN_DIR, "signin_bin.exe")
    credit = os.path.join(BIN_DIR, "credit.exe")
    login = os.path.join(BIN_DIR, "login.exe")
    for p, tag in ((signin, "signin_bin.exe"), (credit, "credit.exe"), (login, "login.exe")):
        if (action in ("signin", "credit", "credit_json", "login_url", "login_poll")
                and not os.path.isfile(p)):
            return (None, "native 模式缺少 %s（bin\\ 目录不完整）" % tag)

    if action == "signin":
        argv = [signin, AUTHS_DIR]
        return ("批量签到（所有账号）", argv, None, '"%s" "%s"' % (signin, AUTHS_DIR), None, None)

    if action == "credit":
        # credit.exe 默认读工作目录下的 ./auths（docker 模式靠 -w /app），
        # native 模式把 cwd 设为仓库根，让 ./auths 指向账号目录
        argv = [credit, "-pretty"]
        return ("积分日报", argv, REPO, '"%s" -pretty  (cwd=%s)' % (credit, REPO), None, None)

    if action == "credit_json":
        argv = [credit]
        return ("积分明细（JSON）", argv, REPO, '"%s"  (cwd=%s)' % (credit, REPO), None, None)

    if action == "restart":
        rt = read_runtime()
        pid = rt.get("gateway_pid")
        if not pid:
            return (None, "找不到网关进程信息（%s 不存在或无 gateway_pid）" % RUNTIME_FILE)
        # 杀网关进程；launcher 的守护线程检测到退出后会自动拉起（= 重载账号）
        argv = ["taskkill", "/PID", str(pid), "/F", "/T"]
        return ("重载账号（重启网关进程）", argv, None,
                "taskkill /PID %s /F /T  → launcher 自动重启 wb2api.exe" % pid, None, None)

    if action == "login_url":
        argv = [login, "url"]
        return ("获取登录授权链接", argv, None, '"%s" url' % login, None, None)

    if action == "login_poll":
        # login.exe 只输出 JSON 到 stdout；落盘 auth 文件是 login.sh（bash+python3）的活，
        # native 模式由本服务接手：on_finish 解析输出 → 写 auths/workbuddy-<uid>.json
        argv = [login, "poll"]
        return ("完成登录（轮询换取凭证并落盘）", argv, None, '"%s" poll → 自动保存到 %s' % (login, AUTHS_DIR),
                _finalize_login, None)

    if action == "tasks_all":
        only = str(params.get("only") or "").strip()
        account = str(params.get("account") or "ALL").strip() or "ALL"
        yes = bool(params.get("yes"))
        env = os.environ.copy()
        env["WB2API_SCRIPTS"] = SCRIPTS_DIR
        env["WB2API_AUTHS"] = AUTHS_DIR
        # 优先用打包进 exe 的 tasks_all.exe（目标机器无需安装 Python），
        # 否则回退到「本机 Python + tasks_all.py」（开发/docker 场景）。
        bundled = os.path.join(BIN_DIR, "tasks_all.exe")
        if os.path.isfile(bundled):
            argv = [bundled, account]
            display = '"%s" %s%s%s' % (bundled, account,
                                       (" --only " + only) if only else "",
                                       " --yes" if yes else "")
        else:
            script = os.path.join(BASE_DIR, "tasks_all.py")
            if not os.path.exists(script):
                return (None, "找不到聚合脚本：%s（也未找到 bin\\tasks_all.exe）" % script)
            argv = [python_exe(), "-u", script, account]
            display = ('set WB2API_SCRIPTS=%s&& set WB2API_AUTHS=%s&& "%s" "%s" %s%s%s'
                       % (SCRIPTS_DIR, AUTHS_DIR, python_exe(), script, account,
                          (" --only " + only) if only else "", " --yes" if yes else ""))
        if only:
            argv += ["--only", only]
        if yes:
            argv.append("--yes")
        seg = {"accept": "接取所有任务", "run": "补跑（不推荐）",
               "claim": "领取所有积分奖励"}.get(only, "接取 + 领奖")
        mode = "真实执行" if yes else "仅预览(dry-run)"
        title = "成长计划 · %s（%s / %s）" % (seg, mode, account)
        return (title, argv, BASE_DIR, display, None, env)

    if action == "task":
        return (None, "native 模式请使用「一键接取 / 一键领奖」（单任务脚本依赖容器内的路径）")

    return (None, "不支持的动作：%s" % action)


def save_auth_payload(data) -> dict:
    """把登录流程拿到的 token bundle 落盘为 auths/workbuddy-<uid>.json。

    字段结构复刻 login.sh:120-139（与 internal/auth 读取格式一致）。
    返回 {ok, uid, nickname, file, action, expires_at, domain} 或 {ok:False, error}。
    """
    if not isinstance(data, dict) or not data.get("access_token"):
        return {"ok": False, "error": "未解析到 token JSON"}
    uid = str(data.get("uid") or "")
    if not uid:
        return {"ok": False, "error": "输出中无 uid"}
    auth = {
        "account": {
            "uid": uid,
            "enterpriseId": data.get("enterprise_id") or "",
            "nickname": data.get("nickname") or "",
        },
        "auth": {
            "accessToken": data["access_token"],
            "refreshToken": data.get("refresh_token") or "",
            "expiresAt": int(time.time()) + int(data.get("expires_in") or 0),
            "domain": data.get("domain") or "",
        },
    }
    os.makedirs(AUTHS_DIR, exist_ok=True)
    path = os.path.join(AUTHS_DIR, "workbuddy-%s.json" % uid)
    action = "覆盖更新" if os.path.exists(path) else "新增"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(auth, fh, ensure_ascii=False, indent=1)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}
    return {
        "ok": True, "uid": uid,
        "nickname": auth["account"]["nickname"],
        "file": os.path.basename(path), "action": action,
        "expires_at": auth["auth"]["expiresAt"], "domain": auth["auth"]["domain"],
    }


def _finalize_login(job: dict):
    """native 登录收尾：从 login.exe poll 的输出解析 token JSON → 落盘 auth 文件。"""
    raw = "\n".join(job.get("lines") or [])
    data = None
    for chunk in reversed(raw.split("\n")):
        chunk = chunk.strip()
        if chunk.startswith("{"):
            try:
                data = json.loads(chunk)
                break
            except Exception:
                continue
    res = save_auth_payload(data)
    if not res.get("ok"):
        job["lines"].append("[落盘] %s，跳过自动保存（可手动检查上方输出）" % res.get("error"))
        return
    job["lines"].append("[落盘] ✓ 已保存（%s）: %s"
                        % (res["action"], os.path.join(AUTHS_DIR, res["file"])))
    job["lines"].append("[落盘] 点「重载账号」让网关加载新账号（会自动重启 wb2api.exe）")


# ---------------------------------------------------------------------------
# OAuth 授权：发起 / 轮询（供面板弹窗使用，同步返回，不走 job）
# ---------------------------------------------------------------------------

LOGIN_STATE_TTL_SEC = 600     # 授权链接有效期（面板展示用）
LOGIN_POLL_INTERVAL_SEC = 2   # 轮询间隔


def login_begin() -> dict:
    """发起授权：调 login.exe url 拿授权链接（同时写入 login 状态文件）。"""
    exe = os.path.join(BIN_DIR, "login.exe")
    if not os.path.isfile(exe):
        return {"ok": False, "error": "找不到 login.exe：%s" % exe}
    try:
        proc = subprocess.run([exe, "url"], capture_output=True, timeout=30,
                              creationflags=CREATE_NO_WINDOW)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}
    out = decode_output(proc.stdout or b"").strip()
    err = decode_output(proc.stderr or b"").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": err or out or ("login url 退出码 %d" % proc.returncode)}
    url = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("http"):
            url = line
            break
    if not url:
        return {"ok": False, "error": "未解析到授权链接：%s" % (out or err or "(无输出)")}
    return {
        "ok": True, "auth_url": url,
        "expires_in": LOGIN_STATE_TTL_SEC, "interval": LOGIN_POLL_INTERVAL_SEC,
    }


def login_check() -> dict:
    """轮询一次：调 login.exe poll。

    成功 → 落盘 auths/ 并返回账号信息（pending=False）；
    仍在等待 → {ok:True, pending:True}（前端继续轮询）；
    会话失效/其他错误 → {ok:False, pending:False, error}（前端停止轮询并提示）。
    """
    exe = os.path.join(BIN_DIR, "login.exe")
    if not os.path.isfile(exe):
        return {"ok": False, "pending": False, "error": "找不到 login.exe：%s" % exe}
    try:
        proc = subprocess.run([exe, "poll"], capture_output=True, timeout=30,
                              creationflags=CREATE_NO_WINDOW)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "pending": False, "error": repr(exc)}
    out = decode_output(proc.stdout or "")
    err = decode_output(proc.stderr or "")
    msg = (err or out).strip()
    if proc.returncode != 0:
        if ("waiting for login" in msg) or ("登录未完成" in msg):
            return {"ok": True, "pending": True}
        if ("read state" in msg) or ("先跑 login url" in msg):
            return {"ok": False, "pending": False,
                    "error": "授权会话已失效或已被使用，请重新发起授权"}
        return {"ok": False, "pending": False,
                "error": msg or ("login poll 退出码 %d" % proc.returncode)}
    data = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except Exception:
                continue
    res = save_auth_payload(data)
    if not res.get("ok"):
        return {"ok": False, "pending": False, "error": res.get("error")}
    return {"ok": True, "pending": False, "account": res}


def login_add_with_token(body: dict) -> dict:
    """用粘贴的 access token（JWT）直接添加账号（refresh token 可选）。

    uid / 昵称 / 过期时间从 JWT payload 解析（sub / preferred_username / exp），
    不需要网关或上游参与。
    """
    token = str((body or {}).get("access_token") or "").strip()
    if not token:
        return {"ok": False, "error": "access token 不能为空"}
    claims = _jwt_claims(token)
    uid = str(claims.get("sub") or "")
    if not uid:
        return {"ok": False, "error": "无法从 token 解析出 uid，请确认粘贴的是完整的 access token（JWT）"}
    exp = claims.get("exp")
    try:
        expires_in = max(60, int(exp) - int(time.time())) if exp else 30 * 86400
    except Exception:  # noqa: BLE001
        expires_in = 30 * 86400
    data = {
        "access_token": token,
        "refresh_token": str(body.get("refresh_token") or "").strip(),
        "expires_in": expires_in,
        "domain": str(body.get("domain") or "www.workbuddy.cn"),
        "uid": uid,
        "nickname": str(claims.get("preferred_username") or claims.get("nickname") or ""),
    }
    res = save_auth_payload(data)
    if res.get("ok"):
        res["uid_short"] = uid[:8]
    return res


def build_action_docker(action: str, params: dict):
    """docker 模式：原有容器命令。"""
    container = detect_container()

    # 注意：docker exec 默认不分配 TTY（-t 才分配），没有 compose 的 -T 开关。
    if action == "signin":
        argv = [DOCKER, "exec", container, "/app/signin_bin", "/app/auths"]
        return ("批量签到（所有账号）", argv, None, "docker exec %s /app/signin_bin /app/auths" % container)

    if action == "credit":
        argv = [DOCKER, "exec", "-w", "/app", container, "/app/credit", "-pretty"]
        return ("积分日报", argv, None, "docker exec -w /app %s /app/credit -pretty" % container)

    if action == "credit_json":
        argv = [DOCKER, "exec", "-w", "/app", container, "/app/credit"]
        return ("积分明细（JSON）", argv, None, "docker exec -w /app %s /app/credit" % container)

    if action == "restart":
        argv = [DOCKER, "restart", container]
        return ("重载账号（重启容器）", argv, None, "docker restart %s" % container)

    if action == "login_url":
        argv = [DOCKER, "exec", container, "/app/login", "url"]
        return ("获取登录授权链接", argv, None, "docker exec %s /app/login url" % container)

    if action == "login_poll":
        argv = [DOCKER, "exec", container, "/app/login", "poll"]
        return ("完成登录（轮询换取凭证）", argv, None, "docker exec %s /app/login poll" % container)

    if action == "task":
        name = str(params.get("task") or "")
        spec = TASKS.get(name)
        if not spec:
            return (None, "未知任务：%s" % name)
        account = str(params.get("account") or "ALL").strip() or "ALL"
        yes = bool(params.get("yes"))
        image = detect_image()
        argv = [
            DOCKER, "run", "--rm", "--user", "root",
            "-v", "%s:/app/scripts:ro" % mount(SCRIPTS_DIR),
            "-v", "%s:/root/workbuddy2api/auths" % mount(AUTHS_DIR),
            "--entrypoint", "python3",
            image,
            "/app/scripts/%s" % spec["file"],
            account,
        ]
        if yes:
            argv.append("--yes")
        mode = "执行" if yes else "试运行(dry-run)"
        title = "积分任务 · %s（%s / %s）" % (spec["label"], mode, account)
        display = (
            "docker run --rm --user root "
            "-v \"%s:/app/scripts:ro\" "
            "-v \"%s:/root/workbuddy2api/auths\" "
            "--entrypoint python3 %s /app/scripts/%s %s%s"
            % (SCRIPTS_DIR, AUTHS_DIR, image, spec["file"], account, " --yes" if yes else "")
        )
        return (title, argv, None, display)

    if action == "tasks_all":
        # 成长计划任务：一键接取 / 一键领奖。任务清单在脚本内动态拉取，
        # 平台新增任务自动适应，不必逐个任务点。两段都不产生任何上游上报。
        only = str(params.get("only") or "").strip()
        account = str(params.get("account") or "ALL").strip() or "ALL"
        yes = bool(params.get("yes"))
        script = os.path.join(BASE_DIR, "tasks_all.py")
        if not os.path.exists(script):
            return (None, "找不到聚合脚本：%s" % script)
        image = detect_image()
        argv = [
            DOCKER, "run", "--rm", "--user", "root",
            "-v", "%s:/app/scripts:ro" % mount(SCRIPTS_DIR),
            "-v", "%s:/app/tasks_all.py:ro" % mount(script),
            "-v", "%s:/root/workbuddy2api/auths" % mount(AUTHS_DIR),
            "--entrypoint", "python3",
            image, "/app/tasks_all.py", account,
        ]
        if only:
            argv += ["--only", only]
        if yes:
            argv.append("--yes")
        seg = {"accept": "接取所有任务", "run": "补跑（不推荐）",
               "claim": "领取所有积分奖励"}.get(only, "接取 + 领奖")
        mode = "真实执行" if yes else "仅预览(dry-run)"
        title = "成长计划 · %s（%s / %s）" % (seg, mode, account)
        display = (
            "docker run --rm --user root "
            "-v \"%s:/app/scripts:ro\" "
            "-v \"%s:/app/tasks_all.py:ro\" "
            "-v \"%s:/root/workbuddy2api/auths\" "
            "--entrypoint python3 %s /app/tasks_all.py %s%s%s"
            % (SCRIPTS_DIR, script, AUTHS_DIR, image, account,
               (" --only " + only) if only else "", " --yes" if yes else "")
        )
        return (title, argv, None, display)

    return (None, "不支持的动作：%s" % action)


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "wb2api-dashboard/1.0"
    protocol_version = "HTTP/1.1"

    # --- 基础设施 -------------------------------------------------------
    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def send_error(self, code, message=None, explain=None):  # noqa: N802
        """协议级错误也回 JSON（默认实现会发一张 HTML 错误页）。

        默认的 HTML 错误页会让前端 r.json() 只报
        `Unexpected token '<', "<!DOCTYPE " is not valid JSON`，
        真实原因（如 501 Unsupported method）被完全盖掉 —— 这个坑踩过一次。
        """
        try:
            title = "Error"
            try:
                title = self.responses[code][0]
            except Exception:
                pass
            payload = {"error": message or title, "http_status": int(code)}
            if explain:
                payload["detail"] = explain
            self.close_connection = True
            self._json(code, payload)
        except Exception:
            BaseHTTPRequestHandler.send_error(self, code, message, explain)

    # 请求体缓存：保证「同一次请求里读第二次不会阻塞」。
    # do_POST 会在最顶部无条件调用一次（见那边的注释），各路由再调也只是取缓存。
    _body_cache = None

    def _read_json(self, limit: int = 1 << 20):
        if self._body_cache is not None:
            return self._body_cache
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._body_cache = {}
            return self._body_cache
        raw = self.rfile.read(min(length, limit))
        try:
            self._body_cache = json.loads(raw.decode("utf-8"))
        except Exception:
            self._body_cache = {}
        return self._body_cache

    # --- 路由 -----------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)

        if path in ("/", "/index.html"):
            return self._serve_index()
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path == "/api/meta":
            return self._json(200, self.build_meta())
        if path == "/api/status":
            return self._proxy("/status")
        if path == "/api/health":
            return self._proxy("/healthz")
        if path == "/api/models":
            return self._proxy("/v1/models")
        if path == "/api/events":
            since = int((query.get("since") or ["0"])[0])
            with EVENTS_LOCK:
                items = [e for e in EVENTS if e["seq"] > since]
                latest = EVENT_SEQ
            return self._json(200, {"events": items, "latest": latest})
        if path == "/api/logs":
            with JOBS_LOCK:
                items = list(LOG_TAIL)
            return self._json(200, {"lines": items, "latest": EVENT_SEQ})
        if path == "/api/jobs":
            with JOBS_LOCK:
                items = [public_job(JOBS[i]) for i in reversed(JOB_ORDER) if i in JOBS]
            return self._json(200, {"jobs": items})
        if path == "/api/job":
            jid = (query.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(jid)
                snap = public_job(job) if job else None
            if not snap:
                return self._json(404, {"error": "job not found"})
            return self._json(200, snap)
        if path == "/api/ping":
            return self._json(200, {"ok": True, "ts": time.time()})
        if path == "/api/probe":
            ep = (query.get("ep") or ["status"])[0]
            model = (query.get("model") or [""])[0]
            prompt = (query.get("prompt") or [""])[0]
            return self._probe(ep, model, prompt)
        return self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        # 每个请求重置请求体缓存：Handler 实例是按「连接」复用的（keep-alive），
        # 不清空就会把上一个请求的 body 当成这一次的。
        self._body_cache = None
        # 必须先无条件把请求体读干净。protocol_version 是 HTTP/1.1，浏览器会复用同一条
        # 长连接；任何「不读 body 就直接 return」的路由都会把 body 留在 socket 缓冲里，
        # 下一个请求的请求行前面就粘上了这些字节 → 服务端按方法名 `{}POST` 找不到处理函数
        # → 501（HTML 错误页）→ 前端 r.json() 报
        # `Unexpected token '<', "<!DOCTYPE " is not valid JSON`。
        # 精确定位过：/api/login/begin 与 /api/login/check 就是直接 return 没读 body，
        # 而前端 jpost 对它们恒定发送 `{}`；OAuth 登录成功后的下一次请求
        # （正是 /api/run 的「重载账号」）因此必挂。
        body = self._read_json()

        if path == "/api/run":
            action = str(body.get("action") or "")
            title, argv, cwd, display, on_finish, env = build_action(action, body)
            if argv is None:
                return self._json(400, {"error": title})
            job = start_job(title, argv, cwd=cwd, display=display,
                            on_finish=on_finish, env=env)
            return self._json(200, public_job(job))

        if path == "/api/job/stop":
            jid = str(body.get("id") or "")
            with JOBS_LOCK:
                job = JOBS.get(jid)
                proc = job.get("proc") if job else None
            if not job:
                return self._json(404, {"error": "job not found"})
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            return self._json(200, {"ok": True})

        if path == "/api/chat/test":
            # 真实发一条 chat/completions，用于验证网关端到端可用（会消耗一次对话）
            model = str(body.get("model") or "").strip()
            if not model:
                # 不写死模型名：取 /v1/models 的第一个，避免换台机器报 model not found
                model = first_model_id()
                if not model:
                    return self._json(200, {"status": 0, "elapsed_ms": 0,
                                            "error": "拿不到模型列表（网关未就绪？）"})
            prompt = str(body.get("prompt") or "你好，请只回复两个字：可用")
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": 32,
            }).encode("utf-8")
            t0 = time.time()
            status, data = api_call("/v1/chat/completions", "POST", payload, timeout=120)
            return self._json(200, {
                "status": status,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "response": data,
            })

        if path == "/api/login/begin":
            # 发起 OAuth 授权：返回授权链接（前端弹窗展示 + 可选自动打开浏览器）
            return self._json(200, login_begin())

        if path == "/api/login/check":
            # 轮询一次授权状态：pending 继续等；成功则落盘 auths/ 并返回账号
            return self._json(200, login_check())

        if path == "/api/login/token":
            # 用粘贴的 access token（JWT）直接添加账号（refresh token 可选）
            body = self._read_json()
            return self._json(200, login_add_with_token(body))

        if path == "/api/import-local-login":
            # 把本机客户端登录态（明文 JSON）导入账号池 auths/。
            # 只读来源文件；uids 为空 = 导入全部；dry_run = 仅预览不落盘。
            body = self._read_json()
            uids = body.get("uids")
            if isinstance(uids, str):
                uids = [uids]
            if not isinstance(uids, list):
                uids = None
            dry = bool(body.get("dry_run"))
            result = import_desktop_logins(uids, dry_run=dry)
            result["scan"] = scan_desktop_logins()
            return self._json(200, result)

        if path == "/api/block-local-login":
            # 拉黑本机客户端账号：从「本机 WorkBuddy 客户端」列表隐藏，
            # 直到该账号在客户端重新登录（登录态指纹变化）自动解除。
            body = self._read_json()
            result = block_local_login(body.get("uid"))
            if result.get("ok"):
                result["scan"] = scan_desktop_logins()
            return self._json(200, result)

        if path == "/api/unblock-local-login":
            # 手动解除拉黑（重新登录时会自动解除，这里供用户反悔）
            body = self._read_json()
            result = unblock_local_login(body.get("uid"))
            if result.get("ok"):
                result["scan"] = scan_desktop_logins()
            return self._json(200, result)

        if path == "/api/open-deeplink":
            # 由本机后端唤起自定义协议（白名单仅 ccswitch:）。
            # 走 ShellExecute，因此与「面板跑在 exe 还是浏览器里」无关；
            # 协议没注册时返回明确原因，前端据此弹框提示（而不是静默无反应）。
            return self._json(200, open_deeplink(body.get("url")))

        if path == "/api/pool/remove":
            # 从账号池移除账号：先备份到（面板目录）removed-auths/，再删 auths/ 里的文件。
            # 前端随后调用 restart 重载网关，使移除立即生效。
            body = self._read_json()
            return self._json(200, remove_pool_account(body.get("file"), body.get("uid")))

        return self._json(404, {"error": "not found"})

    def _serve_index(self):
        try:
            with open(INDEX_HTML, "rb") as fh:
                body = fh.read()
        except Exception:
            body = ("<h1>缺少 index.html</h1><p>请确认 %s 与 server.py 在同一目录。</p>"
                    % INDEX_HTML).encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def _proxy(self, api_path: str):
        status, data = api_call(api_path)
        if status == 0:
            hint = ("请确认网关进程 wb2api.exe 正在运行（双击 启动.bat），端口 %d 已监听。" % API_PORT
                    if NATIVE else
                    "请确认容器 %s 正在运行（docker ps），端口 %d 已监听。" % (CONTAINER, API_PORT))
            return self._json(503, {"error": "网关不可达", "detail": data, "hint": hint})
        if isinstance(data, (dict, list)):
            return self._json(200 if status < 400 else status, data)
        return self._send(status, str(data).encode("utf-8"))

    # --- 接口连通性探针 ---------------------------------------------------
    # 页面上「测试此接口」按钮走后端真实请求 7863，原样返回 HTTP 状态码 / 耗时 / body，
    # 这样浏览器才能拿到真实状态码（前端直接 fetch 7863 会被 CORS 拦掉）。
    PROBE_GET = {
        "healthz": "/healthz",
        "status": "/status",
        "models": "/v1/models",
    }

    def _probe(self, ep: str, model: str, prompt: str):
        t0 = time.time()
        if ep == "chat":
            path = "/v1/chat/completions"
            payload = json.dumps({
                "model": model or "deepseek-v4.1-flash",
                "messages": [{"role": "user", "content": prompt or "只回复两个字：可用"}],
                "stream": False,
                "max_tokens": 32,
            }).encode("utf-8")
            status, data = api_call(path, "POST", payload, timeout=120)
        elif ep in self.PROBE_GET:
            path = self.PROBE_GET[ep]
            status, data = api_call(path)
        else:
            return self._json(400, {"error": "unknown probe: %s" % ep})

        ms = int((time.time() - t0) * 1000)
        if status == 0:
            return self._json(200, {
                "ep": ep, "path": path, "reachable": False, "ok": False,
                "status": 0, "elapsed_ms": ms,
                "hint": ("连不上网关：确认 wb2api.exe 在运行（双击 启动.bat）、端口 %d 已监听。" % API_PORT
                         if NATIVE else
                         "连不上网关：确认容器 %s 在运行、端口 %d 已监听。" % (CONTAINER, API_PORT)),
                "preview": "", "body": data,
            })
        if isinstance(data, str):
            preview = data
        else:
            try:
                preview = json.dumps(data, ensure_ascii=False, indent=2)
            except Exception:
                preview = str(data)
        if len(preview) > 2000:
            preview = preview[:2000] + " …（已截断）"
        # healthz 的 503 是「网关活着但没有可服务账号」，属正常语义，单独标注
        hint = ""
        if ep == "healthz" and status == 503:
            hint = "503 表示网关进程正常，但当前没有可服务账号（冷却/禁用/占满）——接口本身是通的。"
        return self._json(200, {
            "ep": ep, "path": path, "reachable": True,
            "ok": 200 <= status < 300,
            "status": status, "elapsed_ms": ms,
            "hint": hint, "preview": preview, "body": data,
        })

    # --- meta -----------------------------------------------------------
    def build_meta(self):
        import datetime

        now = datetime.datetime.now()
        cfg = REPO_CFG
        sched = cfg.get("schedule") or {}

        def sched_item(name, hours, enabled_key, enabled_default=True):
            enabled = sched.get(enabled_key, enabled_default)
            nxt = next_fire(hours, now) if enabled else None
            return {
                "name": name,
                "hours": hours if isinstance(hours, list) else [],
                "enabled": bool(enabled),
                "next": nxt.isoformat() if nxt else None,
                "next_in_sec": int((nxt - now).total_seconds()) if nxt else None,
            }

        schedule = [
            sched_item("签到 + 余额解冻", sched.get("checkin_hours"), "checkin_enabled"),
            sched_item("猫猫旅行（领养/派出/领奖）", sched.get("travel_hours"), "travel_enabled"),
            sched_item("活跃上报（点亮连登）", sched.get("activity_hours"), "activity_enabled"),
            sched_item("Token 保活", sched.get("keepalive_hours"), "keepalive_enabled"),
        ]

        runtime_state = {}
        if NATIVE:
            rt = read_runtime()
            pid = rt.get("gateway_pid")
            runtime_state = {
                "mode": "native",
                "status": "running" if pid and _pid_alive(pid) else "stopped",
                "pid": pid,
                "started_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(rt.get("started_at") or time.time())),
                "image": "wb2api.exe（原生进程，无 Docker）",
                "restart_count": "-",
                "health": "-",
                "uptime_sec": int(time.time() - rt["started_at"]) if rt.get("started_at") else None,
            }
        elif docker_available():
            try:
                raw = run_capture([
                    DOCKER, "inspect", detect_container(),
                    "--format", "{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}|{{.RestartCount}}|{{.State.Health.Status}}",
                ], timeout=25)
                parts = raw.strip().split("|")
                if len(parts) >= 5:
                    started = parts[1][:19].replace("T", " ")
                    try:
                        st_dt = datetime.datetime.strptime(parts[1][:19], "%Y-%m-%dT%H:%M:%S")
                        # docker 返回 UTC，转本地
                        st_dt = st_dt + (datetime.datetime.now() - datetime.datetime.utcnow())
                        up_sec = int((datetime.datetime.now() - st_dt).total_seconds())
                    except Exception:
                        up_sec = None
                    container_state = {
                        "status": parts[0],
                        "started_at": started,
                        "image": parts[2],
                        "restart_count": parts[3],
                        "health": parts[4],
                        "uptime_sec": up_sec,
                    }
            except Exception:
                pass

        return {
            "now": now.isoformat(),
            "mode": "native" if NATIVE else "docker",
            "panel_port": self.server.server_address[1],
            "api_base": API_BASE,
            "api_port": API_PORT,
            "api_key": API_KEY,
            "api_key_masked": mask_key(API_KEY),
            "repo": REPO,
            "auths_dir": AUTHS_DIR,
            "scripts_dir": SCRIPTS_DIR,
            "state_file": STATE_FILE,
            "state_file_exists": os.path.exists(STATE_FILE),
            "container": detect_container() if not NATIVE else "wb2api.exe",
            "image": detect_image() if not NATIVE else "native",
            "docker_available": (not NATIVE) and docker_available(),
            "container_state": runtime_state,
            "config": cfg,
            "schedule": schedule,
            "auths": read_auth_files(),
            "state": read_state_file(),
            "tasks": TASKS,
            "auth_file_count": len([a for a in read_auth_files() if a.get("uid")]),
            "local_accounts": read_local_accounts(),
            "desktop_logins": scan_desktop_logins(),
            # ccswitch:// 协议是「按机器 + 按用户」注册的，必须每次实测（别用写死的说明）
            "ccswitch": ccswitch_protocol(),
        }


class Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 的语义与 Unix 不同：它允许**抢占**一个已被监听的端口，
    # 于是重复双击 bat 时第二个实例会"启动成功"并悄悄分流请求，用户完全无感。
    # 关掉它，才能拿到正确的 WSAEADDRINUSE，走上面的"端口已被占用"提示分支。
    allow_reuse_address = (os.name != "nt")
    daemon_threads = True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="WorkBuddy2API 本地控制面板")
    ap.add_argument("--port", type=int, default=int(os.environ.get("WB2API_PANEL_PORT", "7864")))
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    ap.add_argument("--api-port", type=int, default=None, help="覆盖网关端口（默认读 config.json）")
    args = ap.parse_args()

    global API_PORT, API_BASE
    if args.api_port:
        API_PORT = args.api_port
        API_BASE = "http://127.0.0.1:%d" % API_PORT

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 68)
    print(" WorkBuddy2API 控制面板")
    print("=" * 68)
    print(" 仓库      : %s" % REPO)
    print(" 网关      : %s  (api_key %s)" % (API_BASE, "已设置" if API_KEY else "未设置"))
    if NATIVE:
        print(" 模式      : native（直启 bin\\wb2api.exe，无 Docker）")
    else:
        print(" 容器      : %s" % CONTAINER)
        print(" Docker    : %s" % ("可用" if docker_available() else "不可用（部分功能将受限）"))
    print(" 面板地址  : http://127.0.0.1:%d" % args.port)
    print("-" * 68)

    start_log_threads()

    try:
        httpd = Server(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        # 端口被占：多半是面板已经在跑（重复双击了 bat），别丢一堆 traceback 给用户
        print("")
        print("[提示] 端口 %d 已被占用：%s" % (args.port, exc))
        print("       面板可能已经在运行了，直接打开 http://127.0.0.1:%d 即可。" % args.port)
        print("       若要重启：先关掉原来那个控制台窗口，或结束占用该端口的 python 进程。")
        if not args.no_browser:
            try:
                webbrowser.open("http://127.0.0.1:%d" % args.port)
            except Exception:
                pass
        print("")
        try:
            input("按回车键退出…")
        except (EOFError, KeyboardInterrupt):
            pass
        return

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d" % args.port)).start()
    print(" 已启动。按 Ctrl+C 停止。\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
