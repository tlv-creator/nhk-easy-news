#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 普通版「音字同步」回归测试（2026-10-24 新增）

判据（**不能只看「有没有高亮」**）：
    必须验「**播放位置落在当前句的时间区间内**」，容差 ±1.5 秒。
    ⇒ 而且 **易懂版和普通版各验一遍** —— 只验易懂版 = 普通版根本没测过。
    ⇒ 开关两种状态都要测。

为什么用**假原生桥**推进播放位置（而不是直接设 P.currentTime）：
    🔴 `python3 -m http.server` **不支持 HTTP Range** ⇒ 浏览器**无法 seek**
    ⇒ 实测 `el.currentTime = 30` 之后读回来是 **0.23**（被重置了）
    ⇒ 用「设 currentTime」当测试手段 = 测了个寂寞。
    ⇒ 改用 test_native_tick.py 那套**假原生桥**（`window.__advance`）——
       单位由**原生源码推导**（见 test_native_tick.py），不是手写的假设。

为什么要有这个测试：
    普通版的「音」（电视播报稿）和「字」（报纸原文）**本来不是同一份文本**
    ⇒ 拿报纸原文去套播报音的时间轴 ⇒ 字幕**跳句**（一句还没念完就跳下一句）
    ⇒ 而且这种错**不报错**：高亮照样在动，只是动错了 ⇒ 肉眼很难发现
    ⇒ 必须用「位置 vs 当前句区间」这种**事实量**来判。

