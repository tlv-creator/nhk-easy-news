#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 把 `sp` 轨（电视播报稿）写进字幕文件 —— 普通版「跟音」模式的数据

做什么：
    data/sp/<id>.json（ASR 原始输出：ja + 时间）
      + work/sp/<id>.json（译文：zh + 成分块中文 yb）
    ⇒ 生成与 easy/web **同构**的 cue，写进 site/app/subs/<id>.js 的 "sp" 键
      （前端因此能直接用同一套渲染 / 点词 / 详解面板 / AI 讲解）

🔴 为什么**不重跑 build_data.py**：
    build_data.py 是从 data/episodes/ 全量重建的 ⇒ 会把「本地 data/ 里没有的期」
    悄悄丢掉（本工程有 4 期的源数据不在本地）⇒ 改用**外科手术式**打补丁：
    只往现有 subs 里加/改 "sp" 键，easy/web 两轨**一个字都不动**。

用法： python3 scripts/build_sp.py [--ids a,b] [--app site/app]
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import parse_text as P  # noqa: E402
import patterns as PAT  # noqa: E402
import structure as ST  # noqa: E402
from lex_rules import is_skippable  # noqa: E402

SKIP_POS = ("助詞", "助動詞", "接続詞", "感動詞", "フィラー", "記号", "判定詞", "接頭詞")


def load_json(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def corpus_freq(app):
    """词频：扫现站所有 subs 的分词（与 build_data 的口径一致：按「出现期数」算）"""
    freq = {"__total__": 0}
    d = os.path.join(app, "subs")
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.endswith(".js"):
            continue
        raw = open(os.path.join(d, f), encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["([^"]+)"\]\s*=\s*(\{.*\});?\s*$', raw, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group(2))
        except Exception:
            continue
        freq["__total__"] += 1
        per = set()
        for ver, cues in data.items():
            if not isinstance(cues, list):
                continue
            for c in cues:
                for tk in (c.get("t") or []):
                    w = c["ja"][tk[0]:tk[0] + tk[1]]
                    if re.search(r"[一-鿿]", w):
                        per.add(w)
        for w in per:
            freq[w] = freq.get(w, 0) + 1
    return freq


def build_cue(s, zh, yb_zh, freq):
    plain = s["ja"]
    toks, wrds = P.janome_seg(plain, 0)
    sent = {"ja": plain, "plain": plain, "tokens": toks, "words": wrds,
            "p2j": list(range(len(plain))),
            "pend": list(range(1, len(plain) + 1))}
    marks = []
    for tk in toks:
        w = plain[tk[0]:tk[0] + tk[1]]
        if not w.strip():
            continue
        winfo = next((x for x in wrds if x["w"] == w), None)
        pos = winfo["p"] if winfo else ""
        if is_skippable(w, pos, freq):
            continue
        if not re.search(r"[一-鿿ァ-ヶ]", w):
            continue
        if len(w) > 10:
            continue
        marks.append([tk[0], tk[1], w])
    gmarks = []
    for pid, (a, b) in PAT.detect(sent):
        p2j, pend = sent.get("p2j"), sent.get("pend")
        if not p2j or a >= len(p2j):
            continue
        js = p2j[a]
        je = pend[min(b - 1, len(pend) - 1)]
        if je > js:
            gmarks.append([js - p2j[0], je - js, pid])
    try:
        y = ST.analyze(plain)
    except Exception:
        y = []
    yb = list(yb_zh or [])
    if len(yb) != len(y):
        yb = []
    return {"s": s["s"], "e": s["e"], "ja": plain, "zh": zh,
            "t": toks, "c": marks, "g": gmarks, "y": y, "yb": yb}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(ROOT, "site", "app"))
    ap.add_argument("--sp", default=os.path.join(ROOT, "data", "sp"))
    ap.add_argument("--tr", default=os.path.join(ROOT, "work", "sp"))
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    app = os.path.normpath(a.app)
    want = [x.strip() for x in a.ids.split(",") if x.strip()]

    freq = corpus_freq(app)
    print("== 词频（期数 %d）==" % freq.get("__total__", 0))
    files = sorted(f for f in os.listdir(a.sp) if f.endswith(".json"))
    if want:
        files = [f for f in files if f[:-5] in want]
    n = 0
    for f in files:
        eid = f[:-5]
        sp = load_json(os.path.join(a.sp, f))
        tr = load_json(os.path.join(a.tr, f))
        if not sp or not (sp.get("sents") or []):
            print("  ! %s 没有 ASR 结果 ⇒ 跳过" % eid)
            continue
        if not tr or not (tr.get("items") or []):
            print("  ! %s 没有译文 ⇒ 跳过（不发布半成品）" % eid)
            continue
        items = tr["items"]
        if len(items) != len(sp["sents"]):
            print("  ❌ %s 句数对不上（%d vs %d）⇒ 跳过"
                  % (eid, len(items), len(sp["sents"])))
            continue
        subp = os.path.join(app, "subs", eid + ".js")
        if not os.path.exists(subp):
            print("  ! %s 没有字幕文件 ⇒ 跳过" % eid)
            continue
        raw = open(subp, encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["%s"\]\s*=\s*(\{.*\});?\s*$' % eid, raw, re.S)
        if not m:
            print("  ! %s 字幕文件解析不出来 ⇒ 跳过" % eid)
            continue
        data = json.loads(m.group(1))
        cues = []
        for s, it in zip(sp["sents"], items):
            cues.append(build_cue(s, it.get("zh", ""), it.get("yb"), freq))
        data["sp"] = cues
        head = raw[:m.start(1)]
        tail = raw[m.end(1):]
        out = head + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + tail
        _atomic_write(subp, out)
        n += 1
        print("  ✅ %s  sp 轨 %d 句（%.0fs→%.0fs）"
              % (eid, len(cues), cues[0]["s"] / 1000, cues[-1]["e"] / 1000))
    print("== 写完 %d 期 ==" % n)



def _atomic_write(path, text):
    """🔴 原子写：先写临时文件再 rename
    ⇒ 别的进程（fix_sp_asr / gen_blk）不会读到**半截**文件
    ⇒ 以前并发时 JSON 解析报「Invalid control character」（2026-10-25 踩到）"""
    import tempfile
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

if __name__ == "__main__":
    main()
