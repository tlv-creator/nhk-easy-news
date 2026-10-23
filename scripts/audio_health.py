#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_health.py —— 音频体检（峰值 / 平顶占比 / 响度），**自带会自证的对照组**

🔴🔴 这份工具存在的理由（2026-10-23 Shun 提醒后重写）：
  ① **「峰值 > 0.05 dB」抓不到最常见的削波。**
     最常见的削波是**样本被钉在 0 dBFS**（平顶）⇒ 此时 **Peak level = 0.000 dB（不是正数！）**
     ⇒ 用 `peak > 0.05` 判 ⇒ **恒绿**。
     实测（对照组）：
       削波样本 aevalsrc=2.0*sin(...)  → Peak level **0.000265 dB**、Peak count/样本 = **66.7%**
       干净样本 aevalsrc=0.9*sin(...)  → Peak level  -0.914944 dB、Peak count/样本 = **0.45%**
  ② **必须用「全量 astats」。**
     只开 `measure_overall=Peak_level` ⇒ **Peak count / Number of samples 根本不打印**
     ⇒ 平顶占比读成 0% ⇒ 削波被判成「干净」。
  ③ **对照组必须能证明自己造对了。**
     造削波样本时振幅必须 **> 1.0**（lavfi 的 `sine` 默认振幅只有 1/8 ⇒ 推 +6 dB 也才 -12 dBFS
     ⇒ 根本没削到 ⇒ 对照组自己是假的）。

判据（两条独立维度，别混）：
  · 平顶占比 = astats `Peak count` / `Number of samples`   > 5% ⇒ 削波
  · 峰值     = astats `Peak level dB`                      > +0.05 dB ⇒ 过冲（次要信号）

用法：
  python3 scripts/audio_health.py --selftest                     # 自证对照组
  python3 scripts/audio_health.py --check site/app/media/fast     # 体检一个目录
  python3 scripts/audio_health.py --check DIR --max-lufs-spread 2.0   # 顺带查响度极差
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

RESULT = {}
FLAT_RATIO_LIMIT = 5.0      # 平顶占比阈值（%）
PEAK_LIMIT_DB = 0.05        # 过冲阈值（dB）
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".mp4")


def _run(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=600)
        return p.stdout.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return ""


def over_pct(path):
    """🔴 **超满刻度样本占比**（|x| > 1.0 的样本比例）

    🔴 为什么要有这一维（2026-10-23 NHK 实测）：
      **有损编码器会把「波形削平」抹掉**（Shun 实测：硬削平 wav 66.7% → AAC 32k 后 0.00075%）
      ⇒ 「平顶占比」在真实有损链路里基本是空的。
      但 NHK 的源流坏帧**不是**削平，是**零散的、幅度荒谬的垃圾样本**：
      峰值 +2.8 ~ +76.9 dBFS，而平顶占比 = 0.000%
      ⇒ **只有「超满刻度样本占比」抓得住它**（NHK 侧实测 0.004% ~ 0.12%）。
    """
    try:
        import numpy as np
    except Exception:
        return None
    # 🔴🔴 **必须用原生声道布局**（2026-10-23 NHK 实测踩到）：
    #   `-ac 1` 的单声道下混是**另一个信号** ⇒ 峰值与用户实际听到的**差 3 dB**
    #   （实测同一文件：下混 -1.48 dBFS，逐声道 -4.48 dBFS）
    #   ⇒ 用户设备播的是**立体声原文件** ⇒ 判据必须落在**原生逐声道**上。
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                        "-f", "f32le", "-ar", "48000", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600)
    d = p.stdout
    n = len(d) // 4
    if n == 0:
        return None
    a = np.frombuffer(d[:n * 4], dtype=np.float32)
    if len(a) % 2 == 0:
        ch = a.reshape(-1, 2)          # 立体声：逐声道取最大
        m = np.abs(ch).max(axis=1)
        over = (np.abs(ch) > 1.0)
        return 100.0 * float(over.sum()) / over.size
    a = np.abs(a)
    return 100.0 * float((a > 1.0).sum()) / len(a)


