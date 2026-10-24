#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**跑批输出的「异常即失败」检查器**（pony 2026-10-23 提，我实现）

🔴🔴 起因（pony 亲历）：他写 `test_site_gone.js` 时用了不存在的 `ec.collect()`
（真实 API 是 `ec.attach(page, fatal, external)`）⇒
**Node 直接抛异常 → exit 1** ⇒ **而那个 exit 1 和「断言失败」的 exit 1 长得一模一样** ❌
⇒ **他差点把「脚本崩了」当成「测试抓到了 bug」** 🔴

🔴 **现有防护只防一边**：
| 跑批器已有的 | 防得住 | 防不住 |
|---|---|---|
| 「exit 0 但零断言输出 = 判失败」 | **exit 0 的假绿** | **exit 1 的假红** |
| **本脚本** | **exit 1 的假红** | — |

🔴 **通则**：
> **「exit=1」有两种含义：判据拦截 / 脚本崩溃。**
> **⇒ 任何只看退出码的测试，都必须同时检查「输出里有没有 Traceback / Exception」。**
> **⇒ 「exit 1」只能证明「结果是拒绝」，不能证明「是判据拦的」** ⇒ **要断言「拦的理由」**

用法（任何跑批器加一行）：
```bash
python3 scripts/strict_test_exit.py <输出文件>   # 有异常 ⇒ exit 1
```
**⇒ 也可以在跑批器里管道接**：
```bash
node test_x.js 2>&1 | tee /tmp/out.txt
python3 scripts/strict_test_exit.py /tmp/out.txt || exit 1
```
"""
import re
import sys

# 🔴 **只认「真异常」的标志**，不认普通单词
#    （「Error:」可能出现在正常输出里 ⇒ 要更谨慎）
PATTERNS = [
    (re.compile(r'^\s*Traceback \(most recent call last\)', re.M), 'Python Traceback'),
    (re.compile(r'^\s*File "[^"]+", line \d+', re.M), 'Python 栈帧'),
    (re.compile(r'^\s*(?:[A-Za-z_][\w.]*)?(?:Error|Exception)\b', re.M), 'JS/Python 异常行'),
    (re.compile(r'^\s*at [\w.$<>]+ \(', re.M), 'JS 栈帧'),
    (re.compile(r'^\s*node:internal/', re.M), 'Node 内部错误'),
    (re.compile(r'^\s*\*\*\* ', re.M), 'pytest 失败摘要'),
    (re.compile(r'\bAssertionError\b', re.M), 'AssertionError'),
    (re.compile(r'\bSyntaxError\b', re.M), 'SyntaxError'),
    (re.compile(r'\bReferenceError\b', re.M), 'ReferenceError'),
    (re.compile(r'\bNameError\b', re.M), 'NameError'),
    (re.compile(r'\bTypeError\b', re.M), 'TypeError'),
    (re.compile(r'\bModuleNotFoundError\b', re.M), 'ModuleNotFoundError'),
]

# 🔴 **豁免行**：这些是「测试通过时也会出现的正常输出」
ALLOW = [
    re.compile(r'^\s*(?:[✅✓]\s*)?(?:PASS|OK)\b', re.I),
    re.compile(r'^\s*\d+ passed', re.I),
    re.compile(r'^\s*✅.*(?:通过|齐|正常|OK)', re.I),
    re.compile(r'^\s*\[.*\]\s*(?:OK|PASS)', re.I),
]


def scan(text):
    hits = []
    for line in text.splitlines():
        if any(a.search(line) for a in ALLOW):
            continue
        for pat, name in PATTERNS:
            if pat.search(line):
                hits.append((name, line.strip()[:120]))
                break
    return hits


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    p = sys.argv[1]
    try:
        with open(p, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception as e:
        print('  🔴 读不到输出文件：%s' % e)
        return 2
    hits = scan(text)
    if hits:
        print('  🔴 输出里有 %d 处异常痕迹 ⇒ **判失败**（不能当成「判据拦的」）' % len(hits))
        for name, line in hits[:8]:
            print('     · [%s] %s' % (name, line))
        print('  ⇒ 这些行说明脚本**崩了**，不是测试**抓到了问题** ⇒ 退出码 1 是假的。')
        return 1
    print('  ✅ 输出里没有异常痕迹（exit 码可信）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
