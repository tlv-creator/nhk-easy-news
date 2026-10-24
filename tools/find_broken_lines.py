#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find_broken_lines.py —— **确定性**预筛：找出「句尾不完整」的行（被切开的候选）。

🔴 为什么要它（2026-10-24）：模型判断「这两行是不是同一句被切开的」有噪声
   （同一处两次判不同）。但**「这一行的话说完没有」**是个更稳、也更便宜的信号：
   句尾的**形态**决定完整性，不需要看下一行 ⇒ 确定性、可复现、零额度。

🔴 判据（落在 janome 的词性/形态这个事实量上）：
   行尾 token 是「句末形」⇒ 完整（不合并）
       助動詞（ました/ない/でしょう/だ…）、終助詞、形容詞、
       句点記号、感叹/语气词（ね/よ/かな…）
   行尾 token 不是句末形 ⇒ **可能残缺**（候选）
       副詞（色々）、助詞（に/は/を/が/も/の）、接尾/接頭、
       动词「ます形/て形/た形」之外的中间形、连体形…

🔴 假阳性已知（候选集里人工排除即可）：
   · 「皆」单独成句（=all）· 「〜かったら」完整 · 列举的「、」结尾
   · 标题卡（短行）· 罗马音/英文混排行

用法：
    python3 tools/find_broken_lines.py --ep all --context 1
"""
import argparse
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)

try:
    from janome.tokenizer import Tokenizer
    _TOK = Tokenizer()
except Exception:  # noqa: BLE001
    _TOK = None

# 句末形：这些词性/表面 ⇒ 这一行说完了
# 🔴 「記号」不整类判：。？！= 说完了；、= 还没说完（可能是列举 ⇒ 人工排除）
#    ⇒ 符号交给 _END_SURF_SET 集合判断
_END_POS = ("助動詞", "終助詞", "終助詞,接続助詞", "形容詞")
# 明确算「完整」的表面形式
# 明确算「完整」的表面形式（集合判断，比正则可控）
_END_SURF_SET = {
    "です", "ます", "ました", "ません", "ない", "でしょう", "た", "る", "いる",
    "くる", "いく", "ね", "よ", "な", "かな", "さ", "ぞ", "Okay",
    "。", "．", "！", "？", "!", "?", "apers", "…",
}
# 明显是「列举/接续」的行尾（不是被切开）
# 🔴 「、」结尾**也列为候选**：列举行（家族との関係、）与真残缺行（…ああ、）
#    在语法上分不出来 ⇒ 一律进候选，人工排除。
#    （少排一类比多排一类好：漏掉的不会被发现，误排的会被人工剔掉）
_LIST_END = ("…", "ー", "―", "─")


def last_token(text):
    if _TOK is None or not text:
        return ("", "")
    toks = list(_TOK.tokenize(text))
    if not toks:
        return ("", "")
    t = toks[-1]
    return (t.surface, t.part_of_speech or "")


def is_broken(text):
    """→ (是否可能残缺, 理由)"""
    t = (text or "").strip()
    if not t:
        return False, ""
    # 🔴 不按长度过滤：短行也可能是残缺（实测「私は今妻」5 字，明显残缺，
    #    但被「短行」规则滤掉 ⇒ 漏判）
    if t.endswith(_LIST_END):           # 列举、接续
        return False, ""
    # 🔴 罗马音/英文混排行（词表卡）⇒ 根本不是日语切行问题
    lat = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    if lat >= 4 and lat * 2 >= len(t):
        return False, ""
    surf, pos = last_token(t)
    if not surf:
        return False, ""
    if any(pos.startswith(x) for x in _END_POS):
        return False, ""
    if surf in _END_SURF_SET:
        return False, ""
    if pos.startswith(("助動詞", "終助詞", "形容詞")):
        return False, ""
    # 形动词/动词的「ます形」「て形」「た形」也算说完了
    if pos.startswith("動詞") and surf.endswith(("ます", "た", "て")):
        return False, ""
    return True, "%s/%s" % (surf, pos.split(",")[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", default="all")
    ap.add_argument("--context", type=int, default=1)
    args = ap.parse_args()
    eps = ([f"ep{i:02d}" for i in range(1, 14)] if args.ep == "all"
           else args.ep.split(","))
    out = {}
    out_sug = {}
    for ep in eps:
        p = os.path.join(WS, "web", "data", "eps", ep + ".json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        rows = [(c.get("rows") or [{}])[0] for c in d.get("cues") or []]
        cands = []
        for i, r in enumerate(rows):
            ok, why = is_broken(r.get("t") or "")
            if ok:
                cands.append(i)
        out[ep] = cands
        # 🔴 **极大连续段** = 同一句被切成多行 ⇒ 段起点就是要合并的组起点
        groups = []
        for i in cands:
            if groups and i == groups[-1][-1] + 1:
                groups[-1].append(i)
            else:
                groups.append([i])
        # 段尾延伸到「下一个非候选行」（那一行是同句的最后一块）
        sug = []
        for g in groups:
            end = g[-1]
            k = end + 1
            while k < len(rows) and k in cands:
                k += 1
            # 🔴 组尾 = **下一个非候选行**（它是同一句的最后一块）
            #    （之前写成 k-1 ⇒ 组长变成 1 ⇒ 合并被拒）
            gi0, gi1 = g[0], (k if k < len(rows) else len(rows) - 1)
            # 🔴 自动排除两类假阳性（错合会造成内容损坏 ⇒ 必须挡）：
            # ① 罗马音/英文词表行
            seg = "".join((rows[x].get("t") or "") for x in range(gi0, gi1 + 1))
            lat = sum(1 for ch in seg if ch.isascii() and ch.isalpha())
            if lat >= 6 and lat * 2 >= len(seg):
                continue
            # ② **列举行**（这一行和下一行的中文都以「，」结尾 ⇒ 是一串并列项，不是被切开）
            z0 = str(rows[gi0].get("zh") or "").strip()
            z1 = str(rows[gi1 + 1].get("zh") or "").strip() if gi1 + 1 < len(rows) else ""
            if z0.endswith(("，", ",")) and z1.endswith(("，", ",")):
                continue
            sug.append([gi0, gi1])
        out_sug[ep] = sug
        print("%s: %d 行 / %d 处残缺候选 ⇒ %d 个连续段" %
              (ep, len(rows), len(cands), len(sug)))
        for gi, g in enumerate(sug):
            print("  ▸组%d [%d..%d] %s" % (
                gi, g[0], g[1], " + ".join(
                    (rows[k].get("t") or "")[-16:] for k in range(g[0], g[1] + 1))))
            print("      前文: …%s" % (rows[g[0] - 1].get("t") or "")[-18:] if g[0] else "")
            print("      后文: %s…" % (rows[g[1] + 1].get("t") or "")[:18]
                  if g[1] + 1 < len(rows) else "")
    with open(os.path.join(WS, "work", "broken_candidates.json"), "w",
              encoding="utf-8") as f:
        json.dump({"candidates": out, "groups": out_sug}, f,
                  ensure_ascii=False, indent=1)
    print("候选 → work/broken_candidates.json")


if __name__ == "__main__":
    main()
