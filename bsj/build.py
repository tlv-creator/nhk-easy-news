#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日中对照 HTML 生成：ASR 日文 → 逐句分词逐字注音 → 中文翻译 → 儿童书式排版。"""
import json, os, re, sys, time, html, urllib.request, argparse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate import (load_jiten_words, build_jiten_map, build_stem_map,
                      split_sentences, tokenize, annotate, render_ja, esc)

MM_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MM_MODEL = "MiniMax-Text-01"

# ---------------- 翻译 ----------------

def mm_chat(messages, retries=4, temperature=0.2):
    body = json.dumps({"model": MM_MODEL, "messages": messages,
                        "temperature": temperature}).encode("utf-8")
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    for i in range(retries):
        try:
            req = urllib.request.Request(
                MM_URL, data=body, method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8"))
            c = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if c.strip():
                return c
        except Exception as e:
            if i == retries - 1:
                print(f"  !! 翻译失败: {e}", file=sys.stderr)
            time.sleep(2 + 2 * i)
    return ""

def translate_batch(sentences, idx_start, workers=4):
    """→ {全局下标: 中文}"""
    BATCH = 25
    chunks = []
    for i in range(0, len(sentences), BATCH):
        chunk = sentences[i:i + BATCH]
        lines = "\n".join(f"{idx_start + i + j}\t{s}" for j, s in enumerate(chunk))
        chunks.append((i, lines, idx_start + i))
    out = {}

    def one(item):
        i, lines, base = item
        prompt = ("把下面的日语句子逐行翻译成简体中文。\n"
                  "严格要求：\n"
                  "1. 只输出译文，一行一句，行的开头保留原来的行号和制表符\n"
                  "2. 不要输出任何解释、说明、markdown、代码块\n"
                  "3. 语气自然口语化\n"
                  "4. 行数必须与输入行数完全一致\n\n" + lines)
        txt = mm_chat([{"role": "user", "content": prompt}])
        got = {}
        for ln in txt.splitlines():
            ln = ln.strip()
            m = re.match(r"^(\d+)\s*[\t:：、]?\s*(.*)$", ln)
            if m and m.group(2):
                got[int(m.group(1))] = m.group(2).strip()
        return got

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for got in ex.map(one, chunks):
            out.update(got)
    return out

# ---------------- HTML ----------------

CSS = """
:root{ --ink:#111; --sub:#555; --line:rgba(0,0,0,.14); --bg:#fff; }
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);}
body{font-family:"Noto Sans JP","Hiragino Kaku Gothic ProN","Yu Gothic","Microsoft YaHei",sans-serif;
     font-size:30px;line-height:1.9;-webkit-text-size-adjust:100%;}
.wrap{max-width:860px;margin:0 auto;padding:24px 18px 80px;}
h1{font-size:34px;line-height:1.5;margin:0 0 6px;font-weight:700;}
.meta{font-size:20px;color:var(--sub);margin:0 0 26px;padding-bottom:16px;border-bottom:2px solid var(--line);}
.p{margin:0 0 26px;padding:0 0 20px;border-bottom:1px solid var(--line);}
.ja{display:block;line-height:2.1;word-spacing:.05em;}
.w{display:inline-block;margin:0 .28em .12em 0;white-space:nowrap;}
ruby{ruby-position:over;}
rt{font-size:.48em;color:var(--sub);font-weight:400;letter-spacing:.02em;}
.zh{display:block;margin-top:8px;font-size:.86em;color:#222;line-height:1.75;}
@media (max-width:520px){ body{font-size:26px;} h1{font-size:28px;} .zh{font-size:.9em;} }
"""

def build_html(title, lines, zh_map):
    parts = [f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{esc(title)}</h1>"""]
    for i, (ja_html, sent) in enumerate(lines):
        zh = zh_map.get(i, "")
        parts.append(f'<p class="p"><span class="ja">{ja_html}</span>'
                     f'<span class="zh">{esc(zh)}</span></p>')
    parts.append("</div></body></html>")
    return "".join(parts)

# ---------------- 主流程 ----------------

GLOBAL_READING = "/workspace/bsj/data/global_reading.json"

def load_global():
    if os.path.exists(GLOBAL_READING):
        g = json.load(open(GLOBAL_READING, encoding="utf-8"))
        return g.get("seg", {}), g.get("plain", {}), g.get("stem", {})
    return {}, {}, {}

def build(ep_no, title, asr_text, deck_id, out_path, workers=4, use_global=True):
    print(f"[{ep_no}] 分句…")
    sents = split_sentences(asr_text)
    print(f"[{ep_no}] {len(sents)} 句")

    seg_map, plain_map = {}, {}
    if deck_id:
        print(f"[{ep_no}] 拉 jiten 词表 deck={deck_id}")
        s, p = build_jiten_map(load_jiten_words(deck_id))
        seg_map, plain_map = s, p
        print(f"[{ep_no}] 本集词表 {len(seg_map)} 条")
    g_seg, g_plain, g_stem = load_global() if use_global else ({}, {}, {})
    if g_seg:
        # 本集优先，全局补缺
        merged_seg = dict(g_seg); merged_seg.update(seg_map)
        merged_plain = dict(g_plain); merged_plain.update(plain_map)
        stem_map = build_stem_map(merged_seg)
        print(f"[{ep_no}] 全局词表 seg={len(g_seg)} stem={len(g_stem)}")
    else:
        merged_seg, merged_plain = seg_map, plain_map
        stem_map = build_stem_map(merged_seg)

    print(f"[{ep_no}] 分词 + 注音…")
    lines, hit, tothit, tottot = [], 0, 0, 0
    for s in sents:
        toks = tokenize(s)
        ann = annotate(toks, merged_seg, merged_plain, stem_map)
        jhtml = render_ja(ann)
        lines.append((jhtml, s))
        if any(rd for t in ann for _, rd in t["segs"]):
            hit += 1
        for t in ann:
            if re.search(r"[一-鿿]", t["surface"]):
                tottot += 1
                if any(rd for _, rd in t["segs"]):
                    tothit += 1
    pct = tothit / tottot * 100 if tottot else 0
    print(f"[{ep_no}] 注音: {hit}/{len(lines)} 句含 ruby；汉字 token 覆盖 {tothit}/{tottot} = {pct:.1f}%")

    print(f"[{ep_no}] 翻译…")
    zh = translate_batch(sents, 0, workers=workers)
    print(f"[{ep_no}] 译文 {len(zh)}/{len(sents)}")

    html_out = build_html(title, lines, zh)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[{ep_no}] → {out_path}  ({len(html_out):,} bytes)")
    return out_path

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--asr", required=True)
    ap.add_argument("--deck", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    txt = json.load(open(a.asr, encoding="utf-8"))["transcription"] if a.asr.endswith(".json") else open(a.asr, encoding="utf-8").read()
    build(a.ep, a.title, txt, a.deck or None, a.out, a.workers)
