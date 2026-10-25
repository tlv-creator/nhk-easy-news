#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨水屏描边闸门（Python 版）—— 2026-10-22 v3
========================================================================
口径与 JS 版 check_eink_css.js **逐条一致**（两份可互换使用）。

为什么需要这个闸门
--------------------------------------------------------------------------
电子墨水屏（黑白）上，**纯黑描边会糊成一排粗杠杠**。
半透明边框（rgba(0,0,0,.35)）在墨水屏上才是「一条细线」。
真实事故：2026-10-22 v1.5 带着 6 处纯黑盒子边框上线（Shun 实测抓到，
不是自测抓到的）→ 光修 client 目录不够，**每个项目各有一份 css 副本**。

判定口径
--------------------------------------------------------------------------
  盒子边框（四边 / top / right / left）→ 颜色解出来是纯黑就违规
  border-bottom                        → 豁免（结构线），但**只数实黑的**、计入名额（默认 ≤2）
  var(--x)                             → **解析变量链**（--x → var(--y) → 值）
  **跨文件变量表**                     → 传入的所有 css 的变量**合并**
  **同名变量多定义**（多主题）          → 任一定义解析出硬色即算硬
  border: 1px solid #000, var(--x)      → 取**第一个具体色**
  纯黑判定：black / #000 / #000000 / rgb(0,0,0) / rgba(0,0,0,1)
  **!important 要先剥掉**              → 否则 '#000 !important' 判不出纯黑（实测漏检）
  currentColor                         → 解析不了 → **FAIL**
  变量解析不出来                        → **FAIL**（不是 WARN）
  豁免标记：所在行（或上一行）含「eink-ok:」→ 跳过

  🔴 为何「解析不出」判 FAIL 而不是 WARN：
     css 的 :root 变量定义就在同一文件里，解析不出多半是真的有问题；
     放行 = 假绿灯。宁可拦住，发布前人工确认。

用法
--------------------------------------------------------------------------
    python3 check_eink_css.py <css 文件...>            # 检查，违规 exit 1
    python3 check_eink_css.py --fix <css 文件...>       # 自动改成 rgba
    python3 check_eink_css.py --max-structure 2 <f>    # 自定义结构线名额

    🔴 **把所有 css 一起传**（变量表跨文件合并，子项目 css 的变量常定义在 shell 里）

退出码：0 = 通过；1 = 发现不合规（发布前应 die() 掉）
"""
import argparse
import json
import os
import re
import sys

# 统一输出层的「修法」段 —— 与 JS 版逐字一致
FIX_HELP = [
    '═════════════════════════════════════════════',
    '墨水屏描边不合规 —— 拒绝发布。',
    '修法（任选其一）：',
    '  ① 盒子边框改成 rgba(0, 0, 0, .35)',
    '     border: 2px solid #000;  →  border: 2px solid rgba(0, 0, 0, .35);',
    '  ② 直接换用已修好的 client（v1.6+）：',
    '     https://dcjk8zanl3e4r.space.mcode.cn/ai-client.js',
    '  ③ border-bottom 的实黑**不用改** —— 那是结构线（.ai-h / .ai-thead）',
    '  ④ 确实该留的 → 在该行加「eink-ok: 理由」显式豁免',
    '     （例：当前句位置指示竖条 border-left: 6px solid #000 —— 设计要求）',
    '═════════════════════════════════════════════',
]

# 盒子边框 = border / border-top / border-right / border-left
# 🔴 border-bottom 不在这个正则里（结构线，单独统计）
# 🔴 「收紧」时的坑：若只留 bottom 当豁免，top/right/left 的纯黑会
#    既不豁免、也完全不检查 → 从「放水」变成「失明」。所以它们按盒子边框查。
# 🔴 宽度必须能匹配**小数**：`border:1.5px solid var(--line)` 是 hub 总目录页真实在用的
#    → 原来 `(\d+)px` 匹配不到（`5px` 前面是 `.` 不在 `[;{\s]` 里）→ **静默漏检**
SOLID_BOX_RE = re.compile(
    r'(^|[;{\s])(border(?:-(?:top|right|left))?)\s*:\s*(\d+(?:\.\d+)?)px\s+solid\s+([^;]+?)\s*;',
    re.MULTILINE)

# 结构线 = border-bottom
SIDE_BORDER_RE = re.compile(
    r'(^|[;{\s])border-bottom\s*:\s*(\d+(?:\.\d+)?)px\s+solid\s+([^;]+?)\s*;',
    re.MULTILINE)

VAR_DEF_RE = re.compile(r'(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+);')
EINK_OK_RE = re.compile(r'eink-ok\s*:', re.IGNORECASE)
IMPORTANT_RE = re.compile(r'!\s*important\b', re.IGNORECASE)


def strip_important(s):
    """'#000 !important' → '#000'（漏检头号原因：不剥掉就判不出纯黑）。"""
    return IMPORTANT_RE.sub('', str(s if s is not None else '')).strip()


def is_pure_black(color):
    """#000 / #000000 / black / rgb(0,0,0) / rgba(0,0,0,1) 都算纯黑。"""
    c = strip_important(color).lower()
    if c == 'black':
        return True
    if c in ('#000', '#000000'):
        return True
    if re.match(r'^rgba?\(\s*0\s*,\s*0\s*,\s*0\s*(,\s*1(\.0+)?\s*)?\)$', c):
        return True
    return False


