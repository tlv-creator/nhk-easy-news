#!/usr/bin/env python3
"""带 z_at cookie 打开NHK 播放器/页面，抓真实媒体请求（URL+headers）。"""
import asyncio, json, re, sys, os
sys.path.insert(0, "/workspace/nhk")
import auth
from playwright.async_api import async_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
if not os.path.exists(CHROME):
    CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"

MRURL = "https://media.vd.st.nhk/news/nd/20261021de62637_E0000093773_1_s/index.m3u8"
PLAYER = f"https://news.web.nhk/n-data/conf/player/index.html?mrurl={MRURL}&mrposter=/news/u/news/nd/kiji/20261021de62637/20261021de62637_E0000094438_1_l.jpg"

TARGETS = sys.argv[1:] or [PLAYER]


async def main():
    t = auth.get_token()
    print("token scope:", t["scope"])
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", ignore_https_errors=True,
                                  user_agent=auth.UA)
        # 注入 web.nhk 域下的 token cookie
        await ctx.add_cookies([
            {"name": "z_at", "value": t["token"], "domain": ".web.nhk", "path": "/",
             "secure": True, "httpOnly": True},
            {"name": "authz_type", "value": "a-alaz", "domain": ".web.nhk", "path": "/",
             "secure": True, "httpOnly": False},
        ])
        page = await ctx.new_page()
        hits = []

        def rq(r):
            if re.search(r"m3u8|\.mp4|\.m4a|\.mp3|media\.|st\.nhk|\.key|segment", r.url, re.I):
                hits.append(("REQ", r.method, r.url, {k: v for k, v in r.headers.items()
                                                      if k.lower() in ("referer", "origin", "authorization", "cookie", "x-nhk-token", "user-agent")}))
        async def rs(r):
            if re.search(r"m3u8|\.mp4|\.m4a|\.mp3|media\.|st\.nhk", r.url, re.I):
                try: body = (await r.text())[:900]
                except Exception: body = "<binary>"
                hits.append(("RESP", r.status, r.url, body))
        page.on("request", rq); page.on("response", rs)

        for u in TARGETS:
            print(f"\n===== {u[:100]} =====", flush=True)
            try:
                await page.goto(u, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print("  goto:", e)
            await page.wait_for_timeout(12000)
            try:
                print("  title:", await page.title())
                print("  body:", " ".join((await page.inner_text("body")).split())[:700])
            except Exception:
                pass
            # 点播放
            for sel in ["button[class*=play]", "[class*=play]", "video"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await page.evaluate("(e)=>{try{e.click()}catch(x){}}", el)
                        print("  clicked", sel)
                        await page.wait_for_timeout(7000)
                except Exception:
                    pass
            st = await page.evaluate("""()=>Array.from(document.querySelectorAll('video,audio')).map(v=>({tag:v.tagName,src:(v.currentSrc||v.src||'').slice(0,200)}))""")
            print("  media els:", json.dumps(st)[:600])

        print("\n\n########## MEDIA REQUESTS ##########")
        for kind, a, u, extra in hits:
            print(f"[{kind}] {a} {u[:300]}")
            if isinstance(extra, dict):
                print("     H:", json.dumps(extra, ensure_ascii=False)[:700])
            else:
                print("     ", str(extra)[:700])
        await b.close()

asyncio.run(main())
