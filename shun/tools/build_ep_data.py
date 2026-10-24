#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_ep_data.py —— 把字幕行 + 中文 + 振假名 + 中文块，合成 App 要的集数据

输出（与已发布数据同构）：
    web/data/eps/ep13.json        {ep, tag, title, desc, dur, audio, cues:[{s,e,rows:[]}]}
        row = {t:日文, ru:[[表层,读音]], zh:中文, w:[[词,读音]], st:句型id, blk:[[日文块,中文块]]}
    web/data/eps/ep13.blk.json   {"ep13:0": [[日文块,中文块]], ...}（旁挂：整句结构）
    web/data/eps/index.json       集数索引（新增 ep13 一条）

用法：
    python3 tools/build_ep_data.py --ep 13 --cues work/cues/ep13_cues.json \
        --zh work/zh/ep13_zh.json --outdir web/data/eps
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ruby import get_tokenizer, build_ru, covers  # noqa: E402

KANJI = re.compile(r"[一-鿿]")
SENT_END = "。？！!?"


def katakana_to_hiragana(s):
    out = []
    for ch in s:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def split_blocks(jp, zh, max_jp=8, max_zh=5):
    """整句结构：日文按「词/标点」切块，中文按字数切块，**两边块数相同**"""
    # 日文：优先在标点/空格处断，其次按最大长度
    jparts, cur = [], ""
    for ch in jp:
        cur += ch
        if ch in SENT_END or len(cur) >= max_jp:
            jparts.append(cur)
            cur = ""
    if cur:
        jparts.append(cur)
    if not jparts:
        jparts = [jp]
    n = len(jparts)
    # 中文：按 n 块切，字数尽量平均（不切断标点优先）
    zparts, cur = [], ""
    zl = len(zh)
    for i, ch in enumerate(zh):
        cur += ch
        remain = zl - i - 1
        need = n - len(zparts) - 1
        if need <= 0:
            continue
        if len(cur) >= max_zh and remain >= need:
            zparts.append(cur)
            cur = ""
    if cur:
        zparts.append(cur)
    if len(zparts) < n:                      # 中文短 ⇒ 补空块
        while len(zparts) < n:
            zparts.append("")
    elif len(zparts) > n:                    # 中文长 ⇒ 合并尾部
        zparts = zparts[:n - 1] + ["".join(zparts[n - 1:])]
    return [[jparts[i], zparts[i]] for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--zh", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--index", default=None)
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc", required=True)
    ap.add_argument("--dur", type=float, required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--kanji-table", default=None)
    ap.add_argument("--st", default=None, help="句型 id 映射（build_lexicon 的产物）")
    args = ap.parse_args()

    tag = "ep%02d" % args.ep
    cues = json.load(open(args.cues))["cues"]
    zh = json.load(open(args.zh))
    tok = get_tokenizer()
    ktable = {}
    if args.kanji_table and os.path.exists(args.kanji_table):
        ktable = json.load(open(args.kanji_table))
    stmap = {}
    if args.st and os.path.exists(args.st):
        stmap = json.load(open(args.st)).get("st", {})

    out_cues = []
    blk_side = {}
    for i, c in enumerate(cues):
        t = c["t"]
        z = zh.get(str(i), "")
        ru = build_ru(t, tok, ktable)
        if not covers(ru, t):
            # 兜底：切不出来就按字符铺（读音空）—— 宁可没读音，也不能缺字
            ru = [[ch, ""] for ch in t]
        blocks = split_blocks(t, z)
        row = {
            "t": t,
            "ru": ru,
            "zh": z,
            "w": ru,
            "st": stmap.get(str(i), ""),
            "blk": blocks,
        }
        out_cues.append({"s": c["s"], "e": c["e"], "rows": [row]})
        blk_side["%s:%d" % (tag, i)] = blocks

    ep_json = {
        "ep": args.ep,
        "tag": tag,
        "title": args.title,
        "desc": args.desc,
        "dur": args.dur,
        "audio": args.audio,
        "cues": out_cues,
    }
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, tag + ".json"), "w", encoding="utf-8") as f:
        json.dump(ep_json, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.outdir, tag + ".blk.json"), "w", encoding="utf-8") as f:
        json.dump(blk_side, f, ensure_ascii=False, indent=1)

    # 集数索引：把新的一集**插到最前**（tag 是倒着编的：ep01=最新）
    if args.index:
        idx = json.load(open(args.index))
        eps = [e for e in idx["episodes"] if e.get("tag") != tag]
        newep = {
            "ep": args.ep,
            "tag": tag,
            "title": args.title,
            "desc": args.desc,
            "dur": round(args.dur, 1),
            "audio": args.audio,
            "n": len(out_cues),
            "no": re.sub(r"\D", "", args.desc.split("Ep")[-1].split()[0]) or str(args.ep),
        }
        eps.insert(0, newep)
        idx["episodes"] = eps
        with open(args.index, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
        print("✅ 索引已更新（%d 集）" % len(eps), file=sys.stderr)

    print("✅ %s：%d 行 → %s" % (tag, len(out_cues), args.outdir), file=sys.stderr)


if __name__ == "__main__":
    main()