# 🔴🔴🔴 墨水屏是**二值**的 —— 「近黑」在屏上就是纯黑
# 真实事故：hub 总目录页 `:root{ --line:#111 }` + `button{border:1.5px solid var(--line)}`
#   → #111 亮度 7%，黑白墨水屏渲染出来是**纯黑** → 一排粗杠杠
#   → 而老口径只认字面量 #000 → **静默放行**（假绿灯）
# ⇒ 判据从「纯黑字面值」改成「**屏上会变黑的亮度**」。
# 亮度口径 = (R+G+B)/3/255；阈值默认 0.25（约 #3f3f3f 以内都算）→ --max-dark 可配。
MAX_DARK = 0.08   # 纯黑级：亮度 ≤ 此值 → 墨水屏上就是纯黑（#141414 以内）
MAX_MID = 0.15  # 🔴🔴 v4.4 从 0.25 收到 0.15 —— 0.25 **卡在主题线色中间**：
  #   主题线色 #2e4a32(22%) / #3d3358(26%) 差 4% 就翻转判定，视觉上几乎没差
  #   ⇒ 阈值定在「明显暗于主题线色」（0.08~0.15）；22%/26% 一律放行    # 深色级：亮度 ≤ 此值 且 宽度 ≥ MIN_THICK → 粗深线也糊
MIN_THICK = 2     # 深色级要求的最小宽度（px）：1px 深灰细线不是「粗杠杠」


def parse_rgb(color):
    """#rgb / #rrggbb / rgb() / rgba() / black / white → (r,g,b,a)；认不出返回 None。"""
    c = strip_important(color).lower().strip()
    if c == 'black':
        return (0, 0, 0, 1.0)
    if c == 'white':
        return (255, 255, 255, 1.0)
    m = re.match(r'^#([0-9a-f]{3})$', c)
    if m:
        h = m.group(1)
        return (int(h[0] + h[0], 16), int(h[1] + h[1], 16), int(h[2] + h[2], 16), 1.0)
    m = re.match(r'^#([0-9a-f]{6})$', c)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.match(r'^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})'
                 r'\s*(?:,\s*([\d.]+)\s*)?\)', c)
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), a)
    return None


def brightness(rgb):
    return (rgb[0] + rgb[1] + rgb[2]) / 3.0 / 255.0


def alpha_of(rgb):
    return rgb[3] if len(rgb) > 3 else 1.0


