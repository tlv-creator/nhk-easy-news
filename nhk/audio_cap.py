#!/usr/bin/env python3
"""点开 NHK やさしいことばニュース 的音频，抓真实音频请求。"""
import asyncio, json, re, sys, os
sys.path.insert(0, "/workspace/nhk")
import auth
from playwright.async_api import async_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
if not os.path.exists(CHROME):
    CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"

ARTICLE = sys.argv[1] if len(sys.argv) > 1 else \
    "https://news.web.nhk/news/easy/20261020de62106/20261020de62106.html"


async def main():
    t = auth.get_token()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", ignore_https_errors=True,
                                  user_agent=auth.UA)
        await ctx.add_cookies([
            {"name": "z_at", "value": t["token"], "domain": ".web.nhk", "path": "/", "secure": True, "httpOnly": False},
            {"name": "authz_type", "value": "a-alaz", "domain": ".web.nhk", "path": "/", "secure": True, "httpOnly": False},
        ])
        page = await ctx.new_page()
        hits = []

        def rq(r):
            if re.search(r"m3u8|\.mp3|\.m4a|\.mp4|media|sound|audio|st\.nhk|api\.web\.nhk", r.url, re.I):
                hits.append(("REQ", r.method, r.url, {k: v for k, v in r.headers.items()
                                                      if k.lower() in ("referer", "origin", "authorization", "cookie", "x-nhk-token")}))
        async def rs(r):
            if re.search(r"m3u8|\.mp3|\.m4a|\.mp4|media|sound|audio", r.url, re.I):
                try: body = (await r.text())[:700]
                except Exception: body = "<binary>"
                hits.append(("RESP", r.status, r.url, body))
        page.on("request", rq); page.on("response", rs)

        await page.goto(ARTICLE, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        # 点「ニュースを聞きく」
        clicked = await page.evaluate("""()=>{
            const a=document.querySelector('.js-open-audio');
            if(a){a.click(); return 'js-open-audio clicked';}
            const b=Array.from(document.querySelectorAll('a,button')).find(e=>/聞きく/.test(e.textContent||''));
            if(b){b.click(); return 'text clicked: '+b.textContent.trim();}
            return 'not found';
        }""")
        print("click:", clicked)
        await page.wait_for_timeout(10000)

        # 再点所有 audio 相关
        await page.evaluate("""()=>{document.querySelectorAll('.js-open-audio,[class*=audio] button,[class*=audio] a').forEach(e=>{try{e.click()}catch(x){}})}""")
        await page.wait_for_timeout(8000)

        st = await page.evaluate("""()=>Array.from(document.querySelectorAll('video,audio,source')).map(v=>({tag:v.tagName,src:(v.currentSrc||v.src||'').slice(0,250),paused:v.paused}))""")
        print("media els:", json.dumps(st, ensure_ascii=False)[:900])
        try:
            print("player html:", (await page.evaluate("()=>{const e=document.querySelector('#js-audio-inner'); return e?e.innerHTML.slice(0,1200):'(none)'}"))[:1200])
        except Exception as e:
            print(e)

        print("\n########## 音频请求 ##########")
        for h in hits:
            print(h[0], h[1], h[2][:280])
            print("     ", str(h[3])[:600])
        await b.close()

asyncio.run(main())
