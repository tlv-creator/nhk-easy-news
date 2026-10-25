#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cron_tools.py —— 平台定时任务的三个小工具（2026-10-21 产出，2026-10-25 重建）

🔴 为什么需要它：
    平台 cron **只有 mcp_invoke 能调**（沙箱里的 mcode-tools 只有 matrix/email 的 App 工具，
    平台自己的 /minimax-cloud/api/v1/cron 端点要签名 ⇒ 沙箱签不了 ⇒ **建不了也查不了**）
    ⇒ 有 cron 工具的会话，用这个脚本 30 秒完成「查 → 算 → 建 → 验」。

🔴🔴 纪律：
    ① **prompt 从文件读**（work/cron_prompt.txt）⇒ 绝不手抄（手抄过一次，抄出了乱码 U+FFFD）
    ② **只删「NHK 每日更新」这一类**的重复任务 ⇒ 别的项目（Shun 日中精读 · 周更 等）**一律不碰**
    ③ **保留判据 = cron_name 含「NHK 每日更新」且 task_id 不是本任务自己**（不是 updated_at 最新）
    ④ **cron_delete 报「task_id: Expected string」是平台 bug** ⇒ 重试最多 5 次，还不行就让用户去网页端手删
    ⑤ **绝不新建替代任务**（建替代 = 每天跑两次 = 双部署 + 配额翻倍）

用法：
    python3 cron_tools.py payload                # 打印 cron_create 的完整 JSON payload
    python3 cron_tools.py dedup <cron_read.json> # 算「留哪个、删哪些」，印出可直接照抄的 cron_delete 调用
    python3 cron_tools.py verify <cron_read.json> # 验证：任务数=1 / enabled / schedule / tz / cron_name
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(ROOT, "work", "cron_prompt.txt")

# ── 这个 NHK 任务的"身份"（跟平台里已建好的那个保持一致）──
AGENT = "nhk-updater"
CRON_NAME = "NHK 每日更新"
SCHEDULE = "0 21 * * *"          # 每天 21:00
TIMEZONE = "Asia/Shanghai"
KEYWORD = "NHK 每日更新"          # 🔴 只按这个关键词判"是不是 NHK 的任务"


def load_prompt():
    if not os.path.exists(PROMPT_FILE):
        sys.exit("❌ 找不到 %s（prompt 正文一个字都不能手抄）" % PROMPT_FILE)
    with open(PROMPT_FILE, encoding="utf-8") as f:
        s = f.read()
    if "�" in s:
        sys.exit("❌ prompt 文件里有乱码 U+FFFD ⇒ 先修文件，别推上去")
    return s


def build_payload():
    return {
        "schedule": SCHEDULE,
        "prompt": load_prompt(),
        "timezone": TIMEZONE,
        "enabled": True,
        "session": {"mode": 0},          # 🔴 数字枚举：New=0 / SessionId=1（不是字符串！）
    }


def cmd_payload():
    p = build_payload()
    print(json.dumps(p, ensure_ascii=False, indent=1))
    print("\n# 用法：mcp_invoke tool_name=cron_create arguments='%s'"
          % json.dumps(p, ensure_ascii=False))
    return 0


def _tasks(obj):
    """从 cron_read 的返回里挖出任务列表（字段名各种叫法都兜住）"""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("data", "result", "items", "list", "tasks", "crons"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for k2 in ("list", "items", "tasks", "crons"):
                    if isinstance(v.get(k2), list):
                        return v[k2]
        if "cron_id" in obj or "name" in obj:
            return [obj]
    return []


def _name(t):
    for k in ("cron_name", "name", "title"):
        v = t.get(k)
        if v:
            return str(v)
    return ""


def _tid(t):
    for k in ("task_id", "cron_id", "id"):
        v = t.get(k)
        if v:
            return str(v)
    return ""


def cmd_dedup(path):
    raw = json.load(open(path, encoding="utf-8"))
    tasks = _tasks(raw)
    nhk = [t for t in tasks if KEYWORD in _name(t)]
    others = [t for t in tasks if KEYWORD not in _name(t)]
    print("总任务 %d 个；其中 NHK 类 %d 个；**别的项目 %d 个（一律不碰）**"
          % (len(tasks), len(nhk), len(others)))
    for t in others:
        print("  · 保留（非 NHK）：%-28s %s" % (_name(t)[:28], _tid(t)))
    if len(nhk) <= 1:
        print("\n✅ NHK 类只有一个（或零个）⇒ 不用删")
        if not nhk:
            print("⚠️ 但一个都没有 ⇒ 可能被误删了 ⇒ 用 `cron_tools.py payload` 重建一个")
        return 0
    # 保留判据：cron_name 含「NHK 每日更新」且 **task_id 不是本任务自己**
    #   「本任务自己」= prompt 内容与本地 cron_prompt.txt 一致（最硬）⇒ 其它的都删
    mine = load_prompt().strip()
    keep, dele = [], []
    for t in nhk:
        pr = str(t.get("prompt") or "")
        (keep if pr.strip() == mine else dele).append(t)
    if not keep:
        keep = [nhk[0]]          # 都没有一样的 ⇒ 留最早的一个（其余删）
        dele = [t for t in nhk if t is not keep[0]]
    print("\n🔴 要删的重复 NHK 任务（**逐条执行**，别批量）：")
    for t in dele:
        print("  mcp_invoke tool_name=cron_delete arguments='{\"task_id\":\"%s\"}'   # %s"
              % (_tid(t), _name(t)[:24]))
    print("\n🔴 保留：%s（task_id=%s）" % (_name(keep[0])[:24], _tid(keep[0])))
    print("⚠️ cron_delete 报「task_id: Expected string」是平台 bug ⇒ 重试最多 5 次；还不行就让用户去网页端手删")
    print("⚠️ **不要新建替代任务**（建替代 = 每天跑两次 = 双部署 + 配额翻倍）")
    return 0


def cmd_verify(path):
    raw = json.load(open(path, encoding="utf-8"))
    tasks = _tasks(raw)
    nhk = [t for t in tasks if KEYWORD in _name(t)]
    ok = True
    print("NHK 类任务数 = %d（期望 1）%s" % (len(nhk), "✅" if len(nhk) == 1 else "❌"))
    ok = ok and len(nhk) == 1
    if nhk:
        t = nhk[0]
        checks = [
            ("enabled", t.get("enabled"), True),
            ("schedule", t.get("schedule"), SCHEDULE),
            ("timezone", t.get("timezone"), TIMEZONE),
            ("agent", t.get("agent") or t.get("agent_name"), AGENT),
            ("cron_name", _name(t), CRON_NAME),
        ]
        for label, got, want in checks:
            good = (got == want)
            ok = ok and good
            print("  %-10s %-16s %s" % (label, str(got)[:16], "✅" if good else "❌（期望 %s）" % want))
        pr = str(t.get("prompt") or "")
        same = pr.strip() == load_prompt().strip()
        ok = ok and same
        print("  %-10s %s" % ("prompt", "✅ 与本地逐字一致" if same else "❌ 与本地不一致（可能被手抄坏过）"))
    print("\n%s" % ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "payload":
        return cmd_payload()
    if cmd in ("dedup", "verify"):
        if len(sys.argv) < 3:
            print("需要 <cron_read 的 json 文件路径>")
            return 2
        return cmd_dedup(sys.argv[2]) if cmd == "dedup" else cmd_verify(sys.argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
