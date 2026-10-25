#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 GitHub Pages 永久网页版的**断网验收**（2026-10-25）

为什么要有这个测试：
    Pages 版的卖点是「**断网也能看字幕、点句开详解面板、点词查释义**」
    ⇒ 这个能力**只由 Service Worker 的预热决定**（一次预热 44 集字幕 + 9 个词库）
    ⇒ 而预热清单在 `sw.js` 里 ⇒ **sw.js 一旦被旧副本覆盖，能力就没了，而且不报错**
    ⇒ 所以必须**实测断网**，不能只读代码

跑法：
    python3 scripts/test_pages_offline.py [URL]
默认 URL = https://tlv-creator.github.io/nhk-easy-news/nhk-web/
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else \
    "https://tlv-creator.github.io/nhk-easy-news/nhk-web/"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + "  " + name + (("  " + str(extra)[:120]) if extra else ""))
    if not cond:
        fails.append(name)


def main():
    print("════ Pages 永久网页版 · 断网验收 ════")
    print("   %s" % URL)
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = br.new_context(viewport={"width": 412, "height": 900})
        pg = ctx.new_page()
        pg.goto(URL, wait_until="load", timeout=60000)
        pg.wait_for_timeout(3000)

        # ① Service Worker 有没有接管
        sw = pg.evaluate("""async () => {
            const r = await navigator.serviceWorker.getRegistration();
            return r ? (r.active ? 'active' : (r.installing ? 'installing' : 'waiting')) : 'none';
        }""")
        ck("Service Worker 已注册", sw in ("active", "installing", "waiting"), sw)

        # ② 预热：等缓存里出现字幕/词库
        print("   …等预热（4.6MB，限并发 3，可能要几十秒）")
        n_subs = 0
        n_data = 0
        for i in range(24):
            info = pg.evaluate("""async () => {
                const keys = await caches.keys();
                let s = 0, d = 0;
                for (const k of keys) {
                    const c = await caches.open(k);
                    const reqs = await c.keys();
                    for (const r of reqs) {
                        const u = new URL(r.url).pathname;
                        if (u.indexOf('/subs/') >= 0) s++;
                        if (u.indexOf('/data/') >= 0) d++;
                    }
                }
                return {s, d, keys};
            }""")
            n_subs, n_data = info["s"], info["d"]
            print("      第 %d 次：缓存桶 %s · 字幕 %d · 词库/数据 %d"
                  % (i + 1, info["keys"], n_subs, n_data))
            if n_subs >= 40 and n_data >= 8:
                break
            time.sleep(5)
        ck("字幕已预热（≥40 集）", n_subs >= 40, n_subs)
        ck("词库已预热（≥8 个）", n_data >= 8, n_data)

        # ③ 断网重开
        ctx.set_offline(True)
        pg.goto(URL, wait_until="load", timeout=60000)
        pg.wait_for_timeout(2500)
        rows = pg.locator(".ep-row, .eps-row, [data-id]").count()
        if rows == 0:
            rows = pg.locator("text=台風").count()
        ck("断网后仍能出首页/列表", rows > 0, "rows=%s" % rows)

        # ④ 进一期 → 字幕渲染（选择器与 test_local.py 保持一致：.ep-item / .cue）
        clicked = False
        for sel in (".ep-item", "[data-id]", "text=台風"):
            loc = pg.locator(sel).first
            if loc.count() > 0:
                try:
                    loc.click(timeout=5000)
                    clicked = True
                    break
                except Exception:
                    pass
        ck("断网后能进一期", clicked)
        pg.wait_for_timeout(2500)
        cues = pg.locator(".cue").count()
        ck("断网后字幕渲染出来", cues > 0, "cues=%s" % cues)

        # ⑤ 点句 → 详解面板 → 点词 → 释义
        #    （用 JS click：真实鼠标点击会打在句子里的子元素上 ⇒ 面板不开）
        n = pg.locator(".cue").count()
        if n:
            pg.evaluate("(i)=>{document.querySelectorAll('.cue')[i].click();}", 0)
            pg.wait_for_timeout(1500)
        panel_ok = pg.locator("#pop").count() > 0 and pg.locator("#pop").is_visible()
        ck("断网后点句能开详解面板", panel_ok)
        if panel_ok:
            body = pg.locator("#popBody").inner_text()
            ck("面板有逐词拆解（词库预热生效）", "逐词拆解" in body or "大框架" in body,
               repr(body[:40]))
            ck("面板有中文", pg.locator("#popBody .sp-zh").count() > 0)
            w = pg.locator("#popBody .sp-w").first
            if w.count() > 0:
                pg.evaluate("()=>{document.querySelector('#popBody .sp-w').click();}")
                pg.wait_for_timeout(900)
                txt = pg.locator("#wordDetail").first.inner_text()
                ck("断网后点词有释义", len(txt.strip()) > 3, repr(txt[:40]))
            else:
                ck("断网后点词有释义", False, "面板里没有 .sp-w")
        ctx.set_offline(False)
        br.close()
    print("\n== 失败 %d 项 ==" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
