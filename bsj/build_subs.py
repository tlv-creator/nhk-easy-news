#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用【官方 YouTube 字幕】构建日中对照 HTML：逐字注音 + 中文翻译。"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/workspace/bsj")
from annotate import (load_jiten_words, build_jiten_map, build_stem_map,
                      split_sentences, tokenize, annotate, render_ja, esc)
from build import translate_batch, build_html, load_global, GLOBAL_READING

def build_from_subs(ep_no, title, segs, deck_id, out_path, workers=4):
    print(f"[{ep_no}] {len(segs)} 段官方字幕")
    seg_map, plain_map = {}, {}
    if deck_id:
        s, p = build_jiten_map(load_jiten_words(deck_id))
        seg_map, plain_map = s, p
        print(f"[{ep_no}] 本集词表 {len(seg_map)} 条")
    g_seg, g_plain, g_stem = load_global()
    merged_seg = dict(g_seg); merged_seg.update(seg_map)
    merged_plain = dict(g_plain); merged_plain.update(plain_map)
    stem_map = build_stem_map(merged_seg)
    print(f"[{ep_no}] 全局 seg={len(g_seg)} stem={len(g_stem)}")

    lines, tothit, tottot = [], 0, 0
    for st, et, text in segs:
        # 字幕段本身是句子；若含句末标点则按标点再细分（更贴近阅读）
        sents = split_sentences(text) if re.search(r"[。！？!?]", text) else [text]
        for s in sents:
            if not s.strip():
                continue
            ann = annotate(tokenize(s), merged_seg, merged_plain, stem_map)
            lines.append((render_ja(ann), s, st))
            for t in ann:
                if re.search(r"[一-鿿]", t["surface"]):
                    tottot += 1
                    if any(r for _, r in t["segs"]):
                        tothit += 1
    pct = tothit / tottot * 100 if tottot else 0
    print(f"[{ep_no}] {len(lines)} 句；汉字注音 {tothit}/{tottot} = {pct:.1f}%")

    print(f"[{ep_no}] 翻译…")
    zh = translate_batch([x[1] for x in lines], 0, workers=workers)
    print(f"[{ep_no}] 译文 {len(zh)}/{len(lines)}")

    html_out = build_html(title, [(x[0], x[1]) for x in lines], zh)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[{ep_no}] → {out_path}  ({len(html_out):,} bytes)")
    return out_path

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--subs", required=True)
    ap.add_argument("--deck", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    segs = json.load(open(a.subs, encoding="utf-8"))
    build_from_subs(a.ep, a.title, segs, a.deck or None, a.out, a.workers)
