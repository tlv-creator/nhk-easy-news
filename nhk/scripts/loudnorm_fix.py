#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原速音声（fast）响度归一化
========================================================================
为什么需要
--------------------------------------------------------------------------
用户反馈「普通语速的声音不响」。实测（2026-10-22）：
    slow mean_volume = -19.7 dB   max_volume =  0.0 dB
    fast mean_volume = -27.2 dB   max_volume = -5.3 dB
⇒ **原速音声平均比慢速小 2.4 ~ 8.9 dB**（人耳约等于「小一半的音量」）。

根因：原速音声是从**电视视频流抽的音轨**，NHK 电视新闻的混音电平
      本来就比广播朗读低一截 —— 不是编码问题，是**源电平问题**。
      （两边的编码参数完全一致：aac / 48000 / stereo / 64kbps）

修法：把每期的 fast 用**线性增益**拉到与**同期 slow** 相同的平均电平，
      同时用 true-peak 限制器保证不削波。

为什么用「线性增益」而不是 loudnorm 动态模式
--------------------------------------------------------------------------
loudnorm 的动态模式会做动态压缩 → 语音的呼吸感被压平。
⇒ 这里用：**测出增益 → 线性 apply → 峰值超了就退到 loudnorm 两遍法**。
   两遍法（measured_*）也是线性增益，音质最好。

用法
--------------------------------------------------------------------------
    python3 loudnorm_fix.py                     # 处理 site/app/media/fast/
    python3 loudnorm_fix.py --target -20         # 统一目标（dB mean）
    python3 loudnorm_fix.py --align-slow         # 每期对齐自己的 slow（默认）
    python3 loudnorm_fix.py --check              # 只体检不改

