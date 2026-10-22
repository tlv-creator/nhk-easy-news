#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**音频失败处理体检器** —— 三个问题，缺一个就有洞

🔴🔴 起因（Shun pony 2026-10-23 逐项读线上代码的发现）：
| 项目 | error 监听 | 重试 | 看门狗 |
|---|---|---|---|
| ama  | ❌ 没有 | — | — |  ⇒ **完全静默**（音频挂了什么都不说）|
| dora | ❌ 没有 | — | — |  ⇒ **完全静默** |
| pony | ✅ 有   | ❌ | ❌ |  ⇒ 第一次 503 就报「没声音」= **假故障** |
| wakakusa | ✅ | ✅ | ❌ |  ⇒ error 只触发一次时**两头都坏** |
| sakura  | ✅ | ✅ | ❌ |  ⇒ 同上（它靠「换候选节点」硬扛）|

⇒ **三问**：
  ① 有没有 **error 监听**（没有 = **完全静默**）
  ② 失败后**会不会重试**（不会 = **假故障报给用户**）
  ③ 有没有**看门狗**（没有 = error 只触发一次时**两头都坏**）

🔴🔴 **为什么必须有看门狗**（实测）：
`<audio>` 的 error 事件**连续失败时只触发 1 次**（第 2、3 次 503 不再发 error）
⇒ 只靠 error 事件做重试循环 ⇒ **只救得回一次，而且永远不报错**（静默失败）
⇒ 必须加 watchdog（3~4 秒内 `readyState` 没到位 ⇒ 判定失败）

🔴 **本工具是「启发式粗筛」，不是证明** ——
它读**源码/线上代码里的特征**，不能证明运行时行为。
**⇒ 真证明 = 运行时测试**（`test_audio_retry.py` / Shun 的 `test_audio_retry.js`：
A 第一次 503 能救回来 / B 一直 503 会如实报错 / C 服务端挂起（连 error 都不触发）看门狗兜住）
**⇒ 两个都跑才算数。**

用法：
    python3 scripts/check_audio_robustness.py --root https://<hub站根>
    python3 scripts/check_audio_robustness.py --root /path/to/web --verbose
