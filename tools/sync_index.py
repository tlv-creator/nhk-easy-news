#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_index.py —— 从各集 epNN.json **重新派生** index.json 的句数 n。

🔴 为什么必须自动派生（2026-10-24）：
   index.json 里的 `n` 是每集句数。切行合并后行数变了
   ⇒ 手填会漏 ⇒ 列表里显示的句数与实际内容对不上
   ⇒ **单一权威**：`n` 只能是「该集实际 cue 数」的派生值。
   ⇒ 任何时候跑一遍都对 ⇒ 不会漂。

用法：
    python3 tools/sync_index.py            # 打印差异
    python3 tools/sync_index.py --apply    # 写回
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
IDX = os.path.join(WS, "web", "data", "eps", "index.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    d = json.load(open(IDX, encoding="utf-8"))
    eps = d.get("episodes") or []
    changed = 0
    for e in eps:
        tag = e.get("tag")
        p = os.path.join(WS, "web", "data", "eps", tag + ".json")
        if not os.path.exists(p):
            print("  ! %s 没有数据文件" % tag)
            continue
        dd = json.load(open(p, encoding="utf-8"))
        n = len(dd.get("cues") or [])
        if e.get("n") != n:
            print("  %s: n %s → %d" % (tag, e.get("n"), n))
            e["n"] = n
            changed += 1
    if changed:
        print("共 %d 处需要更新" % changed)
    else:
        print("index.json 与实际一致 ✓")
    if args.apply and changed:
        with open(IDX, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print("已写回 index.json")


if __name__ == "__main__":
    main()
