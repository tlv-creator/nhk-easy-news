#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实测每条官方音频的「最后说话时刻」（speech end）。

🔴 为什么需要它：ASR 真值的**最后一行没有结束时间**（后面没有行了）
   ⇒ 对齐器只能猜（用语速外推）⇒ 实测会把末句拖到「音频已经念完的静音区」
   ⇒ 端到端验证直接抓到：otoshimono #41 / ryosan-christmas #21 的高亮落在静音里
🔴 正确做法：**用能量分析测事实**（ffmpeg silencedetect）⇒ 不猜。
   尾静音起点 = 最后一段话的结束时刻。
"""
import json, os, re, subprocess, sys

SIL = re.compile(r"silence_start:\s*(-?[\d.]+)")
SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def speech_end(path, thresh_db=-35, min_dur=1.5):
    """返回 (最后说话时刻, 轨长, 静音段列表)"""
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip() or 0)
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path,
                        "-af", f"silencedetect=n={thresh_db}dB:d={min_dur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    out = r.stderr
    starts = [float(x) for x in SIL.findall(out)]
    ends = [float(x) for x in SIL_END.findall(out)]
    segs = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else dur
        segs.append((s, e))
    if not segs:
        return dur, dur, []
    last_silence_start = segs[-1][0]
    # 末尾静音：最后一段静音从 last_silence_start 开始 ⇒ 说话到那儿为止
    if last_silence_start > 0.5:
        end = last_silence_start
    else:
        end = dur
    return round(end, 2), round(dur, 2), segs


def main():
    units_path = sys.argv[1]
    out_path = sys.argv[2]
    units = json.load(open(units_path))
    res = {}
    print(f"{'tag':28} {'轨长':>8} {'说话到':>8}  尾静音")
    for b in units:
        tag = b["tag"]
        paths = [t["path"] for t in b["tracks"]]
        if len(paths) != 1:
            print(f"  ⏭ {tag:26} 跨 {len(paths)} 轨（本工具只处理单轨）")
            continue
        p = paths[0]
        if not os.path.exists(p):
            print(f"  ⏭ {tag:26} 缺音频 {p}")
            continue
        se, dur, segs = speech_end(p)
        res[tag] = {"speech_end": se, "dur": dur, "audio": p}
        print(f"  {tag:28} {dur:8.2f} {se:8.2f}  尾静音 {dur-se:.2f}s")
    json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(f"\n写到 {out_path}（{len(res)} 本）")


if __name__ == "__main__":
    main()
