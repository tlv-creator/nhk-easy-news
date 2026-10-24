#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ai.py —— AI 讲解「真接口模式」链路验收（用本地 mock，**不烧额度**）

🔴 契约（app.js 的 aiBuildBody）：
    POST {AI_ENDPOINT}
    body = {scene,title,episode,ja,zh,prev[],next[],word,refs[]}
    resp = {ok:true,text:"<markdown>"}
🔴 **前端超时必须 ≥ 中转上游超时 + 余量**（中转 60 秒 ⇒ 前端 75 秒）

用法：
    python3 test_ai.py            # 自己起 mock
    python3 test_ai.py 8898       # 用已经在跑的 mock
"""
import asyncio
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t_lib import Server, new_page, CHROME  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
G = []


def check(name, ok, detail=""):
    G.append((name, ok))
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  — " + detail) if detail and not ok else ""))


async def open_sentence_panel(pg):
    """进第一期 → 点开字幕流里的一句 → 弹出详解面板"""
    await pg.wait_for_selector("#epList .ep-item", timeout=20000)
    items = await pg.query_selector_all("#epList .ep-item")
    await items[0].click()
    await pg.wait_for_timeout(1200)
    await pg.evaluate("""() => {
        const el = document.querySelector('#transcript .cue');
        if (el) el.click();
    }""")
    await pg.wait_for_timeout(1000)
    return await pg.evaluate("""() => {
        const p = document.querySelector('#pop');
        return p ? (p.className || '') : '';
    }""")


async def main():
    from playwright.async_api import async_playwright
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
    log = os.path.join(ROOT, "work", "mock_ai_calls.jsonl")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    if os.path.exists(log):
        os.remove(log)
    proc = None
    if len(sys.argv) <= 1:
        # 🔴 上一次跑剩的 mock 可能还占着端口 ⇒ 它的进程没有 MOCK_LOG ⇒ 日志会空
        subprocess.run(["pkill", "-f", "[m]ock_ai.py"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        env = dict(os.environ, MOCK_LOG=log)
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_ai.py"), str(port)],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
    srv = Server()
    MOCK_URL = "http://127.0.0.1:%d/ai" % port
    print("== test_ai（mock :%d，站点 %s）==" % (port, srv.base))
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME,
                args=["--no-sandbox", "--ignore-certificate-errors", "--disable-dev-shm-usage"])
            ctx, pg = await new_page(browser, srv.base)

            await pg.goto(srv.base + "/index.html", wait_until="domcontentloaded", timeout=30000)
            await pg.wait_for_timeout(1200)
            # 把中转地址改到本地 mock（🔴 运行时改，config.js 里的真地址不碰）
            await pg.evaluate("() => { window.CFG.AI_ENDPOINT = '%s'; }" % MOCK_URL)

            ep = await pg.evaluate("() => window.CFG.AI_ENDPOINT")
            check("① 中转地址指向本地 mock", "127.0.0.1" in (ep or ""), str(ep))
            check("② 前端没有把 key 写进前端", await pg.evaluate(
                "() => !document.documentElement.innerHTML.includes('sk-')"))

            await open_sentence_panel(pg)
            panel = await pg.evaluate("""() => {
                const p = document.querySelector('#pop');
                return p ? (p.className || '') : '';
            }""")
            check("③ 点句子弹出详解面板", "show" in (panel or "") or panel != "", panel)

            # 点「問 AI：講這一句」
            clicked = False
            for sel in ['[data-act="ai-sentence"]', 'button:has-text("問 AI")',
                        'button:has-text("問AI")']:
                el = await pg.query_selector(sel)
                if el:
                    await el.click()
                    clicked = True
                    break
            check("④ 有「問 AI」入口", clicked)
            if not clicked:
                await browser.close()
                return 1

            await pg.wait_for_timeout(2500)
            # 请求体
            body = await pg.evaluate("() => window.__AI && window.__AI.lastBody")
            check("⑤ 前端构造了请求体", bool(body), json.dumps(body)[:120] if body else "")
            if body:
                need = ["scene", "title", "episode", "ja", "zh", "prev", "next", "word", "refs"]
                miss = [k for k in need if k not in body]
                check("⑥ 请求体九个字段齐全", not miss, str(miss))
                check("⑦ scene 合法", body.get("scene") in ("explain_sentence", "explain_word"),
                      str(body.get("scene")))
                check("⑧ 上下文非空（ja/zh）", bool(body.get("ja")) and bool(body.get("zh")),
                      "%r / %r" % (body.get("ja", "")[:20], body.get("zh", "")[:20]))
                check("⑨ prev/next 是数组", isinstance(body.get("prev"), list)
                      and isinstance(body.get("next"), list))
                check("⑩ refs 是数组", isinstance(body.get("refs"), list))
            # 响应渲染
            # ⑪ 回答真的渲染出来了（🔴 不能只看长度：出错时前端也渲染一大段错误提示）
            txt = await pg.evaluate("""() => {
                const b = document.querySelector('#aiBox');
                return b ? (b.innerText || '') : '';
            }""")
            check("⑪ AI 回答渲染到面板", ("原文" in txt and "中文翻译" in txt)
                  and "不可用" not in txt, txt[:80].replace("\n", " "))
            check("⑪b AI 状态是 ok", (await pg.evaluate("() => window.__AI.state")) == "ok",
                  str(await pg.evaluate("() => window.__AI.state")))
            html = await pg.evaluate("""() => {
                const b = document.querySelector('#aiBox');
                return b ? b.innerHTML : '';
            }""")
            check("⑫ 五个小节都在（原文/翻译/逐词/语法/结构）",
                  all(k in html for k in ("原文", "中文翻译", "逐词拆解", "语法说明", "整句结构")),
                  html[:120])
            # mock 真的收到了
            if os.path.exists(log):
                calls = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
                check("⑬ mock 收到请求", len(calls) > 0, str(len(calls)))
                if calls:
                    b0 = calls[0].get("body", calls[0])
                    check("⑭ mock 收到的 body 字段齐",
                          all(k in b0 for k in ("scene", "title", "episode", "ja", "zh",
                                               "prev", "next", "word", "refs")))
            else:
                check("⑬ mock 收到请求", False, "没有日志")

            # 源码级：超时 ≥ 75 秒；两种模式共用同一份上下文
            appjs = open(os.path.join(ROOT, "site", "app", "app.js"), encoding="utf-8").read()
            check("⑮ 前端超时 ≥75 秒（不是 45）", "75000" in appjs and "45000" not in appjs)
            check("⑯ 两种模式共用 aiBuildBody", appjs.count("aiBuildBody(") >= 2)
            check("⑰ aiBuildPrompt 复用同一份 body", "function aiBuildPrompt(body)" in appjs)
            check("⑱ SYSTEM_PROMPT 在复制模式里原样用", "AI_SYSTEM_PROMPT" in appjs)
            await browser.close()
    finally:
        srv.stop()
        if proc:
            proc.terminate()
    nfail = sum(1 for _, ok in G if not ok)
    print("\n%s  %d 项检查，%d 失败" % ("✅ 全绿" if nfail == 0 else "❌", len(G), nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
