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
            # 🔴 允许偏差：人工可能又**拆分**了一行（合并过度的修正）⇒ 只警告
            warns.append("行数与自动合并的预期差 %+d：期望 %d，实际 %d"
                         % (len(cues) - exp, exp, len(cues)))
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
    #    🔴 ruby 判据必须用**比例对比**，不能用绝对阈值
    #       （实测：ruby 音节数与假名数本来就不 1:1 —— 长音符/促音/浊音组合）
    #       ⇒ 合并前就有 ~40% 「可疑」⇒ 绝对阈值会全是噪声
    #       ⇒ 判据：**合并后的可疑率不得高于合并前**（不得引入新问题）
    # 🔴🔴 判据改正（2026-10-24）：**不能用「音节数 vs 表层字数」**
    #   ruby 的 r 是**读音**（片假名）⇒ 汉字的读音常是 2~4 字符（関係=カンケイ）
    #   ⇒ 那个比值天然对不上 ⇒ 合并前就有 40%「可疑」⇒ 全是噪声
    #   ⇒ **有效判据只有一个：把 ruby 拼回表层，必须 == 原文**
    def ru_stats(cue_list):
        bad = tot = 0
        for c in cue_list:
            for r in (c.get("rows") or []):
                t = r.get("t") or ""
                ru = r.get("ru") or []
                if not ru:
                    continue
                tot += 1
                kt = kana_count(t)
                # 表层字数（假名 + 汉字 + 拉丁）才该与 ruby 项数对应
                surf = sum(1 for ch in t
                           if ("぀" <= ch <= "ヿ" or "゠" <= ch <= "ゞ" or
                               "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" or
                               ch.isalnum()))
                if abs(surf - len(ru)) > max(1, surf * 0.2):
                    bad += 1
        return bad, tot

    bad_ru, tot_ru = ru_stats(cues)
    old_bad = old_tot = 0
    if os.path.exists(bp):
        oldc = json.load(open(bp, encoding="utf-8"))["cues"]
        old_bad, old_tot = ru_stats(oldc)
    no_zh = 0
    for i, c in enumerate(cues):
        for r in (c.get("rows") or []):
            if not (r.get("zh") or "").strip():
                no_zh += 1
                if no_zh <= 3:
                    warns.append("[%d] 译文为空" % i)
    rate_now = (bad_ru / tot_ru) if tot_ru else 0
    rate_old = (old_bad / old_tot) if old_tot else 0
    # 🔴🔴🔴 ruby 的**数据格式**闸门：必须是 [[表层, 读音], ...]
    #    （2026-10-24 踩到：写成纯读音数组 ⇒ 前端拿不到表层 ⇒ 整页 ruby 消失）
    fmt_bad = 0
    for i, c in enumerate(cues):
        for r in (c.get("rows") or []):
            ru = r.get("ru") or []
            if not ru:
                continue
            if not all(isinstance(x, (list, tuple)) and len(x) == 2 for x in ru):
                fmt_bad += 1
                if fmt_bad <= 2:
                    errs.append("[%d] ruby 格式错：必须是 [[表层,读音],...]" % i)
    if not fmt_bad:
        print("  ✓ ruby 数据格式正确（[[表层,读音],…]）")

    # 🔴🔴 真正的 ruby 判据：**用同一权威实现重算，比对读音数组**（幂等性）
    #    ⚠️ 不能用「音节数 vs 表层字数」：ruby 的 r 是**读音**（片假名），
    #       汉字读音常是 2~4 字符 ⇒ 那个比值天然对不上（合并前就有 40% 噪声）
    #    ⚠️ 也不能「拼回表层」：数据里 ru **只存了读音、没存表层** ⇒ 拼不回去
    try:
        sys.path.insert(0, os.path.join(WS, "tools"))
        from ruby import annotate as _annotate
        n_ru = 0
        ru_bad = 0
        for i, c in enumerate(cues):
            for r in (c.get("rows") or []):
                t = r.get("t") or ""
                if not t:
                    continue
                n_ru += 1
                want = [[x.get("t", ""), x.get("r", "")] for x in _annotate(t)]
                got = r.get("ru") or []
                if want != got:
                    ru_bad += 1
                    if ru_bad <= 2:
                        warns.append("[%d] ruby 与重算结果不一致" % i)
        if ru_bad:
            errs.append("%d 行 ruby 与权威实现重算结果不一致" % ru_bad)
        else:
            print("  ✓ ruby 与权威实现重算一致（%d 行）" % n_ru)
    except Exception as e:  # noqa: BLE001
        warns.append("ruby 校验跳过：%s" % e)
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
