#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：本机账号拉黑 / 自动解除 / 账号池移除。

覆盖两类曾经只会「上线后才被发现」的行为：
  1. 拉黑本机客户端账号 → 不再出现在列表；该账号在客户端重新登录（登录态 token 换发，
     指纹变化）后必须自动恢复显示。
  2. 从账号池移除账号 → 文件搬到 removed-auths/ 备份、auths/ 里消失；
     非法文件名与目录穿越必须被拒绝。

全部在临时目录里跑，不碰真实的客户端目录与账号池。
"""
import json
import os
import shutil
import sys
import tempfile
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


def make_token(sub, exp_offset=3600, seed="a"):
    """造一个结构合法（不验签）的 JWT，payload 含 sub / exp。"""
    import base64
    head = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps({
        "sub": sub, "exp": int(time.time()) + exp_offset,
    }).encode()).decode().rstrip("=")
    return "%s.%s.%s" % (head, body, seed * 12)


def write_login(d, fname, uid, refresh_seed="r", access_seed="a", exp_offset=3600):
    payload = {
        "account": {"uid": uid, "nickname": "用户-" + uid[:6]},
        "auth": {
            "accessToken": make_token(uid, exp_offset, access_seed),
            "refreshToken": "rt-" + refresh_seed + "-" + uid,
            "domain": "www.workbuddy.cn",
            "expiresAt": int((time.time() + exp_offset) * 1000),
            "lastRefreshTime": int(time.time() * 1000),
        },
    }
    with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return payload


def main():
    tmp = tempfile.mkdtemp(prefix="wb2api_actions_")
    client_dir = os.path.join(tmp, "client-auth")
    auths = os.path.join(tmp, "auths")
    os.makedirs(client_dir)
    os.makedirs(auths)

    old = (server.WB_AUTH_DIR, server.AUTHS_DIR, server.BASE_DIR)
    server.WB_AUTH_DIR = client_dir
    server.AUTHS_DIR = auths
    server.BASE_DIR = tmp          # panel-state.json / removed-auths 落到临时目录
    try:
        uid1 = "aaaaaaaa-1111-2222-3333-444444444444"
        uid2 = "bbbbbbbb-1111-2222-3333-444444444444"
        write_login(client_dir, "workbuddy-desktop.info", uid1, refresh_seed="r1")
        write_login(client_dir, "workbuddy-desktop.2026-01-01T00-00-00-000Z.1.abc.info", uid2, refresh_seed="r2")

        print("---- 1. 初始扫描 ----")
        scan = server.scan_desktop_logins()
        uids = [a["uid"] for a in scan["accounts"]]
        ok(uid1 in uids and uid2 in uids, "两个账号都能被扫到")
        ok(scan["blocked"] == [] and scan["unblocked"] == [], "初始拉黑名单为空")
        fp1 = [a for a in scan["accounts"] if a["uid"] == uid1][0]["fingerprint"]
        ok(bool(fp1), "登录态摘要里带上了指纹（不含 token 明文）")
        ok("accessToken" not in json.dumps(scan), "摘要里没有 token 明文")

        print("---- 2. 拉黑 ----")
        r = server.block_local_login(uid1)
        ok(r.get("ok") is True, "拉黑成功")
        scan = server.scan_desktop_logins()
        uids = [a["uid"] for a in scan["accounts"]]
        ok(uid1 not in uids, "被拉黑的账号不再出现在 accounts 里")
        ok(uid2 in uids, "其他账号不受影响")
        ok(scan["blocked"] and scan["blocked"][0]["uid"] == uid1, "blocked 名单里能查到它")
        ok(server.block_local_login(uid1).get("ok") is True, "重复拉黑不报错")
        ok(server.block_local_login("not-exist-uid").get("ok") is False, "拉黑不存在的账号被拒绝")
        ok(server.block_local_login("").get("ok") is False, "缺少 uid 被拒绝")
        ok(os.path.isfile(server.panel_state_file()), "拉黑状态已落盘 panel-state.json")

        print("---- 2b. 仅身份（无凭据）的账号也能拉黑 ----")
        home = os.path.join(tmp, "workbuddy-home")
        os.makedirs(os.path.join(home, "storage", "skeleton"))
        uid3 = "cccccccc-1111-2222-3333-444444444444"
        os.makedirs(os.path.join(home, "storage", "user-" + uid3))
        with open(os.path.join(home, "storage", "skeleton", "account-snapshot.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"primary": {"uid": uid3, "nickname": "仅身份"}}, fh, ensure_ascii=False)
        old_home = server.WORKBUDDY_HOME
        server.WORKBUDDY_HOME = home
        try:
            ok([a["uid"] for a in server.read_local_accounts()["accounts"]] == [uid3],
               "本机身份来源能扫到「仅身份」账号")
            r = server.block_local_login(uid3)
            ok(r.get("ok") is True, "仅身份账号也能拉黑（不再报「不在登录态中」）")
            rec = server.read_blocklist().get(uid3) or {}
            ok(rec.get("fingerprint") == "",
               "仅身份条目指纹为空（等它重新登录产生凭据后自动解除）")
            ok(uid3 in [b["uid"] for b in server.blocklist_items()], "拉黑名单里能查到它")
            ok(server.unblock_local_login(uid3).get("ok") is True, "清理该测试条目")
        finally:
            server.WORKBUDDY_HOME = old_home

        print("---- 3. 客户端重新登录 → 自动解除 ----")
        # 重新登录 = 换发 token（指纹变化）；lastRefreshTime 变化不算
        write_login(client_dir, "workbuddy-desktop.info", uid1, refresh_seed="r1-NEW", access_seed="a-NEW")
        scan = server.scan_desktop_logins()
        uids = [a["uid"] for a in scan["accounts"]]
        ok(uid1 in uids, "重新登录后自动恢复显示")
        ok([u["uid"] for u in scan["unblocked"]] == [uid1], "unblocked 里回报了该账号（供前端提示一次）")
        ok(server.read_blocklist() == {}, "自动解除后拉黑名单清空")
        scan2 = server.scan_desktop_logins()
        ok(scan2["unblocked"] == [], "再次扫描不再重复回报（不会反复弹提示）")

        print("---- 4. 仅 lastRefreshTime 变化（自动续期）不应解除 ----")
        server.block_local_login(uid2)
        p = os.path.join(client_dir, "workbuddy-desktop.2026-01-01T00-00-00-000Z.1.abc.info")
        data = json.load(open(p, encoding="utf-8"))
        data["auth"]["lastRefreshTime"] = int(time.time() * 1000) + 5000
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        scan = server.scan_desktop_logins()
        ok(uid2 not in [a["uid"] for a in scan["accounts"]], "仅续期（token 未变）仍保持拉黑")
        ok(scan["blocked"] and scan["blocked"][0]["uid"] == uid2, "拉黑记录仍在")

        print("---- 5. 手动解除 ----")
        ok(server.unblock_local_login(uid2).get("ok") is True, "手动解除成功")
        ok(uid2 in [a["uid"] for a in server.scan_desktop_logins()["accounts"]], "解除后重新出现")
        ok(server.unblock_local_login(uid2).get("ok") is False, "解除不在名单里的账号被拒绝")
        ok(server.scan_desktop_logins(unfiltered=True)["blocked"] == [], "unfiltered 扫描也正常")

        print("---- 6. 账号池移除 ----")
        for u in (uid1, uid2):
            with open(os.path.join(auths, "workbuddy-%s.json" % u), "w", encoding="utf-8") as fh:
                json.dump({"account": {"uid": u, "nickname": "池内-" + u[:6]},
                           "auth": {"accessToken": "x", "refreshToken": "y",
                                    "expiresAt": int(time.time()) + 3600, "domain": "d"}}, fh)
        ok(len(server.read_auth_files()) == 2, "账号池里已有 2 个账号")
        r = server.remove_pool_account(uid=uid1)
        ok(r.get("ok") is True, "按 uid 移除成功")
        ok(r.get("nickname") == "池内-" + uid1[:6], "返回值带回昵称（供提示）")
        ok(r.get("remaining") == 1, "返回值带回剩余账号数")
        ok(not os.path.exists(os.path.join(auths, "workbuddy-%s.json" % uid1)), "auths/ 里的文件已删除")
        ok(os.path.isfile(r["backup"]), "文件已备份到 removed-auths/")
        ok(os.path.basename(os.path.dirname(r["backup"])) == "removed-auths", "备份目录名为 removed-auths")
        ok(json.load(open(r["backup"], encoding="utf-8"))["account"]["uid"] == uid1, "备份内容完整可解析")
        ok([a["uid"] for a in server.read_auth_files()] == [uid2], "账号池只剩另一个账号")

        r2 = server.remove_pool_account(file="workbuddy-%s.json" % uid2)
        ok(r2.get("ok") is True, "按文件名移除也支持")
        ok(r["backup"] != r2["backup"], "两次备份不互相覆盖")

        print("---- 7. 移除的安全约束 ----")
        ok(server.remove_pool_account(file="../../../etc/passwd").get("ok") is False, "目录穿越被拒绝")
        ok(server.remove_pool_account(file="..\\..\\secret.json").get("ok") is False, "反斜杠穿越被拒绝")
        ok(server.remove_pool_account(file="other.json").get("ok") is False, "非 workbuddy- 前缀被拒绝")
        ok(server.remove_pool_account(file="workbuddy-x.txt").get("ok") is False, "非 .json 后缀被拒绝")
        ok(server.remove_pool_account(uid="does-not-exist").get("ok") is False, "不存在的账号被拒绝")
        ok(server.remove_pool_account().get("ok") is False, "空参数被拒绝")

        print("---- 8. 面板状态文件位置（打包后必须跟 exe 走）----")
        ok(server.panel_state_file() == os.path.join(tmp, "panel-state.json"),
           "panel_state_file() 跟随 BASE_DIR（函数而非导入期常量）")
    finally:
        server.WB_AUTH_DIR, server.AUTHS_DIR, server.BASE_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n通过 %d 项，失败 %d 项" % (PASSED[0], len(FAILS)))
    for f in FAILS:
        print("  FAILED: " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
