#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""show_wo.py —— 把工作单里的句子打出来（给人/模型看）

用法：
    python3 scripts/show_wo.py --epids a,b --track easy
输出：每行 "<下标>\t<原文>"
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WO = os.path.join(ROOT, "work", "wo")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epids", required=True)
    ap.add_argument("--track", default="easy")
    a = ap.parse_args()
    for eid in [x.strip() for x in a.epids.split(",") if x.strip()]:
        p = os.path.join(WO, "%s.json" % eid)
        if not os.path.exists(p):
            print("!! 缺工作单 %s" % eid)
            continue
        d = json.load(open(p, encoding="utf-8"))
        items = d["tracks"].get(a.track) or []
        print("===== %s  [%s]  %d 句 =====" % (eid, a.track, len(items)))
        for it in items:
            print("%d\t%s" % (it["i"], it["ja"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
