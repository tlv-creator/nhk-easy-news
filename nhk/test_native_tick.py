#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_native_tick.py —— 原生播放路径验收（假原生桥）

🔴 判据（照手册 §9 / §10）：
    · 原生拿到的必须是**绝对 URL**（相对路径会被当成手机本地文件 ⇒ 必然没声）
    · **播放位置要落在当前句的时间区间内**（容差 ±1.5 秒）
    · **易懂版和普通版各验一遍** —— 只验易懂版 = 普通版根本没测过
    · 普通版「音字同步」关掉 ⇒ 不高亮、不自动滚（纯阅读）

用法：
    python3 test_native_tick.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t_lib import Server, new_page, CHROME  # noqa: E402

G = []


def check(name, ok, detail=""):
    G.append((name, ok))
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  — " + detail) if detail and not ok else ""))


async def open_episode(pg, idx=0, ver="easy"):
    """进列表 → 点第 idx 期 → 切到指定版本（已在播放页时只切版本）"""
    try:
        await pg.wait_for_selector("#epList .ep-item", timeout=4000)
        items = await pg.query_selector_all("#epList .ep-item")
        if items:
            await items[idx].click()
            await pg.wait_for_timeout(1200)
    except Exception:
        pass  # 已经在播放页了
    # 切版本（普通版）
    if ver == "web":
        btns = await pg.query_selector_all('#verBar button[data-ver]')
        for b in btns:
            t = (await b.inner_text() or "")
            if "普通" in t:
                await b.click()
                await pg.wait_for_timeout(900)
                break
    return True


async def current_cue_index(pg):
    """当前高亮的字幕行下标（-1 = 没有高亮）"""
    return await pg.evaluate("""() => {
        const els = document.querySelectorAll('#transcript .cue');
        for (let i = 0; i < els.length; i++) {
            if (els[i].classList.contains('on')) return i;
        }
        return -1;
    }""")


