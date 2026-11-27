#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""syn_zh.py —— 补齐**成分块中文**（yb）：与每句的 y 数组一一对应

🔴 为什么单独一个脚本：句子翻译（zh）和成分块中文（yb）是两件事
   · zh = 整句翻译（AGENT_TASK）
   · yb = 句子里**每个结构块**的短中文（与 y 一一对应，SYN_TASK）
   ⇒ 两者不能互相顶替。

🔴 2026-10-24 实测的缺口：**16 期有原速音的 sp 轨，yb 全是空的**
   ⇒ 普通版「跟音」模式的语法讲解块没有中文。

用法：
    python3 scripts/syn_zh.py --epid 20261023de64170 --track sp
    python3 scripts/syn_zh.py --all            # 扫描全部期，把缺的补上
    python3 scripts/syn_zh.py --check          # 只报缺口，不改
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site", "app")
API = "https://api.minimaxi.com/v1/chat/completions"
# 🔴🔴 2026-11-27 修（今天实测）：原来打 **api.minimax.chat + MiniMax-Text-01**
#    ⇒ 该模型 2026-11 已下线（实测返回 status_code=2069 "no longer available"）
#    ⇒ call_minimax 连续 3 次拿不到 choices ⇒ 抛「MiniMax 调用失败」
#    ⇒ 外层 try/except 把它吞成一行 ⇒ **成分块中文一直补不上**（没人发现）
# ✅ 换成 scripts/translate_wo.py 用的同一套（api.minimaxi.com + MiniMax-M3）
MODEL = "MiniMax-M3"

PROMPT = """下面给你一个日语句子的**结构块**列表（每块是原句里的一段，带字符区间）。
请为每一块写一个**简短中文**（8~18 字），说明这一块在句子里是什么。

要求：
1. **一个块对一条**，顺序不变，**不许合并、不许漏**。
2. 输出严格 JSON 数组：每项 {"i": 块序号, "zh": "中文"}。
3. 中文要贴着块的实际意思，**不要泛泛**（不要都写"这是主语"）。
4. 只输出 JSON 数组。

块列表：
{items}"""


def call_minimax(prompt, retries=3):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16000, "temperature": 0.1}   # 🔴 2026-11-27：8000 会被思考过程吃光 ⇒ JSON 被截断
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    if not key:
        raise RuntimeError("没有 MINIMAX_SUB_KEY")
    for _ in range(retries):
        try:
            p = subprocess.run(
                ["curl", "-s", "-X", "POST", API,
                 "-H", "Content-Type: application/json",
                 "-H", "Authorization: Bearer " + key,
                 "-d", json.dumps(body, ensure_ascii=False)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
            d = json.loads(p.stdout.decode())
        except Exception:
            time.sleep(2)
            continue
        if d.get("choices"):
            return d["choices"][0]["message"]["content"]
        time.sleep(2)
    raise RuntimeError("MiniMax 调用失败")


def parse_items(text):
    # 🔴 2026-11-27：模型（MiniMax-M3）会带 <think>…</think> 和 ```json 围栏
    #    ⇒ 先剥掉再抽数组（与 scripts/translate_wo.py 的 parse_json 同一口径）
    # 🔴 2026-11-27 又补一条：**思考过程被截断**（<think> 没闭合）⇒ 常规正则剥不掉
    #    ⇒ 实测有的响应整体是一段 think ⇒ 抽不到数组 ⇒ 「❌ 解析失败」
    #    ⇒ 兜底：think 之后仍有数组就取；没有就把 think 整段砍掉再抽
    t = text or ""
    # ① 闭合的 think 段：全部去掉（贪婪，避免嵌套漏删）
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    # ② 没闭合的 think 段：从 <think> 之后如果还有像数组的内容，就切到它前面
    if "<think>" in t:
        head, _, tail = t.partition("<think>")
        if re.search(r"\[.*\]", tail, re.S):
            t = head + tail
        else:
            t = head
    t = re.sub(r"^```(?:json)?\s*", "", t.strip())
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(0)))
        except Exception:
            return None


def load_subs(eid):
    p = os.path.join(SITE, "subs", "%s.js" % eid)
    if not os.path.exists(p):
        return None, None
    s = open(p, encoding="utf-8").read()
    m = re.search(r'window\.__SUBS__\["[^"]+"\]=(\{.*\})\s*;?\s*$', s, re.S)
    if not m:
        return None, None
    return json.loads(m.group(1)), s


def save_subs(eid, data, raw):
    p = os.path.join(SITE, "subs", "%s.js" % eid)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(p, "w", encoding="utf-8") as f:
        f.write('window.__SUBS__=window.__SUBS__||{};window.__SUBS__["%s"]=%s;\n' % (eid, body))


def need(eid, track):
    """返回 [(句下标, 块列表)] —— 需要补的那些句"""
    data, _ = load_subs(eid)
    if not data:
        return []
    out = []
    for i, c in enumerate(data.get(track) or []):
        y = c.get("y") or []
        yb = c.get("yb") or []
        if len(y) != len(yb) or not yb:
            ja = c.get("ja", "")
            blocks = [{"i": k, "text": ja[st:st + ln]} for k, (st, ln, _) in enumerate(y)]
            out.append((i, blocks, ja))
    return out


def fix_ep(eid, track, check_only=False):
    todo = need(eid, track)
    if not todo:
        return 0
    print("%s/%s：%d 句要补（%d 个块）" % (eid, track, len(todo), sum(len(b) for _, b, _ in todo)))
    if check_only:
        return len(todo)
    fixed = 0
    for i, blocks, ja in todo:
        items = "\n".join('{"i": %d, "ja": %s}' % (b["i"], json.dumps(b["text"], ensure_ascii=False))
                          for b in blocks)
        # 🔴🔴 2026-11-27 修（今天实测踩到）：原来用 **PROMPT.format(items=items)**
        #   ⇒ 而 PROMPT 里**有字面量花括号**（`{"i": 块序号, "zh": "中文"}`）
        #   ⇒ str.format 把它当占位符 ⇒ KeyError('"i"') ⇒ **每个句子都补不上**
        #   ⇒ 外层 try/except 把异常吞成一行「❌ …：'"i"'」⇒ 看不出是脚本坏了
        # ✅ 改用 replace（只替换 {items}，不动其它花括号）
        resp = call_minimax(PROMPT.replace("{items}", items))
        arr = parse_items(resp)
        if not arr:
            print("  ❌ 解析失败：%r" % (resp or "")[:120])
            continue
        got = {}
        for x in arr:
            try:
                got[int(x["i"])] = str(x["zh"]).strip()
            except Exception:
                continue
        if len(got) != len(blocks):
            print("  ⚠️ 块数对不上：拿到 %d / 要 %d" % (len(got), len(blocks)))
        data, raw = load_subs(eid)
        yb = [got.get(k, "") for k in range(len(blocks))]
        if any(yb):
            data[track][i]["yb"] = yb
            save_subs(eid, data, raw)
            fixed += 1
    return fixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epid", default="")
    ap.add_argument("--track", default="sp")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    ids = []
    if a.epid:
        ids = [a.epid]
    else:
        d = os.path.join(SITE, "subs")
        ids = sorted(f[:-3] for f in os.listdir(d)) if os.path.isdir(d) else []
    total = 0
    for eid in ids:
        for track in ([a.track] if a.epid else ("easy", "web", "sp")):
            try:
                total += fix_ep(eid, track, check_only=a.check)
            except Exception as e:
                print("  ❌ %s/%s：%s" % (eid, track, e))
    print("✅ %s %d 处" % ("待补" if a.check else "已补", total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
