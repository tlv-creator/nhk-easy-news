#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_audio_gate.py —— **Shun 自己的**音频健康闸门自检（正反两向）

🔴 为什么需要它（不是「多一个测试」）：
  · 共享体检工具 `scripts/audio_health.py` 自带**对照组自证**（`--selftest`）✅
  · 但 **Shun 的闸门**（`tools/fetch_new.py` 的 `health_gate()`）**没人测**
    ⇒ 闸门自己的 bug（阈值写错、判据接错、体检工具挂了还放行）没人发现
  ⇒ 这里对 **Shun 的真实函数**做正反两向自检：
      正向：干净音频**必须放行**
      反向：每个已知坏形态**必须红**，且红的原因是**那一维**

🔴🔴 纪律（照抄 NHK 那套）：
  ① **反例必须真的造出来** ⇒ 每个反向用例都断言「它确实触发了预期判据」
     （不能只跑一遍就信 ⇒ 断言 reason 里出现的是**那一维**，不是笼统的"失败"）
  ② **闸门不可恒绿** ⇒ 「工具跑不起来」也必须算红（拿不到证据 ≠ 没问题）
  ③ **豁免要显式登记 + 写清理由**（不能靠隐式规则悄悄排除 ⇒ 静默放行 = 看不见的地方）

判据来源：手册第 2 节的音频健康闸门表（硬失败 / 只警告两档）。

用法：
    python3 scripts/test_audio_gate.py
