#!/usr/bin/env python3
"""交付前自检：真浏览器打开两个站，检查渲染 + 四类括号 + 无 pageerror。"""
import asyncio, os, sys
from playwright.async_api import async_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
OUT = "/workspace/shun-web/shots"
os.makedirs(OUT, exist_ok=True)

PAGES = [
    ("shun-web", "https://tlv-creator.github.io/nhk-easy-news/shun-web/", True),
    ("shun-text", "https://tlv-creator.github.io/nhk-easy-news/shun-text.html", False),
]


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME,
                                    args=["--ignore-certificate-errors", "--no-sandbox"])
        for name, url, is_web in PAGES:
            ctx = await b.new_context(viewport={"width": 412, "height": 900},
                                      device_scale_factor=2)
            pg = await ctx.new_page()
            errs, reqfail = [], []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("requestfailed", lambda r: reqfail.append(r.url[:90]))
            try:
                await pg.goto(url, wait_until="networkidle", timeout=60000)
            except Exception as e:
                print("  [%s] 加载异常: %s" % (name, e)); await ctx.close(); continue
            await pg.wait_for_timeout(2500)
            title = await pg.title()
            body = (await pg.inner_text("body"))[:120].replace("\n", " / ")
            nchar = len(await pg.inner_text("body"))
            await pg.screenshot(path=os.path.join(OUT, name + ".png"), full_page=False)
            # 四类括号是否出现
            html = await pg.content()
            marks = {k: html.count(k) for k in ("［", "］", "〈", "〉", "（", "）", "[", "]")}
            print("  [%s] %s" % (name, url))
            print("      title: %s" % title[:60])
            print("      可见文本 %d 字 | 首段: %s" % (nchar, body[:80]))
            print("      括号计数: " + " ".join("%s=%d" % (k, v) for k, v in marks.items() if v))
            print("      pageerror: %d | 请求失败: %d" % (len(errs), len(reqfail)))
            for e in errs[:3]:
                print("        ! %s" % e[:110])
            for r in reqfail[:3]:
                print("        x %s" % r)
            await ctx.close()
        await b.close()
    print("\n截图已存 %s" % OUT)


asyncio.run(main())
