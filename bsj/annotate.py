#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日文分词 + 逐字注音(ruby) + HTML。

读音来源（优先级）：
  1) jiten.moe 该集词表：mainReading 形如 '元[げん]気[き]' / '日本[にほん]語[ご]' / '話[はな]す'
     → 已是逐字注音，直接还原成 (表层片段, 读音) 段列表
  2) fugashi/IPADIC 词级读音（兜底）
"""
import json, re, os, sys

SP = os.path.realpath("/workspace/.venv/lib/python3.11/site-packages")
sys.path.insert(0, SP)
# MeCab 读 ./dicrc → 用 MECABRC 环境变量指定绝对路径 rc，避免 chdir 破坏调用方的相对路径
DICDIR = os.path.join(SP, "ipadic", "dicdir")
RC = os.path.join(DICDIR, "mecabrc_abs")
if not os.path.exists(RC):
    with open(os.path.join(DICDIR, "mecabrc"), encoding="utf-8") as f:
        _rc = f.read()
    _rc = re.sub(r"^dicdir.*$", f"dicdir = {DICDIR}", _rc, flags=re.M)
    with open(RC, "w", encoding="utf-8") as f:
        f.write(_rc)
os.environ["MECABRC"] = RC
import fugashi

_tagger = fugashi.GenericTagger()

KATA2HIRA = {chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)}
def k2h(s):
    return "".join(KATA2HIRA.get(ch, ch) for ch in (s or ""))

HAN = re.compile(r"[一-鿿㐀-䶿]")
KANA = re.compile(r"[぀-ゟ゠-ヿ]")

# ---------- jiten 词表 ----------

def load_jiten_words(deck_id, cache_dir="/workspace/bsj/data"):
    path = os.path.join(cache_dir, f"vocab_{deck_id}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    import urllib.request, time
    words, off = [], 0
    while True:
        u = (f"https://api.jiten.moe/api/media-deck/{deck_id}/vocabulary?offset={off}"
             f"&sortBy=chrono&sortOrder=0&displayFilter=all&search&hideKanaOnly=false")
        d = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "bsj/1.0"})
                d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
                break
            except Exception:
                time.sleep(2)
        if d is None:
            break
        ws = (d.get("data") or {}).get("words") or []
        if not ws:
            break
        words += ws
        off += len(ws)
        if off > 2000:
            break
        time.sleep(0.25)
    json.dump(words, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return words

BRACKET = re.compile(r"\[([^\]]*)\]")

def parse_reading(raw):
    """'元[げん]気[き]' → ([('元','げん'),('気','き')], '元気')
       '話[はな]す'     → ([('話','はな'),('す',None)], '話す')
       'マナーを守[まも]る' → ([('マナーを',None),('守','まも'),('る',None)], 'マナーを守る')"""
    if not raw or "[" not in raw:
        return None, None
    segs, plain, pos = [], [], 0
    for m in BRACKET.finditer(raw):
        head = raw[pos:m.start()]
        reading = m.group(1) or None
        if head:
            segs.append((head, reading))
            plain.append(head)
        pos = m.end()
    tail = raw[pos:]
    if tail:
        segs.append((tail, None))
        plain.append(tail)
    if not segs:
        return None, None
    return segs, "".join(plain)

def build_jiten_map(words):
    """→ (seg_map{表层: [seg...]}, plain_map{表层: 整词读音})
    main 和 alternativeReadings 都要扫，但只采信带方括号的（只有它带读音信息）。"""
    seg_map, plain_map = {}, {}
    for w in words:
        cands = []
        mr = w.get("mainReading") or {}
        if mr.get("text"):
            cands.append(mr["text"])
        for ar in (w.get("alternativeReadings") or []):
            if ar.get("text"):
                cands.append(ar["text"])
        for raw in cands:
            if "[" not in raw:
                continue
            segs, plain = parse_reading(raw)
            if segs and plain:
                if plain not in seg_map:
                    seg_map[plain] = segs
                whole = "".join(r for _, r in segs if r)
                if whole:
                    plain_map.setdefault(plain, k2h(whole))
    return seg_map, plain_map

# ---------- 分句 / 分词 ----------

def split_sentences(text):
    """按句末标点切句；过长再按逗号切；碎片合并回相邻句。"""
    text = re.sub(r"[ \t　]+", " ", text.replace("\r", " ")).strip()
    # ① 主切分：句末标点跟在句尾（不能带 lookbehind，会误吃句中空白/把标点甩到下一句）
    parts = re.findall(r"[^。！？!?…\n]*[。！？!?…]+|[^。！？!?…\n]+", text)
    parts = [p.strip() for p in parts if p and p.strip()]
    # ② 过长句按逗号再切
    out = []
    for p in parts:
        if len(p) > 70:
            buf = ""
            for seg in re.split(r"(?<=[、，,])", p):
                buf += seg
                if len(buf) >= 40:
                    out.append(buf.strip()); buf = ""
            if buf.strip():
                out.append(buf.strip())
        else:
            out.append(p)
    # ③ 碎片合并：纯假名/极短片段并入前一句
    merged = []
    for p in out:
        core = re.sub(r"[。！？!?…、，,\s]", "", p)
        if merged and (len(core) <= 4 or not re.search(r"[぀-ヿ一-鿿]", core)):
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)
    return [m.strip() for m in merged if m.strip()]

def tokenize(sentence):
    # ASR 文本里有多余空格 → 日文不需要，先去掉（保留标点）
    sentence = re.sub(r"[ \t\u3000]+", "", sentence)
    toks = []
    for w in _tagger(sentence):
        s = w.surface
        if not s.strip():
            continue
        f = w.feature
        toks.append({"surface": s, "reading": k2h(f[7]) if len(f) > 7 else "",
                     "pos": f[0] if f else "",
                     "base": f[6] if len(f) > 6 else ""})
    return toks

def _apply_okurigana(segs, base, surface):
    """原形分段 → 实际活用形的分段：把末尾无读音的送り假名片段换成实际表层尾巴。
    話す→segs [('話','はな'),('す',None)]；surface 話し → [('話','はな'),('し',None)]
    """
    segs = [list(s) for s in segs]
    # 末尾连续无读音片段
    tail_idx = [i for i, (_, r) in enumerate(segs) if not r]
    if not tail_idx:
        return segs
    # 词干 = 到第一个无读音片段之前的全部表层
    first = tail_idx[0]
    stem = "".join(f for f, _ in segs[:first])
    if not surface.startswith(stem):
        return segs
    # 实际表层在词干之后的尾巴 → 分摊到各无读音片段
    actual_tail = surface[len(stem):]
    old_tail = "".join(f for f, _ in segs[first:])
    # 若长度不等，按比例切分
    if len(actual_tail) != len(old_tail) or not old_tail:
        segs[first] = [actual_tail, None]
        for i in range(first + 1, len(segs)):
            segs[i] = ["", None]
    else:
        k = 0
        for i in range(first, len(segs)):
            segs[i] = [actual_tail[k:k + len(segs[i][0])], None]
            k += len(segs[i][0])
    return [tuple(s) for s in segs]


def build_stem_map(seg_map):
    """从 seg_map 抽「词干→读音」全局表：'思[おも]う' → 思→おも；'守[まも]る' → 守→まも"""
    out = {}
    for segs in seg_map.values():
        for frag, rd in segs:
            if rd and HAN.search(frag):
                out.setdefault(frag, rd)
    return out

def apply_stem(surface, stem_map):
    """按 stem_map 贪心切分表层 → [(片段, 读音|None)]；一个都没命中时返回 None（交给 IPADIC 兜底）"""
    if not stem_map:
        return None
    res, i, n, nhits = [], 0, len(surface), 0
    while i < n:
        hit = None
        for L in range(min(6, n - i), 0, -1):
            frag = surface[i:i + L]
            if frag in stem_map:
                hit = (frag, stem_map[frag]); break
        if hit:
            res.append(hit); i += len(hit[0]); nhits += 1
        else:
            res.append((surface[i], None)); i += 1
    if nhits == 0:
        return None
    # 合并连续的假名尾段
    merged = []
    for frag, rd in res:
        if merged and rd is None and merged[-1][1] is None:
            merged[-1] = (merged[-1][0] + frag, None)
        else:
            merged.append((frag, rd))
    return merged

def annotate(tokens, seg_map, plain_map, stem_map=None):
    """每个 token → {surface, segs:[(片段,读音|None)]}"""
    stem_map = stem_map or {}
    out = []
    for t in tokens:
        s = t["surface"]
        segs = None
        if s in seg_map:
            segs = seg_map[s]
        elif s.strip() and s.strip() in seg_map:
            segs = seg_map[s.strip()]
        else:
            # 动词/形容动词：查原形的分段，再替换送り假名
            base = t.get("base") or ""
            if base and base in seg_map and t.get("pos", "").startswith(("動詞", "形容")):
                segs = _apply_okurigana(seg_map[base], base, s)
        if segs is None and HAN.search(s):
            segs = apply_stem(s, stem_map)
        if segs is None:
            if s in plain_map:
                segs = [(s, plain_map[s])]
            else:
                r = t["reading"]
                segs = [(s, r if HAN.search(s) else None)]
        clean = []
        for frag, rd in segs:
            if not frag:
                continue
            clean.append((frag, k2h(rd) if rd else None))
        out.append({"surface": s, "segs": clean})
    return out

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

PUNCT_ONLY = re.compile(r"^[。、，,．.!！?？：:;；…\-—~〜\s]+$")

def render_ja(ann):
    """每词一块（儿童书式），词内逐字 ruby；纯标点并入前一块，避免掉行。"""
    out = []
    for t in ann:
        inner = []
        for frag, rd in t["segs"]:
            f = esc(frag)
            if rd:
                inner.append(f"<ruby>{f}<rt>{esc(rd)}</rt></ruby>")
            else:
                inner.append(f)
        piece = "".join(inner)
        if PUNCT_ONLY.match(t["surface"]) and out:
            out[-1] = out[-1][:-len('</span>')] + piece + "</span>"
        else:
            out.append(f'<span class="w">{piece}</span>')
    return "".join(out)

if __name__ == "__main__":
    words = load_jiten_words(155661)
    seg_map, plain_map = build_jiten_map(words)
    print(f"jiten: {len(words)} 词 → seg_map {len(seg_map)} / plain_map {len(plain_map)}")
    for k in ["元気", "友達", "日本語", "今日", "日本"]:
        if k in seg_map:
            print("   ", k, seg_map[k])
    txt = "今日は元気です。日本語の文章を読みます。友達と話しました。"
    for sent in split_sentences(txt):
        toks = tokenize(sent)
        ann = annotate(toks, seg_map, plain_map)
        print("\n句:", sent)
        for a in ann:
            print("   ", a["surface"], "->", a["segs"])
        print("   HTML:", render_ja(ann)[:220])
