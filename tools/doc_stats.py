#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc_stats.py —— 交付文档里所有数字的**唯一来源**。

🔴 为什么要脚本化（2026-10-24）：
   文档里的「1355 句 / 100% / 35.9MB」这类数字**手写必错**
   （切行合并后句数从 1355 变 1173，差 182）
   ⇒ 判据：**文档数字必须能由脚本重新算出来** ⇒ 每次发版跑一遍即可。

用法：
    python3 tools/doc_stats.py
"""
import json
import os
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)


def main():
    eps = sorted(glob.glob(os.path.join(WS, "web", "data", "eps", "ep*.json")))
    eps = [p for p in eps if not p.endswith(".blk.json")]
    tot = 0
    zh = 0
    ru = 0
    w = 0
    blk = 0
    gs = 0
    gs_ok = 0
    dur = 0.0
    print("%-6s %6s %6s %6s %6s %6s %6s %9s" %
          ("集", "句数", "译文", "注音", "拆解", "块", "gs", "时长"))
    for p in eps:
        d = json.load(open(p, encoding="utf-8"))
        cues = d.get("cues") or []
        c = dict(zh=0, ru=0, w=0, blk=0, gs=0, gsok=0)
        for cue in cues:
            for r in (cue.get("rows") or []):
                c["zh"] += 1 if (r.get("zh") or "").strip() else 0
                c["ru"] += 1 if r.get("ru") else 0
                c["w"] += 1 if r.get("w") else 0
                c["blk"] += 1 if r.get("blk") else 0
                if r.get("gs"):
                    c["gs"] += 1
                    g = r["gs"]
                    if g.get("frame") and g.get("blocks") and g.get("whole"):
                        c["gsok"] += 1
        idx = os.path.join(WS, "web", "data", "eps", "index.json")
        tag = os.path.basename(p)[:-5]
        d2 = dur
        for e in json.load(open(idx, encoding="utf-8")).get("episodes") or []:
            if e.get("tag") == tag:
                d2 = e.get("dur") or 0
        dur += d2
        tot += len(cues)
        zh += c["zh"]; ru += c["ru"]; w += c["w"]; blk += c["blk"]
        gs += c["gs"]; gs_ok += c["gsok"]
        print("%-6s %6d %6d %6d %6d %6d %6d %8.1fs" %
              (tag, len(cues), c["zh"], c["ru"], c["w"], c["blk"], c["gsok"], d2))
    print("-" * 62)
    print("合计：%d 集 / %d 句 / %d 分钟" % (len(eps), tot, round(dur / 60)))
    print("  译文 %d/%d (%.1f%%)" % (zh, tot, zh * 100.0 / max(1, tot)))
    print("  注音 %d/%d (%.1f%%)" % (ru, tot, ru * 100.0 / max(1, tot)))
    print("  拆解 %d/%d (%.1f%%)" % (w, tot, w * 100.0 / max(1, tot)))
    print("  结构块 %d/%d (%.1f%%)" % (blk, tot, blk * 100.0 / max(1, tot)))
    print("  新结构详解 gs %d/%d (%.1f%%)（其中三段齐全 %d）" %
          (gs, tot, gs * 100.0 / max(1, tot), gs_ok))
    ap = os.path.join(WS, "output", "shun.apk")
    if os.path.exists(ap):
        print("  APK %.2f MB" % (os.path.getsize(ap) / 1048576.0))
    aud = os.path.join(WS, "audio")
    if os.path.isdir(aud):
        tot_b = sum(os.path.getsize(os.path.join(aud, f))
                    for f in os.listdir(aud) if f.endswith(".m4a"))
        print("  音频 %.1f MB（%d 个文件）" % (tot_b / 1048576.0,
                                          len([f for f in os.listdir(aud) if f.endswith(".m4a")])))


if __name__ == "__main__":
    main()
