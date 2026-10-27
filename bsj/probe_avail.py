#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测哪些集真的有官方字幕（youtubetranslate.net）。"""
import json, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
API = "https://youtubetranslate.net/api/subtitle"

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

def load_rows():
    d = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))
    rows = []
    for x in d:
        m = re.search(r"v=([\w-]{6,})", x.get("youtube") or "")
        e = epno(x.get("title"))
        if m and e:
            rows.append({"ep": e, "vid": m.group(1), "rd": (x.get("releaseDate") or "")[:10],
                         "rt": x.get("runtimeSeconds") or 0, "deck": x["deckId"],
                         "title": x.get("title") or ""})
    return rows

def probe(r):
    pts = [0, 5000, int((r["rt"] or 600) * 500)]
    for t in pts:
        try:
            body = json.dumps({"videoId": r["vid"], "currentTime": t}).encode()
            req = urllib.request.Request(API, data=body, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as f:
                d = json.loads(f.read().decode())
            if d.get("status") == "success" and (d.get("subtitle") or {}).get("original"):
                return {**r, "has": True, "probe_t": t}
        except Exception:
            pass
    return {**r, "has": False}

def main():
    rows = load_rows()
    print(f"探测 {len(rows)} 集…", flush=True)
    out = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i, r in enumerate(ex.map(probe, rows)):
            out.append(r)
            if (i + 1) % 100 == 0:
                print(f"  {i+1}/{len(rows)}", flush=True)
    json.dump(out, open("/workspace/bsj/data/avail.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    ok = sorted([r for r in out if r["has"]], key=lambda r: -r["ep"])
    print(f"有字幕 {len(ok)}/{len(out)}")
    if ok:
        print(f"  集号范围 {min(r['ep'] for r in ok)} ~ {max(r['ep'] for r in ok)}")
        for r in ok[:8]:
            print(f"   Ep{r['ep']} {r['rd']} {r['title'][:44]}")

main()
