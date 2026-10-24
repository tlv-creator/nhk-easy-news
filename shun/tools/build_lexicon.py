#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_lexicon.py —— 新一集带进来的**词库 / 句型表**更新

做三件事：
    ① 词库：把新一集里出现、但词库里还没有的词，用 MiniMax 补「词性/释义/用法/读音」
       ⇒ 合并进 data/lexicon/vocab.json（已有词不动）
    ② 句型说明（styles.json）：给新一集的句子挑一个句型 ⇒ 行的 st 字段
    ③ 句型表（grammar.json）：把新一集的例句挂到对应句型下 ⇒ n 计数 + exs

句型判据（**多字优先**：越具体的句型越有用）
    「〜ことができます」比「と」有用；「と」几乎每句都有 ⇒ 只在前面的都不匹配时才用它

用法：
    python3 tools/build_lexicon.py --ep 13 --cues work/cues/ep13_cues.json \
        --zh work/zh/ep13_zh.json --lexdir web/data/lexicon --out work/lex/ep13_lex.json
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ruby import get_tokenizer  # noqa: E402

API = "https://api.minimax.chat/v1/text/chatcompletion_v2"
MODEL = "MiniMax-Text-01"

DEF_PROMPT = """你是日语词典编纂。下面给你一批日语单词，请给每个词写一条词典条目。

要求：
1. 严格输出 JSON 数组，每个元素：{{"w":"单词","pos":"词性","def":"中文释义","usage":"一句中文用法说明","reading":"平假名读音"}}
2. 释义用中文，简洁（10 字以内），用法说明 1 句（20 字以内），讲清怎么用。
3. reading 必须是**平假名**读音（例如 ともだち / かぞく）；没有读音的（标点、纯数字）就给 ""。
4. 词性用中文：名词/动词/形容词/副词/助词/叹词/连词/代词/数词/接头词/结尾词 等。
5. 只输出 JSON 数组，不要解释。

单词列表：
{items}"""


