#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 jiten 的免登录导出重建某一集的「带标点句子 + 逐字注音」。

数据来源：
  POST /api/media-deck/{deckId}/download  {"format":4}  → TxtRepeated = 完整词流（每词按出现次数展开）
  POST /api/media-deck/{deckId}/download  {"format":2}  → CSV = 每词 ReadingFurigana(逐字注音) + ExampleSentence(带标点，词用 ** 标记)

重建：把 CSV 例句的词序列在词流里贪心定位 → 得到「带标点句子」及其词区间；
      命中不了的词区间用 IPADIC 句末启发式补句。
"""
import csv, io, json, os, re, sys, urllib.request

API = "https://api.jiten.moe/api/media-deck/{}/download"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

def post(deck_id, body, tries=3):
    data = json.dumps(body).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(API.format(deck_id), data=data, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            import time; time.sleep(2)

def get_txt_repeated(deck_id):
    raw = post(deck_id, {"format": 4, "downloadType": 1, "order": 1}).decode("utf-8", "replace")
    return raw

def get_csv(deck_id):
    raw = post(deck_id, {"format": 2, "downloadType": 1, "order": 1}).decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(raw.lstrip("﻿"))))

def parse_example(s):
    """'みなさん は、... **今日**は...' → (纯文本, [词,...])"""
    if not s:
        return None, []
    marked = re.findall(r"\*\*(.+?)\*\*", s)
    plain = re.sub(r"\*\*", "", s)
    plain = plain.replace("  ", " ").strip()
    return plain, marked

def build(deck_id):
    txt = get_txt_repeated(deck_id)
    rows = get_csv(deck_id)
    # 词流：TxtRepeated 是每词重复 N 次连写；用 CSV 的 Occurences 无法直接切分，
    # 所以改为：把 TXT 按「已知词表」贪心切分
    vocab = {}
    for r in rows:
        w = (r.get("Word") or "").strip()
        if w:
            vocab[w] = r
    # 词表按长度降序，贪心切
    keys = sorted(vocab.keys(), key=len, reverse=True)
    words, pos, n = [], 0, len(txt)
    while pos < n:
        hit = None
        for k in keys:
            if len(k) <= n - pos and txt.startswith(k, pos):
                hit = k; break
        if hit:
            words.append(hit); pos += len(hit)
        else:
            pos += 1   # 跳过标点/空白等
    return txt, vocab, words, rows

if __name__ == "__main__":
    deck = int(sys.argv[1]) if len(sys.argv) > 1 else 144090
    txt, vocab, words, rows = build(deck)
    print(f"词流长度 {len(words)} 词（原始 {len(txt)} 字符）")
    # 用例句做锚点：例句要自己分词，得到完整词序列（** 标记只标了目标词，不可靠）
    import sys as _s
    _s.path.insert(0,"/workspace/bsj")
    from annotate import tokenize
    anchors = []
    for r in rows:
        p, _m = parse_example(r.get("ExampleSentence"))
        if p and len(p) > 3:
            seq = [t["surface"] for t in tokenize(p)]
            seq = [w for w in seq if w.strip()]
            if seq:
                anchors.append((p, seq))
    print(f"例句锚点 {len(anchors)} 个")
    # 贪心定位
    covered = [False] * len(words)
    placed = []
    wi = 0
    for plain, mwords in anchors:
        # 在词流里找 mwords 的连续出现
        L = len(mwords)
        found = -1
        for i in range(wi, max(wi, len(words) - L)):
            if words[i:i + L] == mwords:
                found = i; break
        if found < 0:
            continue
        for j in range(found, found + L):
            covered[j] = True
        placed.append((found, found + L, plain))
        wi = found + L
    placed.sort()
    cov = sum(covered)
    print(f"锚定命中 {len(placed)} 句，覆盖词 {cov}/{len(words)} = {cov/len(words)*100:.1f}%")
    for s, e, p in placed[:5]:
        print(f"   [{s}-{e}] {p[:80]}")
