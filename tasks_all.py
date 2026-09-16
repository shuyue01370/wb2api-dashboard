#!/usr/bin/env python3
"""成长计划任务：批量接取 → 你自己去完成 → 回来领奖。

设计前提（2026-09-12 实测）：
  任务列表的 `completed` **不等于**可领取 —— 对列表中显示 completed 1/1 的任务调 claim，
  上游返回 400 "task not completed"。真实门禁在**服务端行为事件**上，比列表严。
  因此「脚本伪造事件去凑进度」这条路不可靠；正确做法是**接取之后由本人到客户端真实完成**，
  真实行为触发真实事件，门禁才会真正满足。

默认只做两段，二者都没有任何上游上报：
  accept  把 not_accepted 批量改为 accepted —— 只调 /v2/activity/growth/tasks/accept
          一个端点，不碰 /v2/report，不产生任何遥测，风险极低
  claim   对所有 completed 任务尝试领奖；400 "task not completed" 属正常结果，幂等

run（补跑）默认**不执行**，需显式 --only run。它会向上游补发合成的对话活跃上报事件，
平台对这类上报的门禁比列表严，性价比低 —— 自己去做一遍更可靠。

用法
  python3 tasks_all.py ALL                      # 接取 + 尝试领奖（dry-run 只打印计划）
  python3 tasks_all.py ALL --yes                # 同上，真实执行
  python3 tasks_all.py ALL --only accept --yes  # 只接取（推荐的最小动作）
  python3 tasks_all.py ALL --only claim --yes   # 只领奖
"""
import argparse
import glob
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# 兼容三种布局：① 挂到 /app，task_common 在 /app/scripts（docker 模式）
#                ② 与 task_common.py 同目录（把本文件直接丢进仓库 scripts/ 也能跑）
#                ③ native 模式：环境变量指定 task_common 目录与账号目录
_env_scripts = os.environ.get("WB2API_SCRIPTS", "")
for _p in (os.path.join(_HERE, "scripts"), _HERE, _env_scripts):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
import task_common as tc  # noqa: E402

# native 模式：task_common.AUTHS 写死了 Linux 路径，用环境变量覆盖（模块级常量，函数内可见）
_env_auths = os.environ.get("WB2API_AUTHS", "")
if _env_auths:
    tc.AUTHS = _env_auths

GAP = 1.05        # 同账号内请求间隔（对齐 task_common.report_activity 既有口径）
ACC_GAP = 1.5     # 账号之间间隔

# 仅 --only run 时使用：判据为 chat_request_send 事件、且已被实测证实的任务
RUNNABLE = ("first_buddy", "chat_5", "Model_chat_GLM5.2")

# 接取后需要你到客户端手动完成的任务（如实列出，不静默跳过）
NO_UPSIDE = {
    "RichMeow_Chat": "脚本作者实测：accept 后多账号多次上报进度均不动（桌面端专属通道）",
    "black_cat": "奖励 0 积分 0 能量，仅 Buddy 盲盒",
}
NEEDS_INTERNAL_ID = {
    "skill_1": "需在「专家-技能」安装 1 个技能并在对话中使用",
    "expert_5": "需召唤 5 次专家并发起对话",
    "Expert_lighthouse": "需使用「腾讯轻量云」专家对话",
    "Expert_Philanthropy": "需使用「公益专家」对话",
    "Expert_team_use_3": "需召唤 3 次专家团",
    "Library_read": "需进入「资料库」使用",
    "playbook_prompt": "需在「灵感」里做同款并完成 1 次对话",
    "create_canvas": "需在「设计创意」模式创建 1 个画布",
    "template_5": "需使用 5 个模板发起对话",
}
UI_ONLY = {
    "Hp_Appearance": "需在「设置-外观」切换主题",
    "Buddy_App": "需在首页打开 Buddy 切换器",
    "Buddy_App_QQ": "需体验「企鹅教师助手」Buddy",
    "automation_1": "需在「自动化」里设置 1 个定时任务",
}


def log(*a):
    print(*a, flush=True)