def probe(path, deep=False):
    """返回 {peak_db, flat_ratio, peak_count, samples, mean_db, lufs, over_pct}"""
    out = {}

    # ── ① 全量 astats（必须全量：否则 Peak count / Number of samples 不打印）──
    txt = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                "-af", "astats", "-f", "null", "-"])
    m = re.search(r"Peak level dB:\s*(-?[\d.]+)", txt)
    out["peak_db"] = float(m.group(1)) if m else None
    # 全量 astats 会打两遍（channel + overall），取最后一组 overall
    pcs = re.findall(r"Peak count:\s*([\d.]+)", txt)
    nss = re.findall(r"Number of samples:\s*(\d+)", txt)
    pc = float(pcs[-1]) if pcs else None
    ns = float(nss[-1]) if nss else None
    out["peak_count"] = pc
    out["samples"] = ns
    out["flat_ratio"] = (pc / ns * 100.0) if (pc is not None and ns) else None

    # ── ② 平均响度（volumedetect 的 mean_volume 不 clamp，可信）──
    txt2 = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                 "-af", "volumedetect", "-f", "null", "-"])
    m2 = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", txt2)
    out["mean_db"] = float(m2.group(1)) if m2 else None

    # ── ③ 整体 LUFS（loudnorm 的 input_i）──
    txt3 = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                 "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    m3 = re.search(r'"input_i"\s*:\s*"(-?[\d.]+)"', txt3)
    if not m3:
        m3 = re.search(r'"input_i"\s*:\s*(-?[\d.]+)', txt3)
    out["lufs"] = float(m3.group(1)) if m3 else None
    out["over_pct"] = over_pct(path) if deep else None
    return out


def make_control_samples(tmpdir):
    """造一对样本：**削波的必须真的被削平**，干净的那条不能有平顶。

    🔴 坑：lavfi 的 `sine` 默认振幅只有 1/8 ⇒ 推 +6 dB 也才 -12 dBFS ⇒ 根本没削到。
    ⇒ 这里用 `aevalsrc=2.0*sin(...)`（振幅 2.0 ⇒ 被钉在 ±1.0）。
    """
    clip = os.path.join(tmpdir, "ctrl_clip.wav")
    clean = os.path.join(tmpdir, "ctrl_clean.wav")
    over = os.path.join(tmpdir, "ctrl_overshoot.m4a")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=2.0*sin(2*PI*440*t):d=2", "-c:a", "pcm_s16le", clip, "-y"])
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=0.9*sin(2*PI*440*t):d=2", "-c:a", "pcm_s16le", clean, "-y"])
    # 🔴 第三个对照样本：**过冲但没削平**（2026-10-23 加）
    #   造法：0.99 振幅 → 有损编码 → **解码器重建后越过 0 dBFS**（实测 +5.4 dB）
    #   但平顶占比 ≈ 0.001% ⇒ **不是**削波
    #   ⇒ 证明「过冲」与「削平」是**两个独立维度**，不能混成一个判据
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=0.99*sin(2*PI*440*t):s=44100:d=5", "-c:a", "aac", "-b:a", "64k",
          "-ar", "44100", "-ac", "1", over, "-y"])
    return clip, clean, over


