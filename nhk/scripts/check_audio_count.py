#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发版闸门：站点的音频必须**逐期真实存在**（防「哑站」）

🔴🔴 为什么要有这条（2026-10-26 实测）：
  · `update.py --build` 拷音频的判据是 `if os.path.exists(src) and not os.path.exists(dst)`
    ⇒ **源缺失时静默跳过** ⇒ 站点会「有字幕没声音」，**且不报任何错**
  · 沙箱重置后从 GitHub 恢复的工程，仓库里**没有 media/** ⇒ 恢复完直接发版 = 哑站
  · 所以发版前必须**拿每期的 audio 字段去对真实文件**，少一个就 FAIL

判据落**事实量**（文件真的在不在、大小对不对），不是落「清单里写了几个」。

用法：
    python3 scripts/check_audio_count.py [--dir site/app]
退出码：0 = 全过；1 = 有缺失（打印清单）；2 = 输入不完整（连期数都读不到 ⇒ 必须 FAIL）
"""
import argparse
import json
import os
import sys

MIN_BYTES = 10000   # 一个正常的 45~300 秒 AAC 至少几十 KB；小于此值视为空壳


def load_eps(site_dir):
    p = os.path.join(site_dir, "data", "episodes.js")
    if not os.path.exists(p):
        return None, "读不到 %s" % p
    raw = open(p, encoding="utf-8").read()
    try:
        eps = json.loads(raw.split("=", 1)[1].rstrip().rstrip(";"))
    except Exception as e:
        return None, "episodes.js 解析失败：%s" % e
    if not isinstance(eps, list) or not eps:
        return None, "episodes.js 里没有期数（空列表或格式不对）"
    return eps, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="site/app")
    ap.add_argument("--min-bytes", type=int, default=MIN_BYTES)
    args = ap.parse_args()
    site = os.path.abspath(args.dir)

    eps, err = load_eps(site)
    if err:
        print("❌ 音频闸门 FAIL：%s" % err, file=sys.stderr)
        return 2

    missing_slow, missing_fast, tiny = [], [], []
    for e in eps:
        eid = e.get("id") or ""
        slow_rel = e.get("audio") or ""
        # ① 字段本身要指对
        if not slow_rel:
            missing_slow.append("%s（audio 字段为空）" % eid)
            continue
        sp = os.path.join(site, slow_rel)
        if not os.path.exists(sp):
            missing_slow.append("%s → %s" % (eid, slow_rel))
        elif os.path.getsize(sp) < args.min_bytes:
            tiny.append("%s → %s（只有 %d B）" % (eid, slow_rel, os.path.getsize(sp)))
        # ② 有原速音的期，文件也得在
        fast_rel = e.get("audioFast") or ""
        if fast_rel:
            fp = os.path.join(site, fast_rel)
            if not os.path.exists(fp):
                missing_fast.append("%s → %s" % (eid, fast_rel))
            elif os.path.getsize(fp) < args.min_bytes:
                tiny.append("%s → %s（只有 %d B）" % (eid, fast_rel, os.path.getsize(fp)))

    n_slow = len([e for e in eps if e.get("audio")])
    n_fast = len([e for e in eps if e.get("audioFast")])
    real_slow = len([f for f in os.listdir(os.path.join(site, "media", "slow"))
                     if f.endswith(".m4a")]) if os.path.isdir(os.path.join(site, "media", "slow")) else 0
    print("  期数 %d ｜ 声明有慢速音 %d ｜ media/slow 实际文件 %d ｜ 声明有原速音 %d"
          % (len(eps), n_slow, real_slow, n_fast))

    bad = False
    for title, lst in (("缺慢速音", missing_slow), ("缺原速音", missing_fast), ("文件过小/空壳", tiny)):
        if lst:
            bad = True
            print("  ❌ %s %d 个：" % (title, len(lst)))
            for x in lst[:8]:
                print("       -", x)
            if len(lst) > 8:
                print("       ... 还有 %d 个" % (len(lst) - 8))
    if bad:
        print("❌ 音频闸门 FAIL：会有「有字幕没声音」的期（或空壳音频）", file=sys.stderr)
        return 1
    print("  ✅ 逐期音频核对通过（%d 期慢速音 + %d 期原速音，文件真实存在）" % (n_slow, n_fast))
    return 0


if __name__ == "__main__":
    sys.exit(main())