def refresh(c, fallback):
    """写操作后重新拉一次任务列表；失败则沿用旧快照。"""
    try:
        return tc.list_tasks(c)
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# 仅 --only run 使用：补跑判据为事件上报的任务
# ---------------------------------------------------------------------------

def do_first_buddy(c):
    tc.report_activity(c, count=1, gap=GAP)
    time.sleep(2)
    tc.do_post(c, tc.chat_base(c), tc.PATH_BUDDY_AGREEMENT, {"agree": True})
    st, r = tc.do_post(c, tc.chat_base(c), tc.PATH_BUDDY_FIRST, {})
    d = (r or {}).get("data") or {}
    return "HTTP=%s credit=%s energy=%s" % (st, d.get("credit"), d.get("energy"))


def do_chat5(c, need):
    return "上报 %d 条 → %s" % (need, tc.report_activity(c, count=need, gap=GAP))


def do_model_chat(c):
    st_a, _ = tc.accept_tasks(c, ["Model_chat_GLM5.2"])
    time.sleep(GAP)
    st_c, first = tc.chat_completion(c, model_id="glm-5.2", prompt="hi，请回复一句话")
    tc.report_activity(c, count=1, gap=GAP, model_id="glm-5.2", model_name="GLM-5.2")
    return "accept=%s chat=%s reply=%r" % (st_a, st_c, (first or "")[:40])


# ---------------------------------------------------------------------------
# 两段主流程
# ---------------------------------------------------------------------------

def accept_phase(c, tasks, yes):
    pending = [t.get("task_code") for t in tasks
               if t.get("accept_status") == "not_accepted"]
    if not pending:
        log("    [accept] 没有待接取的任务")
        return []
    log("    [accept] 待接取 %d 个：%s" % (len(pending), ", ".join(pending)))
    if not yes:
        log("    [accept] [dry-run] 将批量 accept（只改任务状态，不产生任何上报）")
        return []
    st, r = tc.accept_tasks(c, pending)
    code = (r or {}).get("code") if isinstance(r, dict) else None
    msg = (r or {}).get("msg") if isinstance(r, dict) else r
    log("    [accept] HTTP=%s code=%s msg=%s" % (st, code, msg))
    if st == 200:
        log("    [accept] ✓ 已接取；这些任务现在会出现在你的任务列表里，去客户端逐个完成即可")
        return pending
    return []


def claim_phase(c, tasks, yes):
    cands = [t.get("task_code") for t in tasks if t.get("accept_status") == "completed"]
    if not cands:
        log("    [claim] 没有 completed 待领取的任务")
        return []
    log("    [claim] completed 待尝试领取 %d 个：%s" % (len(cands), ", ".join(cands)))
    if not yes:
        log("    [claim] [dry-run] 将逐个尝试 claim（400 \"task not completed\" 属正常）")
        return []
    got = []
    for code in cands:
        st, r = tc.claim_reward(c, code)
        msg = (r or {}).get("msg") if isinstance(r, dict) else str(r)[:80]
        if st == 200 and isinstance(r, dict) and r.get("code") == 0:
            log("    [claim] %-20s ✓ 领取成功" % code)
            got.append(code)
        else:
            log("    [claim] %-20s – HTTP=%s msg=%s（列表进度是乐观显示，真实门禁更严）"
                % (code, st, msg))
        time.sleep(GAP)
    return got


