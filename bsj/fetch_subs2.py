#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 2outube.com 批量抓 YouTube 官方字幕（一集一次请求，直接给日文原轨+时间轴）。"""
import json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT = "/workspace/bsj/data/subs2"
os.makedirs(OUT, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

def epno(t):
    t = t or ""
    for pat in (r"[Ee]p\.?\s*(\d{1,4})\b", r"\bEpisode\s*(\d{1,4})\b"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    m = re.match(r"\s*(\d{1,4})\s*[^\d]", t)
    if m and 1 <= int(m.group(1)) <= 900:
        return int(m.group(1))
    return None

def clean(s):
    s = (s or "").replace("﻿", "").replace("", "")
    s = re.sub(r"^\s*\?", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def fetch(vid, tries=3):
    u = f"https://2outube.com/api/transcript?v={vid}"
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read().decode("utf-8"))
            tr = d.get("transcript") or []
            segs = []
            for it in tr:
                txt = clean(it.get("text"))
                if not txt:
                    continue
                st = int(float(it.get("start") or 0) * 1000)
                du = int(float(it.get("duration") or 0) * 1000)
                segs.append([st, st + du, txt])
            return segs
        except Exception as e:
            if i == tries - 1:
                return []
            time.sleep(1.5)
    return []

def main():
    d = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))
    rows = []
    for x in d:
        m = re.search(r"v=([\w-]{6,})", x.get("youtube") or "")
        e = epno(x.get("title"))
        if m and e:
            rows.append({"ep": e, "vid": m.group(1), "deck": x["deckId"],
                         "title": x.get("title") or "", "rt": x.get("runtimeSeconds") or 0})
    only = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if only:
        rows = [r for r in rows if r["ep"] == only]
    if limit:
        rows = rows[:limit]
    print(f"抓 {len(rows)} 集（2outube）", flush=True)
    t0 = time.time()
    ok = empty = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch, r["vid"]): r for r in rows}
        for i, fu in enumerate(as_completed(futs)):
            r = futs[fu]
            segs = fu.result()
            path = os.path.join(OUT, f"ep{r['ep']:03d}.json")
            if segs:
                json.dump(segs, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                ok += 1
            else:
                empty += 1
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(rows)}  成功 {ok} 空 {empty}  ({time.time()-t0:.0f}s)", flush=True)
    print(f"完成 {time.time()-t0:.0f}s：成功 {ok}，空 {empty}", flush=True)

main()
