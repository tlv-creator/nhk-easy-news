#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按时间轴切音频的**共用实现**（两个测试脚本都用它 ⇒ 不会出现「一处修了另一处没修」）。

🔴🔴 **两个坑（都真踩过）**：
  ① ffmpeg 的 `-to` 是**源时间轴上的绝对位置**（实测：`-ss 100 -to 105` = 5 秒）
     ⇒ 上界必须用「轨内绝对偏移」去 clamp
  ② 🔴 **上界不能拿「轨内剩余时长」去 clamp**（那是长度，不是位置！）
     ⇒ 靠近轨尾的句子会被 clamp 成 `le < ss` ⇒ **切出空文件**
     ⇒ 而脚本若不检查 ffmpeg 返回码 ⇒ **静默产出空切片** ⇒ ASR 返回空
     ⇒ 判据看起来是「音频没念这句」，真相是「我切了个空文件」
     ⇒ 我第一版 13 句「两边都不命中」全是这个假象

⇒ 本模块：**切片必须存在且时长 > 0.2s**，否则返回 ok=False（不许计入统计）
"""
import os
import subprocess


def sh(cmd, timeout=600):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def track_of(units, tag, t):
    """时间 t（全书秒）落在哪条轨、轨内起点、该轨总长"""
    for book in units:
        if book["tag"] != tag:
            continue
        for tr in book["tracks"]:
            off, dur = tr["offset"], tr["dur"]
            if off <= t < off + dur + 1.0:
                return tr["path"], max(0.0, t - off), dur
    return None, None, None


def clip_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def cut_with_pad(units, tag, s, e, out_path, pad_pre=0.9, pad_post=1.6, force=False):
    """切 [s-pad_pre, e+pad_post]（全书秒）到 out_path。
    返回 (path, ok, why)；ok=False ⇒ 切片没生成/是空的 ⇒ **不许计入统计**"""
    path, ls, tdur = track_of(units, tag, s)
    if not path:
        return None, False, "找不到对应音轨"
    ss = max(0.0, ls - pad_pre)
    le = min(ls + pad_pre + max(0.0, e - s) + pad_post, tdur)   # ✅ 用「轨内绝对上界」
    if le <= ss:
        le = min(tdur, ss + 1.0)
    if le <= ss:
        return None, False, "区间为空"
    if os.path.exists(out_path) and not force:
        d = clip_dur(out_path)
        if d > 0.2:
            return out_path, True, ""
        os.remove(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    r = sh(f'ffmpeg -v error -y -ss {ss:.3f} -to {le:.3f} -i "{path}" '
           f'-ac 1 -ar 16000 -b:a 64k "{out_path}"', 600)
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        return None, False, f"ffmpeg 未产出（rc={r.returncode} {r.stderr[:80]}）"
    d = clip_dur(out_path)
    if d <= 0.2:
        return None, False, f"切片时长 {d:.2f}s（空的）"
    return out_path, True, ""
