#!/usr/bin/env python3
"""
准备「翻译 / 词义」工作单，供 subagent 并行处理。

产出：
  /workspace/nhk/work/<epid>.<ver>.json   待翻译句子清单
  /workspace/nhk/work/lex_<n>.json        待写释义的词清单
同时会写一份 /workspace/nhk/work/all_sentences.json 便于统一上下文。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_text as P

DATA = "/workspace/nhk/data"
WORK = "/workspace/nhk/work"
NEP = int(sys.argv[1]) if len(sys.argv) > 1 else 30
# 只给「还没有翻译产出」的期生成工作单（增量更新用）
ONLY_MISSING = "--only-missing" in sys.argv
if ONLY_MISSING:
    NEP = 10 ** 9

os.makedirs(WORK, exist_ok=True)
epdir = os.path.join(DATA, "episodes")
eps = sorted([e for e in os.listdir(epdir)], reverse=True)[:NEP]
eps = [e for e in eps if os.path.exists(os.path.join(DATA, "audio", e + ".m4a"))]

# 句子工作单
lex_words = {}
manifest = []
for e in eps:
    d = os.path.join(epdir, e)
    ep = json.load(open(os.path.join(d, "episode.json"), encoding="utf-8"))
    row = {"id": e, "title": ep.get("title"), "time": ep.get("time")}
    for ver, fn, parser in [("easy", "easy.html", P.parse_easy), ("web", "web.json", P.parse_web)]:
        p = os.path.join(d, fn)
        if not os.path.exists(p):
            continue
        try:
            sents = parser(p)
        except Exception:
            sents = []
        items = []
        for s in sents:
            items.append({"ja": s["plain"], "zh": ""})
            for w in s["words"]:
                surf = w["w"]
                if not re.search(r"[一-鿿]", surf):
                    continue
                if len(surf) > 8:
                    continue
                lex_words.setdefault(surf, {"r": w["r"], "p": w["p"]})
        if items:
            wf = os.path.join(WORK, "%s.%s.json" % (e, ver))
            of = os.path.join(WORK, "out", "%s.%s.json" % (e, ver))
            if ONLY_MISSING and os.path.exists(of):
                continue        # 已有译文 → 不重做工作单
            with open(wf, "w", encoding="utf-8") as f:
                json.dump({"meta": row, "items": items}, f, ensure_ascii=False, indent=1)
    manifest.append(row)

# 词表工作单：按读音/长度分批
words = sorted(lex_words.items(), key=lambda kv: (len(kv[0]), kv[0]))
BATCH = 260
n = 0
for i in range(0, len(words), BATCH):
    chunk = dict(words[i:i + BATCH])
    with open(os.path.join(WORK, "lex_%02d.json" % n), "w", encoding="utf-8") as f:
        json.dump(chunk, f, ensure_ascii=False, indent=1)
    n += 1

with open(os.path.join(WORK, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)
print("期数 %d，句子工作单 %d 份，词表工作单 %d 份，唯一词 %d"
      % (len(manifest), len(eps) * 2, n, len(words)))
