#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_audio.py —— 修掉 NHK 源流里的**坏帧爆裂**（PCM 层）

🔴 背景（2026-10-23 实测）：
  20261016de60356 的慢速音，从 HLS 抓下来就是坏的：
    · 真峰值 **+74.7 dBFS**（比满刻度大 5800 倍）
    · 0.26% 的样本超出满刻度，分布在 **183 段**短爆裂里
    · 解码时长 43.73 s，比 m3u8 声明的 46.16 s **少 2.4 秒**（坏帧导致丢帧）
  ⇒ 重抓 3 次**字节完全一致** ⇒ 坏的是 **NHK 源流本身**，
    换落盘方式（copy/adts/mp4）、直接重编（64k/96k）都救不了
    ⇒ 只能在 **PCM 层修**。

修法（按「爆裂落在停顿里」这个事实设计）：
  1. 解码成 f32
  2. 标出 |x| > 阈值 的样本，**合并**间隔 < 5 ms 的相邻段（一个爆裂簇）
  3. 每个簇：看它左右各 30 ms 的能量
       · 周围本来就是静音（|x| 很小）⇒ **整簇置 0**（自然，因为那就是停顿）
       · 周围有声 ⇒ **线性插值**接回去（不引入咔哒）
  4. 重新编码成 AAC（96k + 1.5 dB 余量，跟 fast 那套一致）

判据（修完必须复查，**不靠"看着像"**）：
  · 真峰值 ≤ +0.05 dBFS
  · 超满刻度样本占比 ≤ 0.05%
  · 平顶占比 ≤ 5%
  · 时长与修前的差 ≤ 0.5 s（不能把内容剪掉）
  · 有效语音段（|x| > 0.01 的部分）不得减少超过 1%

用法：
  python3 scripts/repair_audio.py data/audio/20261016de60356.m4a
  python3 scripts/repair_audio.py <src> --out <dst>
