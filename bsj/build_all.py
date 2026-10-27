#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量构建：轮询 data/subs/ 里已抓好的官方字幕 → 逐字注音 + 翻译 → HTML。"""
import json, os, re, sys, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/workspace/bsj")
from build_subs import build_from_subs

SUBS = "/workspace/bsj/data/subs"
OUT = "/workspace/bsj/out"
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

# videoId → (ep, deckId, 标题)
INDEX = {}
for x in DECKS:
    v = vid_of(x)
    if v:
        INDEX[v] = (epno_of(x.get("title")), x["deckId"], x.get("title") or "")

def clean_title(t):
    t = re.sub(r"\s*\|.*$", "", t)
    t = re.sub(r"\s*Japanese\s*Listening\s*Practice.*$", "", t, flags=re.I)
    t = re.sub(r"\s*#\S+", "", t)
    t = re.sub(r"\s*【[^】]*】", "", t)
    t = re.sub(r"\s*[|｜]\s*$", "", t)
    return t.strip(" -–—｜|")

def one_video(path, workers):
    vid = os.path.basename(path).replace(".json", "")
    # 文件名形如 ep653 / deck143570
    if vid.startswith("ep"):
        try:
            ep = int(vid[2:])
        except ValueError:
            ep = None
    else:
        ep = None
    # 用 youtube id 反查
    meta = None
    try:
        segs = json.load(open(path, encoding="utf-8"))
    except Exception:
        return ("bad-json", 0)
    if not segs:
        return ("empty", 0)
    # 从字幕里拿不到 videoId → 用 deck 映射表（按文件名 ep 找）
    for v, (e, deck, title) in INDEX.items():
        if e == ep:
            meta = (e, deck, title); break
    if meta is None:
        return ("no-meta", 0)
    e, deck, title = meta
    out = os.path.join(OUT, f"bsj-ep{e:03d}.html")
    if os.path.exists(out) and os.path.getsize(out) > 20000:
        return ("cached", len(segs))
    try:
        build_from_subs(e, f"{e} {clean_title(title)}", segs, deck, out, workers=workers)
        return ("ok", len(segs))
    except Exception as e:
        return (f"ERR {type(e).__name__} {str(e)[:60]}", len(segs))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep_conc", type=int, default=4)
    ap.add_argument("--tr_workers", type=int, default=8)
    ap.add_argument("--loop", action="store_true", help="持续轮询新出现的字幕")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    done = set()
    t0 = time.time()
    while True:
        files = sorted(f for f in os.listdir(SUBS) if f.endswith(".json"))
        todo = [os.path.join(SUBS, f) for f in files if f not in done]
        if not todo:
            if a.loop:
                time.sleep(20)
                continue
            break
        print(f"→ 待建 {len(todo)} 集", flush=True)
        with ThreadPoolExecutor(max_workers=a.ep_conc) as ex:
            futs = {ex.submit(one_video, p, a.tr_workers): p for p in todo}
            for fu in as_completed(futs):
                p = futs[fu]
                done.add(os.path.basename(p))
                try:
                    st, n = fu.result()
                except Exception as e:
                    st, n = f"ERR {type(e).__name__} {str(e)[:50]}", 0
                print(f"  {os.path.basename(p)}: {st} ({n} 段)  [{time.time()-t0:.0f}s]", flush=True)
        if not a.loop:
            break
    print(f"构建结束 {time.time()-t0:.0f}s, 共 {len(done)} 集", flush=True)

main()