def call_minimax(prompt, retries=3):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8000, "temperature": 0.2}
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    for _ in range(retries):
        p = subprocess.run(["curl", "-s", "-X", "POST", API,
                            "-H", "Content-Type: application/json",
                            "-H", "Authorization: Bearer " + key,
                            "-d", json.dumps(body, ensure_ascii=False)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        try:
            d = json.loads(p.stdout.decode())
        except Exception:
            continue
        if d.get("choices"):
            return d["choices"][0]["message"]["content"]
    raise RuntimeError("MiniMax 调用失败")


def parse_json(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    s = re.sub(r",\s*([\]}])", r"\1", m.group(0))
    try:
        return json.loads(s)
    except Exception:
        return []


# ── 句型：多字优先 ──────────────────────────────────────────────
STYLE_RULES = [
    ("g_39c6268b", "ことができます"),
    ("g_a06a82a4", "と思いました"),
    ("g_25e917d6", "よね"),
    ("g_07e72e9a", "ませんか"),
    ("g_c108a4db", "ましょう"),
    ("g_5f03141d", "ますか"),
    ("g_5613dea7", "だけ"),
    ("g_6d10809c", "たら"),
    ("g_0d6450f0", "から"),
    ("g_48434eb5", "たり"),
    ("g_1e2b941d", "より"),
    ("g_64bce3c6", "まで"),
    ("g_e5440997", "は"),
    ("g_785d5a16", "が"),
    ("g_cf780f0b", "を"),
    ("g_53d147c3", "と"),
    ("g_18016e98", "の"),
    ("g_33c37e0b", "に"),
    ("g_8c9acd75", "で"),
    ("g_99696fa1", "は"),
]


# 🔴 句型匹配必须走 **token 边界**（tools/token_match.py），不能用 `key in t`：
#     「だけ」⊂「だけど」⇒ 子串匹配把「…友達だけど最近…」误标成「〜だけ（只…）」。
#     真实事故：ep13:69。⇒ 判据落在词边界上，不是子串。
from token_match import StyleMatcher  # noqa: E402

_MATCHER = StyleMatcher(STYLE_RULES)


def pick_style(t):
    return _MATCHER.pick(t)


def style_to_grammar_key(styles, gr, sid):
    name = styles.get(sid, {}).get("name", "")
    for k, v in gr.items():
        if v.get("name", "").split("〜")[0] == name.split("〜")[0]:
            return k
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--zh", required=True)
    ap.add_argument("--lexdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=40)
    args = ap.parse_args()

    cues = json.load(open(args.cues))["cues"]
    zh = json.load(open(args.zh))
    vp = os.path.join(args.lexdir, "vocab.json")
    sp = os.path.join(args.lexdir, "styles.json")
    gp = os.path.join(args.lexdir, "grammar.json")
    vocab = json.load(open(vp))
    styles = json.load(open(sp))
    gr = json.load(open(gp))

    tok = get_tokenizer()

    # ① 新一集用到的词（按词库里最长匹配的方式合并，和 App 的 buildWords 一致）
    words_in_ep = {}
    for i, c in enumerate(cues):
        segs = []
        for t in tok.tokenize(c["t"]):
            segs.append(t.surface())
        # 用词库做最长匹配合并（与前端一致）
        arr = list(segs)
        merged = []
        p = 0
        while p < len(arr):
            hit = 1
            for ln in range(min(6, len(arr) - p), 1, -1):
                cand = "".join(arr[p:p + ln])
                if cand in vocab:
                    hit = ln
                    break
            merged.append("".join(arr[p:p + hit]))
            p += hit
        for w in merged:
            if w and w not in vocab:
                words_in_ep.setdefault(w, []).append(i)

    new_words = sorted(words_in_ep.keys())
    print("新词 %d 个" % len(new_words), file=sys.stderr)

    # ② MiniMax 补词条
    defs = {}
    for s in range(0, len(new_words), args.shard):
        part = new_words[s:s + args.shard]
        items = "\n".join("- " + w for w in part)
        try:
            arr = parse_json(call_minimax(DEF_PROMPT.format(items=items)))
        except Exception as e:  # noqa: BLE001
            print("  ⚠️ 词条生成失败: %s" % e, file=sys.stderr)
            continue
        for x in arr:
            w = (x.get("w") or "").strip()
            if w:
                defs[w] = {
                    "pos": (x.get("pos") or "").strip(),
                    "def": (x.get("def") or "").strip(),
                    "usage": (x.get("usage") or "").strip(),
                    "reading": (x.get("reading") or "").strip(),
                }
        print("  词条 %d/%d" % (len(defs), len(new_words)), file=sys.stderr)

    for w in new_words:
        if w in defs:
            d = defs[w]
            ex_i = words_in_ep[w][0]
            vocab[w] = {
                "pos": d.get("pos") or "名词",
                "def": d.get("def") or "",
                "usage": d.get("usage") or "",
                "ex": cues[ex_i]["t"],
                "exzh": zh.get(str(ex_i), ""),
                "reading": d.get("reading") or "",
            }
        else:
            vocab[w] = {"pos": "名词", "def": "", "usage": "", "reading": ""}

    # ③ 句型：给每行挑 st，并挂进 grammar.json
    tag = "ep%02d" % args.ep
    st_assign = {}
    for i, c in enumerate(cues):
        sid = pick_style(c["t"])
        st_assign[str(i)] = sid
        if not sid:
            continue
        gk = style_to_grammar_key(styles, gr, sid)
        if not gk:
            continue
        entry = gr[gk]
        exs = entry.setdefault("exs", [])
        if not any(e.get("ja") == c["t"] and e.get("ep") == tag for e in exs):
            exs.append({"ja": c["t"], "zh": zh.get(str(i), ""), "ep": tag, "i": i})
            # 🔴 n = 该句型在**全剧**的出现次数（exs 只是样本，两者不相等是正常的）
            entry["n"] = int(entry.get("n", 0)) + 1
        if not entry.get("ex"):
            entry["ex"] = c["t"]
            entry["exzh"] = zh.get(str(i), "")

    with open(vp, "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False, indent=1)
    with open(gp, "w", encoding="utf-8") as f:
        json.dump(gr, f, ensure_ascii=False, indent=1)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(styles, f, ensure_ascii=False, indent=1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"st": st_assign, "new_words": new_words,
                   "defs_added": len(defs)}, f, ensure_ascii=False, indent=1)
    print("✅ 词库 %d 条 ｜ 句型表 %d 条 → %s" % (len(vocab), len(gr), args.out),
          file=sys.stderr)


if __name__ == "__main__":
    main()
