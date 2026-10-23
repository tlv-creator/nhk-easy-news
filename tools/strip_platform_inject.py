#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strip_platform_inject.py —— 剥掉平台部署时注入到 HTML 里的东西

🔴 **它到底能做什么、不能做什么（2026-10-23 NHK 实测后定的位）**：
   · ✅ **能做**：① 剥**打进 APK assets** 的网页里的注入（hub 那种：网页打进 assets 自己加载）
                ② 洗**本地源文件**被工具链写回的污染
   · ❌ **做不到**：让**站上**那份没有注入 —— 平台**每次部署都会重新追加**，
                「上传前剥离」对站上那份**无效**（NHK 实测：手工删过，部署后又回来）。
   ⇒ 所以**纯 WebView App（页面从线上加载，APK 里没有 assets）不需要这个工具** —— 注入不在 APK 里。

🔴 **为什么要剥**：
   ① 统计脚本 `data-minimax-analytics` 会向 `agent.minimax.cn/…/page-views`
      POST 一次 page-view，payload 里带 `node_id` / `session_id` / `biz_id`
      → 学习 App 产生外部打点 = 用户隐私 + 流量
   ② 悬浮球 `#minimax-floating-ball` 会**盖住界面**（NHK 侧早踩过：盖住底栏「⬇下載」「A＋」）
      而且点它会 `window.open('https://agent.minimax.cn/')` → **跳出 App 打开浏览器**

🔴 **判定特征（都是平台特有的，不碰用户自己的代码）**：
   · `<script data-minimax-analytics="…">…</script>`      ← 统计脚本
   · `<div id="minimax-floating-ball">…</div>`            ← 悬浮球节点
   · 紧跟其后的 `<script>…function initFloatingBall()…</script>`  ← 悬浮球的初始化 JS
     （这段**没有**任何 minimax 标记，只能靠 `initFloatingBall` 这个函数名认）

🔴 **用法**：
    # 剥一个目录（递归找 *.html）
    python3 strip_platform_inject.py assets/
    # 只看会改什么，不落盘
    python3 strip_platform_inject.py assets/ --dry-run
    # 剥单个文件
    python3 strip_platform_inject.py web/index.html
    # 剥 APK 里的 html（解出来剥再打回去用别的脚本）
    python3 strip_platform_inject.py build/assets/

🔴 **不静默**：每个文件都报告「剥前/剥后字节 + 删了几处 + 删了哪几行」；
   **剥完必须再扫一遍**，有残留就 exit 1（不许「大概干净了」）。

