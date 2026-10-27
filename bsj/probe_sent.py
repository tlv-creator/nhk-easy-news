#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证覆盖率：拉本集高频词的例句，筛出属于 BSJ 的 deckId 的句子。"""
import json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
DECKS = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))
BSJ_IDS = {x["deckId"] for x in DECKS}
PARENT = 143254

def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def post(url, body, timeout=90):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def main():
    deck = int(sys.argv[1]) if len(sys.argv) > 1 else 144090
    topn = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    # 取本集所有词（分页）
    words = []
    off = 0
    while True:
        u = (f"https://api.jiten.moe/api/media-deck/{deck}/vocabulary?offset={off}&sortBy=chrono"
             f"&sortOrder=0&displayFilter=all&search&hideKanaOnly=false")
        d = get(u)
        ws = (d.get("data") or {}).get("words") or []
        if not ws:
            break
        words += ws
        off += len(ws)
        if off > 3000:
            break
    print(f"本集词 {len(words)}")
    # 按 frequencyRank 升序（最常见）取 topN
    cand = sorted(words, key=lambda w: ((w.get("mainReading") or {}).get("frequencyRank", 10**9)))[:topn]
    print(f"取 top {len(cand)} 高频词")

    def one(w):
        mr = w.get("mainReading") or {}
        u = (f"https://api.jiten.moe/api/vocabulary/{w['wordId']}/{mr.get('readingIndex',0)}"
             f"/example-sentences-by-difficulty/11?take=999&mediaTypes=11")
        try:
            d = post(u, [])
            return d.get("sentences") or []
        except Exception:
            return []

    sents = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for lst in ex.map(one, cand):
            for s in lst:
                sd = s.get("sourceDeck") or {}
                if sd.get("deckId") == deck:
                    sents[s["text"]] = s
    print(f"命中本集句子：{len(sents)}")
    for t in list(sents)[:10]:
        print("   ", t[:90])

main()
