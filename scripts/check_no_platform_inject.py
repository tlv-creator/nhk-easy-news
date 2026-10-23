#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_no_platform_inject.py —— 发布闸门：源文件里不许有平台部署注入的痕迹

为什么要有这道闸门（真实事故链，不是假想）：
  · mcode 的 website_deploy 会往 HTML 里注入两样东西：
      ① <script data-minimax-analytics="...">  ← 会 POST 到 agent.minimax.cn（带 node_id/session_id）
      ② <div id="minimax-floating-ball"> + initFloatingBall()  ← 悬浮球，z-index 9999 会盖住按钮
  · 我的 Pages 发布源是从「本地正式版」来的，本来是干净的；
    但工作区里同时存在一份 live.html（从 mcode 站抓回来的，368KB，带完整 18 行注入）
    ⇒ 哪天 SRC 指错了，或从线上反抓一份当源，注入就跟着上 Pages
  ⇒ 所以：**不自动剥，直接拒绝发布**。
      剥会掩盖「源被污染了」这个事实；Pages 的源应该天然干净。

🔴 一个坑（写这道闸门时踩的）：
  我自己加的那行「藏球 CSS」也是 minimax 字样：
      #minimax-floating-ball { display: none !important; }
  ⇒ 笼统地判 "minimax" 会**误伤自己** ⇒ 判据必须落在「注入的元素/脚本」上，
    而不是「这个词出现过」。

用法：
  python3 tools/check_no_platform_inject.py <file.html> [更多文件...]
  退出码：0 = 干净 / 1 = 有注入（拒绝发布）/ 2 = 没东西可检（拒绝放行）
"""
import io
import os
import re
import sys

# ---------------------------------------------------------------------------
# 注入特征：每一项都必须是「平台注入才有的东西」，不能是「我自己写的 CSS」
#   键 = 特征名，值 = (正则, 为什么它一定是注入)
# ---------------------------------------------------------------------------
FEATURES = [
    ("统计脚本标签",
     r'<script[^>]*\bdata-minimax-analytics\b',
     "平台注入的统计脚本标签，只有部署才会加"),
    ("统计上报域名",
     r'agent\.minimax\.cn',
     "统计脚本 POST 的目标域名，源文件里出现就说明这份是从线上抓回来的"),
    ("悬浮球元素",
     r'<div[^>]*\bid=["\']minimax-floating-ball["\']',
     "平台注入的悬浮球 DOM 节点"),
    ("悬浮球初始化",
     r'\binitFloatingBall\s*\(',
     "平台注入的悬浮球初始化函数（自己写的 CSS 不会带这个函数名）"),
    ("悬浮球内部类",
     r'\bminimax-(?:ball-content|logo-wave|close-icon)\b',
     "平台注入的悬浮球内部元素类名"),
]

# 我自己主动加的「藏球 CSS」——它是**保护**不是污染，要显式放行并打印出来
OWN_CSS = re.compile(
    r'^\s*#minimax-floating-ball\s*\{\s*display\s*:\s*none\s*!\s*important\s*;?\s*\}\s*$',
    re.M,
)


def strip_comments(text):
    """等长剥注释：换行保留、其余换空格 ⇒ 行号/偏移不变。
    （注释里的域名/特征不该算命中 —— 同墨水屏闸门的做法）"""
    def blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    text = re.sub(r'<!--.*?-->', blank, text, flags=re.S)
    text = re.sub(r'/\*.*?\*/', blank, text, flags=re.S)
    text = re.sub(r'(?m)^\s*//[^\n]*', blank, text)
    return text


def scan(path):
    """返回 (命中列表, 自己的CSS处数)"""
    try:
        raw = io.open(path, encoding='utf-8', errors='replace').read()
    except Exception as e:
        return [("读取失败", "无法读取文件: %s" % e, 0)], 0
    text = strip_comments(raw)
    hits = []
    for name, pat, why in FEATURES:
        for m in re.finditer(pat, text, flags=re.I):
            line = text.count('\n', 0, m.start()) + 1
            hits.append((name, why, line))
    own = len(OWN_CSS.findall(raw))
    return hits, own


def main(argv):
    files = [a for a in argv[1:] if not a.startswith('-')]
    if not files:
        sys.stderr.write("❌ 没给要检查的文件（查不到就不能放行）\n")
        return 2

    total_hits = 0
    for f in files:
        if not os.path.isfile(f):
            sys.stderr.write("❌ 文件不存在: %s\n" % f)
            return 2
        hits, own = scan(f)
        name = os.path.basename(f)
        if hits:
            total_hits += len(hits)
            print("❌ %s —— 发现 %d 处平台注入痕迹" % (name, len(hits)))
            for hname, why, line in hits:
                print("     第 %d 行 · %s" % (line, hname))
                print("        理由: %s" % why)
        else:
            extra = ""
            if own:
                extra = "（另有 %d 处**我自己加的藏球 CSS**，已放行 ✅）" % own
            print("✅ %s —— 无平台注入%s" % (name, extra))

    if total_hits:
        print("")
        print("⛔ 源文件被平台污染了 ⇒ 拒绝发布。")
        print("   修法二选一：")
        print("   ① 用本地正式版源（推荐）：site/vocab-unit123-v1-prod-standalone.html")
        print("   ② 先剥干净再发：python3 tools/strip_platform_inject.py <file>")
        print("   （注意：剥掉会掩盖「源是从线上抓的」这个事实，先搞清楚它为什么脏）")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
