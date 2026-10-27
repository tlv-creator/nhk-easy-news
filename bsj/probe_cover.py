#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用已有 ASR 稿做真值，验证「jiten CSV 例句 → 本集句子」的覆盖率。"""
import json, csv, io, re, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/workspace/bsj")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
DECKS = json.load(open("/workspace/bsj/data/jiten_decks.json", encoding="utf-8"))
BY_EP = {}
for x in DECKS:
    ep = x.get("ep")
    if ep:
        BY_EP[int(ep)] = x

def post(deck_id, body, timeout=150):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"https://api.jiten.moe/api/media-deck/{deck_id}/download",
        data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def norm(s):
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[、。？！!?…「」『』（）()\-–—,.．]", "", s)
    return s

def main():
    from annotate import tokenize
    files = sorted(f for f in os.listdir("/workspace/bsj/data/asr") if f.endswith(".json"))
    print(f"ASR 真值 {len(files)} 集")
    res = []
    def one(fn):
        ep = int(re.search(r"(\d+)", fn).group(1))
        meta = BY_EP.get(ep)
        if not meta:
            return None
        truth = json.load(open(f"/workspace/bsj/data/asr/{fn}", encoding="utf-8"))
        # 真值句子
        tsegs = truth.get("segments") or truth.get("segments_raw") or []
        tnorm = set()
        for s in tsegs:
            t = s.get("text") if isinstance(s, dict) else s
            if not t:
                continue
            for piece in re.split(r"[。！？!?\n]", t):
                n = norm(piece)
                if len(n) >= 4:
                    tnorm.add(n)
        try:
            raw = post(meta["deckId"], {"format": 2, "downloadType": 1, "order": 1})
        except Exception as e:
            return (ep, meta, tnorm, {}, f"ERR {e}")
        rows = list(csv.DictReader(io.StringIO(raw.lstrip("﻿"))))
        vocab = {(r.get("Word") or "").strip() for r in rows if (r.get("Word") or "").strip()}
        es = sorted({(r.get("ExampleSentence") or "").strip() for r in rows
                     if (r.get("ExampleSentence") or "").strip()})
        cand = {}
        for e in es:
            ws = [t["surface"].replace("*", "") for t in tokenize(e)]
            ws = [w for w in ws if w.strip() and w not in "、。？！?!「」『』（）…・,. "]
            if not ws:
                continue
            hit = sum(1 for w in ws if w in vocab) / len(ws)
            cand[e] = (hit, norm(e))
        return (ep, meta, tnorm, cand, None)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(one, files):
            if not r:
                continue
            ep, meta, tnorm, cand, err = r
            if err:
                print(f"Ep{ep:3d} {err}")
                continue
            for th in (0.7, 0.8, 0.9, 1.0):
                sel = {n for e, (h, n) in cand.items() if h >= th}
                cov = len(sel & tnorm) / max(1, len(tnorm))
                if th == 0.8:
                    cov8 = cov
                print(f"Ep{ep:3d} 真值{len(tnorm):3d}句 例句{len(cand):3d} th={th} 选出{len(sel):3d} 覆盖真值 {cov*100:5.1f}%")
            res.append((ep, len(tnorm), len(cand), cov8))
    if res:
        avg = sum(r[3] for r in res) / len(res)
        print(f"\n平均覆盖率(th=0.8): {avg*100:.1f}%  ({len(res)} 集)")

main()