"""

import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
TOOLS = os.path.join(WS, "tools")
sys.path.insert(0, TOOLS)

import fetch_new  # noqa: E402  （Shun 的闸门本体）

PASS = []
FAIL = []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("  ← " + detail) if (detail and not cond) else ""))


def say(msg):
    print(msg)


# ── 共享体检工具：位置与 fetch_new 算的是同一个（ROOT/nhk-easy-news/scripts） ──
def find_tool():
    cands = [
        os.path.join(os.path.dirname(WS), "nhk-easy-news", "scripts", "audio_health.py"),
        os.path.join(os.path.dirname(WS), "scripts", "audio_health.py"),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


TOOL = find_tool()
if not TOOL:
    print("❌ 找不到共享体检工具（audio_health.py）⇒ 没法自检")
    print("═══ 0 通过 / 1 失败 ═══")
    sys.exit(1)

_spec = importlib.util.spec_from_file_location("audio_health", TOOL)
audio_health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio_health)


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    return p.stdout.decode("utf-8", "replace"), p.returncode


def write_float_wav(path, amp=1.0, dur=2.0, sr=44100):
    """float32 WAV（**必须 float32**：s16 会被夹到 ±1.0 ⇒ |x|>1 判不出来 ⇒ 样本是假的）"""
    n = int(sr * dur)
    data = bytearray()
    for i in range(n):
        v = amp * math.sin(2 * math.pi * 440.0 * i / sr)
        data += struct.pack("<f", v)
    hdr = b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt "
    hdr += (16).to_bytes(4, "little") + (3).to_bytes(2, "little")
    hdr += (1).to_bytes(2, "little") + sr.to_bytes(4, "little")
    hdr += (sr * 4).to_bytes(4, "little") + (4).to_bytes(2, "little")
    hdr += (32).to_bytes(2, "little") + b"data" + len(data).to_bytes(4, "little")
    with open(path, "wb") as f:
        f.write(hdr + bytes(data))
    return path


def stage(tmp, name, src=None):
    """每个样本**单独一个目录**：闸门是 --check <目录> ⇒ 同目录里的别的文件也会被一起体检"""
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    if src:
        dst = os.path.join(d, name + os.path.splitext(src)[1])
        with open(src, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
        return dst
    return d


def main():
    tmp = tempfile.mkdtemp(prefix="shun_gate_")
    say("== 造样本 ==")
    clip, clean, over, junk, mild = audio_health.make_control_samples(tmp)
    # 与 over/mild 等长的源（闸门要拿源跟成品比时长）
    src5 = write_float_wav(os.path.join(tmp, "src5.wav"), amp=0.9, dur=5.0)
    src2 = write_float_wav(os.path.join(tmp, "src2.wav"), amp=0.9, dur=2.0)
    # 荒谬峰值：float32、振幅 2.5 ⇒ +7.96 dBFS（> +6），
    #   但 |x| 最大 2.5 < 4.0 ⇒ **垃圾维度抓不到它**、平顶也没有 ⇒ 只有峰值这一维会响
    loud = write_float_wav(os.path.join(tmp, "loud.wav"), amp=2.5, dur=2.0)
    say("  样本造好：clip/clean/over/junk/mild + src5/src2/loud")

    # ───────────────────────── 正向 ─────────────────────────
    say("\n== 正向：合格音频必须放行 ==")
    prod_ok_dir = os.path.join(tmp, "ok")
    os.makedirs(prod_ok_dir, exist_ok=True)
    prod_ok = os.path.join(prod_ok_dir, "ep01.m4a")
    out, rc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", clean]
                  + fetch_new.RECIPE + [prod_ok, "-y"])
    ok("成品转码成功（用 fetch_new 的配方）", rc == 0 and os.path.exists(prod_ok))
    good_ok, why = fetch_new.health_gate(prod_ok, clean)
    ok("闸门放行干净音频", good_ok is True, why)

    # ───────────────────────── 反向：每一维都要红，且红的原因是那一维 ─────────
    say("\n== 反向：坏形态必须红（且红的原因是**那一维**） ==")

    # ① 截断（时长对不上）—— 峰值守卫抓不到 ⇒ 必须靠时长闸门
    trunc_dir = os.path.join(tmp, "trunc")
    os.makedirs(trunc_dir, exist_ok=True)
    trunc = os.path.join(trunc_dir, "ep01.m4a")
    out, rc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", prod_ok,
                   "-t", "1", "-c", "copy", trunc, "-y"])
    trunc_ok, trunc_why = fetch_new.health_gate(trunc, clean)
    ok("截断的成品被拦下", trunc_ok is False, str(trunc_ok))
    ok("  拦的理由是**时长**（不是峰值）", "转码被截断" in trunc_why, trunc_why[:90])

    # ② 削平（平顶占比 > 5%）
    clip_p = stage(tmp, "clipdir", clip)
    clip_ok, clip_why = fetch_new.health_gate(clip_p, clip)
    ok("削平的文件被拦下", clip_ok is False, str(clip_ok))
    # 🔴 用**精确标记**判「是哪一维」：不能只搜 "flat" ——
    #    文件名 ctrl_flat.wav 里就有 "flat" ⇒ 搜子串会被**文件名**满足 ⇒ 假绿
    ok("  拦的理由是**flat**（平顶）", "reason=flat" in clip_why, clip_why[:90])

    # ③ 垃圾样本（|x| > 4.0 占比 > 0.05%）
    junk_p = stage(tmp, "junkdir", junk)
    junk_ok, junk_why = fetch_new.health_gate(junk_p, clean)
    ok("垃圾样本文件被拦下", junk_ok is False, str(junk_ok))
    ok("  拦的理由是**junk**（超满刻度占比）", "reason=junk" in junk_why, junk_why[:90])

    # ④ 荒谬峰值（> +6 dBFS，但垃圾/平顶都干净 ⇒ 只有峰值这一维）
    loud_p = stage(tmp, "louddir", loud)
    loud_ok, loud_why = fetch_new.health_gate(loud_p, loud)
    ok("峰值荒谬的文件被拦下", loud_ok is False, str(loud_ok))
    ok("  拦的理由是**峰值**", "幅度荒谬" in loud_why, loud_why[:90])

    # ⑤ 体检工具跑不起来 ⇒ 不放行（拿不到证据 ≠ 没问题）
    orig_exists = os.path.exists
    try:
        os.path.exists = lambda p: False if str(p).endswith("audio_health.py") else orig_exists(p)
        no_tool_ok, no_tool_why = fetch_new.health_gate(prod_ok, clean)
    finally:
        os.path.exists = orig_exists
    ok("体检工具跑不起来 ⇒ **不放行**", no_tool_ok is False, str(no_tool_ok))
    ok("  理由里写明「不放行」", "不放行" in no_tool_why, no_tool_why[:90])

    # ───────────────────────── 只警告的两档：必须放行 ─────────────────────────
    say("\n== 只警告不拦的两档：必须放行（过冲 ≠ 削平 ≠ 垃圾样本） ==")
    # 🔴 **前提断言**：反例/正例必须**真的落在那一维**（否则这段在测空气）
    p_over = audio_health.probe(over, deep=True)
    ok("前提：过冲样本的峰值确实落在 0~+6 dBFS（是过冲，不是荒谬）",
       p_over.get("peak_db") is not None and 0 < p_over["peak_db"] <= 6.0,
       "peak=%s" % p_over.get("peak_db"))
    ok("前提：过冲样本的平顶占比很低（**过冲 ≠ 削平**）",
       (p_over.get("flat_ratio") or 99) < 5.0, "flat=%s" % p_over.get("flat_ratio"))

    over_p = stage(tmp, "overdir", over)
    over_ok, over_why = fetch_new.health_gate(over_p, src5)
    ok("温和过冲（0~+6 dBFS）放行", over_ok is True, over_why[:90])

    p_mild = audio_health.probe(mild, deep=True)
    ok("前提：温和样本在 1.2 口径下确实超限（>0.05%）",
       (p_mild.get("over_pct_mild") or 0) > 0.05, "mild=%s" % p_mild.get("over_pct_mild"))
    ok("前提：温和样本在 4.0 口径下**不**超限（所以只能警告）",
       (p_mild.get("over_pct") or 0) <= 0.05, "over4=%s" % p_mild.get("over_pct"))

    mild_p = stage(tmp, "milddir", mild)
    mild_ok, mild_why = fetch_new.health_gate(mild_p, src2)
    ok("大面积轻微超限（|x|>1.2）放行", mild_ok is True, mild_why[:90])

    # ───────────────────────── 配方与阈值 ─────────────────────────
    say("\n== 配方 / 阈值 ==")
    ok("配方 = AAC-LC / 32k / 44100Hz / 单声道",
       fetch_new.RECIPE == ["-c:a", "aac", "-b:a", "32k", "-ar", "44100", "-ac", "1"],
       str(fetch_new.RECIPE))

    # 🔴 配方只写一遍：**交付**编码入口里只有 fetch_new.py 写死了码率
    #   （分析入口豁免：transcribe_minimax.py 的 64k mp3 是给 ASR 用的中间件，不是交付音频）
    DELIVERY = ["tools/fetch_new.py"]
    EXEMPT = {"tools/transcribe_minimax.py": "ASR 中间件（libmp3lame 64k）⇒ 不是交付音频配方"}
    hits = {}
    for fn in sorted(os.listdir(TOOLS)):
        if not fn.endswith(".py"):
            continue
        rel = "tools/" + fn
        p = os.path.join(TOOLS, fn)
        try:
            src = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if '"32k"' in src or "'32k'" in src:
            hits[rel] = "32k"
    ok("交付配方（32k）只写在 fetch_new.py 一处",
       list(hits.keys()) == ["tools/fetch_new.py"], str(hits))
    ok("分析入口豁免有登记、有理由（不是隐式放过）",
       "tools/transcribe_minimax.py" in EXEMPT and "ASR" in EXEMPT["tools/transcribe_minimax.py"])

    ok("时长容差 = 0.5 s", fetch_new.MAX_DURATION_DIFF == 0.5, str(fetch_new.MAX_DURATION_DIFF))
    ok("峰值硬失败线 = +6 dBFS", fetch_new.MAX_PEAK_DB == 6.0, str(fetch_new.MAX_PEAK_DB))
    ok("垃圾样本占比上限 = 0.05%", fetch_new.MAX_OVER_PCT == 0.05, str(fetch_new.MAX_OVER_PCT))
    # 🔴 **平顶阈值的权威在共享工具里**（audio_health.FLAT_RATIO_LIMIT），
    #    fetch_new.MAX_FLAT_PCT 只是一个**没被传给工具的镜像常量**
    #    ⇒ 断言权威那一份，别断言镜像（断言镜像 = 测了个不生效的东西）
    ok("平顶占比上限 = 5%（权威在共享工具里）",
       getattr(audio_health, "FLAT_RATIO_LIMIT", None) == 5.0,
       str(getattr(audio_health, "FLAT_RATIO_LIMIT", None)))
    ok("（镜像常量与权威一致，别让它们漂移）",
       fetch_new.MAX_FLAT_PCT == getattr(audio_health, "FLAT_RATIO_LIMIT", None),
       "%s vs %s" % (fetch_new.MAX_FLAT_PCT,
                     getattr(audio_health, "FLAT_RATIO_LIMIT", None)))

    # ───────────────────────── 共享工具的对照组自证 ─────────────────────────
    say("\n== 共享体检工具的对照组自证（正反两向） ==")
    out, rc = run([sys.executable, TOOL, "--selftest"])
    tail = [x for x in out.splitlines() if "自检失败" in x]
    ok("audio_health.py --selftest 全绿", rc == 0, (tail[-1] if tail else out[-200:]))

    # ───────────────────────── 汇总 ─────────────────────────
    print("\n═══ 音频闸门自检：%d 通过 / %d 失败 ═══" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        sys.exit(1)
    print("✓ 全部通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
