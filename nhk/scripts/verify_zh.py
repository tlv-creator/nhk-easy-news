#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_zh.py —— 验译文工作单（**键对不上就不许上线**）

判据（全部落在**事实量**上）：
  ① work/zh/<id>_<track>.json 的键集合 == 0..n-1（n = 工作单里该轨的句数）
  ② 每个译文**非空**
  ③ work/syn_out/<id>_easy.json：每个值是列表，且长度 == 该句 blocks[] 的长度
  ④ 译文里**不许出现**日文假名（说明漏翻了/照抄了原文）

用法：
    python3 scripts/verify_zh.py            # 全部新期
    python3 scripts/verify_zh.py --epids a,b
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WO = os.path.join(ROOT, "work", "wo")
ZH = os.path.join(ROOT, "work", "zh")
SYN = os.path.join(ROOT, "work", "syn_out")
KANA = re.compile(r"[ぁ-んァ-ヶ]")


def load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epids", default="")
    a = ap.parse_args()
    if a.epids:
        eps = [x.strip() for x in a.epids.split(",") if x.strip()]
    else:
        eps = sorted([f[:-5] for f in os.listdir(WO) if f.endswith(".json") and not f.startswith("_")])
    bad = 0
    for eid in eps:
        wo = load(os.path.join(WO, "%s.json" % eid))
        if not wo:
            print("  ! %s 没有工作单" % eid)
            bad += 1
            continue
        msgs = []
        for track in ("easy", "web"):
            items = wo["tracks"].get(track) or []
            p = os.path.join(ZH, "%s_%s.json" % (eid, track))
            if not items:
                if os.path.exists(p):
                    msgs.append("%s: 工作单没有该轨，但存在译文文件（多余）" % track)
                continue
            d = load(p)
            if d is None:
                msgs.append("%s: **缺译文文件** work/zh/%s_%s.json" % (track, eid, track))
                continue
            keys = set(d.keys())
            want = set(str(i) for i in range(len(items)))
            if keys != want:
                msgs.append("%s: 键集合对不上（多 %s / 少 %s）"
                            % (track, sorted(keys - want)[:5], sorted(want - keys)[:5]))
            empty = [k for k, v in d.items() if not str(v).strip()]
            if empty:
                msgs.append("%s: %d 句空译文（%s）" % (track, len(empty), empty[:5]))
            # 🔴 括号里的假名是**原文自带的注音**（web 轨 = 报纸原文，NHK 自己就写「御差床（うさすか）」）
            #    ⇒ 只查括号**外面**的**平假名**（片假名多半是地名/专名，如「関ケ原町」）
            kana = []
            for k, v in d.items():
                body = re.sub(r"[（(][ぁ-んァ-ヶー・]+[）)]", "", str(v))
                body = re.sub(r"[「『][^」』]*[」』]", "", body)   # 「」里是引用的原文标题/术语
                if re.search(r"[ぁ-ん]", body):
                    kana.append(k)
            if kana:
                msgs.append("%s: %d 句译文里还有平假名（漏翻？）%s" % (track, len(kana), kana[:3]))
            # 🔴 长度比：**抓「内容错位」**（键集合对得上，但译文配错了句子）
            #    日文 → 中文的经验比 ≈ 0.45~1.3（汉字/假名混排）
            ratio_bad = []
            for it in items:
                z = d.get(str(it["i"]))
                if not z:
                    continue
                jl = len(it["ja"])
                zl = len(str(z))
                if jl < 8:
                    continue
                r = zl / jl
                if r < 0.35 or r > 1.6:
                    ratio_bad.append((it["i"], round(r, 2), jl, zl))
            if ratio_bad:
                msgs.append("%s: %d 句译文长度比异常（可能内容错位）%s"
                            % (track, len(ratio_bad), ratio_bad[:5]))
            # 成分块
            if track == "easy":
                sp = os.path.join(SYN, "%s_easy.json" % eid)
                sd = load(sp)
                if sd is None:
                    msgs.append("easy: **缺成分块文件** work/syn_out/%s_easy.json" % eid)
                else:
                    for it in items:
                        v = sd.get(str(it["i"]))
                        if v is None:
                            msgs.append("easy: 第 %d 句缺成分块" % it["i"])
                            break
                        if not isinstance(v, list) or len(v) != len(it["blocks"]):
                            msgs.append("easy: 第 %d 句成分块长度 %s ≠ blocks %d"
                                        % (it["i"], (len(v) if isinstance(v, list) else "?"),
                                           len(it["blocks"])))
                            break
        if msgs:
            bad += 1
            print("  ❌ %s" % eid)
            for m in msgs:
                print("      - %s" % m)
        else:
            ne = len(wo["tracks"].get("easy") or [])
            nw = len(wo["tracks"].get("web") or [])
            print("  ✅ %s  easy=%d web=%d" % (eid, ne, nw))
    print("\n%s  %d 期，%d 期有问题" % ("✅ 全绿" if bad == 0 else "❌", len(eps), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
