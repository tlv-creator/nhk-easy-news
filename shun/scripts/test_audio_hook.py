#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钩子级单测：在空白页里直接验「覆写 window.Audio」的行为。

🔴 为什么单独测钩子（而不是只靠某个站点的端到端）：
   钩子有两个调用形态，**两种都必须对**：
     ① `new Audio()`      —— 50 集站（无参，之后再设 .src）
     ② `new Audio(url)`   —— 309 集站（带 URL）⇒ 🔴 旧钩子会把 URL 吞掉 ⇒ App 完全没声音
   ⇒ 判据落在「实例的 .src 是不是那个 URL」，不落在 UI 上。
"""
import re, sys
from playwright.sync_api import sync_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
TPL = "/workspace/shun/apk-template-%s/java/com/shun/study/MainActivity.java"


def extract(src_path, name):
    lines = open(src_path, encoding="utf-8").read().split("\n")
    out, collecting = [], False
    for ln in lines:
        if not collecting:
            if re.search(r'String\s+' + name + r'\s*=', ln):
                collecting = True
            else:
                continue
        s = ln.strip()
        if s.startswith("//"):
            continue
        out.append(ln)
        if s.endswith('";'):
            break
    blob = "\n".join(out)
    m = re.search(r'=\s*((?:"(?:[^"\\]|\\.)*"\s*\+\s*)*"(?:[^"\\]|\\.)*")\s*;', blob, re.S)
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return "".join(parts).replace('\\"', '"').replace("\\\\", "\\")


fails = []
def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fails.append(msg)

URL = "https://audio4.redcircle.com/episodes/aaa-bbb/stream.mp3"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    for tpl in ("ru", "net"):
        hook = extract(TPL % tpl, "JS_HOOK_AUDIO")
        pg = b.new_page()
        pg.goto("about:blank")
        ret = pg.evaluate(hook)
        print(f"\n=== 模板 apk-template-{tpl} ===")
        check(ret == "ok", "钩子注入成功")

        # ① 无参（50 集站形态）
        r1 = pg.evaluate("""()=>{
            var a = new Audio();
            return {src: a.src, has: !!window.__shunAu, same: window.__shunAu===a};
        }""")
        check(r1["has"] and r1["same"], "无参 new Audio()：实例被记到 __shunAu")
        check(r1["src"] == "", "无参 new Audio()：src 为空（与旧版行为一致 ⇒ 不回归）")

        # ② 带参（309 集站形态）—— 🔴 这条是本次修的 bug
        r2 = pg.evaluate("""(u)=>{
            var a = new Audio(u);
            return {src: a.src, has: !!window.__shunAu, same: window.__shunAu===a};
        }""", URL)
        check(r2["has"] and r2["same"], "带参 new Audio(url)：实例被记到 __shunAu")
        check(r2["src"] == URL, f"带参 new Audio(url)：**URL 没被吞掉**（src={r2['src'][:60]}）")

        # ③ 原生能力没坏：仍是 Audio 实例，play/pause 存在
        r3 = pg.evaluate("""()=>{
            var a = window.__shunAu;
            return {isAudio: (a instanceof Audio), play: typeof a.play, pause: typeof a.pause};
        }""")
        check(r3["isAudio"], "实例仍然是原生 Audio（instanceof 没坏）")
        check(r3["play"] == "function" and r3["pause"] == "function", "play/pause 方法都在")
        pg.close()
    b.close()

print()
if fails:
    print(f"❌ 失败 {len(fails)} 项：")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ 钩子级验证全部通过（两个模板都验了）")
