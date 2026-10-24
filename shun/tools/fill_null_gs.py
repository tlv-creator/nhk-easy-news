#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_null_gs.py —— 把 gs 为 null 的句子**用规则版回填**。

🔴 为什么需要（2026-10-24 主线反馈）：
   主线用模型版跑 `--apply` 写回时，失败句会写成 **null**
   ⇒ 而**规则版也会被一起抹掉**（等于白跑）
   ⇒ 本脚本在写回之后跑一遍：null 且**是日语句** ⇒ 用规则版重新生成补上
   ⇒ ⚠️ **非日语行**（英文 Patreon 广告 / 罗马音词表）**故意保持 null**
      ⇒ 判据与 clean_gs 一致（同一套判据 = 单一权威）

用法：python3 tools/fill_null_gs.py [--apply]
"""
import argparse
import glob
import json
import os
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "tools"))
import gs_rules  # noqa: E402


def is_japanese(s):
    return any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in (s or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    filled = 0
    left = 0
    for fp in sorted(glob.glob(os.path.join(WS, "web/data/eps/ep??.json"))):
        d = json.load(open(fp, encoding="utf-8"))
        changed = False
        for cue in d.get("cues") or []:
            for row in cue.get("rows") or []:
                if row.get("gs"):
                    continue
                ja = row.get("t") or ""
                if not is_japanese(ja):
                    left += 1
                    continue
                g = gs_rules.build_gs(ja, row.get("ru") or [], row.get("blk") or [],
                                      row.get("w") or [], row.get("zh") or "")
                if g:
                    row["gs"] = g
                    filled += 1
                    changed = True
        if changed and args.apply:
            json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False,
                      separators=(",", ":"))
            print("  回填 %s" % os.path.basename(fp))
    print("回填 %d 句 ｜ 保持 null（非日语行）%d 句" % (filled, left))
    return 0


if __name__ == "__main__":
    sys.exit(main())
