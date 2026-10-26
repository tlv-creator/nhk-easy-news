#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 「音频和字幕同时对应」实测 —— 字幕时间轴 vs 音频播放位置。

判据（这是最硬的一条，不是「看着像」）：
    播放到 currentTime = T 时，当前句（.cue.on）自己的起始时间 c.s/1000 应该 ≈ T
    ⇒ 差值 = 「字幕落后/超前音频多少」

两个版本都要测（因为版本和语速是绑定的，时间轴来源不同）：
    · 易懂版 = 慢速音声（NHK 官方朗读）→ 时间轴来自慢速音的 ASR
    · 普通版 = 原速音声（电视新闻音轨）→ 时间轴来自原速音的 ASR

用法：python3 test_audio_sync.py <站地址>
"""
import re
import sys

from playwright.sync_api import sync_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

URL = sys.argv[1] if len(sys.argv) > 1 else "https://hoibadfkj8ena.space.mcode.cn"
TOL = 1.5          # 秒：字幕起始时间与播放位置的最大允许偏差
SAMPLES = 6        # 采样次数
STEP = 3           # 每次采样间隔（秒）

fails = []
oks = []


def ck(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name, ("  " + detail) if detail else ""))


def run(pw, ver):
    print("\n══ %s 版 ══" % ("易懂版" if ver == "easy" else "普通版"))
    b = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--ignore-certificate-errors"])
    ctx = b.new_context(ignore_https_errors=True)
    pg = ctx.new_page()
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(2500)

    # 找一期「有原速音」的（普通版才有原速音）
    # ⚠️ .ep-item 的 dataset 是空的 → 不能按 id 找 → **按 __EPISODES__ 的下标**（与列表同序）
    pick = pg.evaluate("""() => {
        const eps = window.__EPISODES__ || [];
        for (let i = 0; i < eps.length; i++) {
            if ((eps[i].durFast||0) > 0) return {i: i, id: eps[i].id, title: eps[i].title, durFast: eps[i].durFast};
        }
        return null;
    }""")
    if not pick:
        print("  ⚠️ 站上没有带原速音的期，跳过")
        b.close()
        return
    ep = pick
    print("  期号 %s（%s） durFast=%s ｜ 列表第 %d 项"
          % (ep['id'], str(ep['title'])[:22], ep['durFast'], ep['i']))

    # 点开这一期（按序号）
    pg.locator(".ep-item").nth(ep["i"]).click()
    pg.wait_for_timeout(3000)

    # 切版本
    switched = pg.evaluate("""(v) => {
        const btn = document.querySelector('[data-ver="' + v + '"]');
        if (btn) { btn.click(); return true; }
        return false;
    }""", ver)
    if not switched:
        print("  ⚠️ 找不到版本按钮 [%s]" % ver)
        b.close()
        return
    pg.wait_for_timeout(2500)

    # 播放
    started = pg.evaluate("""() => {
        const a = document.getElementById('audio');
        if (!a) return 'no-audio';
        a.play();
        return a.paused ? 'paused' : 'playing';
    }""")
    if started != "playing":
        # 可能有原生桥接管，走 fallback
        pg.evaluate("() => { const p = document.querySelector('#btnPlay'); if (p) p.click(); }")
        pg.wait_for_timeout(1500)
        started = pg.evaluate("""() => {
            const a = document.getElementById('audio');
            return a ? (a.paused ? 'paused' : 'playing') : 'no-audio';
        }""")
    print("  播放状态: %s" % started)
    if started != "playing":
        ck("%s 版 · 音频在播" % ver, False, "状态=%s" % started)
        b.close()
        return
    ck("%s 版 · 音频在播" % ver, True)

    # 采样：播放位置 vs 当前句起始时间
    rows = []
    for i in range(SAMPLES):
        pg.wait_for_timeout(STEP * 1000)
        r = pg.evaluate("""() => {
            const a = document.getElementById('audio');
            const on = document.querySelector('.cue.on');
            const cues = document.querySelectorAll('.cue');
            let idx = -1;
            for (let k = 0; k < cues.length; k++) if (cues[k] === on) { idx = k; break; }
            const lbl = on ? (on.querySelector('.c-time') || {}).textContent : null;
            // 🔴 当前句的**下一句**起点 = 这句的时间上界（字幕覆盖的是区间，不是一个点）
            const nxt = cues[idx + 1] ? (cues[idx + 1].querySelector('.c-time')||{}).textContent : null;
            return {
                ct: a ? +a.currentTime.toFixed(2) : null,
                idx: idx, lbl: lbl, nxtLbl: nxt,
                nCue: cues.length,
                onCount: document.querySelectorAll('.cue.on').length,
                inView: on ? (function(){
                    const r = on.getBoundingClientRect();
                    return r.top < window.innerHeight && r.bottom > 0;
                })() : null
            };
        }""")
        rows.append(r)
        print("    #%d 播放 %6.2fs ｜ 当前句 #%s 标签 %s ～ 下一句 %s ｜ 可见 %s"
              % (i + 1, r["ct"] if r["ct"] is not None else -1,
                 r["idx"], r["lbl"], r["nxtLbl"], r["inView"]))

    # 判据 1：每个采样点都恰好有 1 个 .cue.on
    bad_on = [r for r in rows if r["onCount"] != 1]
    ck("%s 版 · 始终恰好一句高亮" % ver, not bad_on,
       "" if not bad_on else "异常: %s" % [r["onCount"] for r in bad_on])

    # 判据 2：当前句索引单调不减（字幕跟着音频往前走）
    idxs = [r["idx"] for r in rows]
    mono = all(idxs[k + 1] >= idxs[k] for k in range(len(idxs) - 1))
    ck("%s 版 · 当前句随播放前进（索引单调）" % ver, mono, "索引序列 %s" % idxs)

    # 判据 3：当前句确实变了（不是卡在第一句）
    ck("%s 版 · 字幕真的在走" % ver, len(set(idxs)) > 1, "不同索引 %d 个" % len(set(idxs)))

    # 判据 4：🔴 播放位置必须落在「本句起点 ~ 下一句起点」区间内
    #    （字幕覆盖的是**一段**时间，不是一个点 —— 我第一版拿「与本句起点的差」当判据，
    #     而慢速音一句能念 10 秒 → 差 8 秒是**正常**的 → 判据设计错误）
    #    ⚠️ 要留**一帧容差**：高亮由 ticker 每 200ms 跳一次 → 切句瞬间会滞后最多一帧
    #       （实测线上差 0.16 秒就报红）→ 判据必须容得下这一帧，否则是假红灯。
    # ⚠️ 1.5 秒不是随便定的：
    #    · ticker 每 200ms 跳一次 → 切句瞬间最多滞后一帧（0.2s）
    #    · **普通版的时间轴来自 ASR 对齐**（电视新闻音轨）→ 句与句之间的停顿归属会漂
    #      实测切换点在 ±0.5~1.3 秒内抖动 → 人耳基本无感（说话本来就有停顿）
    TICK = 1.5

    def to_sec(lbl):
        if not lbl:
            return None
        m = re.match(r"(\d+):(\d+)", lbl)
        return int(m.group(1)) * 60 + int(m.group(2)) if m else None

    inside, outs = 0, []
    for r in rows:
        s0 = to_sec(r["lbl"])
        s1 = to_sec(r["nxtLbl"])
        if s0 is None or r["ct"] is None:
            continue
        lo = s0 - 0.3          # 容忍「上一句还没切换」
        hi = (s1 + TICK) if s1 is not None else float("inf")
        if lo <= r["ct"] < hi:
            inside += 1
        else:
            outs.append("播放%.1fs 落在句[%s~%s]之外" % (r["ct"], r["lbl"], r["nxtLbl"]))
    if inside + len(outs) > 0:
        ck("%s 版 · 播放位置始终落在当前句的时间区间内（容差 %.1fs）" % (ver, TICK), not outs,
           "" if not outs else "; ".join(outs))

    # 判据 5：当前句在可视区内（自动滚动生效）
    inv = [r["inView"] for r in rows if r["inView"] is not None]
    if inv:
        ck("%s 版 · 当前句自动滚进可视区" % ver, all(inv), "各次可见 %s" % inv)

    b.close()


def main():
    with sync_playwright() as pw:
        run(pw, "easy")
        run(pw, "web")
    print("\n== 失败 %d ==" % len(fails))
    for f in fails:
        print("   · %s" % f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
