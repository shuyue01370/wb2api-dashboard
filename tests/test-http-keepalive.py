#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：HTTP keep-alive 下的请求体残留 / 协议级错误响应格式 / build_action 返回约定。

守的是 2026-09-18 用户实测到的线上问题：
「添加账号成功后报 `SyntaxError: Unexpected token '<', "<!DOCTYPE " is not valid JSON`」。

根因：`protocol_version = "HTTP/1.1"`（长连接），而 `/api/login/begin` 与
`/api/login/check` 两个路由**没读请求体就直接 return**，前端 jpost 又恒发 `{}`
—— 残留的 2 字节粘到下一个请求的请求行前面，服务端按方法名 `{}POST` 找不到处理函数，
回 501（Python 默认的 HTML 错误页）。OAuth 登录成功后的下一个请求正好是
「重载账号」POST /api/run，于是那一刻必挂。

不依赖真实网关/客户端数据：起一个进程内的 Server，用同一条 http.client 连接发两次请求。
"""
import http.client
import json
import os
import socket
import sys
import threading
import time

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


def post(conn, path, body):
    conn.request("POST", path, body=json.dumps(body).encode("utf-8"),
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    return r.status, (r.getheader("Content-Type") or ""), r.read()


def main():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = server.Server(("127.0.0.1", port), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)

    try:
        print("---- 1. 同一条长连接连续两个 POST（body 残留会让第二个挂掉）----")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
        s1, ct1, b1 = post(conn, "/api/unknown", {})
        ok(s1 == 404 and ct1.startswith("application/json"),
           "第 1 个请求（该路由不单独读 body）回 JSON 404")
        try:
            s2, ct2, b2 = post(conn, "/api/run", {"action": "__不存在的动作__"})
            ok(s2 == 400, "第 2 个请求回 400（修复前是 501）—— 实际 %s" % s2)
            ok(ct2.startswith("application/json"), "第 2 个请求是 JSON（修复前是 text/html）")
            ok(b2.strip().startswith(b"{"), "响应体不是 HTML（修复前是 <!DOCTYPE HTML>）")
            try:
                json.loads(b2)
                ok(True, "第 2 个请求的响应可以被前端 r.json() 解析")
            except Exception as exc:
                ok(False, "响应不是合法 JSON：%s" % exc)
        except Exception as exc:
            ok(False, "第 2 个请求直接失败（连接被掐断）：%r" % exc)
        conn.close()

        print("---- 2. 请求体缓存必须每个请求重置（不能复用上一个请求的 body）----")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
        _, _, rb1 = post(conn, "/api/pool/remove", {"uid": "one"})
        _, _, rb2 = post(conn, "/api/pool/remove", {"uid": "two"})
        e1 = json.loads(rb1).get("error", "")
        e2 = json.loads(rb2).get("error", "")
        ok("one" in e1, "第 1 个请求读到自己的 body（%s）" % e1)
        ok("two" in e2 and "one" not in e2,
           "第 2 个请求读到自己的 body，没被上一个缓存污染（%s）" % e2)
        conn.close()

        print("---- 3. 协议级错误也必须回 JSON（否则前端只能报 Unexpected token '<'）----")
        raw = socket.create_connection(("127.0.0.1", port), timeout=6)
        raw.sendall(b"BOGUS /x HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        chunks = []
        raw.settimeout(4)
        try:
            while True:
                d = raw.recv(4096)
                if not d:
                    break
                chunks.append(d)
        except Exception:
            pass
        raw.close()
        resp = b"".join(chunks).decode("utf-8", "replace")
        head, _, bodytxt = resp.partition("\r\n\r\n")
        ok("501" in head.split("\r\n")[0], "非法方法得到 501（%s）" % head.split("\r\n")[0][:40])
        ok("text/html" not in head.lower(), "501 不再返回 HTML 错误页")
        ok(bodytxt.strip().startswith("{"), "501 的响应体是 JSON")
        try:
            payload = json.loads(bodytxt)
            ok(int(payload.get("http_status", 0)) == 501, "JSON 里带了 http_status=501")
            ok(bool(payload.get("error")), "JSON 里带了可读的 error（%s）" % payload.get("error"))
        except Exception as exc:
            ok(False, "501 响应体不是合法 JSON：%s" % exc)

        print("---- 4. build_action 必须恒定返回 6 元组（argv=None 表示出错）----")
        for act in ("__不存在的动作__", "restart", "signin", "credit", "tasks_all"):
            r = server.build_action(act, {})
            ok(isinstance(r, tuple) and len(r) == 6,
               "build_action(%r) 返回 6 元组（实际 %d 元）" % (act, len(r)))
        bad = server.build_action("__不存在的动作__", {})
        ok(bad[1] is None and bool(bad[0]), "不支持的动作 → argv=None 且 title 是错误文案（%s）" % bad[0])

        print("---- 5. 正常路由仍可用（POST 带 body）----")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        s, ct, b = post(conn, "/api/login/check", {})
        ok(s == 200 and ct.startswith("application/json"),
           "POST /api/login/check（body 不单独读的路由）正常回 JSON 200")
        s, ct, b = post(conn, "/api/unblock-local-login", {"uid": "no-such-uid"})
        payload = json.loads(b)
        ok(s == 200 and payload.get("ok") is False and "error" in payload,
           "POST /api/unblock-local-login 正常回业务错误（%s）" % payload.get("error"))
        conn.close()
    finally:
        httpd.shutdown()

    print("\n通过 %d 项，失败 %d 项" % (PASSED[0], len(FAILS)))
    for f in FAILS:
        print("  FAILED: " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
