#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 700 集在 2outube 上的缓存状态（fromCache=true 的 = 免费可用）。"""
import json, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

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

def probe(vid):
    try:
        req = urllib.request.Request(f"https://2outube.com/api/transcript?v={vid}",
                                     headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read().decode("utf-8"))
        tr = d.get("transcript") or []
        return {"fromCache": bool(d.get("fromCache")), "available": bool(d.get("available")),
                "n": len(tr), "source": d.get("source"),
                "failureReason": d.get("failureReason"),
                "segments": tr if len(tr) < 2000 else []}
    except Exception as e:
        return {"fromCache": False, "available": False, "n": 0, "err": str(e)[:60], "segments": []}

def main():
    d = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))
    rows = []
    for x in d:
        m = re.search(r"v=([\w-]{6,})", x.get("youtube") or "")
        e = epno(x.get("title"))
        if m and e:
            rows.append({"ep": e, "vid": m.group(1), "deck": x["deckId"], "title": x.get("title") or "",
                         "rt": x.get("runtimeSeconds") or 0})
    only = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if only:
        rows = [r for r in rows if r["ep"] == only]
    print(f"探测 {len(rows)} 集…", flush=True)
    out, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (r, p) in enumerate(zip(rows, ex.map(lambda x: probe(x["vid"]), rows))):
            rec = {**r, **p}
            out.append(rec)
            if (i + 1) % 50 == 0:
                cache = sum(1 for x in out if x.get("fromCache"))
                avail = sum(1 for x in out if x.get("n"))
                print(f"  {i+1}/{len(rows)}  cached={cache} 有字幕={avail}  ({time.time()-t0:.0f}s)", flush=True)
    json.dump(out, open("/workspace/bsj/data/avail2.json", "w", encoding="utf-8"), ensure_ascii=False)
    cache = sum(1 for x in out if x.get("fromCache"))
    avail = sum(1 for x in out if x.get("n"))
    print(f"完成 {time.time()-t0:.0f}s：cached={cache}/{len(out)}  有字幕={avail}")
    eps = sorted(x["ep"] for x in out if x.get("n"))
    if eps:
        print("有字幕集号:", eps[:30], "…" if len(eps) > 30 else "")

main()
