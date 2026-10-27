#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉全部 707 集的 jiten 词表 → 汇总成全局逐字读音词典（seg_map / stem_map）。"""
import json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/workspace/bsj")
from annotate import build_jiten_map, build_stem_map

DECKS = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))
CACHE = "/workspace/bsj/data/vocab"
os.makedirs(CACHE, exist_ok=True)
OUT = "/workspace/bsj/data/global_reading.json"

def fetch_vocab(deck_id):
    path = f"{CACHE}/{deck_id}.json"
    if os.path.exists(path):
        try:
            return deck_id, json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    words, off = [], 0
    while True:
        u = (f"https://api.jiten.moe/api/media-deck/{deck_id}/vocabulary?offset={off}"
             f"&sortBy=chrono&sortOrder=0&displayFilter=all&search&hideKanaOnly=false")
        d = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "bsj/1.0"})
                d = json.loads(urllib.request.urlopen(req, timeout=45).read().decode())
                break
            except Exception:
                time.sleep(1.5)
        if d is None:
            break
        ws = (d.get("data") or {}).get("words") or []
        if not ws:
            break
        words += ws
        off += len(ws)
        if off > 3000:
            break
        time.sleep(0.15)
    json.dump(words, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return deck_id, words

def main():
    ids = [d["deckId"] for d in DECKS]
    print(f"拉 {len(ids)} 集词表…", flush=True)
    t0 = time.time()
    done = 0
    allwords = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch_vocab, i) for i in ids]
        for fu in as_completed(futs):
            did, ws = fu.result()
            done += 1
            allwords.extend(ws)
            if done % 50 == 0:
                print(f"  [{done}/{len(ids)}] {time.time()-t0:.0f}s 累计词 {len(allwords)}", flush=True)
    print(f"拉完 {done} 集，{time.time()-t0:.0f}s，累计 {len(allwords)} 条", flush=True)

    # 汇总
    seg_map, plain_map = build_jiten_map(allwords)
    stem_map = build_stem_map(seg_map)
    json.dump({"seg": seg_map, "plain": plain_map, "stem": stem_map},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"全局: seg={len(seg_map)} plain={len(plain_map)} stem={len(stem_map)} → {OUT}")

main()