def is_eink_black(color, width_px=1.0, is_all=True):
    """屏上会变黑？（二维判据：亮度 × 宽度）

    🔴🔴 **只按亮度判会误报一堆**：实测 hub v1.11 的 8 个 css，
       165 条里 **164 条**是 `#2e4a32`（22% 亮度的深绿灰分隔线，1px 宽）
       → 那是 sakura 主题的分隔线色，1px 深灰线在墨水屏上是**细线**，不是「粗杠杠」。
       真正该抓的只有 1 条 `#0b0b0b`（4.3%）。
    🔴 而且「墨水屏是二值的」这个前提**不严谨** —— 实际是 16 级灰度，
       深灰会显示成深灰，不是纯黑。真正扎眼的是「**纯黑级 + 粗**」。

    ⇒ 判据改成二维：
       ① 纯黑级（亮度 ≤ MAX_DARK，默认 8%，约 #141414 以内）→ 任何宽度都违规
       ② 深色级（≤ MAX_MID，默认 25%）且 宽度 ≥ MIN_THICK（2px）→ 违规
       ③ 1px 的深色线 → 放行（是细线，不是粗杠杠）

    🔴🔴 **半透明不是硬色** —— rgba(0,0,0,.35) 在墨水屏上是**淡线**，不是黑
       （我加近黑判据时漏了 alpha → 把所有半透明边框误判成违规，
        **E1/E2 真实 css 组当场抓到的回归**）。半透明本来就该放行 —— v3 的规则。
    """
    if is_pure_black(color):
        return True
    rgb = parse_rgb(color)
    if not rgb:
        return False
    if alpha_of(rgb) < 1:
        return False          # 半透明 → 放行
    b = brightness(rgb)
    if b <= MAX_DARK:
        return True           # 纯黑级：任何宽度
    if is_all and b <= MAX_MID and width_px >= MIN_THICK:
        return True           # 深色级：只对**四边框**且够粗
    return False


def black_why(color, width_px=1.0, is_all=True):
    """文案：区分「纯黑级」和「深色粗线」—— 用户要知道自己改的是哪种。"""
    c = strip_important(color)
    if is_pure_black(color):
        return '纯黑盒子边框（解出来是 %s）—— 墨水屏上会糊成粗杠杠' % c
    rgb = parse_rgb(color)
    pct = int(round(brightness(rgb) * 100))
    if brightness(rgb) <= MAX_DARK:
        return ('纯黑级盒子边框（解出来是 %s，平均亮度 %d%% ≤ %d%%）'
                '—— 墨水屏上等同纯黑，会糊成粗杠杠'
                % (c, pct, int(round(MAX_DARK * 100))))
    return ('深色粗盒子边框（解出来是 %s，平均亮度 %d%%，四边宽度 %gpx ≥ %dpx）'
            '—— 墨水屏上接近纯黑，粗框会糊成杠杠'
            % (c, pct, width_px, MIN_THICK))


def parse_var_defs(src, into=None):
    """收集变量定义 → {name: [值, 值, ...]}。

    🔴 **同名多定义不能覆盖** —— '--sh-line' 在 shell.css 里有 7 个主题定义
       （#edd9ea / #3d3358 / ... / rgba(255,255,255,.26)），
       覆盖式收集只剩最后一个 → 另外 6 个主题查不到 → 某主题是纯黑就漏检。
    """
    defs = into if into is not None else {}
    for m in VAR_DEF_RE.finditer(src):
        name = m.group(1).lower()
        val = strip_important(m.group(2))
        defs.setdefault(name, [])
        if val not in defs[name]:
            defs[name].append(val)
    return defs


def resolve_var_all(var_name, var_defs, depth=0):
    """var(--x) → var(--y) → 终端值列表（最多 5 层）。

    返回**所有**终端值（一个变量可能有多条定义 / 多主题）。
    """
    if depth > 5:
        return []
    key = var_name.lower()
    if key not in var_defs:
        return []
    out = []
    for raw in var_defs[key]:
        m = re.match(r'^var\(\s*(--[A-Za-z0-9_-]+)\s*\)$', raw)
        if m:
            out.extend(resolve_var_all(m.group(1), var_defs, depth + 1))
        else:
            out.append(raw)
    return out


def split_top_level(value):
    """按**顶层逗号**切。

    🔴 **不能按逗号硬切** —— 颜色自身就可能带逗号（rgb(0,0,0)），
       硬切会把 rgb(0,0,0) 切成 'rgb(0' → 纯黑判定失效（实测漏检）。
       同理 var(--x, #eee) 里的逗号也在括号内，不能切。
    """
    parts, depth, cur = [], 0, ''
    for ch in str(value):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
            continue
        cur += ch
    parts.append(cur)
    return [strip_important(p).strip() for p in parts if p.strip()]


def split_fallback(value):
    """border: 1px solid #000, var(--x) → 第一个「具体色」。"""
    lst = split_top_level(value)
    for p in lst:
        if not p.lower().startswith('var('):
            return p
    return lst[0] if lst else ''


