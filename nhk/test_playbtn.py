#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""播放按钮「左右横跳」+「准备期点击没反应」回归测试（2026-10-22）

## 现象
用户点开一集 → 按钮乐观地显示 ❚❚（在播）→ 每 200ms 被下一拍 tick 顶回 ▶ → 图标左右横跳。
看起来像「点了没反应 / 按钮在乱跳」。

## 根因（两层）
1. 原生 `playerState()` 报的是 **MediaPlayer.isPlaying()**，
   而**网络流准备期间（1~5 秒）恒为 false** → 前端乐观的 ❚❚ 被顶回 ▶。
2. 真原生 `AudioService.play()` 只 prepare、**不会自动播**；要等 `resume()` 置 `wantPlay=true`，
   prepare 完成后 `onPrepared → doStart()`。
   旧前端在「还没 ready」时只记自己的 `_wantPlay`、**不调 `resumeAudio()`**
   → 原生永远不知道用户想播 → **准备期间点播放 = 点了没反应**。

## 修法
- **原生**：`playing` 改报「用户意图」（`wantPlay || isPlaying()`），实际状态另给 `actualPlaying`。
- **前端 A**：`play()` 无条件调 `resumeAudio()`（原生自己判断 prepared，不会抛 IllegalStateException）。
- **前端 B**：兼容旧原生（无 `actualPlaying` 字段）→ 用自己的 `_wantPlay` 意图兜底。

## 🔴 为什么必须双场景
假原生若已用新语义，前端的兼容守卫就**没被考验** → 测不出回归。

## 判据（别写错）
- ❌「变化次数 ≤ 1」—— 一次性变化是**正常的**（准备结束、真正开始播了）
- ✅ **「最后 4 拍必须一模一样」= 稳住了**（这才是「不横跳」的定义）
- ✅ 准备期内**不允许** ❚❚/▶ 交替

