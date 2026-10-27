#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量：mp3 → MiniMax ASR → 日文稿。并发执行。"""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT = "/workspace/bsj/data/asr"
os.makedirs(OUT, exist_ok=True)
WORK = "/workspace/bsj/data/_work"
os.makedirs(WORK, exist_ok=True)

def asr_one(ep_no, mp3_url, deck_id=0, tries=3):
    dst = f"{OUT}/ep{ep_no:03d}.json"
    if os.path.exists(dst) and os.path.getsize(dst) > 500:
        try:
            d = json.loads(open(dst, encoding="utf-8").read())
            if d.get("transcription"):
                return (ep_no, "cached")
        except Exception:
            pass
    mp3 = f"{WORK}/ep{ep_no:03d}.mp3"
    try:
        if not (os.path.exists(mp3) and os.path.getsize(mp3) > 100000):
            r = subprocess.run(["curl", "-sSL", "--max-time", "180", "-o", mp3, mp3_url],
                               capture_output=True, timeout=200)
            if r.returncode != 0 or not os.path.exists(mp3) or os.path.getsize(mp3) < 100000:
                return (ep_no, "dl-fail")
        last = ""
        for attempt in range(tries):
            try:
                up = subprocess.run(["mcode-tools", "upload_temp_url", mp3],
                                    capture_output=True, timeout=600)
                url = json.loads(up.stdout.decode())["temp_url"]
                args = json.dumps({"audio_info": {"url": url, "mime_type": "audio/mpeg",
                                 "prompt": "日本語の音声です。聞こえた日本語を一文字も省略せず、句読点を付けて書き起こしてください。日本語のみを出力。"}})
                r = subprocess.run(["mcode-tools", "connector", "call", "connector__matrix__listen_audio",
                                    "--args", args], capture_output=True, timeout=1500)
                out = r.stdout.decode("utf-8", "replace")
                open(dst, "w", encoding="utf-8").write(out)
                d = json.loads(out)
                n = len(d.get("transcription") or "")
                if n > 100:
                    os.remove(mp3)
                    return (ep_no, f"ok {n}")
                last = str(d.get("message"))[:80]
            except Exception as e:
                last = f"{type(e).__name__} {str(e)[:60]}"
            time.sleep(8 + 12 * attempt)
        return (ep_no, f"fail {last}")
    except Exception as e:
        return (ep_no, f"ERR {type(e).__name__} {str(e)[:60]}")

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/workspace/bsj/data/aligned.json"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    rows = json.load(open(src, encoding="utf-8"))
    # 合并 iTunes 200 集（补齐没有 jiten 词表的）
    itu = [r for r in json.load(open("/workspace/bsj/data/ep_0.json", encoding="utf-8"))["results"]
           if r.get("wrapperType") == "podcastEpisode"]
    import re
    have = {r["itunesEpNo"] for r in rows}
    for e in itu:
        m = re.match(r"\s*(\d+)", e.get("trackName") or "")
        if not m:
            continue
        n = int(m.group(1))
        if n in have:
            continue
        have.add(n)
        rows.append({"itunesEpNo": n, "deckId": 0, "mp3": e["episodeUrl"],
                     "jitenTitle": e["trackName"], "sentenceCount": 0})
    rows = [r for r in rows if r.get("mp3")]
    rows.sort(key=lambda r: -r["itunesEpNo"])
    if limit:
        rows = rows[:limit]
    print(f"待处理 {len(rows)} 集，并发 {workers}", flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(asr_one, r["itunesEpNo"], r["mp3"], r.get("deckId", 0)): r for r in rows}
        for fu in as_completed(futs):
            ep, st = fu.result()
            done += 1
            print(f"[{done}/{len(rows)}] Ep{ep} {st}  ({time.time()-t0:.0f}s)", flush=True)
    print(f"完成 {time.time()-t0:.0f}s")

main()
