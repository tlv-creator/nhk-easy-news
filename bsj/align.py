#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jiten 707 集 × iTunes 200 集 对齐 —— 以「集号」为主键，标题为辅。
jiten 标题里集号有三种形态：前导数字 / 'Ep.NNN' / 'Episode N'（各时期格式不同）。"""
import json, re, difflib, unicodedata, statistics

JK = "/workspace/bsj/data/jiten_decks.json"
IT = "/workspace/bsj/data/ep_0.json"

def jiten_epno(title):
    t = title or ""
    for pat in (r"[Ee]p\.?\s*(\d{1,4})\b", r"\bEpisode\s*(\d{1,4})\b"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    m = re.match(r"\s*(\d{1,4})\s*[^\d]", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 900:
            return n
    return None

def itunes_epno(track):
    m = re.match(r"\s*(\d{1,4})\s", track or "")
    return int(m.group(1)) if m else None

NOISE = re.compile(
    r"(Japanese\s*Listening\s*Practice|Japanese\s*Podcast|日本語ポッドキャスト|ポッドキャスト|podcast|"
    r"#\w+|\bEp\.?\s*\d+|\bEpisode\s*\d+|\bN[0-9]\b|【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\)|"
    r"[A-Za-z0-9️⃣🌸😊😄✨🎉🌟]+|[｜|·・…、。！？!?：:「」『』\"'\-–—~〜%％&/\\*+=])", re.I)

def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"^\s*\d{1,4}\s*[^\d]", " ", s)      # 去掉前导集号
    s = NOISE.sub(" ", s)
    return re.sub(r"\s+", "", s)

def main():
    decks = json.load(open(JK))
    decks.sort(key=lambda d: d["deckId"])
    its = [r for r in json.load(open(IT))["results"] if r.get("wrapperType") == "podcastEpisode"]
    for e in its:
        e["epNo"] = itunes_epno(e.get("trackName"))
    its = [e for e in its if e["epNo"]]
    its.sort(key=lambda e: -e["epNo"])

    for i, d in enumerate(decks):
        d["pos"] = i + 1
        d["epNo"] = jiten_epno(d["title"])
        d["jn"] = norm(d["title"])
    for e in its:
        e["itn"] = norm(e["trackName"])

    used_deck, used_epi = set(), set()
    aligned = []
    # 第一轮：集号直接对上
    byep = {}
    for d in decks:
        if d["epNo"] is not None and d["epNo"] not in byep:
            byep[d["epNo"]] = d
    for e in its:
        d = byep.get(e["epNo"])
        if d and d["deckId"] not in used_deck:
            used_deck.add(d["deckId"]); used_epi.add(e["epNo"])
            aligned.append((d, e, "epno"))
    # 第二轮：标题相似
    for e in its:
        if e["epNo"] in used_epi:
            continue
        best, bs = None, 0.0
        for d in decks:
            if d["deckId"] in used_deck or not d["jn"] or not e["itn"]:
                continue
            s = difflib.SequenceMatcher(None, e["itn"], d["jn"]).ratio()
            if e["itn"] in d["jn"] or d["jn"] in e["itn"]:
                s = max(s, 0.9)
            if s > bs:
                best, bs = d, s
        if best is not None and bs >= 0.8:
            used_deck.add(best["deckId"]); used_epi.add(e["epNo"])
            aligned.append((best, e, f"title:{bs:.2f}"))

    rows = []
    for d, e, how in aligned:
        rows.append({"itunesEpNo": e["epNo"], "deckId": d["deckId"], "pos": d["pos"], "how": how,
                     "jitenEpNo": d["epNo"], "itunesTitle": e["trackName"], "jitenTitle": d["title"],
                     "itunesDate": e["releaseDate"][:10], "jitenDate": d["releaseDate"],
                     "mp3": e["episodeUrl"], "sentenceCount": d["sentenceCount"],
                     "characterCount": d["characterCount"], "youtube": d["youtube"]})
    rows.sort(key=lambda x: -x["itunesEpNo"])
    json.dump(rows, open("/workspace/bsj/data/aligned.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"对齐 {len(rows)}/{len(its)} iTunes 集")
    byepno = sum(1 for r in rows if r["how"] == "epno")
    print(f"  集号直配 {byepno}，标题配 {len(rows)-byepno}")
    pos = [r["pos"] for r in rows]
    print(f"  位序单调: {all(pos[i] < pos[i+1] for i in range(len(pos)-1))}")
    # 单调性违例
    viol = [(rows[i]["itunesEpNo"], rows[i]["pos"], rows[i+1]["itunesEpNo"], rows[i+1]["pos"])
            for i in range(len(rows)-1) if rows[i]["pos"] >= rows[i+1]["pos"]]
    print(f"  单调性违例 {len(viol)} 处（应≈0，违例说明配错）")
    for v in viol[:8]:
        print("    ", v)
    gaps = [r["pos"] - r["itunesEpNo"] for r in rows]
    print(f"  pos-epNo 中位 {statistics.median(gaps):.0f} 范围 {min(gaps)}~{max(gaps)}")
    for r in rows[:4] + rows[-3:]:
        print(f"   Ep{r['itunesEpNo']:>4} deck={r['deckId']} pos={r['pos']:>3} {r['how']:>10} | {r['jitenTitle'][:46]}")

main()
