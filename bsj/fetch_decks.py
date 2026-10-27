#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 jiten.moe 上 Bite Size Japanese 全部子集（每集一个 media deck）。"""
import json, urllib.request, time, os, sys

API = "https://api.jiten.moe/api/media-deck/143254/detail"
OUT = "/workspace/bsj/data/jiten_decks.json"

def fetch(offset, tries=3):
    url = f"{API}?offset={offset}"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bsj-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))["data"]
        except Exception as e:
            if i == tries - 1:
                print(f"  !! offset={offset} failed: {e}", file=sys.stderr)
                return None
            time.sleep(2)

def main():
    all_decks, seen = [], set()
    offset = 0
    while True:
        d = fetch(offset)
        if not d:
            break
        sd = d.get("subDecks") or []
        if not sd:
            break
        new = 0
        for x in sd:
            if x["deckId"] not in seen:
                seen.add(x["deckId"])
                all_decks.append(x)
                new += 1
        print(f"offset={offset:>4} got={len(sd)} new={new} total={len(all_decks)}", flush=True)
        if new == 0:
            break
        offset += 25
        if offset > 2000:
            break
        time.sleep(0.4)

    # 补每集的 YouTube 链接
    out = []
    for i, x in enumerate(all_decks):
        yt = None
        for l in (x.get("links") or []):
            if "youtube.com" in (l.get("url") or ""):
                yt = l["url"]
                break
        out.append({
            "deckId": x["deckId"],
            "title": x.get("originalTitle"),
            "releaseDate": (x.get("releaseDate") or "")[:10],
            "sentenceCount": x.get("sentenceCount"),
            "characterCount": x.get("characterCount"),
            "runtimeSeconds": x.get("runtimeSeconds"),
            "youtube": yt,
        })
        if (i + 1) % 100 == 0:
            print(f"  detail {i+1}/{len(all_decks)}", flush=True)
            json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nTOTAL {len(out)} decks -> {OUT}")
    # 统计
    withyt = sum(1 for x in out if x["youtube"])
    print(f"with youtube: {withyt}")
    print(f"total sentences: {sum(x['sentenceCount'] or 0 for x in out)}")
    print(f"total chars: {sum(x['characterCount'] or 0 for x in out):,}")
    for x in out[:3] + out[-3:]:
        print("  ", x["deckId"], x["releaseDate"], (x["title"] or "")[:55], x["sentenceCount"], "sent")

main()
