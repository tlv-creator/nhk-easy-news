#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剥掉 mcode 部署时注入的 **AUDIO_BASE 媒体 CDN 前缀**（并留一道闸门）

【为什么必须剥】
mcode 部署时会把页面里的音频地址前缀改成
    https://<node>.space.mcode.cn/media/
于是 `AUDIO_BASE + e.audio` 变成
    https://<node>.space.mcode.cn/media/https://audio4.redcircle.com/...
而那个 `/media/` 路由**对任意外部 URL 都返回 404**（连 baidu 都 404）
⇒ 浏览器拿到的是 HTML 404 页 ⇒ `MEDIA_ELEMENT_ERROR: Format error`
⇒ **线上 50 集站点与单文件版的音频全是坏的**（2026-10-26 实测）。

【正确做法】
`e.audio` 里存的就是官方直链（https://audio4.redcircle.com/...），
直链实测可用（206 + Range + duration 正确）⇒ AUDIO_BASE 应当是空串。

🔴 与 `scripts/strip_inject.py` 的分工：
   那个剥「统计脚本 + 悬浮球」，这个剥「媒体 CDN 前缀」——两件事，别混。

用法：
  python3 strip_audio_base.py --check  <文件...>   # 只检查，报红
  python3 strip_audio_base.py --fix    <文件...>   # 剥掉并回读校验
"""
import argparse
import os
import re
import sys

# mcode 注入的媒体 CDN 前缀（任意 *.space.mcode.cn 节点）
# 🔴 有两种变量名、两种写法：
#   `var AUDIO_BASE = '...'`      （站点版 / 单文件版）
#   `AUDIO_BASE_LOCAL = '...'`    （旧内置版 app.js）
PAT = re.compile(
    r"""((?:\bvar\s+)?\bAUDIO_BASE(?:_LOCAL)?\s*=\s*)'([^']*space\.mcode\.cn/media/)'""")

FEATURES = ['audioUrl', 'AUDIO_BASE', 'redcircle', 'hasAudio', 'formatMs', 'curEp']


def strip_text(s):
    """把被注入的 AUDIO_BASE 改回空串；返回 (新文本, 改了几处)"""
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        # 引号保留，只把里面的 URL 抹掉 ⇒ 长度净减 len(URL)
        return m.group(1) + "''"

    return PAT.sub(rep, s), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    if not args.check and not args.fix:
        ap.error("要 --check 或 --fix")

    bad = []
    for f in args.files:
        if not os.path.exists(f):
            print("跳过（不存在）: %s" % f)
            continue
        raw = open(f, encoding="utf-8").read()
        matches = list(PAT.finditer(raw))
        hits = [m.group(0) for m in matches]
        if not hits:
            print("  ✓ %-60s 干净" % f)
            continue
        if args.check:
            bad.append(f)
            print("  ✗ %-60s 被注入 %d 处: %s" % (f, len(hits), hits[0][:60]))
            continue
        # --fix
        before_feat = {k: raw.count(k) for k in FEATURES}
        new, n = strip_text(raw)
        if n == 0:
            print("  ✓ %-60s 无需改" % f)
            continue
        # 断言：只抹掉 URL 本体 ⇒ 净长度变化必须精确等于被抹掉的 URL 长度之和
        expect_delta = sum(len(m.group(2)) for m in matches)
        delta = len(raw) - len(new)
        if delta != expect_delta:
            print("  ✗ %s 长度变化异常（%d != %d）⇒ 拒绝写入" % (f, delta, expect_delta))
            sys.exit(2)
        open(f, "w", encoding="utf-8").write(new)
        # 回读校验
        back = open(f, encoding="utf-8").read()
        if PAT.search(back):
            print("  ✗ %s 回读仍有注入 ⇒ 拒绝" % f)
            sys.exit(2)
        after_feat = {k: back.count(k) for k in FEATURES}
        if before_feat != after_feat:
            print("  ✗ %s 功能特征词数量变了（%s -> %s）⇒ 拒绝"
                  % (f, before_feat, after_feat))
            sys.exit(2)
        print("  ✓ %-60s 剥掉 %d 处（AUDIO_BASE -> ''）" % (f, n))

    if bad:
        print("\n❌ %d 个文件仍带平台注入的媒体前缀：" % len(bad))
        for f in bad:
            print("   " + f)
        sys.exit(1)
    if args.fix:
        print("\n✅ 全部干净")


if __name__ == "__main__":
    main()
