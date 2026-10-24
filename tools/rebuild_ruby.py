#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rebuild_ruby.py —— 合并碎行之后**重算逐字假名**。

🔴 为什么必须重算（2026-10-24 实测）：
   ruby（逐字假名）是**按原文切分**的 ⇒ 合并两行后，新 t 的切分点变了
   ⇒ 旧的 ru **与新 t 错位**（体检抓到：可疑率从 43.5% 涨到 45.6%）
   ⇒ 而 ruby 是**用户直接看的东西**（原句全注音）⇒ 不能带着错位上线。

🔴 用**同一权威实现**（tools/ruby.py 的 annotate）重算，不自己重写注音逻辑
   ⇒ 与当初 build_data.py 产出的完全一致。

用法：
    python3 tools/rebuild_ruby.py --ep all
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(WS, "tools"))

from ruby import annotate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", default="all")
    args = ap.parse_args()
    eps = ([f"ep{i:02d}" for i in range(1, 14)] if args.ep == "all"
           else args.ep.split(","))
    for ep in eps:
        p = os.path.join(WS, "web", "data", "eps", ep + ".json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        n = 0
        bad = 0
        for c in d.get("cues") or []:
            for r in (c.get("rows") or []):
                t = r.get("t") or ""
                if not t:
                    continue
                ru = annotate(t)
                # 🔴 硬校验：ruby 拼起来必须能还原原文（表层）
                surf = "".join(x.get("t", "") for x in ru)
                if surf != t:
                    bad += 1
                    if bad <= 2:
                        print("   %s: ruby 表层与原文不符！\n     原文 %r\n     ruby %r"
                              % (ep, t[:40], surf[:40]))
                    continue
                # 🔴🔴 数据格式必须是 **[[表层, 读音], ...]**（与 build_data.py 一致）
                #    ⚠️ 2026-10-24 踩到：写成纯读音数组 ⇒ 前端拿不到表层
                #       ⇒ 整页 ruby 消失（回归抓到「有汉字注音 → 0 个」）
                r["ru"] = [[x.get("t", ""), x.get("r", "")] for x in ru]
                n += 1
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        print("%s: 重算 ruby %d 行（表层不符 %d 行）" % (ep, n, bad))


if __name__ == "__main__":
    main()
