#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_all_audio.py —— 修**所有**源文件里的坏帧（接进每日更新管线）

🔴 为什么需要（2026-10-23 实测）：
  NHK 的慢速音是从 **HLS 的 TS 分片**拼出来的，分片里含 **51 带的 AAC 帧**
  （解码器只支持 45 带）⇒ 解码出来是**超满刻度的垃圾样本**：
    · 实测峰值 **+11 ~ +43 dBFS**（比满刻度大 3~50 倍）
    · 超限样本 0.004% ~ 0.12% ⇒ 用户听到「啪啪啪」的爆音
    · Android MediaPlayer 对坏帧更严 ⇒ 可能整段跳过

  ⇒ 修法：PCM 层把 |x| > 1 的爆裂簇修掉 → 再交给 retranscode 重编。

判据（修完必须复查，不靠「看着像」）：
  · 真峰值 ≤ +0.05 dBFS
  · 超满刻度样本 ≤ 0.05%
  · 有效语音占比不减少超过 1%（内容不能被剪掉）
  · 落盘文件再验一次（编码器自己会加一点过冲）
"""
import glob
import math
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from repair_audio import decode_f32, repair, encode_with_headroom, stats, verify  # noqa: E402

SR = 48000


def peak_db(path):
    v = decode_f32(path)
    return stats(v)


def main():
    files = sorted(glob.glob("/workspace/nhk/data/audio/*.m4a") +
                   glob.glob("/workspace/nhk/data/audio_fast/*.m4a"))
    fixed = skipped = failed = 0
    for f in files:
        try:
            v0 = decode_f32(f)
        except SystemExit as e:
            print("  ❌ 读不出：%s %s" % (os.path.basename(f), e), flush=True)
            failed += 1
            continue
        s0 = stats(v0)
        if s0["peak_db"] <= 0.05:
            skipped += 1
            continue
        v1, ncl = repair(v0, verbose=False)
        if ncl == 0:
            skipped += 1
            continue
        out = f + ".fixed.m4a"
        g_db, _ = encode_with_headroom(v1, out)
        content = stats(v1)
        bad = verify(v0, out, content)
        if bad:
            print("  ❌ %s 修完仍不合格：%s" % (os.path.basename(f), "；".join(bad)), flush=True)
            if os.path.exists(out):
                os.remove(out)
            failed += 1
            continue
        os.replace(out, f)
        fixed += 1
        print("  ✅ %s 修好：峰值 %.2f → %.2f dBFS（%d 个爆裂簇，整体降 %.1f dB）" %
              (os.path.basename(f), s0["peak_db"],
               stats(decode_f32(f))["peak_db"], ncl, -g_db), flush=True)
    print("== 坏帧修复：修了 %d 个，跳过 %d 个，失败 %d 个 ==" % (fixed, skipped, failed),
          flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