用法： python3 test_playbtn.py [base_url]
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "BASE", "http://127.0.0.1:8899/")
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + "  " + name +
          (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


# ── 假原生桥：完全照真原生 AudioService 的语义 ──
# playAudio → 只 prepare（**不自动播**）；resumeAudio → wantPlay=true；prepare 完成 → 若 wantPlay 则起播
MOCK = r"""
(function(){
  var M = window.__mock = { pos:0, dur:120, playing:false, ready:false,
                            completed:false, error:'',
                            preparing:false, wantPlay:false,
                            plays:[], lastUrl:'', resumes:0 };
  window.__SEMANTIC__ = window.__SEMANTIC__ || 'new';
  window.Android = {
    playAudio: function(url, ms){ M.lastUrl = String(url||'');
        M.plays.push({url:String(url||''), ms:ms});
        M.dur = 120;
        M.preparing = true; M.playing = false; M.ready = false; M.wantPlay = false;
        setTimeout(function(){
            M.preparing = false; M.ready = true;
            if (M.wantPlay) M.playing = true;   // onPrepared → if(wantPlay) doStart()
        }, 2500);
    },
    playerState: function(){
      var o = { pos:M.pos, dur:M.dur, ready:M.ready,
                completed:M.completed, error:M.error, url:M.lastUrl };
      if (window.__SEMANTIC__ === 'new') {
        o.playing = M.playing || M.wantPlay;     // 🔴 意图
        o.actualPlaying = M.playing;             // 实际
      } else {
        o.playing = M.playing;                   // 旧语义：实际（准备期恒 false）
      }
      return JSON.stringify(o);
    },
    resumeAudio: function(){ M.resumes++; M.wantPlay = true; if (M.ready) M.playing = true; },
    pauseAudio: function(){ M.playing = false; M.wantPlay = false; },
    seekAudio: function(ms){ M.pos = (ms||0)/1000; },
    stopAudio: function(){ M.playing = false; M.wantPlay = false; },
    setSpeed: function(v){ },
    ping: function(){ return JSON.stringify({ok:true, lastError:''}); },
    resolveAudio: function(){ return ''; }
  };
  window.__advance = function(sec){ M.pos += sec; };
})();
"""


def btn(pg):
    return pg.evaluate(
        "()=>{var b=document.querySelector('#btnPlay');return b?b.textContent.trim():'';}")


def sample(pg, n=12, gap=200):
    out = []
    for _ in range(n):
        out.append(btn(pg))
        pg.wait_for_timeout(gap)
    return out


def enter_ep(pg):
    """进播放页（会触发 attach → 原生准备 2.5 秒 → 自动起播）。"""
    pg.evaluate("""()=>{
        var m = window.__mock;
        m.playing=false; m.preparing=false; m.ready=false; m.pos=0;
        m.wantPlay=false; m.completed=false; m.error=''; m.resumes=0;
    }""")
    pg.evaluate("document.querySelector('.ep-item').click()")
    pg.wait_for_timeout(700)      # 进播放页 + attach（原生开始准备）


def run(pg, semantic, label):
    print("\n── 场景 %s（%s 原生语义）──" % (label, semantic))
    pg.evaluate("window.__SEMANTIC__ = %r" % semantic)
    enter_ep(pg)

    mode = pg.evaluate("window.__P && window.__P.mode")
    ck("走原生路径", mode == "native", mode)

    # ★ 横跳就发生在「自动起播的准备期」→ 直接采样，不点按钮
    seq = sample(pg, 12, 200)
    print("     按钮序列: %s" % " ".join(x or "?" for x in seq))

    tail = seq[-4:]
    ck("最后 4 拍完全一致（不横跳）", len(set(tail)) == 1, tail)

    prep = seq[:12]
    flips = sum(1 for i in range(1, len(prep)) if prep[i] != prep[i - 1])
    ck("准备期内没有 ❚❚/▶ 交替", flips <= 1, "翻转 %d 次：%s" % (flips, prep))

    # 准备结束后应显示播放态
    ck("准备期结束后是播放态 ❚❚", seq[-1] == "❚❚", seq[-1])

    # 真在播了（原生实际状态）
    ck("原生实际在播", pg.evaluate("window.__mock.playing") is True,
       pg.evaluate("window.__mock.playing"))


def run_pause_then_play_during_prepare(pg, semantic, label):
    """🔴 场景 C：**准备期内「暂停 → 再点播放」** → 必须能恢复出声。

    这才是「点了没反应」的真实复现路径：
      进播放页 → 自动起播（attach + resumeAudio → 原生 wantPlay=true）
      → 准备期内点一下 = 暂停（pauseAudio → 原生 wantPlay=false）
      → 再点播放：
         ❌ 旧代码：ready=false → 不调 resumeAudio → 原生 wantPlay 一直是 false
                   → **准备完成后也不起播 → 永远没声音（点了没反应）**
         ✅ 新代码：无条件 resumeAudio → wantPlay=true → prepare 完 onPrepared → doStart()
    """
    print("\n── 场景 %s（%s 原生语义）：准备期「暂停→再播放」 ──" % (label, semantic))
    pg.evaluate("window.__SEMANTIC__ = %r" % semantic)
    enter_ep(pg)

    preparing = pg.evaluate("window.__mock.preparing")
    ready = pg.evaluate("window.__mock.ready")
    ck("操作前还在准备期", preparing is True and ready is False,
       "preparing=%s ready=%s" % (preparing, ready))

    # ① 点一下 = 暂停
    pg.evaluate("document.querySelector('#btnPlay').click()")
    pg.wait_for_timeout(250)
    ck("暂停后原生意图被清掉（wantPlay=false）",
       pg.evaluate("window.__mock.wantPlay") is False,
       pg.evaluate("window.__mock.wantPlay"))

    # ② 再点 = 恢复播放（这是关键一步）
    before = pg.evaluate("window.__mock.resumes")
    pg.evaluate("document.querySelector('#btnPlay').click()")
    pg.wait_for_timeout(250)
    after = pg.evaluate("window.__mock.resumes")
    ck("恢复播放时调了 resumeAudio（把意图还给原生）", after > before,
       "%d -> %d" % (before, after))

    # ③ 等准备完成 → 应该真在播
    pg.wait_for_timeout(3200)
    ck("准备期「暂停→再播放」→ 最后真在播",
       pg.evaluate("window.__mock.playing") is True,
       pg.evaluate("window.__mock.playing"))
    ck("按钮停在播放态 ❚❚", btn(pg) == "❚❚", btn(pg))


with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME,
                            args=["--no-sandbox", "--ignore-certificate-errors",
                                  "--autoplay-policy=no-user-gesture-required"])
    pg = br.new_page(viewport={"width": 412, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.add_init_script(MOCK)
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(900)

    run(pg, "new", "A")
    run(pg, "old", "B")
    run_pause_then_play_during_prepare(pg, "new", "A")
    run_pause_then_play_during_prepare(pg, "old", "B")

    ck("无 JS 错误", len(errs) == 0, errs[:3])
    br.close()

print("\n== 失败 %d 项 ==" % len(fails))
sys.exit(1 if fails else 0)
