#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剥掉 mcode 部署时注入的平台统计/悬浮球（**三层保险**）

背景：mcode 每次部署都会自动往 index.html 里注入
  ① PV 统计脚本（<script data-minimax-analytics=...>）→ POST 到 agent.minimax.cn
  ② 悬浮球 CSS（#minimax-floating-ball{...}）
  ③ 悬浮球 HTML + 初始化 JS（点它会 window.open 到 agent.minimax.cn）
  ⇒ 源码里删了也没用（下次部署又被注入）⇒ 必须三层一起做。

🔴 三条纪律（都是踩过的）：
  ① **不能把拦截器自己删掉** —— head 最前面那段 <script> 里写着
     `var BLOCK = 'agent.minimax.cn';` ⇒ 它是保护层
     ⇒ 删注入时如果用「把残留 host 全替换掉」这种粗暴做法，
        **会连拦截器的白名单常量一起替换掉** ⇒ 拦截器彻底失效且静默
  ② **括号计数不能当语法检查**（注释里的括号会骗）⇒ 删完必须 node --check 逐块验
  ③ **删完要断言「功能特征词数量不变」** ⇒ 防误删
"""
import re, sys, os, subprocess, tempfile

TARGET = sys.argv[1] if len(sys.argv) > 1 else None
if not TARGET:
    print("用法: strip_inject.py <index.html>")
    sys.exit(1)

s = open(TARGET, encoding='utf-8').read()
orig_len = len(s)
report = []


def must(cond, msg):
    if not cond:
        print("❌ " + msg)
        sys.exit(1)


# ── 记下「功能特征词」数量（用于事后证明没误删）──
FEATURES = ['字幕', '收藏', '字号', '墨水屏', '播放', '详解', '跟音', '普通版', '易解', '易読']
feat_before = {f: s.count(f) for f in FEATURES}

# ── ① 删统计脚本整块 ──
n = 0
while True:
    m = re.search(r'<script[^>]*data-minimax-analytics[^>]*>.*?</script>', s, re.S)
    if not m:
        break
    s = s[:m.start()] + s[m.end():]
    n += 1
report.append("删统计脚本块 ×%d" % n)

# ── ② 删悬浮球 HTML ──
m = re.search(r'<div[^>]*id=["\']minimax-floating-ball["\'][^>]*>.*?</div>\s*</div>', s, re.S)
if m:
    s = s[:m.start()] + s[m.end():]
    report.append("删悬浮球 HTML")

# ── ③ 删悬浮球初始化 JS（含 window.open 到外部那段）──
m = re.search(r'<script>\s*//\s*Initialize floating ball functionality.*?</script>', s, re.S)
if m:
    s = s[:m.start()] + s[m.end():]
    report.append("删悬浮球初始化 JS")

# ── ④ 悬浮球 CSS：整块规则改成永久隐藏（而不是删掉，因为 mcode 会重新注入 HTML）──
def hide_rule(m):
    body = m.group(2)
    if 'display' in body:
        return m.group(0)
    return m.group(1) + '{ display:none !important; ' + body.strip() + m.group(3)

# 多条规则逐条处理
n_css = 0
def css_repl(m):
    global n_css
    n_css += 1
    sel = m.group(1)
    body = m.group(2)
    # ⚠️ 不能「有 display 就跳过」—— 原来那条规则本来就有 display:flex
    #    ⇒ 改成**强制在规则体最前面**插 display:none !important（同规则内 !important 必赢）
    body2 = re.sub(r'display\s*:\s*[^;}]+;?', '', body)
    return sel + '{ display:none !important; ' + body2.strip() + '}'
s = re.sub(r'([#.][^{}]*minimax-floating-ball[^{}]*)\{([^{}]*)\}', css_repl, s)
report.append("悬浮球 CSS 规则 %d 条 → 加 display:none !important" % n_css)

# ── ⑤ 追加一条保险：万一 mcode 重新注入了 HTML，也强制隐藏 ──
if 'minimax-floating-ball' in s and 'display:none !important' not in s.split('</style>')[0]:
    s = s.replace('</style>',
                  '/* 平台悬浮球：永久隐藏（mcode 每次部署会重新注入） */\n'
                  '#minimax-floating-ball{display:none !important;}\n</style>', 1)
    report.append("追加保险隐藏规则")

# ══ 断言 ══
# ① 拦截器必须还在
must(re.search(r"var\s+BLOCK\s*=\s*'agent\.minimax\.cn'", s) is not None,
     "🔴 拦截器被误删了！必须保留 head 最前面那段 <script>")
must('navigator.sendBeacon' in s and 'window.fetch' in s, "🔴 拦截器内容不完整")

# ② 统计 token 必须没了
must('eyJ2Ijox' not in s, "🔴 统计 token 还在")
must(not re.search(r'data-minimax-analytics', s), "🔴 统计脚本还在")

# ③ 悬浮球必须不可见、不可点
must('window.open(\'https://agent.minimax.cn/' not in s, "🔴 悬浮球的 window.open 还在（会把用户甩出 App）")
must(not re.search(r'<div[^>]*id=["\']minimax-floating-ball', s), "🔴 悬浮球 HTML 还在")
if 'minimax-floating-ball' in s:
    must(re.search(r'#minimax-floating-ball\s*\{[^}]*display\s*:\s*none\s*!important', s),
         "🔴 悬浮球 CSS 规则还在但没被隐藏")

# ④ 功能特征词数量不变（防误删）
for f in FEATURES:
    must(feat_before[f] == s.count(f),
         "🔴 功能特征词 '%s' 数量变了：%d → %d（误删）" % (f, feat_before[f], s.count(f)))

# ⑤ 所有内联 script 必须语法正确
scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', s, re.S)
bad = 0
for i, code in enumerate(scripts):
    if not code.strip():
        continue
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(code)
        tmp = f.name
    r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode != 0:
        bad += 1
        print("  🔴 内联 script #%d 语法错: %s" % (i, r.stderr.strip()[:200]))
must(bad == 0, "🔴 有 %d 个内联 script 语法错" % bad)

open(TARGET, 'w', encoding='utf-8').write(s)
print("✅ 剥除完成（%d → %d 字节，-%d）" % (orig_len, len(s), orig_len - len(s)))
for r in report:
    print("   ·", r)
print("   · 内联 script %d 段，node --check 全过" % len(scripts))
print("   · 拦截器保留 ✅  功能特征词 %d 个全部不变 ✅" % len(FEATURES))
