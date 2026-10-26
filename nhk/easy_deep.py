#!/usr/bin/env python3
"""深入 NHK やさしいことばニュース：拿文章 id、原文、音频 URL。"""
import asyncio, json, re, sys, os
sys.path.insert(0, "/workspace/nhk")
import auth
from playwright.async_api import async_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
if not os.path.exists(CHROME):
    CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"


async def main():
    t = auth.get_token()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", ignore_https_errors=True,
                                  user_agent=auth.UA)
        await ctx.add_cookies([
            {"name": "z_at", "value": t["token"], "domain": ".web.nhk", "path": "/", "secure": True, "httpOnly": True},
            {"name": "authz_type", "value": "a-alaz", "domain": ".web.nhk", "path": "/", "secure": True, "httpOnly": False},
        ])
        page = await ctx.new_page()
        api = []

        def rq(r):
            if re.search(r"api\.web\.nhk|/r8/|m3u8|\.mp4|\.mp3|\.m4a|media\.|\.st\.nhk", r.url, re.I):
                api.append(("REQ", r.method, r.url))
        async def rs(r):
            if re.search(r"api\.web\.nhk|/r8/|m3u8|\.mp4|\.mp3|\.m4a|media\.", r.url, re.I):
                try: body = (await r.text())[:5000]
                except Exception: body = "<binary>"
                api.append(("RESP", r.status, r.url, body))
        page.on("request", rq); page.on("response", rs)

        await page.goto("https://news.web.nhk/news/easy/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        links = await page.evaluate("""()=>Array.from(document.querySelectorAll('a'))
            .map(a=>({t:(a.innerText||'').trim().replace(/\\s+/g,' ').slice(0,70), h:a.getAttribute('href')}))
            .filter(x=>x.h && /easy|na\\/|article|detail/i.test(x.h)).slice(0,30)""")
        print("### 文章链接 ###")
        for l in links:
            print("  ", l["h"], "|", l["t"])

        target = None
        for l in links:
            if l["h"] and l["h"] != "https://news.web.nhk/news/easy/":
                target = l["h"]
                break
        if target:
            url = target if target.startswith("http") else "https://news.web.nhk" + target
            print("\n### 打开文章:", url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(10000)
                txt = await page.evaluate("()=>document.body.innerText")
                print("---- 正文 ----")
                print(txt[:4000])
                st = await page.evaluate("""()=>Array.from(document.querySelectorAll('video,audio,source')).map(v=>({tag:v.tagName,src:(v.currentSrc||v.src||'').slice(0,250)}))""")
                print("---- media ----", json.dumps(st)[:800])
            except Exception as e:
                print("  err", e)

        print("\n\n########## API / MEDIA ##########")
        for a in api:
            print(a[0], a[1], a[2][:260])
            if len(a) > 3:
                print("     ", str(a[3])[:1400])
        await b.close()

asyncio.run(main())