def parse_var_call(s):
    """var(--x) / var(--x, #eee) / var(--x, var(--y)) → (name, fallback|None)"""
    t = strip_important(s)
    if not t.lower().startswith('var('):
        return None
    depth, end = 0, -1
    for i in range(3, len(t)):
        if t[i] == '(':
            depth += 1
        elif t[i] == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    parts = split_top_level(t[4:end])
    if not parts:
        return None
    fallback = ', '.join(parts[1:]) if len(parts) > 1 else None
    return parts[0].strip(), fallback


def extract_style_blocks(src):
    """🔴 HTML 内联 <style> 也要扫。

    真实事故：hub 总目录页把主题变量（:root{--line:#111}）**内联在 <style> 块**里
    → 只扫 .css 文件 = **完全扫不到** → 假绿灯。
    行号保持：<style> 之前的内容用等行数的空行占位
    → 块内第 k 行的行号 = 原文件里那一行的行号。
    """
    out = []
    last = 0
    count = 0
    for m in re.finditer(r'<style\b[^>]*>(.*?)</style>', src,
                         re.IGNORECASE | re.DOTALL):
        before = src[last:m.start()]
        out.append('\n' * (before.count('\n')))
        out.append(m.group(1))
        last = m.end()
        count += 1
    out.append('\n' * src[last:].count('\n'))
    return ''.join(out), count


def strip_comments(src):
    """🔴🔴 剥掉 CSS 注释（**保留行号**：注释里的换行留着，其余换成空格）

    为什么必须剥：
      ① **注释里的变量定义会污染跨文件合并的变量表**
         —— 实测：hub 的 sakura/style.css 注释里写了「它的 `--line:#000000`
         还会污染跨文件合并的变量表」→ 闸门把这条**注释里的** `--line:#000000`
         当成真定义 → dora/pony/tadoku 里合法的 var(--line) 全被带成误报
         （一次扫描 81 条，其中绝大多数是假的）
      ② 注释里写的 `border: 2px solid #000` 不该算违规
      ③ 注释里写的 `eink-ok:` 不该真的豁免代码行
      ⇒ 判据统一走「剥掉注释后的文本」，行号不变（换行保留）。
    """
    def _blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    return re.sub(r'/\*.*?\*/', _blank, src, flags=re.S)


# ══════════════════════════════════════════════════════════════════
# 🔴🔴 变量表必须**按页面分组** —— 同名变量在不同页面里是**无关**的
#
# CSS 语义：变量在**同一文档**内解析。
# hub 的结构：总目录 index.html（自己内联 --line:#111）+ 6 个项目子页
#   → 总目录的 `--line:#111` **不该**污染 sakura 的 `var(--line)`
#   → 全局合并变量表 ⇒ 一次扫描 93 条，其中 **80 条是假的**
#
# 分组规则（两个实现必须**完全一致**）：
#   ① 每个 html = 一个页面组 = {该 html} ∪ {它引用的 css（按引用顺序）}
#   ② 没被任何 html 引用的 css → 自己单独一组
#   ③ 传入列表里**没有 html** → 退回旧行为（全局合并），单文件扫描口径不变
CSS_REF_RE = re.compile(r'''(?:href|src)\s*=\s*["']([^"']+\.css[^"']*)["']''', re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r'''@import\s+(?:url\()?["']([^"']+\.css)["']''', re.IGNORECASE)


def parse_css_refs(text, html_path):
    out = []
    d = os.path.dirname(os.path.abspath(html_path))

    def push(raw):
        rel = raw.split('?')[0].split('#')[0].strip()
        if not rel:
            return
        if re.match(r'^(https?:)?//', rel, re.IGNORECASE):
            return
        ab = os.path.normpath(os.path.join(d, rel))
        if ab not in out:
            out.append(ab)

    for m in CSS_REF_RE.finditer(text):
        push(m.group(1))
    for m in CSS_IMPORT_RE.finditer(text):
        push(m.group(1))
    return out


