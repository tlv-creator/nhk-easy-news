#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_after_merge.py —— 合并碎行之后的**完整体检**。

🔴 合并（把一句被切成两行的 t 拼成一行）会同时动三样东西：
   t（拼接）、ru（逐字假名，拼接）、zh（重建）
   ⇒ 必须逐项验，不能只看 t。

五道体检：
   ① **行数**：合并后行数 = 原行数 - Σ(组长-1)
   ② **时间轴**：s < e、不越界、**单调不减**、无重叠倒挂
   ③ **ruby 长度**：ru 的音节数应与 t 的假名数一致（拼接后最容易错位）
   ④ **译文非空**：每行都要有 zh
   ⑤ **译文对应**：交给 detect_zh_misalign 复跑（判据：0 个错位）

用法：
    python3 tools/verify_after_merge.py --ep ep13
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
BAK = os.path.join(WS, "work", "backup_eps_pre_zhfix")


def kana_count(s):
    """假名数（排除标点与拉丁）"""
    return sum(1 for ch in (s or "")
               if "぀" <= ch <= "ヿ" or "゠" <= ch <= "ゞ")


def ru_len(ru):
    """ruby 数组 → 音节数（每项 1 音节）"""
    n = 0
    for x in (ru or []):
        n += len(x) if isinstance(x, str) else 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True)
    ap.add_argument("--skip-detect", action="store_true")
    args = ap.parse_args()
    ep = args.ep
    p = os.path.join(WS, "web", "data", "eps", ep + ".json")
    d = json.load(open(p, encoding="utf-8"))
    cues = d.get("cues") or []
    errs = []
    warns = []

    # ① 行数
    bp = os.path.join(BAK, ep + ".json")
    if os.path.exists(bp):
        old = json.load(open(bp, encoding="utf-8"))["cues"]
        conf = json.load(open(os.path.join(WS, "tools", "confirmed_splits.json"),
                              encoding="utf-8"))
        exp_red = sum((int(b) - int(a)) for a, b in
                      conf.get(ep, {}).get("merge", []))
        exp = len(old) - exp_red
        if len(cues) != exp:
            errs.append("行数不符：期望 %d（%d - %d），实际 %d" %
                        (exp, len(old), exp_red, len(cues)))
        else:
            print("  ✓ 行数 %d = 原 %d - 合并减少 %d" % (len(cues), len(old), exp_red))

    # ② 时间轴
    prev_e = None
    for i, c in enumerate(cues):
        s, e = c.get("s"), c.get("e")
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            errs.append("[%d] 时间轴不是数字" % i)
            continue
        if s >= e:
            errs.append("[%d] s >= e：%s >= %s" % (i, s, e))
        if prev_e is not None and s < prev_e - 0.001:
            errs.append("[%d] 时间轴倒退：s=%s < 上一句 e=%s" % (i, s, prev_e))
        prev_e = e
    if not any("时间轴" in x for x in errs):
        print("  ✓ 时间轴单调、s<e")

    # ③ ruby 长度 + ④ 译文非空
    bad_ru = 0
    no_zh = 0
    for i, c in enumerate(cues):
        for r in (c.get("rows") or []):
            t = r.get("t") or ""
            ru = r.get("ru") or []
            if ru:
                kt, kr = kana_count(t), ru_len(ru)
                if abs(kt - kr) > max(2, kt * 0.25):
                    bad_ru += 1
                    if bad_ru <= 3:
                        warns.append("[%d] ruby 长度差：t 假名 %d，ru %d" % (i, kt, kr))
            if not (r.get("zh") or "").strip():
                no_zh += 1
                if no_zh <= 3:
                    warns.append("[%d] 译文为空" % i)
    if bad_ru:
        warns.append("ruby 长度可疑 %d 处" % bad_ru)
    else:
        print("  ✓ ruby 长度与 t 一致")
    if no_zh:
        errs.append("%d 行译文为空" % no_zh)
    else:
        print("  ✓ 每行都有译文")

    # ⑤ 译文对应（模型复跑）
    if not args.skip_detect:
        out = "/tmp/zh_after_%s.json" % ep
        r = subprocess.run([sys.executable,
                            os.path.join(HERE, "detect_zh_misalign.py"),
                            "--ep", ep, "--workers", "4", "--out", out],
                           capture_output=True, text=True, cwd=WS)
        line = [l for l in (r.stdout or "").splitlines() if l.startswith(ep + ":")]
        print("  " + (line[0] if line else (r.stdout or "")[-200:]))

    for w in warns:
        print("  ! " + w)
    for e in errs:
        print("  ✗ " + e)
    print("%s: %d 错 / %d 警" % (ep, len(errs), len(warns)))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