"""
import argparse
import os
import re
import sys
import urllib.request
import urllib.parse

# ① error 监听
RE_ERR = [
    re.compile(r"addEventListener\(\s*['\"]error['\"]"),
    re.compile(r"\.on\(\s*['\"]error['\"]"),
    re.compile(r"onerror\s*="),
]
# ② 重试
RE_RETRY = [
    re.compile(r"retry", re.I),
    re.compile(r"重试"),
    re.compile(r"\btries\b", re.I),
    re.compile(r"\battempt", re.I),
]
# ③ 看门狗 —— 🔴🔴🔴 **必须分「网页腿」和「原生腿」**（hub pony 2026-10-23 抓出我的假阴性）
#
#   **ama 的看门狗有两条腿**：
#     · **原生腿**：`playerState()` 轮询 + 8 秒超时 → fallback 到网页 `<audio>`
#     · **网页腿**：`<audio>` 的 `readyState` + 定时器
#   ⇒ 我第一版只认「readyState + 定时器」⇒ **ama 被判成「无看门狗」= 假阴性** ❌
#   ⇒ **一个概念有几条实现路径，判据就得有几条**（同「同一个概念两个层判据不一致」那一族）✅
#
#   🔴 **第一版判据太松**（另一个方向）：只找 `readyState` ⇒ **`document.readyState === 'loading'`
#      也会命中**（每个项目都有这句！）⇒ 5 个项目全被判「有看门狗」= **假绿灯** ❌
#
#   ⇒ 现在的判据：
#     **网页腿** = 非 `document.` 的 `readyState` + 同窗口有定时器
#     **原生腿** = `playerState` + 同窗口有定时器（轮询/超时分支）
#     **任一成立即算「有看门狗」** ⇒ 但**必须分别报出来**，因为缺哪条腿就是哪个洞
RE_WATCH_WEB = re.compile(r"(?<!document)\.readyState")
RE_WATCH_NATIVE = re.compile(r"playerState")
RE_TIMER = re.compile(r"setTimeout|setInterval")
WATCH_WINDOW = 250

TARGETS = [
    ('ama',      ['ama/app.js', 'ama/index.html']),
    ('dora',     ['dora/app.js', 'dora/index.html']),
    ('pony',     ['pony/app.js', 'pony/index.html']),
    ('sakura',   ['sakura/app.js', 'sakura/index.html']),
    ('wakakusa', ['wakakusa/app.js', 'wakakusa/index.html']),
    ('tadoku',   ['tadoku/app.js', 'tadoku/index.html']),
]


def fetch(root, path):
    if root.startswith('http'):
        url = root.rstrip('/') + '/' + path.lstrip('/')
        req = urllib.request.Request(url, headers={'User-Agent': 'audio-robustness-check'})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode('utf-8', 'replace')
    p = os.path.join(root, path.lstrip('/'))
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8', errors='replace') as f:
        return f.read()


def _near_timer(text, m):
    a = max(0, m.start() - WATCH_WINDOW)
    b = min(len(text), m.end() + WATCH_WINDOW)
    return bool(RE_TIMER.search(text[a:b]))


def watchdog_legs(text):
    """返回 (网页腿, 原生腿) 两个 bool —— 🔴 必须分开报，缺哪条腿就是哪个洞"""
    web = any(_near_timer(text, m) for m in RE_WATCH_WEB.finditer(text))
    native = any(_near_timer(text, m) for m in RE_WATCH_NATIVE.finditer(text))
    return web, native


def check_text(text):
    web, native = watchdog_legs(text)
    return (any(r.search(text) for r in RE_ERR),
            any(r.search(text) for r in RE_RETRY),
            web or native, web, native)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True, help='站根 URL 或本地 web 目录')
    ap.add_argument('--verbose', action='store_true', help='打印命中的具体片段')
    args = ap.parse_args()

    print('=== 音频失败处理体检（%s）===' % args.root)
    print('判据：① error 监听 ② 重试 ③ 看门狗（readyState）')
    print('🔴 这是**启发式粗筛**，不是证明 —— 运行时测试（test_audio_retry）才算数\n')

    rows, missing = [], []
    for name, paths in TARGETS:
        blob, got = '', False
        for p in paths:
            try:
                t = fetch(args.root, p)
            except Exception as e:
                if args.verbose:
                    print('   (%s: %s 取不到: %s)' % (name, p, e))
                t = None
            if t:
                blob += '\n' + t
                got = True
        if not got:
            print('  ⚠️  %-9s 取不到源码（跳过）' % name)
            continue
        e, r, w, leg_web, leg_native = check_text(blob)
        rows.append((name, e, r, w))
        lack = []
        if not e: lack.append('①error监听')
        if not r: lack.append('②重试')
        if not w: lack.append('③看门狗')
        if lack:
            missing.append((name, lack))
        mark = lambda b: '✅' if b else '❌'
        # 🔴 **网页腿 / 原生腿 分开标** —— 「有原生腿但没网页腿」= 用户在网页/App 里听到的是网页腿
        legs = '腿:网页%s/原生%s' % (mark(leg_web), mark(leg_native))
        print('  %-9s ①%s ②%s ③%s  %-18s %s' % (
            name, mark(e), mark(r), mark(w), legs,
            ('🔴 缺 ' + ' '.join(lack)) if lack else '✅ 三问齐'))

    print()
    if missing:
        print('=== 结论：%d 个项目有洞 ===' % len(missing))
        for n, l in missing:
            print('  🔴 %-9s 缺 %s' % (n, ' '.join(l)))
        print('\n  ⇒ 优先修「完全静默」（① 都没有）—— 那是用户最无感的失败。')
        print('  ⇒ 修法：① 加 error 监听 ② 失败后重试（最多 2 次，第 2 次带 cache-buster）')
        print('     ③ 加看门狗（3~4 秒内 readyState < 1 ⇒ 判定失败）')
        print('  ⇒ 参考实现：restore-tadoku/tadoku/app.js + test_audio_retry.py（4/4 + 反向验证成立）')
        return 1
    print('=== 结论：全部项目三问齐 ✅（但仍建议跑一次运行时测试）===')
    return 0


if __name__ == '__main__':
    sys.exit(main())