def build_groups(files):
    ab = [os.path.abspath(f) for f in files]
    aset = set(ab)
    htmls = [f for f in files if re.search(r'\.html?$', f, re.IGNORECASE)]
    css_only = [f for f in files if not re.search(r'\.html?$', f, re.IGNORECASE)]
    groups = []
    if not htmls:
        return None          # 没有 html → 退回全局合并（旧行为）
    for h in htmls:
        g = [os.path.abspath(h)]
        # 🔴 必须读**原始 html** —— read_for_scan 对 html 只返回 <style> 块，
        #    `<link href="...css">` 全丢了 → 一个引用都找不到 → 分组全废
        try:
            with open(h, encoding='utf-8') as fh:
                txt = fh.read()
        except Exception:
            txt = ''
        for ref in parse_css_refs(txt, h):
            if ref in aset and ref not in g:
                g.append(ref)
        groups.append(g)
    for c in css_only:
        a = os.path.abspath(c)
        if not any(a in g for g in groups):
            groups.append([a])
    return groups


def group_vars(group):
    out = {}
    for f in group:
        if not os.path.exists(f):
            continue
        try:
            parse_var_defs(read_for_scan(f)[0], out)
        except Exception:
            pass
    return out


def vars_for_file(file, groups):
    """一个文件可能被多个页面引用（例：shell/shell.css）→ 各页面的表取并集"""
    if not groups:
        return None
    out = {}
    hit = False
    a = os.path.abspath(file)
    for g in groups:
        if a not in g:
            continue
        hit = True
        for f in g:
            if not os.path.exists(f):
                continue
            try:
                parse_var_defs(read_for_scan(f)[0], out)
            except Exception:
                pass
    return out if hit else None


def read_for_scan(path):
    """读一个文件用于扫描：html → 只取 <style> 块（行号保持）。

    🔴🔴 **判断和提取必须用同一个逻辑** ——
       原来这里用 `re.search(r'<style\b')` 判断、那边用「有闭合标签」提取
       → 一个**只在注释里提到 `<style` 的 .css 文件**会被误判成 HTML
       → 整份 css 被抽成 49 字节空壳 → **一条违规都扫不到**（假绿灯）
       （我自己写测试样本时踩到的：注释里写了「HTML 内联 <style>」）
       ⇒ 改成：先 extract，再看抽到几个块。
    """
    with open(path, encoding='utf-8') as f:
        raw = f.read()
    css, count = extract_style_blocks(raw)
    # 🔴 统一在**读取时**剥注释 → 下游（扫描 / 修 / 变量表）拿到的都是干净文本
    #    （放在读取层 → --fix 路径与扫描路径口径必然一致）
    if not count:
        return strip_comments(raw), 0, raw
    # 🔴 第三项 text_raw = **带注释**的原文（strip_comments 是等长替换 → 偏移/行号不变）
    #    → 豁免标记 has_eink_ok 用 text_raw 判定：
    #      「在上一行注释里写 eink-ok: 理由」是**正常用法**（ai-client.css 就这么写）
    #    剥注释只该影响「变量定义收集」和「边框正则匹配」，不该让豁免失效
    return strip_comments(css), count, css


def has_eink_ok(src, index):
    """所在行（或上一行）有没有 eink-ok 豁免标记。"""
    line_no = src[:index].count('\n') + 1
    lines = src.split('\n')
    cur = lines[line_no - 1] if 0 <= line_no - 1 < len(lines) else ''
    prev = lines[line_no - 2] if 0 <= line_no - 2 < len(lines) else ''
    return bool(EINK_OK_RE.search(cur) or EINK_OK_RE.search(prev))


def resolve_color_value(raw_value, var_defs, depth=0):
    """把一个颜色表达式解析成（候选色列表, error）。

    🔴 **var 带 fallback 时要用 fallback**：
       `border: 1px solid var(--nope, #eeeeee)` —— --nope 没定义时，
       浏览器渲染的就是 #eeeeee → 合法。不该报「解析不出来」。
       （这是对拍样本自己抓出来的 bug：两份实现一开始都误报了这条）
    """
    if depth > 5:
        return [], '变量链太深（>5 层），解析不了'
    v = strip_important(raw_value)

    pc = parse_var_call(v)
    if pc is None:
        if v.lower() == 'currentcolor':
            return [], ('currentColor 跟着 color 走，解析不了 —— '
                        '请改成具体色，或加「eink-ok: 理由」')
        return [v], None

    name, fallback = pc
    resolved = resolve_var_all(name, var_defs)
    if resolved:
        out = []
        for r in resolved:
            colors, err = resolve_color_value(r, var_defs, depth + 1)
            if err:
                return [], err
            out.extend(colors)
        return out, None

    # var 本身没定义 → 用 fallback
    if fallback:
        fparts = split_top_level(fallback)
        concrete = [p for p in fparts if not p.lower().startswith('var(')]
        return resolve_color_value(concrete[0] if concrete else fparts[0],
                                   var_defs, depth + 1)

    return [], ('%s 在这些 css 里解析不出来 —— 可能是纯黑但没法确认；'
                '要么补上定义，要么在该行加「eink-ok: 理由」显式豁免' % name)


