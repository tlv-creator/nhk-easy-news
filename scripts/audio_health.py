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


def probe(path):
    """返回 {peak_db, flat_ratio, peak_count, samples, mean_db, lufs}"""
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
    return out


def make_control_samples(tmpdir):
    """造一对样本：**削波的必须真的被削平**，干净的那条不能有平顶。

    🔴 坑：lavfi 的 `sine` 默认振幅只有 1/8 ⇒ 推 +6 dB 也才 -12 dBFS ⇒ 根本没削到。
    ⇒ 这里用 `aevalsrc=2.0*sin(...)`（振幅 2.0 ⇒ 被钉在 ±1.0）。
    """
    clip = os.path.join(tmpdir, "ctrl_clip.wav")
    clean = os.path.join(tmpdir, "ctrl_clean.wav")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=2.0*sin(2*PI*440*t):d=2", "-c:a", "pcm_s16le", clip, "-y"])
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=0.9*sin(2*PI*440*t):d=2", "-c:a", "pcm_s16le", clean, "-y"])
    return clip, clean


def selftest():
    """**对照组自证**：证明判据抓得住真削波、且**旧判据抓不住**（否则这工具没意义）"""
    fails = []

    def ck(name, cond, extra=""):
        print(("  OK  " if cond else "  FAIL") + "  " + name +
              (("  " + str(extra)) if extra else ""))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        clip, clean = make_control_samples(td)
        ck("对照组样本造出来了", os.path.exists(clip) and os.path.exists(clean))
        c = probe(clip)
        k = probe(clean)
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
    return fails


def check_dir(d, max_lufs_spread=None):
    files = []
    for root, _dirs, names in os.walk(d):
        for n in sorted(names):
            if n.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        print("❌ 目录里没有音频文件：%s" % d)
        return 2

    rows = []
    bad = []
    for f in files:
        p = probe(f)
        fr = p.get("flat_ratio")
        pk = p.get("peak_db")
        clipped = (fr is not None and fr > FLAT_RATIO_LIMIT) or \
                  (pk is not None and pk > PEAK_LIMIT_DB)
        rows.append((os.path.basename(f), pk, fr, p.get("lufs"), clipped))
        if clipped:
            bad.append(f)

    print("== %s（%d 个文件）==" % (d, len(rows)))
    print("  %-34s %10s %10s %9s" % ("文件", "峰值dB", "平顶%", "LUFS"))
    for name, pk, fr, lu, _c in rows:
        print("  %-34s %10s %10s %9s" % (
            name[:34],
            "%.3f" % pk if pk is not None else "-",
            "%.3f" % fr if fr is not None else "-",
            "%.1f" % lu if lu is not None else "-"))

    lufs_vals = [r[3] for r in rows if r[3] is not None]
    spread = (max(lufs_vals) - min(lufs_vals)) if len(lufs_vals) > 1 else 0.0
    print("\n  削波文件：%d 个 %s" % (len(bad), bad if bad else ""))
    print("  LUFS：%.1f ~ %.1f，极差 %.2f LU" %
          (min(lufs_vals), max(lufs_vals), spread) if lufs_vals else "  LUFS: 无数据")

    if max_lufs_spread is not None and spread > max_lufs_spread:
        print("  ❌ LUFS 极差 %.2f > 允许的 %.2f" % (spread, max_lufs_spread))
        return 1
    if bad:
        print("  ❌ 有削波文件")
        return 1
    print("  ✅ 全部通过")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="append", default=[])
    ap.add_argument("--max-lufs-spread", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        fails = selftest()
        print("\n== 自检失败 %d 项 ==" % len(fails))
        return 1 if fails else 0

    if a.check:
        rc = 0
        for d in a.check:
            r = check_dir(d, a.max_lufs_spread)
            if r == 1:
                rc = 1
            elif r == 2:
                rc = 2
        return rc

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
