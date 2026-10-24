#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ai_copy.py —— AI「複製提問」模式验收（零部署，永远可用）

🔴 契约：
    CFG.AI_ENDPOINT 为空 ⇒ 走复制模式
    ⇒ 把 SYSTEM_PROMPT + 上下文拼成**纯文本**写进剪贴板
    ⇒ 拼出来的文本必须与真接口模式**用同一份上下文**（aiBuildBody → aiBuildPrompt）
🔴 剪贴板：clipboard API + `<textarea>` + `document.execCommand('copy')` **双路兜底**
   （WebView 里 clipboard API 常常没权限）

用法：
    python3 test_ai_copy.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t_lib import Server, new_page, CHROME  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
G = []


def check(name, ok, detail=""):
    G.append((name, ok))
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  — " + detail) if detail and not ok else ""))


async def main():
    from playwright.async_api import async_playwright
    srv = Server()
    print("== test_ai_copy（站点 %s）==" % srv.base)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME,
                args=["--no-sandbox", "--ignore-certificate-errors", "--disable-dev-shm-usage"])
            ctx, pg = await new_page(browser, srv.base)

            await pg.goto(srv.base + "/index.html", wait_until="domcontentloaded", timeout=30000)
            await pg.wait_for_timeout(1200)
            # 把中转地址清空 ⇒ 强制复制模式（🔴 运行时改，不碰 config.js）
            await pg.evaluate("() => { window.CFG.AI_ENDPOINT = ''; }")
            ep = await pg.evaluate("() => window.CFG.AI_ENDPOINT")
            check("① 中转地址为空 ⇒ 复制模式", not (ep or "").strip(), repr(ep))
            check("② AI 按钮显示为「複製提問」",
                  (await pg.evaluate("() => !document.querySelector('[data-act=\"ai-ask\"]')")) is True)

            # 进第一期 → 点一句 → 点「複製提問」
            await pg.wait_for_selector("#epList .ep-item", timeout=20000)
            items = await pg.query_selector_all("#epList .ep-item")
            await items[0].click()
            await pg.wait_for_timeout(1200)
            # 🔴 取**原始日文**（数据里的 ja），不要用 DOM innerText
            #    （DOM 里汉字被 ruby 插了假名 ⇒ 比对必然失败）
            ja = await pg.evaluate("""() => {
                const el = document.querySelector('#transcript .cue');
                if (!el) return '';
                el.click();
                const i = +el.dataset.i;
                const cur = (window.__EPISODES__ || [])[0];
                const d = (window.__SUBS__ || {})[cur.id];
                const arr = d ? (d.easy || []) : [];
                return (arr[i] && arr[i].ja) || '';
            }""")
            await pg.wait_for_timeout(900)

            clicked = False
            for sel in ['[data-act="ai-copy"]', 'button:has-text("複製提問")']:
                el = await pg.query_selector(sel)
                if el:
                    await el.click()
                    clicked = True
                    break
            check("③ 有「複製提問」入口且点了", clicked)
            await pg.wait_for_timeout(1200)

            last = await pg.evaluate("() => (window.__AI && window.__AI.lastText) || ''")
            check("④ 拼出了提问文本", len(last) > 100, "长度 %d" % len(last))
            check("⑤ 含角色提示词", "【角色】" in last and "【任务】" in last)
            check("⑥ 含剧名", "NHKやさしいことばニュース" in last)
            check("⑦ 含集数", "集数：" in last)
            check("⑧ 含当前句", (ja[:8] in last) if ja else False, (ja or "")[:40])
            check("⑨ 含上下文小节", "【上下文】" in last)
            check("⑩ 含本剧台词参考", "本剧台词参考" in last)

            # ⑪⑫ 剪贴板：clipboard API 或 execCommand 兜底
            clip = await pg.evaluate("""async () => {
                try { return await navigator.clipboard.readText(); }
                catch (e) { return ''; }
            }""")
            check("⑪ 剪贴板里确实写进去了", len(clip) > 100, "长度 %d" % len(clip))
            check("⑫ 剪贴板内容 = 提问文本", bool(clip) and clip.strip() == last.strip(),
                  "clip=%d last=%d" % (len(clip), len(last)))

            # ⑬ 源码级：双路兜底 + 两种模式同一份上下文
            appjs = open(os.path.join(ROOT, "site", "app", "app.js"), encoding="utf-8").read()
            check("⑬ 有 execCommand 兜底", "execCommand('copy')" in appjs or
                  'execCommand("copy")' in appjs)
            check("⑭ 有 textarea 兜底", "textarea" in appjs)
            check("⑮ 复制后有可见反馈", "已複製" in appjs or "已复制" in appjs)
            check("⑯ 两种模式共用 aiBuildBody/aiBuildPrompt",
                  "aiBuildBody" in appjs and "aiBuildPrompt" in appjs)
            # ⑰ 复制模式与真接口模式拼出的文本必须一致
            same = await pg.evaluate("""() => {
                try {
                    // 🔴 复制模式写进剪贴板的文本 == aiBuildPrompt(同一个 body)
                    //    ⇒ 复制模式与真接口模式**用的是同一份上下文**
                    const p = window.__aiBuildPrompt ? window.__aiBuildPrompt(window.__AI.lastBody) : '';
                    const t = window.__AI.lastText || '';
                    return !!p && !!t && p === t;
                } catch (e) { return 'ERR:' + e; }
            }""")
            check("⑰ 复制内容 = aiBuildPrompt(同一个 body)（两模式同一份上下文）",
                  same is True, str(same))
            await browser.close()
    finally:
        srv.stop()
    nfail = sum(1 for _, ok in G if not ok)
    print("\n%s  %d 项检查，%d 失败" % ("✅ 全绿" if nfail == 0 else "❌", len(G), nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
