#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr.py —— 本地 ASR（faster-whisper / CTranslate2），产出带时间戳的日文分段

为什么自己写：原 tools/asr.py 随沙箱重置丢失，本脚本按**已发布数据的契约**重建：
    分段 = [{start, end, text}]，start/end 是**秒**（float）
    text  = 该时间段的日文原文（不带标点由 whisper 给出；标点由后处理补）

用法：
    python3 tools/asr.py --audio work/ep13.mp3 --out work/asr/ep13.json
"""
import argparse
import json
import os
import sys

MODEL_DIR = "/workspace/models/fw-base"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--initial-prompt", default="")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=2)
    segments, info = model.transcribe(
        args.audio,
        language="ja",
        task="transcribe",
        beam_size=args.beam,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
        initial_prompt=args.initial_prompt or None,
    )

    out = []
    for seg in segments:
        words = []
        for w in (seg.words or []):
            words.append({"s": round(float(w.start), 3), "e": round(float(w.end), 3),
                          "w": (w.word or "").strip()})
        out.append({
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "text": (seg.text or "").strip(),
            "words": words,
        })
        print("  [%7.2f - %7.2f] %s" % (seg.start, seg.end, (seg.text or "").strip()),
              file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"audio": args.audio, "duration": float(info.duration),
                   "segments": out}, f, ensure_ascii=False, indent=1)
    print("✅ %d 段 → %s（音频 %.1fs）" % (len(out), args.out, info.duration),
          file=sys.stderr)


if __name__ == "__main__":
    main()