def judge_color(raw_value, var_defs):
    """返回 (colors, error) —— colors 是候选色列表；error 非空表示这条要 FAIL。"""
    return resolve_color_value(split_fallback(raw_value), var_defs, 0)


def scan(src, max_structure=2, var_defs=None, src_raw=None):
    """返回 (违规列表, 实黑结构线条数, 警告列表)

    var_defs 传**跨文件合并**后的变量表。
    src_raw   传**带注释**的原文（与 src 等长）→ 判 eink-ok 豁免用；
              省略时退化为 src（豁免只认代码行上的标记）。
    """
    if var_defs is None:
        var_defs = parse_var_defs(src)
    if src_raw is None:
        src_raw = src
    bad, warns = [], []

    for m in SOLID_BOX_RE.finditer(src):
        if has_eink_ok(src_raw, m.start()):
            continue
        value = m.group(4).strip()
        line = src[:m.start()].count('\n') + 1
        colors, err = judge_color(value, var_defs)
        if err:
            bad.append((line, m.group(0).strip(), err))
            continue
        # 任一候选色是硬色 → 违规（多主题定义同理）
        wpx = float(m.group(3))
        # 🔴 「粗」的判定**只对四边框**生效：单边 2px 只是一条分隔线
        is_all = (m.group(2) == 'border')
        for c in colors:
            if is_eink_black(c, wpx, is_all):
                bad.append((line, m.group(0).strip(), black_why(c, wpx, is_all)))
                break

    # 🔴 结构线**只数实黑的** —— 半透明的 border-bottom 不该占名额
    struct_lines = []
    for m in SIDE_BORDER_RE.finditer(src):
        if has_eink_ok(src_raw, m.start()):
            continue
        value = m.group(3).strip()
        colors, err = judge_color(value, var_defs)
        if err:
            continue
        for c in colors:
            if is_eink_black(c, float(m.group(2)), True):
                struct_lines.append((src[:m.start()].count('\n') + 1, m.group(0).strip()))
                break

    # 🔴 结构线超名额**也要当一条 problem 列出**（line=0）
    #    —— JS 版是这么做的；两边格式不一致会让「跨实现对拍」永远红
    if len(struct_lines) > max_structure:
        bad.append((0,
                    'border-bottom 实黑出现 %d 条' % len(struct_lines),
                    '结构线最多 %d 条（.ai-h 五段标题 + .ai-thead 表头）' % max_structure))

    return bad, struct_lines, warns


