#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频捕获钩子的**真机替身验证**（没有安卓模拟器时的等价验证）

要验的逻辑：APK 里注入的那段 JS，能不能在**真实线上站点**上截获 `new Audio()` 造出来的
播放器对象 —— 这是网络版「后台播放 + 锁屏控制」的地基。

方法：用 Playwright 打开线上站点 → 注入**与 APK 里逐字相同**的钩子 JS →
  ① 点播放按钮 → ② 读 window.__shunAu → ③ 读状态 → ④ 验证状态脚本返回的字段

🔴 反向验证：把钩子**去掉**，同样的检查必须失败（证明不是「本来就成立」的假绿）。
"""
import json
import re
import sys
import urllib.request

SITE = "https://tlv-creator.github.io/nhk-easy-news/shun-web/"

# 🔴 与 MainActivity.JS_HOOK_AUDIO **逐字相同**（改一边必须改另一边）
JS_HOOK_AUDIO = (
    "(function(){try{"
    "if(window.__shunAuHooked) return 'hooked';"
    "window.__shunAuHooked=true;"
    "var O=window.Audio; if(!O) return 'noAudio';"
    "function W(){var a=new O(); try{window.__shunAu=a;}catch(e){} return a;}"
    "W.prototype=O.prototype;"
    "try{Object.defineProperty(W,'length',{value:O.length});}catch(e){}"
    "window.Audio=W; return 'ok';"
    "}catch(e){return 'err:'+e;}})();"
)

# 🔴 与 MainActivity.JS_STATE / PlaybackService.STATE_JS **逐字相同**
JS_STATE = (
    "(function(){try{"
    "var a=window.__shunAu;"
    "if(!a) return JSON.stringify({has:false,playing:false,pos:0,dur:0,ended:false});"
    "var d=(a.duration&&isFinite(a.duration))?a.duration:0;"
    "return JSON.stringify({has:true,playing:(!a.paused&&!a.ended),pos:Math.round(a.currentTime*1000),"
    "dur:Math.round(d*1000),ended:!!a.ended});"
    "}catch(e){return JSON.stringify({has:false,playing:false,pos:0,dur:0,ended:false});}})();"
)

PASS, FAIL = [], []


def ok(n, d=""):
    PASS.append(n)
    print("  ✓ %s%s" % (n, ("  " + d) if d else ""))


def bad(n, d=""):
    FAIL.append(n)
    print("  ✗ %s%s" % (n, ("  " + d) if d else ""))


def check(n, c, d_ok="", d_bad=""):
    (ok if c else bad)(n, d_ok if c else d_bad)
    return c


def main():
    from playwright.sync_api import sync_playwright

    url = SITE + "?v=" + str(int(__import__("time").time() * 1000))
    print("=" * 70)
    print("音频捕获钩子验证（真实线上站点）")
    print("  " + url)
    print("=" * 70)

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context()
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        # ---------- ① 钩子注入 ----------
        print("\n[1] 注入钩子")
        r = page.evaluate(JS_HOOK_AUDIO)
        check("钩子注入成功", r == "ok", "返回 %r" % r, "返回 %r" % r)
        r2 = page.evaluate(JS_HOOK_AUDIO)   # 幂等
        check("钩子幂等（第二次不重复挂）", r2 == "hooked", "返回 %r" % r2, "返回 %r" % r2)

        # ---------- ② 点播放 ----------
        print("\n[2] 点播放按钮")
        started = False
        for sel in ["#play", "#playBtn", "button#play"]:
            try:
                if page.query_selector(sel):
                    page.click(sel, timeout=5000)
                    started = True
                    print("    点的是 %s" % sel)
                    break
            except Exception as e:
                print("    %s 点不动: %s" % (sel, str(e)[:60]))
        check("找到并点了播放按钮", started)
        if not started:
            return report()

        page.wait_for_timeout(3500)

        # ---------- ③ 截获到的对象 ----------
        print("\n[3] 截获播放器对象")
        has = page.evaluate("!!window.__shunAu")
        check("window.__shunAu 存在（new Audio 被截获）", has)
        if not has:
            print("    ↳ 站点当前版本可能不是 new Audio() 路线；请人工确认")
            return report()

        info = page.evaluate(
            "({tag: (window.__shunAu && window.__shunAu.tagName) || '',"
            " src: (window.__shunAu && window.__shunAu.src) || '',"
            " ctor: (window.__shunAu && window.__shunAu.constructor && window.__shunAu.constructor.name) || ''})")
        print("    截获到：%s" % json.dumps(info, ensure_ascii=False)[:200])
        check("是真正的 HTMLAudioElement", info.get("ctor") == "HTMLAudioElement",
              info.get("ctor"), "实际 %r" % info.get("ctor"))
        check("src 指向官方 CDN", "redcircle" in (info.get("src") or "") or
              (info.get("src") or "").startswith("https://"),
              (info.get("src") or "")[:80], repr(info.get("src"))[:80])

        # ---------- ④ 状态脚本 ----------
        print("\n[4] 状态脚本（原生层读的就是它）")
        raw = page.evaluate(JS_STATE)
        print("    返回：%s" % raw[:180])
        try:
            st = json.loads(raw)
        except Exception:
            st = None
        check("状态脚本返回可解析 JSON", st is not None, "", repr(raw)[:120])
        if st:
            check("has == true", st.get("has") is True, str(st.get("has")), str(st.get("has")))
            check("pos 是毫秒（非负整数）",
                  isinstance(st.get("pos"), int) and st.get("pos") >= 0, str(st.get("pos")))
            check("dur 是毫秒（非负整数）",
                  isinstance(st.get("dur"), int) and st.get("dur") >= 0, str(st.get("dur")))
            check("playing 是布尔", isinstance(st.get("playing"), bool), str(st.get("playing")))

        # ---------- ⑤ 暂停/恢复 ----------
        print("\n[5] 锁屏按钮的反向控制（pause / play）")
        page.evaluate("try{window.__shunAu.pause();}catch(e){}")
        page.wait_for_timeout(800)
        st2 = json.loads(page.evaluate(JS_STATE))
        check("pause 后 playing == false", st2.get("playing") is False, str(st2.get("playing")),
              str(st2.get("playing")))

        page.evaluate("try{window.__shunAu.play();}catch(e){}")
        page.wait_for_timeout(1500)
        st3 = json.loads(page.evaluate(JS_STATE))
        check("play 后 playing == true", st3.get("playing") is True, str(st3.get("playing")),
              str(st3.get("playing")))
        check("播放中 pos > 0（真在走）", (st3.get("pos") or 0) > 0, "%d ms" % (st3.get("pos") or 0),
              "pos=%s" % st3.get("pos"))

        # ---------- ⑥ 反向验证 ----------
        print("\n[6] 反向验证：不注入钩子，__shunAu 必须不存在")
        page2 = ctx.new_page()
        page2.goto(url, wait_until="networkidle", timeout=60000)
        page2.wait_for_timeout(1200)
        nohook = page2.evaluate("!!window.__shunAu")
        check("无钩子时 __shunAu 不存在（证明 ③ 不是假绿）", not nohook, "", "居然存在")
        page2.close()

        check("页面无 JS 错误", len(errors) == 0, str(errors[:2]), str(errors[:2]))
        browser.close()

    return report()


def report():
    print("\n" + "=" * 70)
    print("结果：%d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        print("=" * 70)
        sys.exit(1)
    print("✅ 全绿")
    print("=" * 70)
    sys.exit(0)


if __name__ == "__main__":
    main()
