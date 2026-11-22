#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_pages_web.py —— **Pages 永久网页版**的专用验收（2026-11-22）

🔴 为什么要有这个（今天的实测踩出来的）：
    `test_online.py` 是给 **mcode 内容站**设计的（音频和 APK 都在那儿）
    ⇒ 拿它去测 **Pages 永久站**（https://…/nhk-web/）会得到一堆**假红**：
       ⑩⑪ 音频 404（**设计如此**：音频留 mcode，Pages 不放，见手册 §31.2）
       ⑬ 死链（同上：media/ 不在 Pages）
       ⑭ APK 404（**设计如此**：APK 由引导页指向 mcode 站）
       ⑮b 引导页指向的不是 Pages（**设计如此**：引导页指向 mcode 内容站）
    ⇒ 「假红」会让人以为 Pages 版坏了 ⇒ **必须有一把专门的尺子**

✅ 这个脚本测 Pages 版**真正要成立的事**：
    ① 站能开（200）+ 关键资源齐
    ② 期数与 mcode 现役站**一致**（内容同步没掉）
    ③ 最新期一致
    ④ **音频从 mcode MEDIA_BASE 取**（这是 Pages 版能不能听的关键！）
       ⇒ 真在浏览器里点播放，抓实际请求的 URL ⇒ 必须是 mcode 站且 200
    ⑤ config.js 的 MEDIA_BASE 指向**现役站 + 永久兜底站**
    ⑥ 缓存三件套同号（APP_VER / sw.js CACHE / ?v=）

用法：
    python3 test_pages_web.py [PagesURL]
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from t_lib import Server, new_page, CHROME  # noqa: E402

PAGES = sys.argv[1] if len(sys.argv) > 1 else \
    "https://tlv-creator.github.io/nhk-easy-news/nhk-web/"
G = []


def check(name, ok, detail=""):
    G.append((name, ok, detail))
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  — " + detail) if detail and not ok else ""))


def curl(url, timeout=60):
    p = subprocess.run(["curl", "-s", "-L", "-m", str(timeout), url],
                       capture_output=True)
    return p.stdout


def main():
    import asyncio
    base = PAGES.rstrip("/")
    print("== test_pages_web %s ==" % base)

    html = curl(base + "/index.html").decode("utf-8", "replace")
    check("① 站首页 200 + 内容非空", bool(html) and "<html" in html.lower())
    appjs = curl(base + "/app.js").decode("utf-8", "replace")
    check("② 关键资源齐（app.js/config.js/sw.js）",
          bool(appjs) and bool(curl(base + "/config.js")) and bool(curl(base + "/sw.js")))

    # 与 mcode 现役站比期数
    live = json.loads(curl("https://tlv-creator.github.io/nhk-easy-news/version.json").decode())
    live_site = (live.get("site") or "").rstrip("/")
    live_eps = curl(live_site + "/data/episodes.js").decode("utf-8", "replace")
    pages_eps = curl(base + "/data/episodes.js").decode("utf-8", "replace")

    def cnt(s):
        try:
            arr = json.loads(s[s.index("["):s.rindex("]") + 1])
            return len(arr), max(str(e.get("date") or "") for e in arr)
        except Exception:
            return -1, "?"

    ln, ld = cnt(live_eps)
    pn, pd = cnt(pages_eps)
    check("③ 期数与现役站一致（Pages %d / mcode %d）" % (pn, ln), pn == ln and pn > 0)
    check("④ 最新期与现役站一致（%s / %s）" % (pd, ld), pd == ld)

    cfg = curl(base + "/config.js").decode("utf-8", "replace")
    m = re.search(r"window\.CFG\.MEDIA_BASE\s*=\s*\[(.*?)\];", cfg, re.S)
    bases = re.findall(r"'(https://[^']+)'", m.group(1)) if m else []
    check("⑤ MEDIA_BASE 指向 mcode 源站", len(bases) >= 1 and all("mcode.cn" in b for b in bases),
          str(bases))
    check("⑤b MEDIA_BASE 含永久兜底站", any("vkwwuh9wgkzo0" in b for b in bases), str(bases))

    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME,
                args=["--no-sandbox", "--ignore-certificate-errors", "--disable-dev-shm-usage"])
            ctx = await browser.new_context()
            page = await ctx.new_page()
            reqs = []
            page.on("request", lambda r: reqs.append(r.url))
            await page.goto(base + "/", wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(2500)
            # 🔴 先点开一期（首页是列表 ⇒ 不进播放页就没有 <audio>）
            await page.evaluate("""() => {
                const el = document.querySelector('#epList .ep-item');
                if (el) el.click();
            }""")
            await page.wait_for_timeout(2500)
            # 点播放（按钮 id = btnPlay）
            await page.evaluate("""() => {
                const b = document.getElementById('btnPlay');
                if (b) b.click();
            }""")
            await page.wait_for_timeout(5000)
            audio_reqs = [u for u in reqs if ".m4a" in u or ".mp3" in u]
            check("⑥ 浏览器实际请求了音频", len(audio_reqs) > 0,
                  " | ".join(audio_reqs[:3]))
            check("⑦ 音频请求指向 mcode 源站（不是 Pages 404）",
                  bool(audio_reqs) and all("mcode.cn" in u for u in audio_reqs),
                  " | ".join(audio_reqs[:3]))
            # 音频能不能真取到（HEAD 200 + 有大小）
            if audio_reqs:
                code = subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "40",
                                        "-w", "%{http_code} %{size_download}", audio_reqs[0]],
                                       capture_output=True, text=True).stdout.split()
                check("⑧ 音频源站真的能下载", code and code[0] == "200" and int(code[1] or 0) > 10000,
                      " ".join(code))
            # 缓存三件套
            mver = re.search(r"var APP_VER = 'v(\d+)'", appjs)
            mcache = re.search(r"nhk-easynews-v(\d+)", curl(base + "/sw.js").decode("utf-8", "replace"))
            mv = re.findall(r"\?v=v(\d+)", html)
            check("⑨ Pages 自己的三件套同号（APP_VER %s / CACHE %s / ?v %s）"
                  % (mver.group(1) if mver else "?", mcache.group(1) if mcache else "?",
                     mv[0] if mv else "?"),
                  bool(mver and mcache and mv) and
                  mver.group(1) == mcache.group(1) == mv[0])
            await browser.close()

    asyncio.run(run())
    bad = [g for g in G if not g[1]]
    print("\n%s  %d 组检查，%d 失败" % ("✅ 全绿" if not bad else "❌", len(G), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