def fix(src, var_defs=None):
    """把纯黑盒子边框改成 rgba(0,0,0,.35)；保留原属性名 + 保留 !important。"""
    if var_defs is None:
        var_defs = parse_var_defs(src)
    n = 0

    def _repl(m):
        nonlocal n
        if has_eink_ok(src_raw, m.start()):
            return m.group(0)
        value = m.group(4).strip()
        colors, err = judge_color(value, var_defs)
        if err or not any(is_eink_black(c, float(m.group(3)), True) for c in colors):
            return m.group(0)
        n += 1
        # 保留原属性名（border / border-top / border-right / border-left）
        prop = m.group(2).strip()
        # 保留 !important（改颜色不改优先级）
        imp = ' !important' if IMPORTANT_RE.search(value) else ''
        return '%s%s: %spx solid rgba(0, 0, 0, .35)%s;' % (
            m.group(1), prop, m.group(3), imp)

    return SOLID_BOX_RE.sub(_repl, src), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--fix', action='store_true', help='自动改成 rgba')
    ap.add_argument('--json', action='store_true', help='输出 JSON（机器可读）')
    ap.add_argument('--max-structure', type=int, default=2,
                    help='border-bottom 实黑的豁免名额（默认 2）')
    ap.add_argument('--max-dark', type=float, default=0.08,
                    help='纯黑级亮度阈值（#141414 以内 → 墨水屏上等同纯黑）')
    ap.add_argument('--max-mid', type=float, default=0.15,
                    help='深色级亮度阈值（且宽度 ≥2px 才算「粗杠杠」）；'
                         'v4.4 起 0.15 —— 0.25 卡在主题线色中间会翻转判定')
    args = ap.parse_args()

    global MAX_DARK, MAX_MID
    MAX_DARK = max(0.0, min(1.0, args.max_dark))
    MAX_MID = max(0.0, min(1.0, args.max_mid))

    json_report = {'ok': True, 'max_structure': args.max_structure, 'files': []}

    def emit(text):
        if not args.json:
            print('[eink-css] ' + text)

    failed = 0

    # 🔴🔴 变量表按**页面**分组（同名变量在不同页面里无关）—— 见 build_groups 注释
    #    没有 html 传入 → 退回旧的「全局合并」行为（单文件扫描口径不变）
    groups = build_groups(args.files)
    if not groups and len(args.files) > 1:
        emit('提示：没传 html → 变量表按「跨文件合并」算 → 多页面站里同名变量会互相污染。')
        emit('      建议把 html 一起传（每个 html = 一个页面组）。')

    def scan_all():
        """一个文件可能被多个页面引用（例：shell/shell.css）→ 各页面各判一次，结果合并"""
        per = {}

        def put(f, bad, struct, warns, blocks):
            # 🔴🔴 key **必须**用绝对路径！
            #   否则：主循环用 os.path.abspath(path) 查、这里用相对路径存 → 对不上
            #   → 单文件（或任何没传 html 的多文件）扫描**全部返回 0 条 = 假绿灯**
            #   → 而且只在**相对路径**调用时触发 → 自检全用绝对路径 → 永远抓不到
            f = os.path.abspath(f)
            cur = per.setdefault(f, {'problems': [], 'warnings': [],
                                     'struct_count': 0, 'blocks': 0, 'error': None})
            seen = set('%s|%s|%s' % (p[0], p[1], p[2]) for p in cur['problems'])
            for p in bad:
                k = '%s|%s|%s' % (p[0], p[1], p[2])
                if k not in seen:
                    seen.add(k)
                    cur['problems'].append(p)
            seenw = set('%s|%s|%s' % (w[0], w[1], w[2]) for w in cur['warnings'])
            for w in warns:
                k = '%s|%s|%s' % (w[0], w[1], w[2])
                if k not in seenw:
                    seenw.add(k)
                    cur['warnings'].append(w)
            cur['struct_count'] = max(cur['struct_count'], len(struct))
            cur['blocks'] = cur['blocks'] or blocks

        if groups:
            for g in groups:
                gv = group_vars(g)
                for f in g:
                    try:
                        src, blocks, src_raw = read_for_scan(f)
                        bad, struct, warns = scan(src, args.max_structure, gv, src_raw)
                        put(f, bad, struct, warns, blocks)
                    except Exception as e:
                        per.setdefault(f, {'problems': [], 'warnings': [],
                                           'struct_count': 0, 'blocks': 0,
                                           'error': str(e)})
        else:
            gv = group_vars([os.path.abspath(f) for f in args.files])
            for f in args.files:
                try:
                    src, blocks, src_raw = read_for_scan(f)
                    bad, struct, warns = scan(src, args.max_structure, gv, src_raw)
                    put(f, bad, struct, warns, blocks)
                except Exception as e:
                    per.setdefault(f, {'problems': [], 'warnings': [],
                                       'struct_count': 0, 'blocks': 0,
                                       'error': str(e)})
        return per

    if args.fix:
        total = 0
        for path in args.files:
            if not os.path.exists(path):
                # 🔴 路径写错不能当「没东西可检」→ 那是假绿灯
                failed += 1
                emit('FAIL %s' % path)
                emit('  → 文件不存在（路径写错了？）。拒绝发布 —— 不能因为找不到文件就放行。')
                json_report['files'].append(
                    {'file': path, 'ok': False, 'error': 'missing_file'})
                continue
            if re.search(r'\.html?$', path, re.IGNORECASE):
                # 🔴 --fix 不改 HTML —— 内联 <style> 改写风险大（会动到整份文档结构）
                failed += 1
                emit('FAIL %s' % path)
                emit('  → --fix 不改 HTML（内联 <style> 改写风险大）—— 请手工改这处。')
                json_report['files'].append(
                    {'file': path, 'ok': False, 'error': 'html_no_autofix'})
                continue
            try:
                src, _blocks, src_raw = read_for_scan(path)
            except Exception as e:
                failed += 1
                emit('FAIL %s' % path)
                emit('  → 读不了：%s。拒绝发布 —— 不能因为读不了就放行。' % e)
                json_report['files'].append(
                    {'file': path, 'ok': False, 'error': 'unreadable'})
                continue

            fx_defs = (vars_for_file(path, groups)
                       or group_vars([os.path.abspath(x) for x in args.files]))
            new, n = fix(src, fx_defs)
            before = len(scan(src, args.max_structure, fx_defs, src_raw)[0])
            after = before
            if n:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new)
                with open(path, encoding='utf-8') as f:
                    after = len(scan(f.read(), args.max_structure, fx_defs, src_raw)[0])
                # 🔴 回滚条件是「修复让情况变差」，不是「还有问题」
                #    剩下那几条（var 解析不出 / currentColor）**本来就修不了**
                #    → 「只要还剩问题就整份回滚」会把**已经修好的也一起撤销**
                if after > before:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(src)
                    failed += 1
                    emit('FAIL %s' % path)
                    emit('  → 修复让情况变差，已回滚。')
                    json_report['files'].append(
                        {'file': path, 'ok': False, 'error': 'rollback'})
                    continue
            emit('%s %s（改了 %d 处）' % ('FIX ' if n else '—   ', path, n))
            total += n
            if after:
                failed += 1
                emit('⚠️  %s 还剩 %d 处修不了的（var 解析不出 / currentColor / 需人工）'
                     '—— 发布仍应被拦' % (path, after))
            json_report['files'].append(
                {'file': path, 'ok': after == 0, 'fixed': n, 'remain': after})

        emit('共改 %d 处。' % total)
        json_report['ok'] = (failed == 0)
        if args.json:
            print(json.dumps(json_report, ensure_ascii=False, indent=2))
        sys.exit(1 if failed else 0)

    per_file = scan_all()
    for path in args.files:
        if not os.path.exists(path):
            failed += 1
            emit('FAIL %s' % path)
            emit('  → 文件不存在（路径写错了？）。拒绝发布 —— 不能因为找不到文件就放行。')
            json_report['files'].append(
                {'file': path, 'ok': False, 'error': 'missing_file'})
            continue
        rec = per_file.get(os.path.abspath(path))
        if rec is None:
            rec = {'problems': [], 'warnings': [], 'struct_count': 0,
                   'blocks': 0, 'error': None}
        if rec.get('error'):
            failed += 1
            emit('FAIL %s' % path)
            emit('  → 读不了：%s。拒绝发布 —— 不能因为读不了就放行。' % rec['error'])
            json_report['files'].append(
                {'file': path, 'ok': False, 'error': 'unreadable'})
            continue

        bad = rec['problems']
        struct = rec['struct_count']
        warns = rec['warnings']
        blocks = rec['blocks']
        for line, frag, why in warns:
            emit('WARN %s' % path)
            emit('  第 %d 行：%s' % (line, frag))
            emit('    → %s' % why)
        note = '，扫了 %d 个 <style> 块' % blocks if blocks else ''
        if not bad:
            emit('OK   %s（实黑结构线 %d 条，豁免名额 %d%s）'
                 % (path, struct, args.max_structure, note))
            json_report['files'].append(
                {'file': path, 'ok': True, 'struct_count': struct,
                 'problems': []})
        else:
            failed += 1
            emit('FAIL %s' % path)
            for line, frag, why in bad:
                if line:
                    emit('  第 %d 行：%s' % (line, frag))
                else:
                    emit('  %s' % frag)
                emit('    → %s' % why)
            json_report['files'].append(
                {'file': path, 'ok': False, 'struct_count': struct,
                 'problems': [{'line': b[0], 'text': b[1], 'why': b[2]}
                              for b in bad]})

    if failed:
        emit('')
        for h in FIX_HELP:
            emit(h)
    else:
        emit('全部通过 ✅')
    json_report['ok'] = (failed == 0)
    if args.json:
        print(json.dumps(json_report, ensure_ascii=False, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
