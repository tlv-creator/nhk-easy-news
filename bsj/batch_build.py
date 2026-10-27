#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量构建 HTML：ASR 日文 → 注音 + 翻译 → HTML。"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/workspace/bsj")
from build import build

ASR_DIR = "/workspace/bsj/data/asr"
OUT_DIR = "/workspace/bsj/out"
os.makedirs(OUT_DIR, exist_ok=True)

def load_index():
    idx = {}
    for r in json.load(open("/workspace/bsj/data/aligned.json", encoding="utf-8")):
        idx[r["itunesEpNo"]] = r
    for e in [x for x in json.load(open("/workspace/bsj/data/ep_0.json", encoding="utf-8"))["results"]
              if x.get("wrapperType") == "podcastEpisode"]:
        m = re.match(r"\s*(\d+)", e.get("trackName") or "")
        if not m:
            continue
        n = int(m.group(1))
        idx.setdefault(n, {"itunesEpNo": n, "deckId": 0, "jitenTitle": e["trackName"]})
    return idx

def main():
    only = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    idx = load_index()
    files = sorted(f for f in os.listdir(ASR_DIR) if f.endswith(".json"))
    jobs = []
    for f in files:
        ep = int(re.search(r"ep(\d+)", f).group(1))
        if only and ep != only:
            continue
        meta = idx.get(ep, {})
        jobs.append((ep, meta.get("deckId", 0), meta.get("jitenTitle") or f"Ep{ep}",
                     os.path.join(ASR_DIR, f)))
    print(f"待构建 {len(jobs)} 集", flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for ep, deck, title, path in jobs:
            out = os.path.join(OUT_DIR, f"bsj-ep{ep:03d}.html")
            if os.path.exists(out) and os.path.getsize(out) > 20000:
                print(f"  skip Ep{ep}（已存在）", flush=True); continue
            futs[ex.submit(_one, ep, deck, title, path, out)] = ep
        for fu in as_completed(futs):
            ep = futs[fu]
            done += 1
            try:
                msg = fu.result()
            except Exception as e:
                msg = f"ERR {type(e).__name__} {str(e)[:80]}"
            print(f"[{done}/{len(jobs)}] Ep{ep} {msg}  ({time.time()-t0:.0f}s)", flush=True)
    print(f"完成 {time.time()-t0:.0f}s")

def _one(ep, deck, title, asr_path, out):
    try:
        d = json.load(open(asr_path, encoding="utf-8"))
        txt = d.get("transcription") or ""
        if len(txt) < 100:
            return f"skip(短文 {len(txt)})"
        t = re.sub(r"\s*[#＃]\w+\s*", " ", title)
        t = re.sub(r"\s*\|.*$", "", t)
        t = re.sub(r"\s*Japanese.*$", "", t).strip()
        if not t:
            t = f"Ep{ep}"
        build(ep, f"{ep} {t}", txt, deck or None, out, workers=2)
        return "ok"
    except Exception as e:
        return f"ERR {type(e).__name__} {str(e)[:80]}"

main()
