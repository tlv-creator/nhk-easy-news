#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量抓官方字幕：jiten 707 集 → youtubetranslate.net。跨集并发。"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/workspace/bsj")
from fetch_subs import fetch_all, get_duration

OUT = "/workspace/bsj/data/subs"
os.makedirs(OUT, exist_ok=True)
DECKS = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))

def vid_of(x):
    m = re.search(r"v=([\w-]{6,})", x.get("youtube") or "")
    return m.group(1) if m else None

def epno_of(title):
    t = title or ""
    for pat in (r"[Ee]p\.?\s*(\d{1,4})\b", r"\bEpisode\s*(\d{1,4})\b"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    m = re.match(r"\s*(\d{1,4})\s*[^\d]", t)
    if m and 1 <= int(m.group(1)) <= 900:
        return int(m.group(1))
    return None

def one(x, workers):
    vid = vid_of(x)
    ep = epno_of(x.get("title"))
    if not vid:
        return (ep, "no-vid", 0)
    name = f"ep{ep:03d}.json" if ep else f"deck{x['deckId']}.json"
    path = os.path.join(OUT, name)
    if os.path.exists(path) and os.path.getsize(path) > 300:
        try:
            n = len(json.load(open(path, encoding="utf-8")))
            if n > 20:
                return (ep, "cached", n)
        except Exception:
            pass
    dur = (x.get("runtimeSeconds") or 0) * 1000
    if not dur:
        dur = get_duration(vid) or 0
    if not dur:
        dur = 25 * 60 * 1000
    try:
        segs = fetch_all(vid, dur, workers=workers, verbose=False)
        json.dump(segs, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return (ep, f"ok {len(segs)}", len(segs))
    except Exception as e:
        return (ep, f"ERR {type(e).__name__} {str(e)[:40]}", 0)

def main():
    only = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    ep_conc = int(sys.argv[2]) if len(sys.argv) > 2 else 6      # 同时处理几集
    probe = int(sys.argv[3]) if len(sys.argv) > 3 else 8       # 每集内并发
    rows = [x for x in DECKS if vid_of(x)]
    if only:
        rows = [x for x in rows if epno_of(x.get("title")) == only]
    print(f"待抓 {len(rows)} 集（跨集并发 {ep_conc} × 每集 {probe}）", flush=True)
    t0 = time.time()
    done = 0
    ok = 0
    with ThreadPoolExecutor(max_workers=ep_conc) as ex:
        futs = [ex.submit(one, x, probe) for x in rows]
        for fu in as_completed(futs):
            ep, st, n = fu.result()
            done += 1
            if st.startswith("ok"):
                ok += 1
            if done % 10 == 0 or st.startswith("ERR"):
                print(f"[{done}/{len(rows)}] Ep{ep} {st}  ({time.time()-t0:.0f}s, 成功 {ok})", flush=True)
    print(f"完成 {time.time()-t0:.0f}s，成功 {ok}/{len(rows)}", flush=True)

main()