def run_phase(c, tasks, yes):
    by = {t.get("task_code"): t for t in tasks}
    ran = []
    for code in RUNNABLE:
        t = by.get(code)
        if not t:
            log("    [run] %-20s 该账号无此任务，跳过" % code)
            continue
        ast = t.get("accept_status")
        pr = t.get("progress") or {}
        cur, tgt = pr.get("current") or 0, pr.get("target") or 0
        if ast == "claimed":
            log("    [run] %-20s 已 claimed，跳过" % code)
            continue
        if tgt and cur >= tgt:
            log("    [run] %-20s 进度已满 %s/%s，跳过" % (code, cur, tgt))
            continue
        if not yes:
            log("    [run] %-20s [dry-run] 将补跑（当前 %s）"
                % (code, ("%s/%s" % (cur, tgt)) if tgt else "未接取/进度未知"))
            continue
        try:
            if code == "first_buddy":
                info = do_first_buddy(c)
            elif code == "chat_5":
                info = do_chat5(c, max(1, (tgt or 5) - cur))
            else:
                info = do_model_chat(c)
            log("    [run] %-20s ✓ %s" % (code, info))
            ran.append(code)
        except Exception as e:
            log("    [run] %-20s ✗ %r" % (code, e))
        time.sleep(GAP)
    return ran


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="成长计划任务：批量接取 / 领奖")
    ap.add_argument("account", help="uid 前缀，或 ALL")
    ap.add_argument("--yes", action="store_true", help="真实执行（默认 dry-run）")
    ap.add_argument("--only", default="", choices=["", "accept", "run", "claim"],
                    help="只跑某一段（默认 accept + claim）")
    a = ap.parse_args()

    if a.account.upper() == "ALL":
        prefixes = [os.path.basename(p)[10:18]
                    for p in sorted(glob.glob(tc.AUTHS + "/workbuddy-*.json"))]
    else:
        prefixes = [a.account]
    if not prefixes:
        log("没有找到任何账号（%s/workbuddy-*.json）" % tc.AUTHS)
        return 1

    # 默认只做「接取 + 领奖」：两者都不产生任何上游上报。
    phases = [a.only] if a.only else ["accept", "claim"]

    log("=" * 74)
    log("成长计划任务 · 批量接取 / 领奖")
    log("模式 : %s" % ("真实执行 --yes" if a.yes else "试运行 dry-run（确认输出后加 --yes）"))
    log("账号 : %d 个" % len(prefixes))
    log("执行 : %s" % " → ".join(phases))
    log("=" * 74)

    if "run" in phases:
        log("")
        log("[警告] 你选择了 run（补跑）：它会向上游补发**合成的**对话活跃上报事件。")
        log("       平台对这类上报的门禁比任务列表严（实测：列表 completed 的任务 claim 仍 400），")
        log("       更可靠的做法是自己到客户端把任务做一遍 —— 真实行为才会真正满足门禁。")

    summary = []
    for i, pre in enumerate(prefixes):
        if i:
            time.sleep(ACC_GAP)
        try:
            c = tc.load_auth(pre)
        except SystemExit as e:
            log("\n== %s 加载失败：%s" % (pre, e))
            continue
        log("")
        log("== %s (%s) ==" % (c["uid"][:8], c["nick"]))
        try:
            tasks = tc.list_tasks(c)
        except Exception as e:
            log("    [列表] 拉取失败：%r" % e)
            continue
        log("    [列表] 共 %d 个任务" % len(tasks))

        rec = {"uid": c["uid"], "nick": c["nick"],
               "accepted": [], "ran": [], "claimed": []}
        if "accept" in phases:
            rec["accepted"] = accept_phase(c, tasks, a.yes)
        if "run" in phases:
            tasks = refresh(c, tasks) if a.yes else tasks
            rec["ran"] = run_phase(c, tasks, a.yes)
        if "claim" in phases:
            tasks = refresh(c, tasks) if a.yes else tasks
            rec["claimed"] = claim_phase(c, tasks, a.yes)
        summary.append(rec)

    # 接取之后，这些要你本人去客户端做 —— 明确列出，不静默跳过
    log("")
    log("-" * 74)
    log("接取后请到 WorkBuddy 客户端手动完成以下任务（本工具不代为上报）：")
    for code, why in sorted(NO_UPSIDE.items()):
        log("  %-22s %s" % (code, why))
    for code, why in sorted(NEEDS_INTERNAL_ID.items()):
        log("  %-22s %s" % (code, why))
    for code, why in sorted(UI_ONLY.items()):
        log("  %-22s %s" % (code, why))
    log("")
    log("完成后回到面板点「一键领取所有积分奖励」即可（幂等，重复点无副作用）。")

    log("")
    log("=" * 74)
    log("SUMMARY|" + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
