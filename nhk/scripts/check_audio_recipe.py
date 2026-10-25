#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_audio_recipe.py —— 守「音频配方只写一遍」

🔴 **它防的那个事故**（2026-10-23 真发生过）：
主管线从 64k 改成 96k（64k 是 AAC 削波真凶），**同一天的旁路还写着 `-b:a 64k`**
⇒ **每日 21:00 的自动更新会自己把削波音频推上线**。

⇒ 两条判据：
```
① **登记在册的交付编码入口里不允许出现字面量码率**（"96k" / "64k" / "128k" …）
   ⇒ 必须从 audio_recipe 取
② **每个登记入口都必须 import audio_recipe**
   （否则配方会从第二个地方长出来）
③ **未登记但出现「交付 codec + 硬编码码率」的文件 ⇒ 报出来**
   （新增入口忘了登记，也要被抓到）
```

🔴🔴 **写这条守卫时踩的五个坑（都是别人教 + 自己踩的）**：
① **正则/判据必须覆盖「真实代码里出现的写法」** ——
   我第一版用 AST 逐个找「含 ffmpeg 的列表字面量」⇒ 我的代码是 `cmd = [...]` 之后 `cmd += [...]`
   ⇒ 编码参数不在同一个字面量里 ⇒ **一个入口都没找到** ⇒ **守卫自己变成假绿**
   ⇒ 改成「从源码文本里抽 **-b:a 与它的值 的关联**」（两种写法都认）+ AST 判「是不是交付 codec」。
② **守卫不能越权** ——
   `audio_health.py` 里的 "32k"/"64k" 是**自检对照组故意造的坏样本**，不是配方副本
   ⇒ 登记豁免 + **写清为什么**（静默放行 = 又一个看不见的地方）。
③ **守卫不能把自己算进去**（权威本身就是配方）⇒ 排除自己（用 realpath）。
④ **守卫不能是恒绿** —— 找不到入口 / 登记名单为空 ⇒ 判据坏了 ⇒ **必须 exit 1**。
⑤ **只靠退出码不够** —— 「exit 1」可能是判据拦的，也可能是脚本崩的
   ⇒ 输出里必须能看出**拦的理由**。

**⇒ 用法**：`python3 scripts/check_audio_recipe.py`（exit 0 = 合格；1 = 有副本）
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

# 🔴 **登记在册的交付音频编码入口**（新增入口时必须同时登记到这里 + 改成从 audio_recipe 取值）
DELIVERY_ENTRIES = [
    "retranscode.py",                 # 全量重编（96k + 原生峰值守卫 + 时长闸门）
    "scripts/repair_audio.py",        # 坏帧修复后的落盘编码
    "scripts/loudnorm_fix.py",        # 原速音响度对齐后的重编
    "scripts/refetch_audio.py",       # 重抓后的体检 + 重编
]

# 🔴 **豁免**（必须写清为什么，否则就是看不见的放行）
EXEMPT = {
    "scripts/audio_recipe.py": "配方本体（权威）",
    "scripts/check_audio_recipe.py": "守卫自己",
    "scripts/audio_health.py": "自检对照组故意用 32k/64k 造坏样本（不是交付配方）",
}

DELIVERY_CODECS = {"aac", "libmp3lame", "mp3", "libfdk_aac"}
BITRATE_LITERAL = re.compile(r"^\d+(\.\d+)?k$")

# 🔴 **判据必须覆盖真实写法**：`["-b:a", "96k"]`（列表）与 `-b:a 96k`（shell）都认
PAT_BITRATE_LIT = re.compile(r"""["']-b:a["']\s*,\s*["']([^"']+)["']""")
PAT_BITRATE_SHELL = re.compile(r"""-b:a\s+["']?([\w.]+)["']?""")


def _docstring_nodes(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value == d:
                        out.add(id(sub))
    return out


def _string_literals(tree):
    docs = _docstring_nodes(tree)
    vals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            vals.append(node.value)
    return vals


def scan(path):
    """→ (是交付编码入口吗, 硬编码码率列表, 是否 import audio_recipe)"""
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return False, ["语法错误:%s" % e], False
    lits = _string_literals(tree)
    imports_recipe = ("import audio_recipe" in src) or ("from audio_recipe" in src)
    # 🔴 「是交付编码入口」两种认法：有交付 codec 字面量 **或** 已经引用了配方
    #    （改完配方收敛后，入口里就没有 codec 字面量了 ⇒ 只认字面量会误判「名单过期」）
    is_delivery = any(v in DELIVERY_CODECS for v in lits) or imports_recipe
    hard = []
    if is_delivery:
        for pat in (PAT_BITRATE_LIT, PAT_BITRATE_SHELL):
            for v in pat.findall(src):
                if BITRATE_LITERAL.match(v):
                    hard.append(v)
    return is_delivery, sorted(set(hard)), imports_recipe


def all_py_files():
    out = []
    for d in (ROOT, SCRIPTS):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py"):
                p = os.path.join(d, fn)
                if os.path.realpath(p) == os.path.realpath(__file__):
                    continue
                out.append(p)
    return out


def main():
    problems = []
    found_entries = []

    print("== ① 登记在册的交付编码入口 ==")
    for rel in DELIVERY_ENTRIES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            problems.append("❌ 登记入口 %s 不存在（名单与现实脱节 ⇒ 守卫失真）" % rel)
            continue
        found_entries.append(rel)
        is_delivery, hard, imports_recipe = scan(p)
        if not is_delivery:
            problems.append("❌ %s：登记为交付编码入口，但源码里找不到交付 codec ⇒ 名单过期了" % rel)
        if hard:
            problems.append("❌ %s：硬编码了码率 %s ⇒ 必须从 audio_recipe 取"
                            % (rel, "/".join(hard)))
        if not imports_recipe:
            problems.append("❌ %s：没有 import audio_recipe ⇒ 配方会从这里长出第二份" % rel)
        print("   %-32s 硬编码码率=%s  用配方=%s" % (rel, hard or "无", "是" if imports_recipe else "❌否"))
    print("   共 %d 个" % len(found_entries))

    # 🔴 恒绿检测：登记名单为空/太少 ⇒ 判据坏了，不是「没问题」
    if len(found_entries) < 2:
        print("❌ 只认出 %d 个交付编码入口 ⇒ 判据坏了（至少应有 2 个）⇒ 不能放行" % len(found_entries))
        return 1

    print()
    print("== ② 未登记但出现「交付 codec + 硬编码码率」的文件 ==")
    hit = 0
    for p in all_py_files():
        rel = os.path.relpath(p, ROOT)
        if rel in DELIVERY_ENTRIES or rel in EXEMPT:
            continue
        is_delivery, hard, _ = scan(p)
        if is_delivery and hard:
            hit += 1
            problems.append("❌ %s：有交付 codec 且硬编码码率 %s，但不在登记名单里 ⇒ "
                            "要么登记成入口改成用配方，要么把码率挪走" % (rel, "/".join(hard)))
    if hit == 0:
        print("   （无）")
    print()
    print("== ③ 豁免清单（每条都写清为什么）==")
    for rel, why in EXEMPT.items():
        print("   %-34s %s" % (rel, why))

    if problems:
        print()
        for x in problems:
            print(x)
        return 1
    print("\n✅ 配方只写一遍：所有交付编码入口都从 audio_recipe 取值")
    return 0


if __name__ == "__main__":
    sys.exit(main())