输出：每个文件的 处理前→处理后 mean_volume / max_volume / 实际增益
"""
import argparse
import json
import os
import re
import subprocess
import sys
import audio_recipe  # noqa: E402  （配方唯一权威）

NHK = "/workspace/nhk"
MEDIA = os.path.join(NHK, "site", "app", "media")


def probe_volume(path):
    """返回 (mean_db, peak_db)；失败返回 (None, None)

    🔴🔴 2026-10-23 修正：**峰值判据不能用 volumedetect 的 max_volume**。
    同一个文件三种测法：
        volumedetect max_volume → **-0.0 dB**  ← 它把溢出 **clamp 到 0**，永远看不到正数
        astats Peak level        → **+0.95 dB** ← 真实削波
        解码原始样本算峰值        → **+2.87 dBFS** ← 最硬的判据
    ⇒ 后果：原来用 `max_volume > 0.05` 判削波 → **恒绿** → 3 期削波的音频被当成已修好交付。
    ⇒ 现在峰值一律用 **astats 的 Peak level**（不被 clamp）。
    """
    try:
        p = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-af", "volumedetect",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=600)
        err = p.stderr
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", err)
        # 🔴 峰值：astats（不 clamp）优先；拿不到才退回 volumedetect（但它最多只能证明「没超 0」）
        peak = None
        try:
            p2 = subprocess.run(
                ["ffmpeg", "-nostdin", "-nostats", "-hide_banner", "-i", path,
                 "-af", "astats=measure_overall=Peak_level:measure_perchannel=none",
                 "-f", "null", "/dev/null"],
                capture_output=True, text=True, timeout=600)
            m2 = re.search(r"Peak level dB:\s*(-?[\d.]+)", p2.stderr)
            if m2:
                peak = float(m2.group(1))
        except Exception:
            pass
        if peak is None:
            x = re.search(r"max_volume:\s*(-?[\d.]+) dB", err)
            peak = (float(x.group(1)) if x else None)
        if not m:
            return None, None
        return float(m.group(1)), peak
    except Exception:
        return None, None


def loudnorm_two_pass(src, dst, target_mean, tp=-1.5):
    """loudnorm 两遍法：先测量，再线性应用。"""
    # ---- 第一遍：测量 ----
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", src, "-af",
         "loudnorm=I=%s:TP=%s:LRA=11:print_format=json" % (target_mean, tp),
         "-f", "null", "/dev/null"],
        capture_output=True, text=True, timeout=900)
    err = p.stderr
    j = err[err.rfind("{"): err.rfind("}") + 1] if "{" in err else ""
    try:
        m = json.loads(j)
        mi = float(m["input_i"]); mt = float(m["input_tp"])
        lra = float(m["input_lra"])
    except Exception:
        return None
    if not mi or mi < -70:          # 静音/无效
        return None
    # ---- 第二遍：应用 ----
    af = ("loudnorm=I=%s:TP=%s:LRA=%s:measured_I=%s:measured_TP=%s:measured_LRA=%s:linear=true"
          % (target_mean, tp, lra, mi, mt, lra))
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-af", af,
         "-c:a", "aac", "-b:a", audio_recipe.AAC_BITRATE, "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", dst],
        capture_output=True, text=True, timeout=1800)
    return m


FORCE = False


def process_one(fast_path, target_mean):
    """返回 (ok, before_mean, after_mean, before_max, after_max, gain)

    🔴 2026-10-22 改用「线性增益 + 限幅器」而不是 loudnorm 两遍法：
       loudnorm 的 linear=true 模式在源动态范围大时**达不到目标电平**（实测 13 个里 5 个没动），
       ⇒ 改用最直接可控的方案：
         gain = 目标 mean - 当前 mean
         若 峰值+gain 会超过 -1.0 dB → 用 alimiter 兜住（limit=0.891 ≈ -1 dB）
    """
    bm, bx = probe_volume(fast_path)
    if bm is None:
        return False, None, None, None, None, None
    gain = target_mean - bm
    # 🔴🔴 2026-10-23：**削波是独立于「响度对齐」的第二个维度**。
    #    原来「增益 < 0.5 dB 就跳过」→ 而削波的几期增益正好是 0.0（响度已对齐）
    #    ⇒ **削波永远不会被修** ⇒ 必须单独判。
    clipped = (bx is not None and bx > 0.05)
    if abs(gain) < 0.5 and not clipped and not FORCE:
        return True, bm, bm, bx, bx, 0.0

    # 🔴🔴 2026-10-23：**AAC 编码的 overshoot 因期而异（实测 1~3 dB）**，
    #    而且瞬态越尖 overshoot 越大（`alimiter` 的 attack 2ms 跟不上 → 限不住）。
    #    ⇒ 参数调不出来（换 attack/limit 各期表现不同）。
    #    ⇒ **通用解法 = 迭代收敛**：处理 → 实测峰值 → 还超就按超限量再降一点增益 → 重做。
    #    （最多 4 轮；每轮都从**原始文件**重做，不是叠加，避免误差累积）
    src = fast_path
    am = ax = None
    for attempt in range(1, 5):
        tmp = fast_path + ".norm.m4a"
        # 峰值是否会削波？
        # 🔴🔴🔴 2026-10-23 修一个「扣了两次」的 bug：
        #   目标**已经**是 `slow - 1.5`（target = sm - 1.5，main() 里定的）
        #   ⇒ 这里如果再减 1.5，就变成 slow - 3.0 ⇒ **系统性低 1.5 dB**
        #   （线上实测：本地 fast -22.8 dB vs 线上 -21.3 dB，正好差 1.5）
        #   ⇒ 「给编码器留余量」这件事由**目标本身**承担（比 slow 低 1.5 dB），
        #      不该在这里再扣一次。真溢出由下面的迭代循环按实测峰值收。
        if bx is not None and (bx + gain) > -1.0:
            af = ("volume=%.2fdB,alimiter=limit=0.794:attack=2:release=50:level=disabled"
                  % gain)
        else:
            af = "volume=%.2fdB" % gain
        try:
            r = subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-af", af,
                 "-c:a", "aac", "-b:a", audio_recipe.AAC_BITRATE,
                 "-ar", str(audio_recipe.AAC_RATE), "-ac", str(audio_recipe.AAC_CHANNELS),
                 "-movflags", "+faststart", tmp],
                capture_output=True, text=True, timeout=1800)
            if not os.path.exists(tmp) or os.path.getsize(tmp) < 10000:
                raise RuntimeError("ffmpeg 输出异常: %s" % (r.stderr or "")[:200])
        except Exception as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            print("      ! %s" % str(e)[:160], flush=True)
            return False, bm, None, bx, None, None

        am, ax = probe_volume(tmp)
        if am is None:
            os.remove(tmp)
            return False, bm, None, bx, None, None
        # 🔴 阈值说明：**0.0 dB = 满刻度，不是削波**
        #    慢速音实测 max_volume 就是 0.0 dB（用户听着正好）→ 原速对齐到 0.0 dB 是对的
        #    只有 > 0.05 dB 才真的溢出削波。
        if ax is not None and ax > 0.05:
            # 还超 → 按超限量再降一点（留 0.5 dB 余量），从**原始文件**重做
            over = ax - 0.5
            os.remove(tmp)
            if attempt < 4:
                print("      · 第 %d 轮峰值仍 %.2f dB → 增益再降 %.2f dB 重做"
                      % (attempt, ax, over), flush=True)
                gain -= over
                continue
            print("      ! 处理 4 轮后峰值仍 %.1f dB，保留原样" % ax, flush=True)
            return False, bm, None, bx, None, None
        os.replace(tmp, fast_path)
        return True, bm, am, bx, ax, am - bm
    return False, bm, None, bx, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=None,
                    help="统一目标 mean_volume(dB)；不给就每期对齐自己的 slow")
    ap.add_argument("--align-slow", action="store_true", default=True,
                    help="每期对齐自己的 slow（默认）")
    ap.add_argument("--check", action="store_true", help="只体检，不改文件")
    ap.add_argument("--dir", default=None,
                    help="要处理的 fast 目录（默认 site/app/media/fast）")
    ap.add_argument("--slow-dir", default=None,
                    help="参照的 slow 目录（默认 site/app/media/slow）")
    ap.add_argument("--only", default=None,
                    help="只处理这些 id（逗号分隔）")
    ap.add_argument("--force", action="store_true",
                    help="强制重编码（哪怕电平已对齐）—— 换码率 64k→96k 时用")
    args = ap.parse_args()
    global FORCE
    FORCE = args.force

    fastdir = args.dir or os.path.join(MEDIA, "fast")
    slowdir = args.slow_dir or os.path.join(MEDIA, "slow")
    only = set(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    if not os.path.isdir(fastdir):
        print("没有 fast 目录: %s" % fastdir)
        return 1

    files = sorted(f for f in os.listdir(fastdir) if f.endswith(".m4a"))
    if only:
        files = [f for f in files if f[:-4] in only]
    if not files:
        print("没有 fast 音频")
        return 0

    print("═══ 原速音声响度体检 %d 个文件 ═══\n" % len(files))
    ok_n = 0
    for f in files:
        fid = f[:-4]
        fp = os.path.join(fastdir, f)
        sp = os.path.join(slowdir, f)
        fm, fx = probe_volume(fp)
        sm, sx = (None, None)
        if os.path.exists(sp):
            sm, sx = probe_volume(sp)
        if fm is None:
            print("  %-20s 读不出电平 ⚠️" % fid, flush=True)
            continue

        if args.target is not None:
            target = args.target
        elif sm is not None:
            # 🔴 2026-10-23：96k AAC 编码仍有约 0.5~1 dB 的 overshoot
            #    → 峰值必须留余量 → **响度目标比 slow 低 1.5 dB**（这是刻意的，不是没对齐）
            #    判据也相对这个目标算，否则会误报「全部超标」。
            target = sm - 1.5
        else:
            target = -20.0        # 没有 slow 参照 → 用经验值 -20 dB

        if args.check:
            gap = (fm - target) if target is not None else None
            # 🔴🔴 削波是**独立于响度对齐**的第二个维度 —— 必须一起报出来
            #    （2026-10-23：响度对齐 ✓ 但 3 期削波，而原来只报响度 → 恒绿）
            clipped = (fx is not None and fx > 0.05)
            ok_gap = (gap is not None and abs(gap) <= 0.5)
            flag = "✓" if (ok_gap and not clipped) else "⚠️"
            why = ""
            if not ok_gap:
                why = "响度未对齐"
            if clipped:
                why = (why + " + " if why else "") + "🔊削波 %.2f dB" % fx
            print("  %s %-20s fast %6.1f dB  目标 %6.1f dB  差 %+5.1f dB  峰值 %6.2f dB %s"
                  % (flag, fid, fm, target, gap if gap is not None else 0,
                     fx if fx is not None else 0.0, why), flush=True)
            continue

        ok, bm, am, bx, ax, gain = process_one(fp, target)
        if ok and gain is not None and abs(gain) > 0.05:
            ok_n += 1
            print("  ✓ %-20s %6.1f → %6.1f dB  (增益 %+5.1f dB, 峰值 %s → %s)"
                  % (fid, bm, am, gain,
                     ("%5.1f" % bx) if bx is not None else "  ?",
                     ("%5.1f" % ax) if ax is not None else "  ?"), flush=True)
        elif ok:
            print("  · %-20s 已在目标电平 %6.1f dB，不用动" % (fid, fm), flush=True)
        else:
            print("  ✗ %-20s 处理失败（%6.1f dB → 保持原样）" % (fid, fm), flush=True)

    if not args.check:
        print("\n  处理了 %d 个文件" % ok_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
