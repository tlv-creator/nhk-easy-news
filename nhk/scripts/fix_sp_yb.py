#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_sp_yb.py —— 补 sp 轨里**成分块中文数量对不上**的句子

🔴 为什么单独写（2026-11-21）：
    `scripts/translate_sp.py` 要的是 JSON **数组** ⇒ 模型经常**少给/多给**元素
    （sp 轨的原文是 ASR 裸输出 ⇒ 句子支离破碎 ⇒ 块被模型合并）
    ⇒ 数组长度对不上 ⇒ 那一整句的 yb 被置空 ⇒ 面板里那块只有日文
    ⇒ 线上已发布的期是 100% 有 yb 的 ⇒ 这里得补齐

🔴 修法：改成要 **带下标的对象** {"0":"…","1":"…"}
    ⇒ 模型不用「数个数」，只要把每个下标填上 ⇒ 长度天然对齐
    ⇒ 还缺的下标补空串（面板里那一条只显示日文，不影响别的块）

用法： python3 scripts/fix_sp_yb.py --ids a,b,c
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import structure as ST  # noqa: E402
from translate_wo import api, save, load  # noqa: E402


def parse_obj(text):
    """🔴 解析 JSON **对象**（translate_wo.parse_json 只认数组 ⇒ 这里自己来）"""
    t = (text or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

SYS = """你是日语精读 App 的译者。下面给你一句日文（NHK 新闻的**电视播报稿**，可能是 ASR 转写，句子有点乱）和它切好的成分块。
请给**每个成分块**一个中文意思。

硬要求：
1. 严格输出 JSON 对象，**键是成分块的编号**（"0","1","2"…），**一个都不能少**。
2. 每个值是该块的中文意思（几个字到十来个字），**简体中文**，贴着块的字面意思。
3. 原句可能是 ASR 转写、语法不完整 ⇒ 照直翻，**不要改原文、不要补原文**。
4. 只输出 JSON 对象，不要任何解释文字。"""


def blocks_of(plain):
    try:
        y = ST.analyze(plain)
    except Exception:
        y = []
    return [plain[b[0]:b[0] + b[1]] for b in y]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    if not ids:
        ids = sorted(f[:-5] for f in os.listdir(os.path.join(ROOT, "data", "sp"))
                     if f.endswith(".json"))
    fixed = left = 0
    for eid in ids:
        spp = os.path.join(ROOT, "data", "sp", "%s.json" % eid)
        trp = os.path.join(ROOT, "work", "sp", "%s.json" % eid)
        sp = load(spp)
        tr = load(trp)
        if not sp or not tr:
            continue
        sents = sp.get("sents") or []
        items = tr.get("items") or []
        changed = False
        for k, s in enumerate(sents):
            if k >= len(items):
                break
            ja = s["ja"]
            bl = blocks_of(ja)
            if not bl:
                continue
            if len(items[k].get("yb") or []) == len(bl):
                continue
            user = ("原句：%s\n成分块（共 %d 个）：\n%s\n\n"
                    "请输出 JSON 对象，键为 \"0\" ~ \"%d\"，每个键填该块的中文意思。"
                    % (ja, len(bl),
                       "\n".join("  %d) %s" % (j, t) for j, t in enumerate(bl)),
                       len(bl) - 1))
            got = None
            for t in range(3):
                obj = parse_obj(api([{"role": "system", "content": SYS},
                                       {"role": "user", "content": user}]))
                if isinstance(obj, dict) and obj:
                    # 🔴 **不要求模型把每个下标都填上**：
                    #    句子里有「高」「さ」这种单字块时模型常会略过
                    #    ⇒ 缺的下标补空串（**长度必须对** ⇒ build_cue 才不会把整句的 yb 置空）
                    #    ⇒ 效果：大部分块有中文，个别块只显示日文（比整句都没有好）
                    vals = []
                    for j in range(len(bl)):
                        v = obj.get(str(j), obj.get(j, ""))
                        vals.append(v if isinstance(v, str) else "")
                    if any(v.strip() for v in vals):
                        got = vals
                        break
                print("     (重试 %d/3：没解析出对象)" % (t + 1), flush=True)
            if got:
                items[k]["yb"] = got
                changed = True
                fixed += 1
            else:
                left += 1
                print("  ⚠️ %s 第 %d 句仍然补不齐 ⇒ 保留空（面板里该句只显示日文块）"
                      % (eid, k), flush=True)
        if changed:
            tr["items"] = items
            save(trp, tr)
    print("== 补齐 %d 句，仍缺 %d 句 ==" % (fixed, left))
    return 0


if __name__ == "__main__":
    sys.exit(main())
