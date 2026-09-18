#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：改过 REPO_CFG 之后必须重算 API_KEY，且请求真的带上鉴权头。

## 为什么需要这个测试

2026-09-18 的现象：**每个页签顶部都挂着一条 `[object Object]`**，看不到真实原因。

根因链：

1. `server.py` 的 `API_KEY` 是**模块导入时算一次**的常量
   （`API_KEY = str(REPO_CFG.get("api_key") or "")`）
2. `app.py` 是在 import 之后才解析数据根、把该目录的 `config.json` 赋给
   `server.REPO_CFG` —— 但从不重算 `API_KEY`
3. 打包成 exe 后，导入那一刻数据根还指向临时目录、读不到配置 → `API_KEY = ""`
4. 面板请求网关不带 `Authorization` → 网关返回 401
5. 401 的错误体是 `{"error":{"code":"invalid_api_key",...}}`（**对象**）→
   前端 `esc(o.error)` 渲染成 `[object Object]`

开发态（源码启动）之所以看不出问题：导入时就能按 `../workbuddy2api/config.json`
读到 key，所以只有「打包 + 便携数据根」这条路径会中招。

## 这个测试怎么跑

不需要真网关：自起一个会记录 `Authorization` 头的 HTTP 服务充当网关。

    python tests/test-api-key-refresh.py
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

SEEN = []          # 记录假网关收到的 Authorization 头


class FakeGateway(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        SEEN.append(self.headers.get("Authorization"))
        body = json.dumps({"accounts": [], "healthy": 0, "total": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> int:
    failures = []

    # 假网关（端口随机）
    srv = socketserver.TCPServer(("127.0.0.1", 0), FakeGateway)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print("  导入期 API_KEY = %r（开发机有 ../workbuddy2api 时通常非空）" % server.API_KEY)

    # --- 用例 1：refresh_api_config 必须把 REPO_CFG 里的值算进去 ---
    server.REPO_CFG = {"listen": "127.0.0.1:%d" % port, "api_key": "secret_abc"}
    if not hasattr(server, "refresh_api_config"):
        failures.append("server 缺少 refresh_api_config()：外部改 REPO_CFG 后无法重算鉴权")
        print("  用例1  refresh_api_config 不存在 → 直接失败")
        srv.shutdown()
        return _report(failures)
    server.refresh_api_config()
    print("  用例1  refresh 后 API_KEY=%r  API_BASE=%r" % (server.API_KEY, server.API_BASE))
    if server.API_KEY != "secret_abc":
        failures.append("refresh_api_config 没有把 REPO_CFG.api_key 算进 API_KEY（实际 %r）"
                        % server.API_KEY)
    if not server.API_BASE.endswith(":%d" % port):
        failures.append("refresh_api_config 没有按 REPO_CFG.listen 更新 API_BASE（实际 %r）"
                        % server.API_BASE)

    # --- 用例 2：api_call 必须真的把 key 放进 Authorization 头 ---
    SEEN.clear()
    status, data = server.api_call("/status")
    print("  用例2  api_call → HTTP %s，假网关收到 Authorization=%r" % (status, SEEN[-1] if SEEN else None))
    if status != 200:
        failures.append("api_call 未能连上假网关（status=%s）" % status)
    if (SEEN[-1] if SEEN else None) != "Bearer secret_abc":
        failures.append("请求没有带上正确的鉴权头（收到 %r）—— 这就是网关 401 的原因"
                        % (SEEN[-1] if SEEN else None))

    # --- 用例 3：不调用 refresh 时应保持旧值（用来证明「必须显式重算」这一点）---
    server.REPO_CFG = {"listen": "127.0.0.1:%d" % port, "api_key": "another_key"}
    stale = server.API_KEY
    print("  用例3  只改 REPO_CFG 不 refresh → API_KEY 仍为 %r（期望保持旧值，说明必须显式调用）"
          % stale)
    if stale != "secret_abc":
        failures.append("未调用 refresh 时 API_KEY 却被改了，测试前提不成立")

    srv.shutdown()
    return _report(failures)


def _report(failures: list) -> int:
    print()
    if failures:
        print("  ✗ 失败 %d 项：" % len(failures))
        for f in failures:
            print("      - %s" % f)
        return 1
    print("  ✓ 全部通过：REPO_CFG 变更后 API_KEY 会重算，请求会带上正确的鉴权头")
    return 0


if __name__ == "__main__":
    sys.exit(main())
