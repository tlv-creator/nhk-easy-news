#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端代码完整性检查（2026-10-22）

## 为什么要这个
🔴 2026-10-22 栽了一次：用 `re.search(r"var AI_SYSTEM_PROMPT\\s*=\\s*(.*?);\\n", src, re.S)`
去定位并替换 prompt 块 —— **`(?s)` + 非贪婪 `.*?` 匹配到了更远的 `;\\n`**，
结果把**中间整段代码（两条模型输出清洗函数）吃掉了**，
而且 `node --check` 仍然通过（语法合法，只是功能没了）→ 一直到浏览器报
`cleanBadParen is not defined` 才发现。

⇒ **教训：改前端时不要用跨行正则去定位大块代码。**
- 用**唯一锚点字符串**（`"  function aiStruct(text) {"` 这种）
- 替换后**立刻 grep 校验关键函数还在**
- 本脚本就是那道防线。

## 检查项
1. 关键函数/常量在位（清单在下面）
2. app.js 体积不低于下限（防止大段被吃）
3. 没有重复定义（同名函数出现两次 → 覆盖风险）
4. AI 关键契约在位（超时 75 秒 / prompt / 清洗 / 树状 / 额度）

用法： python3 test_integrity.py
"""
import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "app", "app.js")
CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "app", "style.css")

fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + "  " + name +
          (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


src = open(APP, encoding="utf-8").read()
css = open(CSS, encoding="utf-8").read()

# ── 1) 关键函数在位 ──
MUST_FUNCS = [
    "cleanOrphan", "cleanBadParen",        # 模型输出清洗（2026-10-22 加，曾被误删）
    "aiStruct",                            # 句子结构剖析渲染（2026-10-22 新答题模式）
    "aiMd", "aiSplit", "aiRender", "aiFixReading",
    "aiShowQuota", "aiQuotaText",
    "updateTime", "isEinkUI", "setTextIfChanged",
    "watchNativeAudio", "fallbackToWeb", "claimAudio",
    "syncCue", "openStudy", "renderStudyPop",
    "readOf", "jaRich", "absUrl",
]
for fn in MUST_FUNCS:
    ck("函数在位：%s" % fn, len(re.findall(r"function %s\b" % re.escape(fn), src)) >= 1)

# ── 2) 关键契约常量在位 ──
ck("AI 超时是 75000ms", "AI_TIMEOUT = 75000" in src)
ck("AI_SYSTEM_PROMPT 存在", "var AI_SYSTEM_PROMPT" in src)
ck("AI 段名表含「句子结构剖析」", "'句子结构剖析'" in src)
ck("实际在播字段（actualPlaying）", "actualPlaying" in src)
ck("树状缩进类名", "ai-tree" in src and "ai-tree" in css)
ck("额度小字元素", "aiQuota" in src)
ck("墨水屏高对比底色仍是黄底黑字（用户明确要求）",
   "html.hc .cue.on" in css and "#ffe9a8" in css)

# ── 3) 体积下限（防止大段被吃）──
# ⚠️ 必须用**字节数**（中文 UTF-8 占 3 字节），别用字符数比阈值
ck("app.js 体积 > 140KB", len(src.encode("utf-8")) > 140 * 1024,
   "%.1f KB" % (len(src.encode("utf-8")) / 1024))
ck("style.css 体积 > 55KB", len(css.encode("utf-8")) > 55 * 1024,
   "%.1f KB" % (len(css.encode("utf-8")) / 1024))

# ── 4) 没有重复定义（同名函数两次 → 后者覆盖前者）──
names = re.findall(r"^  function ([A-Za-z_0-9]+)\s*\(", src, re.M)
dup = sorted({n for n in names if names.count(n) > 1})
ck("没有重复定义的函数", not dup, dup)

# ── 5) prompt 逐字长度（改 prompt 时这里会红，提醒同步中转）──
m = re.search(r"var AI_SYSTEM_PROMPT\s*=\s*(.*?);\n", src, re.S)
if m:
    parts = re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))
    fe = "".join(parts).replace("\\n", "\n").replace("\\'", "'").strip()
    ck("前端 prompt 长度 ≥ 900 字符（新答题模式约 1036）", len(fe) >= 900, len(fe))
    ck("prompt 含「整体大框架」要求", "整体大框架" in fe)
    ck("prompt 含「内部骨架」要求", "内部骨架" in fe)
else:
    ck("能定位到 AI_SYSTEM_PROMPT", False)

print("\n== 失败 %d 项 ==" % len(fails))
sys.exit(1 if fails else 0)