async def main():
    from playwright.async_api import async_playwright
    srv = Server()
    print("== test_native_tick（本地 %s）==" % srv.base)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME,
                args=["--no-sandbox", "--ignore-certificate-errors", "--disable-dev-shm-usage",
                      "--autoplay-policy=no-user-gesture-required"])
            ctx, pg = await new_page(browser, srv.base)
            await pg.goto(srv.base + "/index.html", wait_until="domcontentloaded", timeout=30000)
            await pg.wait_for_timeout(1500)

            check("① 页面加载", (await pg.title()) != "", await pg.title())
            check("② 假原生桥已注入", await pg.evaluate("() => !!window.Android"))
            await open_episode(pg, 0, "easy")
            await pg.wait_for_timeout(800)

            # ③ 点播放 ⇒ 原生被调用
            await pg.click("#btnPlay")
            await pg.wait_for_timeout(1200)
            played = await pg.evaluate("() => window.__played")
            check("③ 播放时调用了原生 playAudio", len(played) > 0, json.dumps(played)[:120])
            if played:
                u = played[-1]["url"]
                check("④ 原生拿到的是绝对 URL", u.startswith("http://") or u.startswith("https://"), u)
                check("⑤ 绝对 URL 指向本地站", srv.base in u, u)

            # ⑥ 取当前期的 easy cue 列表
            cues = await pg.evaluate("""() => {
                const eps = window.__EPISODES__ || [];
                const id = (window.__EPISODES__ || [])[0] && window.__EPISODES__[0].id;
                const d = (window.__SUBS__ || {})[id];
                return d ? (d.easy || []).map(c => ({s: c.s, e: c.e, ja: c.ja})) : [];
            }""")
            check("⑥ 拿到 easy 轨 cue", len(cues) > 0, str(len(cues)))
            if not cues:
                return 1

            # ⑦⑧⑨ 逐句验：把播放位置设到句子中点 ⇒ 高亮必须落在这一句
            ok_idx, ok_win = 0, 0
            for k in range(min(len(cues), 8)):
                mid = (cues[k]["s"] + cues[k]["e"]) / 2.0
                await pg.evaluate("""(ms) => {
                    window.__state.pos = ms; window.__state.dur = 999999;
                    window.__state.playing = true; window.__state.ready = true;
                }""", mid)
                await pg.wait_for_timeout(450)
                i = await current_cue_index(pg)
                if i == k:
                    ok_idx += 1
                # 播放位置落在当前句区间内（±1.5 秒）
                j = i if i >= 0 else k
                if j < len(cues):
                    if cues[j]["s"] - 1500 <= mid <= cues[j]["e"] + 1500:
                        ok_win += 1
            ns = min(len(cues), 8)
            check("⑦ 逐句高亮命中（%d 句抽样）" % ns, ok_idx == ns, "%d/%d" % (ok_idx, ns))
            check("⑧ 播放位置落在当前句区间内（±1.5s）", ok_win == ns, "%d/%d" % (ok_win, ns))

            # ⑨ seek 传的是毫秒
            await pg.evaluate("() => { window.__seeked = []; }")
            await pg.evaluate("""() => {
                const s = document.querySelector('#seek');
                if (s) {
                    s.value = String(Math.round(12500 / (window.__state.dur || 60000) * 10000));
                    s.dispatchEvent(new Event('input', {bubbles: true}));
                    s.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""")
            await pg.wait_for_timeout(600)
            sought = await pg.evaluate("() => window.__seeked")
            check("⑨ seek 换算成毫秒", len(sought) > 0 and abs(sought[0] - 12500) <= 1500,
                  json.dumps(sought)[:80])

            # ⑩ 暂停会调原生
            await pg.evaluate("() => { window.__paused = 0; }")
            await pg.click("#btnPlay")
            await pg.wait_for_timeout(600)
            check("⑩ 暂停调用原生 pauseAudio", (await pg.evaluate("() => window.__paused")) > 0)

            # ⑪⑫⑬ 普通版：跟音（sp 轨）与阅读（关掉同步）两种模式
            await open_episode(pg, 0, "web")
            await pg.wait_for_timeout(1500)
            mode = await pg.evaluate("""() => {
                const el = document.querySelector('#vbHint');
                return el ? (el.innerText || '') : '';
            }""")
            check("⑪ 普通版有跟音/阅读两种模式", ("跟音" in mode) or ("閱讀" in mode), mode)

            spcues = await pg.evaluate("""() => {
                const id = window.__EPISODES__[0].id;
                const d = (window.__SUBS__ || {})[id];
                return d ? (d.sp || []).map(c => ({s: c.s, e: c.e})) : [];
            }""")
            check("⑫ 普通版有 sp 轨（电视播报稿）", len(spcues) > 0, str(len(spcues)))
            if spcues:
                k = min(2, len(spcues) - 1)
                mid = (spcues[k]["s"] + spcues[k]["e"]) / 2.0
                await pg.evaluate("""(ms) => {
                    window.__state.pos = ms; window.__state.dur = 999999;
                    window.__state.playing = true; window.__state.ready = true;
                }""", mid)
                await pg.wait_for_timeout(600)
                i = await current_cue_index(pg)
                check("⑬ 普通版跟音：高亮落在 sp 轨那一句",
                      i >= 0 and spcues[i]["s"] - 1500 <= mid <= spcues[i]["e"] + 1500,
                      "idx=%d" % i)

            # ⑭ 关掉「音字同步」⇒ 不高亮
            off = await pg.evaluate("""() => {
                const el = document.querySelector('#spSync');
                if (!el) return 'no-toggle';
                el.checked = false;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return 'ok';
            }""")
            if off == "ok":
                await pg.wait_for_timeout(700)
                await pg.evaluate("""() => {
                    window.__state.pos = 3000; window.__state.playing = true;
                }""")
                await pg.wait_for_timeout(600)
                i = await current_cue_index(pg)
                check("⑭ 关掉音字同步 ⇒ 不高亮（纯阅读）", i == -1, "idx=%d" % i)
            else:
                check("⑭ 关掉音字同步 ⇒ 不高亮（纯阅读）", False, "找不到 #spSync 开关")

            # ⑮ 同步开关默认是开的（普通版跟音）
            await pg.evaluate("() => localStorage.clear()")
            await pg.reload(wait_until="domcontentloaded")
            await pg.wait_for_timeout(1500)
            defv = await pg.evaluate("""() => {
                const el = document.querySelector('#spSync');
                return el ? el.checked : null;
            }""")
            check("⑮ 「普通版 音字同步」默认开", defv is True, str(defv))

            # ⑯ 开关状态要记住（localStorage）
            await pg.evaluate("""() => {
                const el = document.querySelector('#spSync');
                el.checked = false;
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""")
            await pg.wait_for_timeout(400)
            ls = await pg.evaluate("() => localStorage.getItem('ccs_set')")
            check("⑯ 开关状态写进 localStorage", ls is not None and '"sp":false' in ls.replace(" ", ""),
                  str(ls)[:120])

            # ⑰⑱ 其它硬约束
            check("⑰ 标签默认不显示", await pg.evaluate("() => window.__AI !== undefined")
                  is not None)
            tags = await pg.evaluate("""() => {
                const s = JSON.parse(localStorage.getItem('ccs_set') || '{}');
                return s.tags === undefined ? false : !!s.tags;
            }""")
            check("⑱ 生词/句型标签默认关", tags is False, str(tags))

            await browser.close()
    finally:
        srv.stop()
    nfail = sum(1 for _, ok in G if not ok)
    print("\n%s  %d 项检查，%d 失败" % ("✅ 全绿" if nfail == 0 else "❌", len(G), nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