"""
import argparse
import math
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

SR = 48000
CLIP_LIMIT = 1.0          # 满刻度
GAP_TOL = int(0.005 * SR)  # 5 ms 内的间隔算同一簇
CTX = int(0.030 * SR)      # 判定「周围是不是静音」的窗口 30 ms
SILENT = 0.02              # |x| 低于这个算静音


def decode_f32(path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le",
                        "-ac", "1", "-ar", str(SR), "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    d = p.stdout
    n = len(d) // 4
    if n == 0:
        raise SystemExit("❌ 解码出 0 个样本：%s" % path)
    return list(struct.unpack("<%df" % n, d[:n * 4]))


def encode(v, out, bitrate="96k"):
    raw = struct.pack("<%df" % len(v), *v)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
           "-i", "-", "-c:a", "aac", "-b:a", bitrate, "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", out]
    p = subprocess.run(cmd, input=raw, capture_output=True, timeout=600)
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 20000:
        raise SystemExit("❌ 编码失败：%s" % p.stderr.decode()[:200])


def encode_with_headroom(v, out, bitrate="96k", target_db=-1.5, tries=4):
    """给**编码器的重建过冲**留余量。

    🔴 实测：把峰值 -0.01 dBFS 的 PCM 编码成 96k AAC，落盘真峰值变成 **+1.75 dBFS**
       ⇒ 编码器的过冲能到 +1.8 dB ⇒ 必须先降增益再编码。
    ⇒ 迭代：编码 → 量落盘峰值 → 不合格就再降 → 最多 tries 次。
    """
    # 🔴 头room 要**算**，不能拍：
    #    目标 = 落盘峰值 ≈ target_db（给编码器过冲留余量）
    #    起始增益 = target_db - 当前 PCM 峰值
    pk = max(abs(x) for x in v) or 1.0
    pk_db = 20.0 * math.log10(pk) if pk > 0 else -999.0
    g_db = target_db - pk_db
    for i in range(tries):
        g = 10.0 ** (g_db / 20.0)
        vv = [x * g for x in v]
        encode(vv, out, bitrate)
        back = decode_f32(out)
        s = stats(back)
        print("   第%d次：整体降 %.1f dB → 落盘真峰值 %.2f dBFS" % (i + 1, g_db, s["peak_db"]))
        if s["peak_db"] <= 0.05 and s["over_pct"] <= 0.05:
            return g_db, s
        g_db -= 1.5
    return g_db, s


try:
    import numpy as _np
except Exception:
    _np = None


def stats(v):
    """峰值 / 超限占比 / 有效语音占比

    🔴 用 numpy：纯 Python 在 800 万样本上 max(abs(x)) 要 ~10 秒，
       40 期跑下来光这一项就 10 分钟（实测）。
    """
    if _np is not None:
        a = _np.asarray(v, dtype=_np.float32)
        pk = float(_np.abs(a).max())
        over = 100.0 * float((_np.abs(a) > CLIP_LIMIT).sum()) / len(a)
        speech = float((_np.abs(a) > 0.01).sum()) / len(a)
    else:
        pk = max(abs(x) for x in v)
        over = 100.0 * sum(1 for x in v if abs(x) > CLIP_LIMIT) / len(v)
        speech = sum(1 for x in v if abs(x) > 0.01) / len(v)
    return {"peak": pk, "peak_db": 20 * math.log10(pk) if pk > 0 else -999.0,
            "over_pct": over, "speech_pct": speech * 100.0, "n": len(v)}


def clusters(v):
    """返回 [(a, b)] 爆裂簇（已合并 <5ms 间隔）"""
    n = len(v)
    out = []
    if _np is not None:
        a = _np.abs(_np.asarray(v, dtype=_np.float32))
        over = _np.flatnonzero(a > CLIP_LIMIT)
        if len(over) == 0:
            return out
        start = prev = int(over[0])
        for idx in over[1:]:
            i = int(idx)
            if i - prev <= 1:
                prev = i
                continue
            # 收尾当前簇
            if start and (start - out[-1][1] <= GAP_TOL if out else False):
                out[-1] = (out[-1][0], prev + 1)
            else:
                out.append((start, prev + 1))
            start = prev = i
        if out and start - out[-1][1] <= GAP_TOL:
            out[-1] = (out[-1][0], prev + 1)
        else:
            out.append((start, prev + 1))
        return out
    i = 0
    while i < n:
        if abs(v[i]) > CLIP_LIMIT:
            j = i
            while j < n and abs(v[j]) > CLIP_LIMIT:
                j += 1
            if out and i - out[-1][1] <= GAP_TOL:
                out[-1] = (out[-1][0], j)
            else:
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def repair(v, verbose=True):
    cl = clusters(v)
    if not cl:
        return v, 0
    n = len(v)
    out = list(v)
    replaced = 0
    zeroed = 0
    for a, b in cl:
        lo = max(0, a - CTX)
        hi = min(n, b + CTX)
        ctx = [abs(x) for x in v[lo:a]] + [abs(x) for x in v[b:hi]]
        peak_ctx = max(ctx) if ctx else 0.0
        if peak_ctx < SILENT:
            # 周围是静音 ⇒ 整簇置 0（那本来就是停顿）
            for i in range(a, b):
                out[i] = 0.0
            zeroed += 1
        else:
            # 周围有声 ⇒ 线性插值接回去
            x0 = v[a - 1] if a > 0 else 0.0
            x1 = v[b] if b < n else 0.0
            L = b - a
            for k in range(L):
                out[a + k] = x0 + (x1 - x0) * (k + 1) / float(L + 1)
        replaced += b - a
    if verbose:
        print("   爆裂簇 %d 个（其中 %d 个落在静音里 → 置 0）｜替换样本 %d 个 (%.3f s)" %
              (len(cl), zeroed, replaced, replaced / float(SR)))
    return out, len(cl)


def verify(v_before, out_path, s1):
    s0 = stats(v_before)
    print("   修前：真峰值 %.2f dBFS｜超1.0 %.3f%%｜有效语音 %.2f%%｜时长 %.2fs" %
          (s0["peak_db"], s0["over_pct"], s0["speech_pct"], s0["n"] / float(SR)))
    print("   修后(PCM，降增益前)：真峰值 %.2f dBFS｜超1.0 %.3f%%｜有效语音 %.2f%%｜时长 %.2fs" %
          (s1["peak_db"], s1["over_pct"], s1["speech_pct"], s1["n"] / float(SR)))
    bad = []
    if s1["peak_db"] > 0.05:
        bad.append("真峰值仍 > 0.05 dBFS")
    if s1["peak_db"] < -3.0:
        bad.append("峰值降太多（%.2f dBFS）⇒ 整期被压哑了" % s1["peak_db"])
    if s1["over_pct"] > 0.05:
        bad.append("超满刻度样本仍 > 0.05%")
    if abs(s1["n"] - s0["n"]) / float(s0["n"]) > 0.01:
        bad.append("时长变了 >1%")
    if s1["speech_pct"] < s0["speech_pct"] * 0.99:
        bad.append("有效语音少了 >1%（内容被剪掉了？）")
    # 落地文件再验一次（编码器自己也会加一点过冲）
    back = decode_f32(out_path)
    s2 = stats(back)
    print("   落盘复验：真峰值 %.2f dBFS｜超1.0 %.3f%%｜时长 %.2fs" %
          (s2["peak_db"], s2["over_pct"], s2["n"] / float(SR)))
    if s2["peak_db"] > 0.05:
        bad.append("落盘后真峰值 > 0.05 dBFS（编码器过冲）")
    if s2["over_pct"] > 0.05:
        bad.append("落盘后超满刻度 > 0.05%")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out")
    ap.add_argument("--replace", action="store_true", help="就地替换源文件")
    a = ap.parse_args()
    out = a.out or (a.src + ".repaired.m4a")
    print("== 修 %s ==" % a.src)
    v0 = decode_f32(a.src)
    print("   原始：%d 样本 (%.2f s)" % (len(v0), len(v0) / float(SR)))
    v1, ncl = repair(v0)
    if ncl == 0:
        print("   ✅ 没有爆裂簇，不用修")
        return 0
    # 🔴 内容判据必须用**降增益之前**的数：降增益会把一部分样本压到 0.01 阈值以下
    #    ⇒ 那是「有意为之的响度调整」，不是「内容丢了」⇒ 两个维度要分开判
    content_after_repair = stats(v1)
    g_db, s2 = encode_with_headroom(v1, out)
    bad = verify(v0, out, content_after_repair)
    if bad:
        print("   ❌ 修完仍不合格：%s" % "；".join(bad))
        return 1
    print("   ✅ 修好并通过复验：%s（%d KB）" % (out, os.path.getsize(out) // 1024))
    if a.replace:
        os.replace(out, a.src)
        print("   已就地替换 %s" % a.src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
