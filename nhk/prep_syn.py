#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「成分块中文」工作单：给每句的成分块逐块写中文。

读 work/out/<id>.<ver>.json（整句翻译）
写 work/syn/<id>.<ver>.json  {"items":[{"ja":..., "blocks":[...], "zh":[...]}]}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure as ST

WORK = "/workspace/nhk/work"
SYN = os.path.join(WORK, "syn")
NEP = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ONLY_MISSING = "--only-missing" in sys.argv
if ONLY_MISSING:
    NEP = 10 ** 9
os.makedirs(SYN, exist_ok=True)

outdir = os.path.join(WORK, "out")
files = sorted([f for f in os.listdir(outdir)
                if f.endswith(".json") and not f.startswith("lex_")])
n = 0
for f in files:
    d = json.load(open(os.path.join(outdir, f), encoding="utf-8"))
    items = []
    for it in d.get("items", []):
        ja = it.get("ja", "")
        try:
            blocks = ST.analyze(ja)
        except Exception:
            blocks = []
        if not blocks:
            continue
        items.append({
            "ja": ja,
            "zh": it.get("zh", ""),
            "blocks": [ja[b[0]:b[0] + b[1]] for b in blocks],
            "roles": [b[2] for b in blocks],
        })
    if items:
        # 🔴 判据必须是「成分块译文 work/syn_out/<f> 在不在」，
        #    原来错用「整句译文 work/out/<f>」→ 刚翻完整句的期会被整份跳过。
        of = os.path.join(WORK, "syn_out", f)
        if ONLY_MISSING and os.path.exists(of):
            continue
        json.dump({"items": items}, open(os.path.join(SYN, f), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        n += 1
print("生成 %d 份成分块工作单 -> %s" % (n, SYN))
