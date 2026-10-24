#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_gs.py —— 把 gs 按**约定格式**导出（给 hub 课程站用）。

格式（2026-10-24 与 hub 主线会话约定）：
    每集一个文件 gs/epNN.json：
    {
      "ep01": {
        "13": { "ja": "<原句>", "gs": { ...原样... } },
        "14": { ... }
      }
    }
    · key = **行号（cue index，整数）** ⇒ 与 data/eps/epNN.json 的 cues[i] 同索引，可直接 join
    · ja = 原始日文（对方用它逐字比对，不一致就红）

用法：
    python3 tools/export_gs.py --out /workspace/gs_export
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(WS, "gs_export"))
    ap.add_argument("--eps", default="all")
    ap.add_argument("--src", default=os.path.join(WS, "web", "data", "eps"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    eps = ([f"ep{i:02d}" for i in range(1, 14)] if args.eps == "all"
           else args.eps.split(","))
    total = 0
    for ep in eps:
        p = os.path.join(args.src, ep + ".json")
        if not os.path.exists(p):
            print("跳过 %s（没有数据）" % ep)
            continue
        d = json.load(open(p, encoding="utf-8"))
        out = {}
        for i, c in enumerate(d.get("cues") or []):
            r = (c.get("rows") or [{}])[0]
            gs = r.get("gs")
            if not gs:
                continue
            out[str(i)] = {"ja": r.get("t") or "", "gs": gs}
        with open(os.path.join(args.out, ep + ".json"), "w", encoding="utf-8") as f:
            json.dump({ep: out}, f, ensure_ascii=False, indent=1)
        total += len(out)
        print("%s: %d 句带 gs" % (ep, len(out)))
    print("\n合计 %d 句 → %s" % (total, args.out))


if __name__ == "__main__":
    main()
