#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NHK やさしいことばニュース —— 文本解析

核心约定（与魔卡 v2.10 前端完全一致）：
  · cue.ja   = **纯文本**，不含任何括号注音
  · cue.t    = tokens [[start, len, word]]，start/len 相对 ja（纯文本坐标）
  · 读音     = 一律走 reading.js 查表（{词面: 读音}），前端渲染 ruby

  easy 版（NHK 官方逐字 ruby）：
      ruby 块即 token 边界 → 「20日(はつか)」整体是一个 token，读音はつか
  web 版（普通版書き起こし）：
      用 janome 分词，有汉字的词取 janome 读音
"""
import html
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from janome.tokenizer import Tokenizer

_tok = Tokenizer()

SENT_END = "。！？"
KANA_ONLY = re.compile(r"[ぁ-んァ-ヶ・ー]+")


def kata_to_hira(s: str) -> str:
    """janome 的 reading 是片假名 → 平假名"""
    s = unicodedata.normalize("NFKC", s)
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def read_of(tok) -> str:
    r = getattr(tok, "reading", None)
    if not r or r == "*":
        return ""
    return kata_to_hira(r)


# ---------------- easy.html：抽 ruby 块 ----------------
def ruby_blocks(fragment: str):
    """返回 (plain_text, blocks)

    blocks: [(start, end, reading)]  —— plain_text 上的区间，reading 可能为 None
    """
    plain = []
    blocks = []
    total = 0
    i, n = 0, len(fragment)
    while i < n:
        if fragment.startswith("<ruby", i):
            end = fragment.find("</ruby>", i)
            if end < 0:
                i += 1
                continue
            tagend = fragment.find(">", i)
            if tagend < 0 or tagend > end:
                i = end + 7
                continue
            inner = fragment[tagend + 1:end]
            rm = re.search(r"<rt>(.*?)</rt>", inner, re.S)
            base = re.sub(r"<rt>.*?</rt>", "", inner, flags=re.S)
            base = html.unescape(re.sub(r"<[^>]+>", "", base))
            base = re.sub(r"\s+", "", base)
            read = html.unescape(rm.group(1)).strip() if rm else ""
            read = re.sub(r"\s+", "", read)
            if base:
                s = total
                total += len(base)
                plain.append(base)
                blocks.append((s, total, read if KANA_ONLY.match(read) else None))
            i = end + 7
            continue
        if fragment[i] == "<":
            j = fragment.find(">", i)
            if j < 0:
                break
            i = j + 1
            continue
        ch = fragment[i]
        if ch in "\n\r\t":
            i += 1
            continue
        plain.append(ch)
        total += 1
        i += 1
    return "".join(plain), blocks


def janome_seg(text, base_off):
    """把一段纯文本用 janome 分词，返回 (tokens, words)"""
    tokens, words = [], []
    pos = 0
    for t in _tok.tokenize(text):
        surf = t.surface
        if not surf.strip():
            pos += len(surf)
            continue
        s, e = pos, pos + len(surf)
        pos = e
        tokens.append([base_off + s, len(surf), surf])
        words.append({"w": surf, "r": read_of(t), "p": t.part_of_speech,
                      "bf": getattr(t, "base_form", surf), "ps": base_off + s, "pe": base_off + e})
    return tokens, words


def ruby_seg(blocks, plain, base_off):
    """有 ruby 的块：整块当一个 token"""
    tokens, words = [], []
    for (s, e, rd) in blocks:
        if rd is None:
            continue
        w = plain[s:e]
        tokens.append([base_off + s, e - s, w])
        words.append({"w": w, "r": rd, "p": "名詞,一般,*,*",
                      "bf": w, "ps": base_off + s, "pe": base_off + e})
    return tokens, words


# ---------------- 切句 ----------------
def split_sentences(text: str, minlen=4):
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in SENT_END:
            b = buf.strip()
            if len(b) >= minlen:
                out.append(b)
            buf = ""
    b = buf.strip()
    if len(b) >= minlen:
        out.append(b)
    return out


# ---------------- easy.html ----------------
EASY_BODY_RE = re.compile(
    r'<div[^>]*class="[^"]*article-body[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S)


def parse_easy(path):
    h = open(path, encoding="utf-8", errors="replace").read()
    m = EASY_BODY_RE.search(h)
    if not m:
        return []
    paras = re.findall(r"<p[^>]*>(.*?)</p>", m.group(1), re.S)
    sents = []
    for p in paras:
        plain, blocks = ruby_blocks(p)
        if len(plain) < 3:
            continue
        # 把 blocks 切成「ruby 段」和「无 ruby 段」
        segs, pos = [], 0
        for (s, e, rd) in blocks:
            if s > pos:
                segs.append(("plain", pos, s, None))
            segs.append(("ruby", s, e, rd))
            pos = e
        if pos < len(plain):
            segs.append(("plain", pos, len(plain), None))
        cursor = 0
        for sp in split_sentences(plain):
            idx = plain.find(sp, cursor)
            if idx < 0:
                idx = plain.find(sp)
            if idx < 0:
                continue
            cursor = idx + len(sp)
            toks, wrds = [], []
            for seg in segs:
                kind = seg[0]
                if kind == "ruby":
                    s, e, rd = seg[1], seg[2], seg[3]
                    if e <= idx or s >= idx + len(sp):
                        continue
                    if rd is None:
                        continue
                    # 🔴 坐标必须相对「本句 ja」——前端用 ja.substr(tk[0], tk[1]) 切词
                    #    之前误用整段坐标(s) → 注音和生词 span 全部错位
                    toks.append([s - idx, e - s, plain[s:e]])
                    wrds.append({"w": plain[s:e], "r": rd, "p": "名詞,一般,*,*",
                                 "bf": plain[s:e], "ps": s - idx, "pe": e - idx})
                else:
                    s, e = seg[1], seg[2]
                    ss, ee = max(s, idx), min(e, idx + len(sp))
                    if ee <= ss:
                        continue
                    # 🔴 base_off 也要减掉本句在整段里的起点（idx）
                    t2, w2 = janome_seg(plain[ss:ee], ss - idx)
                    toks += t2
                    wrds += w2
            sents.append({"ja": sp, "plain": sp, "tokens": toks, "words": wrds,
                          "p2j": list(range(idx, idx + len(sp))),
                          "pend": list(range(idx + 1, idx + len(sp) + 1))})
    return sents


# ---------------- web.json（普通版書き起こし） ----------------
def parse_web(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d.get("articleBody") or ""
    sents = []
    for para in body.split("\n\n"):
        para = para.strip()
        if len(para) < 4:
            continue
        for sp in split_sentences(para):
            toks, wrds = janome_seg(sp, 0)
            sents.append({"ja": sp, "plain": sp, "tokens": toks, "words": wrds,
                          "p2j": list(range(len(sp))),
                          "pend": list(range(1, len(sp) + 1))})
    return sents


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("epdir")
    a = ap.parse_args()
    ep = json.load(open(os.path.join(a.epdir, "episode.json"), encoding="utf-8"))
    print("==", ep["news_id"], ep["title"])
    ep_path = os.path.join(a.epdir, "easy.html")
    if os.path.exists(ep_path):
        print("-- easy --")
        for s in parse_easy(ep_path):
            print("   ", s["ja"][:90])
            print("      tokens:", len(s["tokens"]),
                  [(t[2], next((w["r"] for w in s["words"] if w["w"] == t[2]), "")) for t in s["tokens"][:8]])
    wp = os.path.join(a.epdir, "web.json")
    if os.path.exists(wp):
        print("-- web --")
        for s in parse_web(wp):
            print("   ", s["ja"][:90])
            print("      tokens:", len(s["tokens"]),
                  [(t[2], next((w["r"] for w in s["words"] if w["w"] == t[2]), "")) for t in s["tokens"][:8]])
