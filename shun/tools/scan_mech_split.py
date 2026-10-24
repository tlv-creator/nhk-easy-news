#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_mech_split.py —— 找出**按长度机械切**的结构块（不是语法切分）。

🔴 起因（2026-10-24 主线课程站反馈）：
   站方 `blk` 大部分按**语法**切分，但有少数是**按长度机械切**的
   （例：ep13:22 被切成 `反対にお金がたく / さんあってもいい / 人間関係がなかっ / …`）
   ⇒ 那不是结构，是**断字** ⇒ 用户看到的是一串没意义的碎片。

🔴 判据（采纳主线的判据，**必须去掉标点块**再算长度）：
   core = 非标点块
   all(8 <= len(core_i) <= 12) 且 len(core) >= 2  ⇒ 机械切
   ⚠️ 句末「。」块长度 1 ⇒ 不去掉标点的话整条判据失灵（主线踩过，漏掉 11 句）

用法：python3 tools/scan_mech_split.py [--json]
"""
import glob
import json
import os
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUNCT = set("、。！？!?，,．.「」『』（）()【】〔〕…—・:：;；\"' ")


def core_blocks(frame):
    out = []
    for b in frame or []:
        s = (b.get("s") or "").strip()
        if not s:
            continue
        if all(ch in PUNCT for ch in s):     # 纯标点块
            continue
        out.append(s)
    return out


def is_mech(frame):
    core = core_blocks(frame)
    if len(core) < 2:
        return False
    return all(8 <= len(x) <= 12 for x in core)


def main():
    out = {}
    total = 0
    for fp in sorted(glob.glob(os.path.join(WS, "web/data/eps/ep??.json"))):
        ep = os.path.basename(fp)[3:5]
        d = json.load(open(fp, encoding="utf-8"))
        rows = []
        for ci, cue in enumerate(d.get("cues") or []):
            for ri, row in enumerate(cue.get("rows") or []):
                total += 1
                g = row.get("gs") or {}
                fr = g.get("frame") or []
                if is_mech(fr):
                    rows.append({
                        "cue": ci, "row": ri, "src": g.get("src"),
                        "t": row.get("t"),
                        "blocks": [b.get("s") for b in fr],
                        "lens": [len(x) for x in core_blocks(fr)],
                    })
        if rows:
            out[ep] = rows
    n = sum(len(v) for v in out.values())
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print("总行数 %d ｜ 机械切 %d 句（%.2f%%）" % (total, n, 100.0 * n / max(total, 1)))
    for ep, rows in out.items():
        print("--- ep%s：%d 句" % (ep, len(rows)))
        for r in rows[:4]:
            print("   cue%d/row%d src=%s len=%s" % (r["cue"], r["row"], r["src"], r["lens"]))
            print("     %s" % (r["t"] or "")[:60])
            print("     块：%s" % " | ".join(r["blocks"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