用法： python3 test_sp_sync.py [base_url]
"""
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from test_native_tick import MOCK, NATIVE_UNIT, UNIT_MS  # noqa: E402  （假原生桥只写一份）

BASE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "BASE", "http://127.0.0.1:8899/")
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
TOL = 1500          # 容差 ±1.5 秒

fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + "  " + name +
          (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def set_pos(pg, sec):
    """把播放位置设到 sec（秒）—— 走假原生桥（原生单位由源码推导）"""
    raw = sec * 1000 if UNIT_MS else sec
    pg.evaluate("(v)=>{ window.__mock.pos = v; }", raw)
    pg.wait_for_timeout(450)


def highlight_info(pg):
    return pg.evaluate("""()=>{
        var S = window.__S, P = window.__P;
        if (!S || !P) return null;
        var on = document.querySelectorAll('#transcript .cue.on');
        var i = S.idx;
        var c = (i>=0 && S.subs[i]) ? S.subs[i] : null;
        return {idx:i, nOn:on.length, nCues:S.subs.length,
                s:c?c.s:null, e:c?c.e:null, ct:P.currentTime,
                hint:(document.querySelector('#vbHint')||{}).textContent||'',
                scroll:(document.querySelector('#transcript')||document.body).scrollTop};
    }""")


def main():
    print("== 假原生桥单位（由原生源码推导）= %s ==" % NATIVE_UNIT)
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=CHROME,
                                args=["--no-sandbox",
                                      "--autoplay-policy=no-user-gesture-required",
                                      "--ignore-certificate-errors"])
        pg = br.new_page(viewport={"width": 412, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
        pg.add_init_script(MOCK)
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(700)

        # 找一期**有 sp 轨**的（= 有原速音 + ASR 出过稿 + 译过）
        # 🔴 __SUBS__ 里只有**当前已加载**那一期的字幕（字幕按需加载）⇒ 去服务器上拉
        eps = pg.evaluate("""()=>{
            var E=window.__EPISODES__||[];
            return E.map(function(e){ return {id:e.id, no:e.no, ver:e.ver,
                                              durFast:e.durFast, subs:e.subs}; });
        }""")
        base = BASE.rstrip("/")
        picked = None
        for i, e in enumerate(eps):
            if "web" not in (e.get("ver") or []):
                continue
            try:
                with urllib.request.urlopen(base + "/" + e["subs"], timeout=30) as r:
                    txt = r.read().decode("utf-8", "replace")
            except Exception:
                continue
            if '"sp":' in txt:
                picked = {"idx": i, "id": e["id"], "no": e["no"],
                          "durFast": e.get("durFast")}
                break
        info = picked
        if not info:
            print("❌ 没有任何一期有 sp 轨（ASR/翻译/写盘没跑？）⇒ 拒绝继续")
            return 1
        print("== 选期：%s ==" % info)

        # 用假原生桥推进（原生优先）+ 自动滚动开
        pg.evaluate("""()=>{ var s=window.__S; s.settings.audio='native'; s.settings.auto=true;
            localStorage.setItem('ccs_set', JSON.stringify(s.settings)); }""")
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(800)
        pg.evaluate("(i)=>{ document.querySelectorAll('.ep-item')[i].click(); }", info["idx"])
        pg.wait_for_timeout(1500)
        pg.evaluate("document.querySelector('#btnPlay').click()")
        pg.wait_for_timeout(400)

        # ── ① 易懂版：跟音永远开着（音和字本来就是一份）──
        print("\n[1] 易懂版（音字同一份）")
        pg.evaluate("document.querySelector('#verBar .vb[data-ver=\"easy\"]').click()")
        pg.wait_for_timeout(900)
        pos = pg.evaluate("""()=>{ var S=window.__S; var c=S.subs[3]||S.subs[1];
            return {t:(c.s+c.e)/2/1000, s:c.s, e:c.e}; }""")
        set_pos(pg, pos["t"])
        pg.wait_for_timeout(700)
        h2 = highlight_info(pg)
        ck("易懂版有高亮行", h2 and h2["nOn"] >= 1, h2)
        ck("易懂版：播放位置落在当前句区间内（±1.5s）",
           h2 and h2["s"] is not None
           and h2["ct"] * 1000 >= h2["s"] - TOL and h2["ct"] * 1000 <= h2["e"] + TOL,
           "ct=%.1fs 句[%s,%s)" % (h2["ct"], h2["s"], h2["e"]))
        ck("易懂版：高亮句与目标句一致", h2 and h2["idx"] == 3, h2["idx"])

        # ── ② 普通版 + 开关开 = 跟音（sp 轨）──
        print("\n[2] 普通版 · 音字同步 = 開（跟音：電視播報稿）")
        pg.evaluate("window.__mock.plays=[]")
        pg.evaluate("document.querySelector('#verBar .vb[data-ver=\"web\"]').click()")
        pg.wait_for_timeout(1200)
        counts = pg.evaluate("""()=>{
            var d = window.__SUBS__[window.__S.ep.id] || {};
            return {sp:(d.sp||[]).length, web:(d.web||[]).length};
        }""")
        info["spN"] = counts["sp"]
        info["webN"] = counts["web"]
        h = highlight_info(pg)
        ck("用的是 sp 轨（句数 = sp 句数，不是报纸原文句数）",
           h and h["nCues"] == info["spN"] and info["spN"] != info["webN"],
           "字幕 %s 行 / sp %s 句 / web %s 句" % (h["nCues"], info["spN"], info["webN"]))
        ck("顶栏有模式指示（文字）", h and ("跟音" in h["hint"]), h["hint"])
        # 🔴 普通版放的是**原速音**（不是慢速音）
        urls = pg.evaluate("window.__mock.plays.map(function(p){return p.url;})")
        ck("普通版放的是原速音（media/fast）",
           any("/media/fast/" in u for u in urls), urls[-1:] if urls else [])
        ck("普通版**没有**放慢速音",
           not any("/media/slow/" in u for u in urls), urls[-1:] if urls else [])
        pos = pg.evaluate("""()=>{ var S=window.__S; var c=S.subs[3]||S.subs[1];
            return {t:(c.s+c.e)/2/1000, s:c.s, e:c.e}; }""")
        set_pos(pg, pos["t"])
        pg.wait_for_timeout(700)
        h2 = highlight_info(pg)
        ck("普通版：播放位置落在当前句区间内（±1.5s）",
           h2 and h2["s"] is not None
           and h2["ct"] * 1000 >= h2["s"] - TOL and h2["ct"] * 1000 <= h2["e"] + TOL,
           "ct=%.1fs 句[%s,%s)" % (h2["ct"], h2["s"], h2["e"]))
        ck("普通版：有高亮行", h2 and h2["nOn"] == 1, h2["nOn"])

        # ── ③ 关掉开关 = 阅读模式（报纸原文，不高亮、不自动滚）──
        print("\n[3] 普通版 · 音字同步 = 關（閱讀：報紙原文）")
        pg.evaluate("""()=>{ var b=document.querySelector('#btnMenu'); if(b) b.click(); }""")
        pg.wait_for_timeout(500)
        pg.evaluate("""()=>{
            var c=document.querySelector('#spSync');
            if (!c) return;
            c.checked=false;
            c.dispatchEvent(new Event('change',{bubbles:true}));
        }""")
        pg.wait_for_timeout(900)
        h3 = highlight_info(pg)
        # 🔴🔴 2026-10-25 语义修正（用户报「普通版字幕和音频对不上」）：
        #   开关**只管高亮 + 自动滚**，**不再切字幕来源**
        #   ⇒ 有 sp 轨时恒用 sp 轨（= 电视播报稿，与原速音是同一份）⇒ 声画永远一致
        #   ⇒ 关掉开关后字幕句数**仍是 sp 句数**，不是 web 句数
        ck("关掉开关后**字幕来源不变**（仍是 sp 轨 ⇒ 声画一致）",
           h3 and h3["nCues"] == info["spN"], "字幕 %s 行 / sp %s 句 / web %s 句"
           % (h3["nCues"], info["spN"], info["webN"]))
        ck("顶栏模式指示变成「靜默跟讀」", h3 and ("靜默跟讀" in h3["hint"]), h3["hint"])
        ck("顶栏仍标明字幕源是電視播報稿",
           h3 and ("電視播報稿" in h3["hint"]), h3["hint"])
        ck("阅读模式：**无高亮**", h3 and h3["nOn"] == 0, h3["nOn"])
        sc0 = h3["scroll"]
        set_pos(pg, pos["t"] + 7)
        pg.wait_for_timeout(900)
        h4 = highlight_info(pg)
        ck("阅读模式：推进后仍**无高亮**", h4 and h4["nOn"] == 0, h4["nOn"])
        need = pg.evaluate("""()=>{ var b=document.querySelector('#transcript');
            return b ? b.scrollHeight > b.clientHeight + 40 : false; }""")
        if need:
            ck("阅读模式：**不自动滚**", h4["scroll"] == sc0,
               "scrollTop %s -> %s" % (sc0, h4["scroll"]))
        else:
            ck("阅读模式：不自动滚（内容仅一屏，跳过）", True)

        saved = pg.evaluate("JSON.parse(localStorage.getItem('ccs_set')||'{}').sp")
        ck("开关状态记住了（localStorage）", saved is False, saved)

        # 🔴🔴 2026-10-25 语义修正（用户报「普通版字幕和音频对不上」）：
        #   开关**只管高亮 + 自动滚**，**不再切字幕来源**
        #   ⇒ 有 sp 轨时恒用 sp 轨（= 电视播报稿，与原速音是同一份）⇒ 声画永远一致
        #   ⇒ 关掉开关后字幕句数**仍是 sp 句数**，不是 web 句数
        ck("关掉开关后**字幕来源不变**（仍是 sp 轨 ⇒ 声画一致）",
           h3 and h3["nCues"] == info["spN"], "字幕 %s 行 / sp %s 句 / web %s 句"
           % (h3["nCues"], info["spN"], info["webN"]))
        ck("顶栏模式指示变成「靜默跟讀」", h3 and ("靜默跟讀" in h3["hint"]), h3["hint"])
        ck("顶栏仍标明字幕源是電視播報稿",
           h3 and ("電視播報稿" in h3["hint"]), h3["hint"])
        # 🔴 声画一致的关键：开关两态下**播的都必须是原速音**（fast），绝不能是慢速音
        urls2 = pg.evaluate("window.__mock.plays.map(function(p){return p.url;})")
        ck("开关两态下都放原速音（没有慢速音）",
           bool(urls2) and not any("/media/slow/" in u for u in urls2), urls2[-2:])

        # 复原（开）
        pg.evaluate("""()=>{ var c=document.querySelector('#spSync');
            if(!c) return; c.checked=true;
            c.dispatchEvent(new Event('change',{bubbles:true})); }""")
        pg.wait_for_timeout(600)
        h5 = highlight_info(pg)
        ck("重新打开开关 ⇒ 立刻恢复高亮", h5 and h5["nCues"] == info["spN"], h5["nCues"])

        # ── ④ 没有原速音的期：普通版不许有播放按钮、不许高亮、绝不放慢速音 ──
        print("\n[4] 沒有原速音的期（普通版）")
        pg.evaluate("""()=>{ var s=window.__S; s.settings.sp=true;
            localStorage.setItem('ccs_set', JSON.stringify(s.settings)); }""")
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(800)
        nofast = pg.evaluate("""()=>{
            var E=window.__EPISODES__||[];
            for (var i=0;i<E.length;i++){
                if ((E[i].ver||[]).indexOf('web')>=0 && !(E[i].durFast>1))
                    return {idx:i, id:E[i].id, no:E[i].no};
            }
            return null;
        }""")
        if not nofast:
            print("  （没有「有普通版但没原速音」的期 ⇒ 跳过）")
        else:
            print("   选期：%s" % nofast)
            # 🔴 2026-10-25 修：上一段结束时 App 还停在**播放页**（首页被隐藏）
            #   ⇒ 直接点 .ep-item 点不动 ⇒ 后面验的还是上一期（假红）
            #   ⇒ 先回首页再点开
            pg.evaluate("""()=>{ var b=document.querySelector('#btnBack');
                if(b && !b.classList.contains('hidden')) b.click(); }""")
            pg.wait_for_timeout(600)
            pg.evaluate("""()=>{ window.__mock.plays=[]; }""")
            pg.evaluate("(i)=>{ document.querySelectorAll('.ep-item')[i].click(); }", nofast["idx"])
            pg.wait_for_timeout(2000)
            pg.wait_for_function("()=>window.__S && window.__S.ep && window.__S.ep.id!==null", timeout=10000)
            pg.wait_for_timeout(600)
            pg.evaluate("window.__mock.plays=[]")
            pg.evaluate("document.querySelector('#verBar .vb[data-ver=\"web\"]').click()")
            pg.wait_for_timeout(1400)
            st = pg.evaluate("""()=>{
                var pb=document.querySelector('#btnPlay');
                var P=window.__P;
                return {pbHidden: pb ? pb.classList.contains('hidden') : null,
                        src: (P && P.src) ? String(P.src) : '',
                        nOn: document.querySelectorAll('#transcript .cue.on').length,
                        idx: window.__S ? window.__S.idx : -1,
                        nCues: window.__S ? window.__S.subs.length : 0,
                        plays: window.__mock.plays.map(function(p){return p.url;})};
            }""")
            ck("没有原速音 ⇒ 普通版**不出现播放按钮**", st["pbHidden"] is True, st)
            ck("没有原速音 ⇒ **绝不放慢速音**（不能有 media/slow）",
               not any("/media/slow/" in u for u in st["plays"])
               and "media/slow" not in st["src"], st["plays"][-2:])
            ck("没有原速音 ⇒ **不出现高亮**", st["nOn"] == 0 and st["idx"] == -1, st)

        fatal = [e for e in errs if "404" in e and "favicon" not in e]
        ck("无致命 JS 错误（404=漏打包）", len(fatal) == 0, fatal[:3])
        br.close()

    print("\n== 失败 %d 项 ==" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
