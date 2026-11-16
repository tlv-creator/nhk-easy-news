#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prep_wo.py —— 给 subagent 准备**翻译工作单**（句子翻译 + 成分块中文）

产出：
    work/wo/<epid>.json      一期的工作单（easy / web 两轨）
    work/wo/_manifest.json   清单（期号 + 句数 + 分组建议）

🔴 为什么工作单里带「成分块」：
    成分块中文（yb）必须与 `structure.analyze()` 算出来的 **y 数组一一对应**
    （长度相同、顺序相同）⇒ 所以工作单里要把**每个块的原文**直接给出来，
    让 subagent 照着逐块写中文（不许自己重新切块）。

用法： python3 scripts/prep_wo.py [--ids a,b,c] [--all-new]
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import parse_text as P  # noqa: E402
import structure as ST  # noqa: E402

DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")
SITE = os.path.join(ROOT, "site", "app")
WO = os.path.join(WORK, "wo")

ROLES = {"0": "主语", "1": "谓语", "2": "宾语", "3": "状语", "4": "修饰",
         "5": "从句", "6": "语气", "7": "其他"}


def published_ids():
    p = os.path.join(SITE, "data", "episodes.js")
    if not os.path.exists(p):
        return set()
    s = open(p, encoding="utf-8").read()
    return set(re.findall(r'"id"\s*:\s*"([^"]+)"', s))


def all_new():
    d = os.path.join(DATA, "episodes")
    pub = published_ids()
    return sorted([e for e in os.listdir(d)
                   if os.path.isdir(os.path.join(d, e)) and e not in pub], reverse=True)


def track_items(sents):
    items = []
    for i, s in enumerate(sents):
        ja = s["plain"]
        try:
            y = ST.analyze(ja)
        except Exception:
            y = []
        blocks = []
        for (st, ln, role) in y:
            blocks.append({"text": ja[st:st + ln], "role": ROLES.get(str(role), "其他"),
                           "s": st, "l": ln})
        items.append({"i": i, "ja": ja, "blocks": blocks})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--all-new", action="store_true")
    a = ap.parse_args()
    ids = ([x.strip() for x in a.ids.split(",") if x.strip()] if a.ids
           else (all_new() if a.all_new else []))
    if not ids:
        print("需要 --ids 或 --all-new")
        return 2
    os.makedirs(WO, exist_ok=True)
    manifest = []
    for eid in ids:
        d = os.path.join(DATA, "episodes", eid)
        epj = {}
        p = os.path.join(d, "episode.json")
        if os.path.exists(p):
            try:
                epj = json.load(open(p, encoding="utf-8"))
            except Exception:
                epj = {}
        wo = {"epid": eid, "title": epj.get("title", ""), "tracks": {}}
        for track, fn, parser in (("easy", "easy.html", P.parse_easy),
                                  ("web", "web.json", P.parse_web)):
            fp = os.path.join(d, fn)
            if not os.path.exists(fp):
                continue
            try:
                sents = parser(fp)
            except Exception as e:
                print("  ! %s %s parse: %s" % (eid, track, e))
                sents = []
            if sents:
                wo["tracks"][track] = track_items(sents)
        out = os.path.join(WO, "%s.json" % eid)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(wo, f, ensure_ascii=False, indent=1)
        n_e = len(wo["tracks"].get("easy", []))
        n_w = len(wo["tracks"].get("web", []))
        manifest.append({"epid": eid, "title": wo["title"],
                         "easy": n_e, "web": n_w, "total": n_e + n_w,
                         "fast": os.path.exists(os.path.join(DATA, "audio_fast", eid + ".m4a"))})
        print("  %s  easy=%d web=%d" % (eid, n_e, n_w))
    with open(os.path.join(WO, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("== 工作单 %d 期，句子 %d 句 ==" % (len(manifest), sum(x["total"] for x in manifest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