def selftest():
    """**对照组自证**：证明判据抓得住真削波、且**旧判据抓不住**（否则这工具没意义）"""
    fails = []

    def ck(name, cond, extra=""):
        print(("  OK  " if cond else "  FAIL") + "  " + name +
              (("  " + str(extra)) if extra else ""))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        clip, clean, over = make_control_samples(td)
        ck("对照组样本造出来了",
           os.path.exists(clip) and os.path.exists(clean) and os.path.exists(over))
        c = probe(clip)
        k = probe(clean)
        o = probe(over)
        print("  过冲样本：peak=%s dB  平顶占比=%s%%" %
              (o.get("peak_db"), o.get("flat_ratio")))
        print("  削波样本：peak=%s dB  平顶占比=%s%%" %
              (c.get("peak_db"), c.get("flat_ratio")))
        print("  干净样本：peak=%s dB  平顶占比=%s%%" %
              (k.get("peak_db"), k.get("flat_ratio")))

        ck("对照组 A：削波样本的平顶占比 > 阈值",
           (c.get("flat_ratio") or 0) > FLAT_RATIO_LIMIT, c.get("flat_ratio"))
        ck("对照组 A：干净样本的平顶占比 < 阈值",
           (k.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, k.get("flat_ratio"))
        # 🔴 关键：证明「旧的 peak>0.05 判据」确实漏掉了这个削波样本
        old_missed = not ((c.get("peak_db") or -99) > PEAK_LIMIT_DB)
        ck("对照组 A：旧判据(peak>0.05)确实会漏掉它（这才是要修的理由）",
           old_missed, "peak=%s" % c.get("peak_db"))
        # 反向：新判据不能把干净样本判成削波
        ck("对照组 B：新判据不误伤干净样本",
           (k.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, k.get("flat_ratio"))
        ck("对照组 B：干净样本峰值 < 0 dB",
           (k.get("peak_db") or 99) < 0, k.get("peak_db"))
        # 🔴 对照组 C：**过冲 ≠ 削平**（两个独立维度）
        ck("对照组 C：过冲样本峰值 > 0 dB（确实是过冲）",
           (o.get("peak_db") or -99) > 0, o.get("peak_db"))
        ck("对照组 C：过冲样本平顶占比 < 阈值（**不是**削平）",
           (o.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, o.get("flat_ratio"))
        ck("对照组 C：过冲样本**不该**被当成削平（否则就是假警报）",
           not ((o.get("flat_ratio") or 0) > FLAT_RATIO_LIMIT), o.get("flat_ratio"))
    return fails


def check_dir(d, max_lufs_spread=None, strict_overshoot=False, quiet=False,
              deep=False, max_over_pct=None):
    files = []
    for root, _dirs, names in os.walk(d):
        for n in sorted(names):
            if n.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        if not quiet:
            print("❌ 目录里没有音频文件：%s" % d)
        RESULT.setdefault("dirs", []).append(
            {"dir": d, "files": [], "flat_bad": [], "overshoot": [],
             "lufs_spread": None, "reason": "no_files", "rc": 2})
        return 2

    rows = []
    bad = []          # 削平（真正的削波）⇒ 硬失败
    over = []         # 过冲（解码器重建超出 0 dBFS）⇒ **默认只警告**
    junk = []         # 🔴 垃圾样本（超满刻度占比超限）⇒ 源里有坏帧 ⇒ 硬失败
    for f in files:
        p = probe(f, deep=deep)
        fr = p.get("flat_ratio")
        pk = p.get("peak_db")
        # 🔴🔴 两条独立维度（2026-10-23 修）：**过冲 ≠ 削波**
        #   削平：样本被钉在 ±1.0（平顶占比高）⇒ 波形削成方波 ⇒ 真正的「爆」⇒ 硬失败
        #   过冲：解码器重建后**极少量**样本超过 0 dBFS ⇒ 听感是「偶尔极轻的咔」
        #        ⇒ 与削平混在一起报，会把「0.002% 样本过冲」的文件误判成爆音（假警报）
        flat = (fr is not None and fr > FLAT_RATIO_LIMIT)
        overshoot = (pk is not None and pk > PEAK_LIMIT_DB)
        op = p.get("over_pct")
        is_junk = (op is not None and max_over_pct is not None and op > max_over_pct)
        rows.append((os.path.basename(f), pk, fr, p.get("lufs"), flat, overshoot, op, is_junk))
        if is_junk:
            junk.append(f)
        if flat:
            bad.append(f)
        if overshoot:
            over.append(f)

    if not quiet:
        print("== %s（%d 个文件）==" % (d, len(rows)))
    if not quiet:
        print("  %-34s %10s %10s %9s" % ("文件", "峰值dB", "平顶%", "LUFS"))
    if not quiet:
        for name, pk, fr, lu, _f, _o in rows:
            print("  %-34s %10s %10s %9s" % (
                name[:34],
                "%.3f" % pk if pk is not None else "-",
                "%.3f" % fr if fr is not None else "-",
                "%.1f" % lu if lu is not None else "-"))

    lufs_vals = [r[3] for r in rows if r[3] is not None]
    spread = (max(lufs_vals) - min(lufs_vals)) if len(lufs_vals) > 1 else 0.0
    if not quiet:
        print("\n  🔴 削平文件（真削波，必须重做）：%d 个 %s" % (len(bad), bad if bad else ""))
        print("  🟡 过冲文件（峰值>0dBFS，默认放行；要严加 --strict-overshoot）：%d 个 %s"
              % (len(over), [os.path.basename(x) for x in over] if over else ""))
    if not quiet:
        print("  LUFS：%.1f ~ %.1f，极差 %.2f LU" %
              (min(lufs_vals), max(lufs_vals), spread) if lufs_vals else "  LUFS: 无数据")

    # 🔴🔴 **判定与写 JSON 必须解耦**：原来 RESULT 只在「全过」时才写
    #   ⇒ 一旦判失败就**提前 return** ⇒ JSON 里**什么都没有**
    #   ⇒ 调用方（闸门）恰好在最该拦的那种文件上**读不到任何数据** ⇒ 变成瞎子（假绿灯！）
    #   （2026-10-23 由 Shun 的闸门自检抓出来：平顶 66.7% 的文件 → JSON {"rc":1} → 闸门放行）
    reason = "ok"
    rc = 0
    if max_lufs_spread is not None and spread > max_lufs_spread:
        reason, rc = "lufs_spread", 1
        if not quiet:
            print("  ❌ LUFS 极差 %.2f > 允许的 %.2f" % (spread, max_lufs_spread))
    elif bad:
        reason, rc = "flat", 1
        if not quiet:
            print("  ❌ 有削平文件（波形被削成方波 ⇒ 听着会爆）")
    elif junk:
        reason, rc = "junk", 1
        if not quiet:
            print("  ❌ 有垃圾样本文件（超满刻度占比 > %.3f%% ⇒ 源里有坏帧）" % max_over_pct)
    elif over and strict_overshoot:
        reason, rc = "overshoot", 1
        if not quiet:
            print("  ❌ 有过冲文件（--strict-overshoot）")
    elif not quiet:
        print("  ✅ 全部通过")

    RESULT.setdefault("dirs", []).append({
        "dir": d,
        "files": [{"file": r[0], "peak_db": r[1], "flat_ratio": r[2], "lufs": r[3],
                   "flat": r[4], "overshoot": r[5],
                   "over_pct": (r[6] if len(r) > 6 else None),
                   "junk": (r[7] if len(r) > 7 else False)} for r in rows],
        "flat_bad": [os.path.basename(x) for x in bad],
        "junk_bad": [os.path.basename(x) for x in junk],
        "overshoot": [os.path.basename(x) for x in over],
        "lufs_spread": round(spread, 2) if lufs_vals else None,
        "reason": reason,          # 🔴 为什么失败（调用方不用去猜）
        "rc": rc,
    })
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="append", default=[])
    ap.add_argument("--max-lufs-spread", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true",
                    help="额外解码一遍算「超满刻度样本占比」（抓源里的坏帧）")
    ap.add_argument("--max-over-pct", type=float, default=None,
                    help="超满刻度样本占比上限（%%），超了判 junk（源里有坏帧）")
    ap.add_argument("--strict-overshoot", action="store_true",
                    help="把「过冲」也算失败（默认只警告：过冲≠削波）")
    a = ap.parse_args()

    if a.selftest:
        fails = selftest()
        print("\n== 自检失败 %d 项 ==" % len(fails))
        return 1 if fails else 0

    if a.check:
        rc = 0
        for d in a.check:
            r = check_dir(d, a.max_lufs_spread, a.strict_overshoot, quiet=a.json,
                          deep=a.deep, max_over_pct=a.max_over_pct)
            if r == 1:
                rc = 1
            elif r == 2:
                rc = 2
        if a.json:
            # 🔴 机器可读输出（闸门要用）—— 以前这个参数是**声明了没实现**的假参数
            RESULT["rc"] = rc
            print(json.dumps(RESULT, ensure_ascii=False, indent=1))
        return rc

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
