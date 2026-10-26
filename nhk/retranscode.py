#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 HLS 拼来的 m4a 重新编码成干净的 AAC（原生 MediaPlayer 对坏帧更严）。

🔴🔴🔴 2026-10-23 重写（原来这行是 64k，害得整站 10 期削波）：
  ① **64k → 96k**：实测 64k AAC 的重建过冲能把峰值推到 **+2 dBFS**（真削波），
     而 NHK 自己的源流本来就是 64k（`CODECS="mp4a.40.5"`）⇒ 64k 是在「差码率上再差码率」。
  ② **编码后必须量峰值**：峰值 > 0.05 dBFS 就按超限量再降一点增益重做（最多 3 轮）。
     🔴 判据用**解码后的原始样本**算真峰值（astats 也行，但它 clamp 到 0 附近时看不准）。
  ③ **不降低整体响度目标**：慢速音是「响度基准」，原速音要对齐它；
     这里只做「必要时微降 0.5~1 dB 去掉过冲」，且 loudnorm_fix 会在**之后**重新对齐。
"""
import json
import math
import os
import struct
import subprocess
import sys
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import audio_recipe  # noqa: E402

DATA = "/workspace/nhk/data"
SITE = "/workspace/nhk/site/app"
BITRATE = "96k"
PEAK_LIMIT = 0.05          # dBFS


try:
    import numpy as _np
except Exception:
    _np = None


def true_peak_db(path):
    """真峰值：解码成 f32 逐样本取 max（astats 会被 clamp，量不准）

    🔴🔴 **口径必须是「原生逐声道」**（2026-10-23 修）：
       原来用 `-ac 1`（单声道下混）⇒ **比用户实际听到的高 3 dB**
       ⇒ 阈值 0.05 dBFS 在这个口径下**永远达不到** ⇒ 27 个文件白跑 4 轮后被「保留原样」
       （用户设备播的是**立体声原文件** ⇒ 判据必须落在原生逐声道上）
    🔴🔴 **也不能带 `-ar 48000`**：重采样器自己会造出超满刻度样本
       （Shun 实测：同一文件 原生率 0.1009% → 重采样后 0.1656%，放大 1.6 倍）
    """
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-f", "f32le",
                        "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600)
    d = p.stdout
    n = len(d) // 4
    if n == 0:
        return None
    if _np is not None:                      # 🔴 numpy：800 万样本省 ~8 秒
        a = _np.frombuffer(d[:n * 4], dtype=_np.float32)
        if len(a) % 2 == 0:                  # 🔴 原生逐声道：立体声取每帧的最大幅度
            a = _np.abs(a.reshape(-1, 2)).max(axis=1)
        else:
            a = _np.abs(a)
        pk = float(a.max())
    else:
        v = struct.unpack("<%df" % n, d[:n * 4])
        pk = max(abs(x) for x in v)
    return (20.0 * math.log10(pk)) if pk > 0 else -999.0


def duration_sec(path):
    """容器时长（秒）——用于「输出被截断」的闸门"""
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    try:
        return float(p.stdout.decode().strip())
    except Exception:
        return None


def encode_clean(src, dst):
    """编码 + 峰值守卫；返回 (ok, 实际降了多少 dB, 峰值)

    🔴🔴 2026-10-23 修一个自伤 bug：
       原来「降增益重做」是**另起一条 ffmpeg 命令**，但下一轮循环又从**原始文件**无滤镜重编
       ⇒ 降掉的增益每次都被丢弃 ⇒ 峰值纹丝不动（实测「降 2.32 dB → 峰值还是 2.82 dB」）
       ⇒ 正确做法：**把累计增益带进下一轮**。
    """
    cut = 0.0
    for attempt in range(4):
        tmp = dst + ".new.m4a"
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src]
        if abs(cut) > 0.01:
            cmd += ["-af", "volume=%.2fdB" % cut]
        # 🔴 配方只写一遍 → 从 audio_recipe 取（scripts/check_audio_recipe.py 守着这条）
        cmd += audio_recipe.ffmpeg_encode_args()
        cmd += [tmp]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 20000:
            print("  FAIL %s: %s" % (os.path.basename(src), r.stderr[:140]), flush=True)
            if os.path.exists(tmp):
                os.remove(tmp)
            return False, 0.0, None
        # 🔴🔴 **时长闸门**：截断的输出，峰值会「看起来很正常」⇒ 峰值守卫放行它
        #   （2026-10-23 实测：kill 掉写了一半的文件 782 KB 只解出 0.17 秒，峰值守卫照样放行）
        src_dur = duration_sec(src)
        out_dur = duration_sec(tmp)
        if src_dur and out_dur and abs(src_dur - out_dur) > 0.5:
            print("  ⚠ 时长对不上（源 %.2fs vs 新 %.2fs）⇒ 丢弃" % (src_dur, out_dur), flush=True)
            os.remove(tmp)
            return False, 0.0, None
        pk = true_peak_db(tmp)
        if pk is None:
            print("  FAIL（读不出峰值）%s" % os.path.basename(src), flush=True)
            os.remove(tmp)
            return False, 0.0, None
        if pk <= PEAK_LIMIT:
            os.replace(tmp, dst)
            return True, cut, pk
        over = pk - PEAK_LIMIT - 0.5      # 留 0.5 dB 余量
        os.remove(tmp)
        if attempt == 3:
            print("  ⚠ 4 轮后峰值仍 %.2f dB，保留原样" % pk, flush=True)
            return False, 0.0, pk
        cut -= over
        print("    · 第 %d 轮峰值 %.2f dB → 累计降 %.2f dB 重做" % (attempt + 1, pk, -cut),
              flush=True)
    return False, 0.0, None


def main():
    eps = json.loads(open(os.path.join(SITE, "data/episodes.js"), encoding="utf-8").read()
                     .split("=", 1)[1].rstrip().rstrip(";"))
    n = 0
    bad = []
    for e in eps:
        for src_dir, dst_dir in (("audio", "media/slow"), ("audio_fast", "media/fast")):
            src = os.path.join(DATA, src_dir, e["id"] + ".m4a")
            if not os.path.exists(src):
                continue
            dst = os.path.join(SITE, dst_dir, e["id"] + ".m4a")
            ok, cut, pk = encode_clean(src, dst)
            if not ok:
                bad.append("%s/%s" % (src_dir, e["id"]))
                continue
            n += 1
            print("  [%d] %s/%s  %dKB  峰值 %.2f dB%s" %
                  (n, src_dir, e["id"], os.path.getsize(dst) // 1024, pk,
                   ("（降 %.1f dB）" % cut) if abs(cut) > 0.01 else ""), flush=True)
    print("== 重转码完成 %d 个，失败 %d 个 %s ==" % (n, len(bad), bad), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
