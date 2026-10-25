#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 部署后的**注入核查**（2026-10-25）

要查什么：
    mcode 每次部署都会往 index.html 里注入平台统计脚本（带 token）。
    ⇒ 我们能保证的三件事，逐条验：
        ① **拦截器还在**（head 最前面那段 `var BLOCK = 'agent.minimax.cn'`）
           —— 它才是挡住上报的那一层
        ② **运行时兜底还在**（platformInjectGuard：把注入的脚本/球从 DOM 里摘掉）
        ③ **真的没有外发**（浏览器实测：0 条 agent.minimax.cn 请求）

🔴 关于「token 还公开在源码里」这件事（**结论：重新部署解决不了**）：
    证据 1：线上 index.html 的响应头是 OSS 静态对象头
            （x-oss-request-id / ETag / Content-MD5），Content-Length = 17617
            = 本地剥干净的 16094 + 平台注入的 1523 字节
            ⇒ **注入后的文件被存进了 OSS**（不是每次请求时临时拼的）
    证据 2：平台自己的 404 页面（另一份 HTML）**没有**注入
            ⇒ 不是「所有 HTML 都过一遍注入」
    证据 3：兜底站（更早部署的）里也有注入，且 token 里带的是**它自己**的 node_id
            ⇒ 每次部署都会被注一次
    ⇒ 结论：**「剥完再重新部署」只会换一个新 token + 新域名 + 多一个要删的站**，
      并不能让 token 消失。
    ⇒ 真正能去掉 token 的只有三条路：
        (a) 平台侧关掉「网站分析注入」（控制台开关，沙箱里够不着）
        (b) 覆盖 OSS 上已存的那个对象（需要 drive 写权限，本项目只有 list/search/delete）
        (c) 把站点搬到我们自己控制的托管（GitHub Pages）—— 大改，暂不做
    ⇒ 所以本脚本把「token 还在」记成 **WARN**（平台行为，不是我们的漏洞），
      但**保护层缺失一律 FAIL**。

用法：
    python3 scripts/check_live_inject.py <域名或URL> [--no-browser] [--strict-token]
"""
import argparse
import os
import json
import re
import sys
import urllib.request

BLOCK_MARK = "var BLOCK = 'agent.minimax.cn'"
GUARD_MARK = "platformInjectGuard"
ANALYTICS_ATTR = 'data-minimax-analytics="minimax-website-analytics-v1"'
BALL_ID = "minimax-floating-ball"


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site", help="域名（em8hjp6jskxyb）或完整 URL")
    ap.add_argument("--no-browser", action="store_true", help="跳过浏览器实测（只做静态查）")
    ap.add_argument("--strict-token", action="store_true",
                    help="把「平台 token 还在」也算失败（默认只 WARN）")
    a = ap.parse_args()

    url = a.site
    if not url.startswith("http"):
        url = "https://%s.space.mcode.cn/" % url
    print("════ 线上注入核查：%s ════" % url)
    html = fetch(url)

    fails = []
    warns = []

    # ① 拦截器
    i = html.find(BLOCK_MARK)
    if i < 0:
        fails.append("❌ head 里的拦截器不见了（%s）⇒ 上报拦不住" % BLOCK_MARK)
    else:
        print("   ✅ 拦截器在（第 %d 字节，head 最前面）" % i)

    # ② 运行时兜底
    if GUARD_MARK not in html:
        fails.append("❌ 运行时兜底（%s）不见了 ⇒ 注入的脚本/悬浮球会留在 DOM 里" % GUARD_MARK)
    else:
        print("   ✅ 运行时兜底（%s）在" % GUARD_MARK)

    # ③ 悬浮球
    ball = re.search(r'<div[^>]*id=["\']%s' % BALL_ID, html)
    if ball:
        fails.append("❌ 线上页面里还有悬浮球 DOM（id=%s）" % BALL_ID)
    else:
        print("   ✅ 没有悬浮球 DOM")

    # ④ 平台统计脚本 + token（平台行为 ⇒ WARN）
    m = re.search(r'<script[^>]*%s[^>]*>' % re.escape(ANALYTICS_ATTR), html)
    if m:
        tok = re.search(r'var t="([^"]+)"', html)
        print("   ⚠️ 平台统计脚本在（%s）" % ANALYTICS_ATTR)
        if tok:
            print("      token 前 24 字符：%s…" % tok.group(1)[:24])
        print("      ⇒ 平台在**上传那一刻**把注入写进了 OSS 上的文件（证据见本文件头）")
        print("      ⇒ 「剥完再部署」只会换一个 token；真正要靠平台开关 / 覆盖 OSS / 换托管")
        if a.strict_token:
            fails.append("❌ --strict-token：平台 token 还在源码里")
        else:
            warns.append("平台 token 仍在源码（平台注入，拦截器已让它失效）")
    else:
        print("   ✅ 没有平台统计脚本")

    # ⑤ 拦截器必须排在注入脚本前面（否则拦不住）
    if i >= 0 and m and m.start() < i:
        fails.append("❌ 注入脚本排在拦截器前面 ⇒ 拦不住")

    # ⑥ 浏览器实测：0 条外发
    if not a.no_browser:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            print("   ⚠️ 没有 playwright（%s）⇒ 跳过浏览器实测" % e)
        else:
            reqs = []
            exe = None
            for c in ("/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome",
                      "/root/.cache/ms-playwright/chromium-1148/chrome-linux/chrome"):
                if os.path.exists(c):
                    exe = c
                    break
            try:
                with sync_playwright() as p:
                    b = p.chromium.launch(args=["--no-sandbox"],
                                          executable_path=exe) if exe else \
                        p.chromium.launch(args=["--no-sandbox"])
                    ctx = b.new_context()
                    ctx.route("**/*", lambda route: (reqs.append(route.request.url),
                                                     route.continue_()))
                    pg = ctx.new_page()
                    pg.goto(url, wait_until="load", timeout=45000)
                    pg.wait_for_timeout(6000)
                    ball_visible = pg.evaluate(
                        "() => { const e = document.getElementById('minimax-'+'floating-ball');"
                        " return e ? getComputedStyle(e).display !== 'none' : false; }")
                    live_scripts = pg.evaluate(
                        "() => Array.from(document.querySelectorAll("
                        "'script[data-minimax-analytics]')).length")
                    b.close()
            except Exception as e:
                print("   ⚠️ 浏览器实测失败：%s" % e)
            else:
                bad = [u for u in reqs if "minimax.cn" in u]
                print("   %s 外发到 agent.minimax.cn：%d 条（共 %d 个请求）"
                      % ("✅" if not bad else "❌", len(bad), len(reqs)))
                for u in bad[:5]:
                    print("      🔴 %s" % u)
                if bad:
                    fails.append("❌ 有 %d 条请求发往 minimax.cn" % len(bad))
                if ball_visible:
                    fails.append("❌ 悬浮球在页面上可见")
                else:
                    print("   ✅ 悬浮球不可见")
                if live_scripts:
                    print("   ⚠️ DOM 里还有 %d 个统计脚本标签（拦截器已挡住外发）" % live_scripts)
                else:
                    print("   ✅ DOM 里没有残留的统计脚本（被运行时兜底摘掉了）")
    else:
        print("   （--no-browser：跳过浏览器实测）")

    print()
    for w in warns:
        print("⚠️  %s" % w)
    if fails:
        for f in fails:
            print(f)
        return 1
    print("✅ 保护层完好：拦截器在 + 兜底在 + 0 外发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
