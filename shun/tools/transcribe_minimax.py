#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcribe_minimax.py —— 用 MiniMax 的听写接口逐块转写（拿干净日文 + 分块时间锚点）

为什么不用本地 ASR 的文本：本地 ASR（whisper base）时间准但**字错**（如「シュント」、
漏标点）；MiniMax 的文本准但**没有时间戳**。
⇒ 两者配合：MiniMax 给文本，whisper 给时间，块级对齐（chunk anchor）避免长程漂移。

用法：
    python3 tools/transcribe_minimax.py --audio work/ep13.mp3 \
        --asr work/asr/ep13.json --out work/asr/ep13_mm.json
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

SITE = "https://bo8u69hdw58t4.space.mcode.cn"  # 只作占位，真正用的是 mcode-tools


def cut_chunk(audio, start, end, out):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % start,
           "-to", "%.3f" % end, "-i", audio, "-c:a", "libmp3lame", "-b:a", "64k",
           "-ar", "44100", "-ac", "1", out, "-y"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def upload(path):
    p = subprocess.run(["mcode-tools", "upload-temp-url", path],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return json.loads(p.stdout.decode())["temp_url"]


def listen(url):
    args = json.dumps({"audio_info": {"url": url, "mime_type": "audio/mpeg"}},
                      ensure_ascii=False)
    p = subprocess.run(["mcode-tools", "connector", "call",
                        "connector__matrix__listen_audio", "--args", args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    d = json.loads(p.stdout.decode())
    if d.get("code") not in (0, None):
        raise RuntimeError("listen_audio failed: %s" % d.get("message"))
    return (d.get("transcription") or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--asr", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chunk-sec", type=float, default=70.0)
    args = ap.parse_args()

    asr = json.load(open(args.asr))
    segs = asr["segments"]

    chunks = []
    cur = []
    for s in segs:
        cur.append(s)
        if s["end"] - cur[0]["start"] >= args.chunk_sec:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    out = []
    tmpd = tempfile.mkdtemp(prefix="mmchunk_")
    for i, ch in enumerate(chunks):
        start = max(0.0, ch[0]["start"] - 0.25)
        end = min(asr["duration"], ch[-1]["end"] + 0.25)
        path = os.path.join(tmpd, "c%03d.mp3" % i)
        cut_chunk(args.audio, start, end, path)
        url = upload(path)
        try:
            text = listen(url)
        except Exception as e:  # noqa: BLE001
            print("  ❌ chunk %d 失败: %s" % (i, e), file=sys.stderr)
            text = ""
        out.append({"start": round(start, 3), "end": round(end, 3),
                    "text": text, "idx": i})
        print("  chunk %2d [%7.2f-%7.2f] %d 字" % (i, start, end, len(text)),
              file=sys.stderr)
        os.remove(path)
    os.rmdir(tmpd)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"audio": args.audio, "chunks": out}, f, ensure_ascii=False, indent=1)
    print("✅ %d 块 → %s" % (len(out), args.out), file=sys.stderr)


if __name__ == "__main__":
    main()
