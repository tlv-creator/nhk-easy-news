#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 `sp` 轨（播报稿）质量体检：ASR 认对了多少「关键词」

为什么要有这个：
    「ASR 真实输出」是硬要求，但 ASR 会**认错字**（雨雲→甘くもが、氾濫→反乱、
    気温→手元の温度…）⇒ 跟音模式下一屏错字 = 没法用的产品。
    ⇒ 所以要有一个**能量的**判据，不能靠肉眼。

判据（落在事实量上）：
    ① 从**同一期的报纸原文**里抽出「关键词」（连续 2 字以上的汉字词，去掉停用词）
    ② 看这些词在 ASR 播报稿里出现了几个 ⇒ **覆盖率 = 命中数 / 总数**
    ③ 覆盖率低 ⇒ 说明 ASR 把词认错了 ⇒ 换模型 / 换 prompt，别硬发

用法： python3 scripts/sp_quality.py [--ids a,b] [--app site/app]
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STOP = set("""今日 日本 中 後 前 こと これ それ あり ない なる する という ため および その
この その  Rico ように こと する いる ので から まで など ため こと 日本人 大人 一人
年 月 日 時 分 秒 人 場合 一方 Against""".split())


def keywords(text):
    """从日文里抽「连续 2+ 字的汉字词」当关键词（不含纯假名）"""
    words = re.findall(r"[一-鿿]{2,}", text or "")
    out = []
    seen = set()
    for w in words:
        if w in STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(ROOT, "site", "app"))
    ap.add_argument("--sp", default=os.path.join(ROOT, "data", "sp"))
    ap.add_argument("--ids", default="")
    ap.add_argument("--show-miss", action="store_true")
    a = ap.parse_args()
    app = os.path.normpath(a.app)
    want = [x.strip() for x in a.ids.split(",") if x.strip()]
    files = sorted(f for f in os.listdir(a.sp) if f.endswith(".json"))
    if want:
        files = [f for f in files if f[:-5] in want]
    tot_hit = tot_all = 0
    for f in files:
        eid = f[:-5]
        sp = json.load(open(os.path.join(a.sp, f), encoding="utf-8"))
        asr = "".join(s["ja"] for s in sp.get("sents", []))
        raw = open(os.path.join(app, "subs", eid + ".js"), encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["%s"\]\s*=\s*(\{.*\});?\s*$' % eid, raw, re.S)
        if not m:
            print("  ! %s 字幕读不出来" % eid)
            continue
        data = json.loads(m.group(1))
        web = "".join(c.get("ja", "") for c in (data.get("web") or []))
        kws = keywords(web)
        hit = [k for k in kws if k in asr]
        miss = [k for k in kws if k not in asr]
        tot_hit += len(hit)
        tot_all += len(kws)
        rate = (100.0 * len(hit) / len(kws)) if kws else 0
        print("  %s  关键词 %2d  命中 %2d  覆盖率 %5.1f%%  句数 %d"
              % (eid, len(kws), len(hit), rate, len(sp.get("sents", []))))
        if a.show_miss and miss:
            print("       没认出来的：", "、".join(miss[:20]))
    if tot_all:
        print("== 总覆盖率 %.1f%%（%d/%d）==" % (100.0 * tot_hit / tot_all, tot_hit, tot_all))


if __name__ == "__main__":
    main()
