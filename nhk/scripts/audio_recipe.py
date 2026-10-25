#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_recipe.py —— **NHK 音频配方的唯一权威**（2026-10-23 建）

🔴🔴🔴 **为什么要有这个文件**（Shun 那句「加一道新闸门比加一个指标危险」把我扫出来的）：
今天我花大力气把主管线的码率从 **64k 改成 96k**（64k 的 AAC 重建过冲会把波形压扁 ⇒ 削波），
**结果同一天的旁路 `scripts/refetch_audio.py` 里还写着 `-b:a 64k`** ⇒
**每日 21:00 的自动更新会自己把削波音频推上线**。

⇒ **通则**：
> **同一个配方（码率 / 采样率 / 声道 / 阈值）在 N 个入口各写了一遍 ⇒ 改了一处 = 修了 1/N。**
> ⇒ 修法：**配方收敛到一个地方，各入口都从它取**。

🔴 **比「收敛成一个函数」更硬的一层**：把配方放进**一个文件**，
于是「Python 入口」和「shell 入口」也能共用同一份（shell 侧用 `python3 audio_recipe.py --print-args`）。

**⇒ 所有交付音频的编码入口都必须引用本文件**（`scripts/check_audio_recipe.py` 守着这条）。
"""

import os
import subprocess
import sys

# ── 配方本体（改这里就只有一处） ─────────────────────────────────────────
AAC_BITRATE = "96k"        # 🔴 64k 会削波（实测 13 期里 10 期峰值 +0.6~+2.0 dBFS）⇒ 96k + 留 1.5 dB 余量
AAC_RATE = 48000           # 输出采样率
AAC_CHANNELS = 2           # 输出声道（用户设备播的是立体声 ⇒ 判据也按立体声）
AAC_CODEC = "aac"

# 响度目标：原速音对齐到「同期慢速音 − 1.5 dB」
#   🔴 1.5 dB 只在**一处**扣（目标里定义过 ⇒ 滤镜里就不要再减一次）
LOUDNESS_OFFSET_DB = -1.5
LOUDNESS_TOL_DB = 0.5      # 目标与实测的允许差

# 闸门档
DUR_TOL = 0.5              # 时长闸门：|成品时长 − 源时长| ≤ 0.5s
PEAK_LIMIT_DB = 0.05       # 峰值上限（原生逐声道口径）
FLAT_RATIO_LIMIT = 5.0     # 平顶占比上限（%）
OVER_PCT_LIMIT = 0.05      # 超满刻度样本占比上限（%），**4.0 口径**（1.0 口径合法内容就 0.942% ⇒ 恒红）
OVER_CLIP = 4.0            # 🔴 超满刻度的**口径**（不是阈值）：1.0 会恒红；4.0 对正常内容恒为 0.000%
OVER_MILD = 1.2            # 轻微超限口径（**只做警告**，与合法编码过冲数值不可分）

# 管线顺序（每日 cron 照这个走，别自己改顺序）
PIPELINE_ORDER = [
    "scripts/repair_all_audio.py",   # ① 修坏帧（PCM 层，源流自带 51 带 AAC 帧）
    "retranscode.py",                 # ② 96k 重编（原生逐声道峰值守卫 + 时长闸门）
    "scripts/loudnorm_fix.py",        # ③ 原速音对齐（slow − 1.5 dB）
    "scripts/audio_health.py",        # ④ 四维体检（削平 / 垃圾 / 过冲 / 时长）
]


def ffmpeg_encode_args():
    """交付音频的 ffmpeg 编码参数（**唯一一份**）"""
    return ["-c:a", AAC_CODEC, "-b:a", AAC_BITRATE, "-ar", str(AAC_RATE),
            "-ac", str(AAC_CHANNELS), "-movflags", "+faststart"]


def ffmpeg_decode_args():
    """解码成原始样本测峰值/超限（**不要带 -ac 1，也不要带 -ar**）：
    🔴 `-ac 1` 下混是**另一个信号** ⇒ 比设备实际播的高 3 dB
    🔴 `-ar` 重采样器**自己会造出**超满刻度样本（0.1009% → 0.1656%）
    """
    return ["-f", "f32le", "-"]


def duration(path):
    """容器时长（秒）；取不到返回 None"""
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    try:
        return float(p.stdout.decode().strip())
    except Exception:
        return None


def dur_ok(out_dur, src_dur, tol=None):
    """时长闸门：**「峰值正常」≠「文件正常」**（被截断一半的输出，峰值照样看着正常）"""
    tol = DUR_TOL if tol is None else tol
    if out_dur is None or src_dur is None:
        return False
    return abs(out_dur - src_dur) <= tol


def main():
    import argparse
    ap = argparse.ArgumentParser(description="NHK 音频配方（唯一权威）")
    ap.add_argument("--print-args", action="store_true", help="打印交付音频的 ffmpeg 编码参数")
    ap.add_argument("--duration", help="打印某个音频文件的时长（秒）")
    ap.add_argument("--dur-tol", action="store_true", help="打印时长容差")
    ap.add_argument("--check-dur", nargs=2, metavar=("OUT", "SRC"),
                    help="对两个文件做时长闸门检查，差 > 容差则 exit 1")
    a = ap.parse_args()
    if a.print_args:
        print(" ".join(ffmpeg_encode_args()))
        return 0
    if a.duration:
        d = duration(a.duration)
        print(d if d is not None else "NA")
        return 0 if d is not None else 2
    if a.dur_tol:
        print(DUR_TOL)
        return 0
    if a.check_dur:
        o, s = duration(a.check_dur[0]), duration(a.check_dur[1])
        ok = dur_ok(o, s)
        print("时长闸门：成品 %.3fs / 源 %.3fs / 容差 %.2fs ⇒ %s"
              % (o or -1, s or -1, DUR_TOL, "OK" if ok else "❌ 不合格"))
        return 0 if ok else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