🔴 **--check 模式**（只查不剥，可当 build 卡点 / 源文件污染巡检）：
```bash
python3 strip_platform_inject.py <路径> --check     # 有残留 → exit 1
```
⇒ 「剥完再扫一遍」这个校验**可以单独用**，不一定要先剥。
"""
import argparse
import os
import re
import sys

# ── 注入特征 ──────────────────────────────────────────────────────────
# ① 统计脚本：<script ... data-minimax-analytics="..."> ... </script>
RE_ANALYTICS = re.compile(
    r'[ \t]*<script\b[^>]*\bdata-minimax-analytics\b[^>]*>.*?</script\s*>[ \t]*\r?\n?',
    re.I | re.S)
# ② 悬浮球节点：<div id="minimax-floating-ball"> ... </div>
#    🔴 **不能用非贪婪 `.*?</div>`** —— 悬浮球内部还有**嵌套 div**
#       （`minimax-ball-content` / `minimax-close-icon`）→ 非贪婪会在**第一个** `</div>` 停 →
#       留下**孤儿节点**（页面里显示一个孤零零的「×」）→ 必须用**配对匹配**。
RE_BALL_DIV_START = re.compile(
    r'[ \t]*<div\b[^>]*\bid=["\']minimax-floating-ball["\'][^>]*>', re.I)
RE_DIV_OPEN = re.compile(r'<div\b', re.I)
RE_DIV_CLOSE = re.compile(r'</div\s*>', re.I)


def _match_ball_div(text, start):
    """从 <div id=minimax-floating-ball> 的起点开始，返回配对 </div> 之后的位置
    （找不到就返回 None —— 宁可不动，也不留半截）"""
    m = RE_DIV_OPEN.search(text, start)
    if not m:
        return None
    depth = 0
    pos = start
    while True:
        om = RE_DIV_OPEN.search(text, pos)
        cm = RE_DIV_CLOSE.search(text, pos)
        if not cm:
            return None
        if om and om.start() < cm.start():
            depth += 1
            pos = om.end()
            continue
        depth -= 1
        pos = cm.end()
        if depth <= 0:
            return pos
# ③ 悬浮球初始化脚本：<script> ... function initFloatingBall() ... </script>
RE_BALL_JS = re.compile(
    r'[ \t]*<script\b(?![^>]*\bdata-minimax-analytics\b)[^>]*>'
    r'(?:(?!</script\s*>).)*?\binitFloatingBall\b(?:(?!</script\s*>).)*?</script\s*>[ \t]*\r?\n?',
    re.I | re.S)

# ④ 内联 <style> 里针对悬浮球的 CSS 规则（节点没了就是死规则，可清）
#    只删**含 minimax 关键字的整条规则**，不碰别的。
RE_CSS_MINIMAX_RULE = re.compile(
    r'[ \t]*[^{}\n]*\bminimax[^{}\n]*\{[^}]*\}[ \t]*\r?\n?', re.I)


def strip_ball_div(text):
    """配对匹配地剥掉悬浮球 div → (新文本, 剥了几处)"""
    out = []
    pos = 0
    n = 0
    while True:
        m = RE_BALL_DIV_START.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        end = _match_ball_div(text, m.start())
        if end is None:
            # 配不上 → **保守不动**（留半截比删错更糟）
            out.append(text[pos:m.end()])
            pos = m.end()
            continue
        out.append(text[pos:m.start()])
        pos = end
        n += 1
        # 连带吃掉后面的换行/空白
        while pos < len(text) and text[pos] in ' \t\r\n':
            pos += 1
    return ''.join(out), n


RULES = [
    ('统计脚本 data-minimax-analytics', RE_ANALYTICS),
    ('悬浮球初始化脚本 initFloatingBall', RE_BALL_JS),
]


def count_residual(text, keep_ball=False):
    """还剩多少注入（剥完必须为 0）"""
    n = 0
    for _name, rx in RULES:
        if keep_ball and _name.startswith('悬浮球初始化脚本'):
            continue
        n += len(rx.findall(text))
    # 统计脚本可能被上面的规则吃掉一部分，单独再数一次特征串
    n += len(re.findall(r'data-minimax-analytics', text, re.I))
    n += len(re.findall(r'id=["\']minimax-floating-ball["\']', text, re.I))
    n += len(re.findall(r'\binitFloatingBall\b', text))
    # 🔴 **孤儿节点检测**：非贪婪剥法留下的残渣（`minimax-close-icon` 单独出现）
    n += len(re.findall(r'class=["\'][^"\']*minimax-(?:close-icon|ball-content|logo-wave|ball-text)', text, re.I))
    if keep_ball:
        # 放行的文件里，**球相关的东西本来就该留着** → 不算残留
        n -= len(re.findall(r'class=["\'][^"\']*minimax-(?:close-icon|ball-content|logo-wave|ball-text)', text, re.I))
        n -= len(re.findall(r'id=["\']minimax-floating-ball["\']', text, re.I))
        n -= len(re.findall(r'\binitFloatingBall\b', text))
    return n


def _is_allowed(path, allow_list):
    """路径后缀匹配 --allow 列表？"""
    p = str(path).replace('\\', '/')
    for suf in allow_list:
        suf = str(suf).replace('\\', '/').lstrip('./')
        if p.endswith(suf) or suf in p:
            return True
    return False


def strip_text(text, keep_ball=False):
    """剥掉注入 → 返回 (新文本, [(规则名, 删了几处)])

    🔴 `keep_ball=True`（--allow 放行）时：
       **保留悬浮球**（角标可能是**手写功能**，不是平台注入 —— 2026-10-23 pony 实测发现），
       但**统计脚本仍然剥**（那是纯平台注入，隐私问题）。
       ⚠️ 理由：悬浮球的**初始化脚本跟平台注入的一模一样**（都是 `function initFloatingBall()`），
       **唯一区别是前面有没有一句中文注释** → 靠猜太脆，**必须让人显式指定**。
    """
    removed = []
    if not keep_ball:
        # ① 悬浮球 div（**配对匹配**，不能用非贪婪）
        text, k = strip_ball_div(text)
        if k:
            removed.append(('悬浮球节点 #minimax-floating-ball', k))
    # ② 其余带标记的块
    for name, rx in RULES:
        if keep_ball and name.startswith('悬浮球初始化脚本'):
            continue          # 球留着，它的初始化脚本也得留着
        text, k = rx.subn('', text)
        if k:
            removed.append((name, k))
    # ③ 内联 style 里的 minimax 死规则（球留着时不能删它的样式）
    if not keep_ball:
        text, k = RE_CSS_MINIMAX_RULE.subn('', text)
        if k:
            removed.append(('内联 CSS 里的 minimax 死规则', k))
    # ④ 🔴 删完规则后，**空的 <style> 壳要一并删掉**（否则留下一个空标签）
    text = _drop_empty_style(text)
    return text, removed


RE_STYLE_BLOCK = re.compile(r'<style\b[^>]*>(.*?)</style\s*>', re.I | re.S)


def _drop_empty_style(text):
    """把**内容为空**的 <style>…</style> 整块删掉（只留纯空白）"""
    def rep(m):
        inner = m.group(1)
        if inner.strip() == '':
            return ''
        return m.group(0)
    return RE_STYLE_BLOCK.sub(rep, text)


def html_files(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if f.lower().endswith(('.html', '.htm')):
                        out.append(os.path.join(root, f))
        elif os.path.isfile(p):
            out.append(p)
        else:
            print('[WARN] 路径不存在，跳过：%s' % p)
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description='剥掉 HTML 里平台注入的统计脚本与悬浮球')
    ap.add_argument('paths', nargs='+', help='文件或目录（目录会递归找 *.html）')
    ap.add_argument('--dry-run', action='store_true', help='只看会改什么，不落盘')
    ap.add_argument('--quiet', action='store_true', help='只报有改动的文件')
    ap.add_argument('--check', action='store_true',
                    help='**只查不剥**：有残留就 exit 1（可当 build 卡点 / 源文件污染巡检）')
    ap.add_argument('--check-apk', action='store_true',
                    help='**直接扫 APK** 里的 assets/**/*.html（不解压、不落盘）')
    ap.add_argument('--allow', action='append', default=[], metavar='SUFFIX',
                    help='**放行**：路径后缀匹配的文件**保留悬浮球**（角标可能是手写功能），'
                         '但统计脚本仍剥。可多次给。'
                         '（例：--allow wakakusa/index.html）')
    a = ap.parse_args()

    files = html_files(a.paths)
    if not files:
        print('[FAIL] 没找到任何 html 文件')
        return 1

    allow_list = list(a.allow or [])
    changed = 0
    total_removed = 0
    problems = []
    dirty = []

    # ── --check-apk 模式：直接读 APK 里的 html（不解压落盘）──
    if a.check_apk:
        import zipfile
        dirty = []
        total = 0
        for apk in a.paths:
            try:
                zf = zipfile.ZipFile(apk)
            except Exception as e:
                print('[FAIL] 读不了 APK %s (%s)' % (apk, e))
                return 1
            names = [n for n in zf.namelist()
                     if n.lower().endswith(('.html', '.htm')) and not n.endswith('/')]
            if not names:
                print('  —  %-50s 没有 html（纯 WebView App，注入不在 APK 里 ✅）' % apk)
                continue
            for n in names:
                total += 1
                try:
                    text = zf.read(n).decode('utf-8', 'replace')
                except Exception as e:
                    print('[FAIL] %s 里 %s 读不了 (%s)' % (apk, n, e))
                    return 1
                k = count_residual(text, keep_ball=_is_allowed(n, allow_list))
                if k:
                    dirty.append((apk, n, k))
                    print('  🔴 %-46s %-34s 仍有 %d 处注入' % (apk.split('/')[-1], n, k))
                elif not a.quiet:
                    print('  ✅ %-46s %-34s 干净' % (apk.split('/')[-1], n))
        print('')
        print('[strip] --check-apk：扫描 %d 个 APK / %d 个 html / **%d 个文件有残留**'
              % (len(a.paths), total, len(dirty)))
        if dirty:
            for apk, n, k in dirty:
                print('[FAIL] %s 里的 %s 仍有 %d 处平台注入（不许放行）' % (apk, n, k))
            return 1
        return 0

    # ── --check 模式：只查不剥 ──
    if a.check:
        for f in files:
            try:
                text = open(f, 'rb').read().decode('utf-8')
            except Exception as e:
                problems.append('%s: 读不了 (%s)' % (f, e))
                continue
            n = count_residual(text, keep_ball=_is_allowed(f, allow_list))
            if n:
                dirty.append((f, n))
                print('  🔴 %-58s 仍有 %d 处注入' % (f, n))
            elif not a.quiet:
                print('  ✅ %-58s 干净' % f)
        print('')
        print('[strip] --check：扫描 %d 个 html / **%d 个文件有残留**' % (len(files), len(dirty)))
        if dirty:
            for f, n in dirty:
                print('[FAIL] %s 仍有 %d 处平台注入（剥不掉就 exit 1，不许放行）' % (f, n))
            return 1
        return 0

    for f in files:
        try:
            raw = open(f, 'rb').read()
        except Exception as e:
            problems.append('%s: 读不了 (%s)' % (f, e))
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            problems.append('%s: 不是 utf-8，跳过' % f)
            continue

        keep_ball = _is_allowed(f, allow_list)
        # 🔴🔴 `before` **必须按 keep_ball 算**（pony 2026-10-23 撞出来的边界）：
        #    否则「被 --allow 放行的手写角标」会被算成「待剥的注入」→
        #    但又按 --allow 不剥 → 剥了 0 处 → 触发「报有注入却一处没剥」→ **exit 1 把 build 卡死**。
        before = count_residual(text, keep_ball=keep_ball)
        if before == 0:
            if not a.quiet:
                print('  —  %-58s 干净%s' % (f, '（--allow 放行，保留手写角标）' if keep_ball else ''))
            continue

        new, removed = strip_text(text, keep_ball=keep_ball)
        after = count_residual(new, keep_ball=keep_ball)
        if after != 0:
            problems.append('%s: 剥完**仍有 %d 处**注入（exit 1）' % (f, after))
            continue

        b_bytes, a_bytes = len(raw), len(new.encode('utf-8'))
        changed += 1
        n = sum(k for _nm, k in removed)
        total_removed += n
        detail = ' / '.join('%s×%d' % (nm, k) for nm, k in removed)
        print('  ✅ %-58s 注入 %d 处 → 已剥  %d → %d 字节 (-%d)  [%s]'
              % (f, before, b_bytes, a_bytes, b_bytes - a_bytes, detail))
        if not a.dry_run:
            open(f, 'wb').write(new.encode('utf-8'))

    print('')
    print('[strip] 处理 %d 个 html / 改动 %d 个 / 共剥 %d 处注入'
          % (len(files), changed, total_removed))
    # 🔴🔴 **--allow 规则一条都没匹配上 → 必须显式警告**（pony 2026-10-23 踩过）
    #    「路径后缀没对上 → 静默不生效 → 刻意保留的功能被删掉」——
    #    而原来的报错说的是「判定逻辑有 bug」→ **把人引向查工具，其实该查自己的路径**。
    if allow_list:
        _hit = set()
        for _f in files:
            for _suf in allow_list:
                if _is_allowed(_f, [_suf]):
                    _hit.add(_suf)
        for _suf in allow_list:
            if _suf not in _hit:
                print('[strip] ⚠️  --allow 规则「%s」**一条都没匹配上任何文件** —— '
                      '路径后缀没对上？' % _suf)
                print('         （后果：该文件里的**手写功能会被删掉**）')
    if a.dry_run and changed:
        print('[strip] （--dry-run：未落盘）')

    if problems:
        print('')
        for p in problems:
            print('[FAIL] %s' % p)
        return 1
    # 🔴 「报有注入却一处没剥」这个判定**已删除**（v8.5）：
    #    「剥完仍有残留」（after != 0）上面已经判过并 exit 1 了 ✅
    #    而这条判据在「--allow 放行」的场景下会**误报**：
    #    被放行的手写角标算「待剥的注入」→ 却按 --allow 不剥 → 剥了 0 处 → exit 1 **把 build 卡死**
    #    （pony 2026-10-23 实测撞到：wakakusa 源文件已剥干净、只剩手写角标 → build 直接挂）
    return 0


if __name__ == '__main__':
    sys.exit(main())
