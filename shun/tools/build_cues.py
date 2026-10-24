#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_cues.py —— 把「MiniMax 干净文本」对齐到「whisper 时间轴」，切成字幕行

🔴 切行规则是**照已发布数据反推出来的**（已发布 12 集 1355 行的统计）：
        行时长   中位 3.80s ／ p75 5.85s ／ p90 7.91s
        行间停顿 中位 1.88s ／ p25 1.01s
        行字数   中位 15 ／ p75 21 ／ p90 28
        密度     0.151 ~ 0.165 行/秒（每集都稳定在这个区间）
    ⇒ **行的边界 = 说话人自然停顿**（不是标点、不是固定时长）

做法：
    ① whisper 词级时间轴按「词间空隙 ≥ PAUSE」切成停顿块 ⇒ 行的时间骨架
       （实测：PAUSE=0.75 ⇒ 135 块，行时长中位 3.82s，与已发布 3.80s 吻合）
    ② MiniMax 逐块转写（字准、有标点）↔ whisper 逐字符对齐 ⇒ 行的字
       · 对上的字按「属于哪个块」归位
       · 没对上的字（标题卡的英文等）**只在时间上紧邻本块时才并进来**（不让它撑爆整行）
    ③ 行太长 ⇒ 按空格/标点再切一次（时间按比例摊）

用法：
    python3 tools/build_cues.py --asr work/asr/ep13.json --mm work/asr/ep13_mm.json \
        --out work/cues/ep13_cues.json
"""
import argparse
import difflib
import json
import os
import re
import sys

SENT_END = "。？！!?"
SPACE_RE = re.compile(r"[\s　]+")


def norm_char(ch):
    if ch in SENT_END or ch in "、。．，,·":
        return None
    if SPACE_RE.match(ch):
        return None
    return ch.lower()


def build_timeline(words, lo=None, hi=None):
    """→ [(char, start, end, word_index)]"""
    out = []
    for wi, w in enumerate(words):
        if lo is not None and w["e"] < lo:
            continue
        if hi is not None and w["s"] > hi:
            continue
        for ch in w["w"]:
            if SPACE_RE.match(ch):
                continue
            out.append((ch, w["s"], w["e"], wi))
    return out


def align_block(mm_text, tl):
    """→ {mm_char_index: (start, end, word_index)}"""
    mm_idx, mm_norm = [], []
    for i, ch in enumerate(mm_text):
        n = norm_char(ch)
        if n is None:
            continue
        mm_idx.append(i)
        mm_norm.append(n)
    w_idx, w_norm = [], []
    for i, (ch, s, e, wi) in enumerate(tl):
        n = norm_char(ch)
        if n is None:
            continue
        w_idx.append(i)
        w_norm.append(n.lower())

    times = {}
    sm = difflib.SequenceMatcher(None, "".join(mm_norm), "".join(w_norm), autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            continue
        for k in range(i2 - i1):
            times[mm_idx[i1 + k]] = (tl[w_idx[j1 + k]][1], tl[w_idx[j1 + k]][2],
                                     tl[w_idx[j1 + k]][3])
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr", required=True)
    ap.add_argument("--mm", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pause", type=float, default=0.75)
    ap.add_argument("--max-sec", type=float, default=9.0)
    ap.add_argument("--max-chars", type=int, default=42)
    ap.add_argument("--near-sec", type=float, default=0.9)
    ap.add_argument("--merge-max-chars", type=int, default=20,
                    help="上一行短于这个字数才允许并入下一行（长句自成一行）")
    ap.add_argument("--intro", default=None,
                    help="标题卡的人工行表（JSON: [{s,e,t}]）⇒ 覆盖该时间之前的行")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    asr = json.load(open(args.asr))
    mm = json.load(open(args.mm))

    words = []
    for seg in asr["segments"]:
        for w in seg["words"]:
            if w["w"]:
                words.append(w)
    words.sort(key=lambda w: w["s"])
    nw = len(words)

    # ① 逐块对齐 → mm 字符 ↔ whisper 词
    mm_text, ctime = [], {}
    base = 0
    for ch in mm["chunks"]:
        # 🔴 MiniMax 的空格是**它自己的分词**（会把「色々な仕事」切成「色々 / な仕事」）
        #    ⇒ 加载时就去掉空格，词边界交给 Sudachi 判断
        ctext = re.sub(r"[\s\u3000]+", "", ch["text"])
        tl = build_timeline(words, ch["start"], ch["end"])
        bt = align_block(ctext, tl)
        for i, c in enumerate(ctext):
            mm_text.append(c)
            if i in bt:
                ctime[base + i] = bt[i]
        base += len(ctext)   # 🔴 必须用**去掉空格后**的长度，否则全局下标会错位
    n = len(mm_text)

    # 未匹配的字符 → 用邻居时间（只用于「是否紧邻」的判断）
    known = sorted(ctime.keys())
    for i in range(n):
        if i in ctime:
            continue
        prev = max([k for k in known if k < i], default=None)
        nxt = min([k for k in known if k > i], default=None)
        if prev is not None and nxt is not None:
            ctime[i] = (ctime[prev][1], ctime[nxt][0], None)
        elif prev is not None:
            ctime[i] = (ctime[prev][0], ctime[prev][1], None)
        elif nxt is not None:
            ctime[i] = (ctime[nxt][0], ctime[nxt][1], None)

    # ② whisper 词 → 停顿块
    groups, cur = [], []
    for wi in range(nw):
        if cur and (words[wi]["s"] - words[cur[-1]]["e"]) >= args.pause:
            groups.append(cur)
            cur = []
        cur.append(wi)
    if cur:
        groups.append(cur)

    # ③ 每个块：**按词**归属（不是按字！）
    #    🔴 按字归属会切在词中间（「\+各種」→ 行首只剩「な仕事」）⇒ 行首/行尾都缺字
    #    ⇒ 先把 mm 原文切成词（Sudachi），一个词整体归一个块 ⇒ 行边界天然在词边界上
    owner = {}
    for i in range(n):
        wi = ctime[i][2]
        if wi is None:
            continue
        for gi, g in enumerate(groups):
            if g[0] <= wi <= g[-1]:
                owner[i] = gi
                break
    try:
        from ruby import get_tokenizer
        _tok = get_tokenizer()
        _full = "".join(mm_text)
        wspan, _pos = [], 0
        for t in _tok.tokenize(_full):
            wspan.append((_pos, _pos + len(t.surface())))
            _pos += len(t.surface())
    except Exception as e:  # noqa: BLE001
        print("⚠️ 分词跳过: %s" % e, file=sys.stderr)
        wspan = [(i, i + 1) for i in range(n)]
    # 每个词 → 归属块（按块内字数的多数票；没有对上的字 ⇒ 跟左右邻块）
    word_owner = []
    for (a, z) in wspan:
        votes = {}
        for i in range(a, min(z, n)):
            gi = owner.get(i)
            if gi is not None:
                votes[gi] = votes.get(gi, 0) + 1
        if votes:
            word_owner.append(max(votes.items(), key=lambda kv: kv[1])[0])
        else:
            word_owner.append(None)
    # 🔴 没票的词（对不上音频的）⇒ **按它自己的时间**归到那一组
    #    （不能用「前一个/后一个」：那会把「妻… My wife」拆到两组去，
    #      已发布数据是「妻Mywife」这种连写）
    def block_by_time(t0):
        for gi, g in enumerate(groups):
            gs, ge = words[g[0]]["s"], words[g[-1]]["e"]
            if gs - 0.35 <= t0 <= ge + 0.35:
                return gi
        best, bestd = None, 1e9
        for gi, g in enumerate(groups):
            gs, ge = words[g[0]]["s"], words[g[-1]]["e"]
            d = min(abs(t0 - gs), abs(t0 - ge))
            if d < bestd:
                best, bestd = gi, d
        return best

    for k, (a, z) in enumerate(wspan):
        if word_owner[k] is not None:
            continue
        ts = [ctime[i][0] for i in range(a, min(z, n)) if i in ctime]
        if not ts:
            continue
        word_owner[k] = block_by_time(sorted(ts)[len(ts) // 2])

    # 🔴 **按「词」逐个归属 + 连续成行**（不要用 min..max 的范围）：
    #    范围写法遇到「一个词错配到隔壁组」就会把整段文字撑过来
    #    （实测：一行 2.1 秒却塞了 40 个字）
    runs = []      # [(group_index, [word_idx...])]
    for k, o in enumerate(word_owner):
        if runs and runs[-1][0] == o:
            runs[-1][1].append(k)
        else:
            runs.append((o, [k]))

    if os.environ.get("CUE_DEBUG"):
        for k in range(90, 108):
            a, z = wspan[k]
            votes = {}
            for i in range(a, min(z, n)):
                gi = owner.get(i)
                if gi is not None:
                    votes[gi] = votes.get(gi, 0) + 1
            print("W%03d %-10s owner=%s votes=%s" % (k, "".join(mm_text[a:z]), word_owner[k], votes), file=sys.stderr)
        for gi, wlist in runs:
            a = wspan[wlist[0]][0]; z = wspan[wlist[-1]][1]
            txt = "".join(mm_text[a:z])
            if "家族" in txt or "この研究" in txt:
                print("RUN owner=%s span=%d:%d %r" % (gi, a, z, txt[:60]), file=sys.stderr)
    # ④ 出行：**一句一行**（先按句末标点切开，时间按这一段自己的字算）
    #    不切开的话，「第一句」和「下一段内容」会挤在一行里 ⇒ 时间对不上（实测 17s 空白）
    rows = []
    for gi, wlist in runs:
        if gi is None or not (0 <= gi < len(groups)):
            continue
        g = groups[gi]
        a = wspan[wlist[0]][0]
        z = wspan[wlist[-1]][1]
        # 句末标点 ⇒ 切点（标点跟着前一句）
        cuts = [a]
        for i in range(a, z):
            if mm_text[i] in SENT_END:
                cuts.append(i + 1)
        if cuts[-1] != z:
            cuts.append(z)
        # 🔴 **MiniMax 的字跟音频对不上时，用本地 ASR 的字**
        #    （节目里有一段「本集用到的单词」：主持人念「つま… My wife」，
        #      MiniMax 写的是「妻 My wife」⇒ 对不上 ⇒ 这一段就该用 ASR 的音译字，
        #      而且**已发布数据就是这么记的**：「妻Mywife」这种日语+英文连写）
        _mmc = sum(1 for i in range(a, z) if i in ctime and ctime[i][2] is not None)
        _tot = max(1, z - a)
        low_match = (_mmc / _tot) < 0.5
        for ci in range(len(cuts) - 1):
            a2, z2 = cuts[ci], cuts[ci + 1]
            text = re.sub(r"[\s\u3000]+", "", "".join(mm_text[a2:z2]).strip())
            if low_match:
                text = ""
            if not text:
                continue
            if low_match:
                # 用 whisper 的词文本（这一组的词），时间用词的起止
                wtext = re.sub(r"[\s\u3000]+", "",
                               "".join(words[wi]["w"] for wi in g)).strip()
                if not wtext:
                    continue
                rows.append({"s": words[g[0]]["s"], "e": words[g[-1]]["e"],
                             "t": wtext, "a": a, "z": z})
                continue
            ts = [ctime[i][0] for i in range(a2, z2) if i in ctime and ctime[i][2] is not None]
            te = [ctime[i][1] for i in range(a2, z2) if i in ctime and ctime[i][2] is not None]
            if ts and te:
                s_, e_ = min(ts), max(te)
            else:
                s_, e_ = words[g[0]]["s"], words[g[-1]]["e"]
            rows.append({"s": s_, "e": e_, "t": text, "a": a2, "z": z2})

    # ⑤ 太长的行按空格/标点再切（时间按比例摊）
    expanded = []
    for r in rows:
        if (r["e"] - r["s"]) <= args.max_sec and len(r["t"]) <= args.max_chars:
            expanded.append(r)
            continue
        parts, cur = [], []
        for ch in r["t"]:
            cur.append(ch)
            if ch in SENT_END or SPACE_RE.match(ch):
                if "".join(cur).strip():
                    parts.append("".join(cur).strip())
                cur = []
        if "".join(cur).strip():
            parts.append("".join(cur).strip())
        if len(parts) <= 1:
            expanded.append(r)
            continue
        # 🔴 parts 是「空格/标点切出来的小段」⇒ 要**再合成块**（每块 ~15 字），
        #    不能一小段就出一行（那会变成一行一个词）
        chunks, cur, curlen = [], [], 0
        for p in parts:
            cur.append(p)
            curlen += len(p)
            if curlen >= args.max_chars * 0.45:
                chunks.append("".join(cur))
                cur, curlen = [], 0
        if cur:
            chunks.append("".join(cur))
        np_ = len(chunks)
        for i, p in enumerate(chunks):
            f0, f1 = i / np_, (i + 1) / np_
            expanded.append({"s": r["s"] + (r["e"] - r["s"]) * f0,
                             "e": r["s"] + (r["e"] - r["s"]) * f1, "t": p})
    rows = expanded

    # 🔴 **时间归一**：相邻行不能重叠
    #    · 同一时间起头的后一行 = 碎片（没对上音频的字）⇒ 并进前一行
    #    · 真的重叠 ⇒ 把前一行截到后一行开始处
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        if b["s"] <= a["s"] + 0.01 and b.get("t"):
            a["t"] += b["t"]
            a["e"] = max(a["e"], b["e"])
            b["_drop"] = True
            continue
        if b["s"] < a["e"] - 0.01:
            a["e"] = max(a["s"] + 0.2, b["s"])
    rows = [r for r in rows if not r.get("_drop")]
    for r in rows:
        if r["e"] <= r["s"]:
            r["e"] = r["s"] + 0.2

    # 🔴 **填补字幕空洞**：两行之间空太久（> GAP_FILL）⇒ 中间那段没字幕
    #    （实测：节目里「Words and phrases used in this episode」那段
    #      本地 ASR 没认出词 ⇒ 没有行 ⇒ 出现 25s 空白 ⇒ 用户看字幕会以为没声音）
    #    ⇒ 用**没被任何行用掉的 mm 字**补上（时间取它们的插值时间）
    GAP_FILL = 6.0
    used = set()
    for r in rows:
        for i in range(r.get("a", 0), r.get("z", 0)):
            used.add(i)
    filler = []
    for i in range(len(rows) - 1):
        r1, r2 = rows[i], rows[i + 1]
        gap = r2["s"] - r1["e"]
        if gap <= GAP_FILL:
            continue
        lo = r1.get("z", 0)
        hi = r2.get("a", n)
        chars = [(c, ctime[k][0], ctime[k][1]) for k, c in enumerate(mm_text)
                 if lo <= k < hi and k not in used]
        if not chars:
            continue
        txt = re.sub(r"[\s\u3000]+", "", "".join(c for c, _, _ in chars))
        if not txt:
            continue
        filler.append({"s": min(x[1] for x in chars), "e": max(x[2] for x in chars),
                       "t": txt, "a": lo, "z": hi})
    if filler:
        rows.extend(filler)
        rows.sort(key=lambda r: r["s"])
        print("  ↩ 补了 %d 段空洞" % len(filler), file=sys.stderr)

    # 🔴 **把「半句」并回上一行**（照已发布数据的形态：每行是一个完整意思）
    #    已发布的行基本都是完整句（如「話したいと思います皆さんはどんな友達がいますか」）
    #    只按停顿切 ⇒ 会切出「前は」「いつか私の両親が」这种半句
    #    ⇒ 判据：**上一行末尾没有句末标点** ⇒ 说明这句话还没说完 ⇒ 并进下一行
    merged_rows = []
    for r in rows:
        if merged_rows and not merged_rows[-1]["t"].endswith(tuple(SENT_END)) \
                and len(merged_rows[-1]["t"]) < args.merge_max_chars \
                and (r["e"] - merged_rows[-1]["s"]) <= args.max_sec * 1.4 \
                and len(merged_rows[-1]["t"]) + len(r["t"]) <= args.max_chars * 1.3:
            merged_rows[-1]["e"] = r["e"]
            merged_rows[-1]["t"] += r["t"]
            continue
        merged_rows.append(dict(r))
    rows = merged_rows

    out = [{"s": round(r["s"], 2), "e": round(r["e"], 2), "t": r["t"]} for r in rows]

    # 🔴 **不丢整行英文**：已发布数据里 ep02/ep04 都保留了整行英文
    #    （"WhatwouldyoudoifAItookyourjob?" / Patreon 英文公告）⇒ 没有「按语言丢行」的规则
    #    只把「纯英文行」并到上一行（照已发布数据的形态：日语词 + 英文词连写，如「妻Mywife」）
    def latin_ratio(t):
        letters = [c for c in t if not c.isspace()]
        if not letters:
            return 0.0
        return sum(1 for c in letters if c.isascii() and c.isalpha()) / len(letters)

    for i in range(1, len(out)):
        if latin_ratio(out[i]["t"]) > 0.5 and latin_ratio(out[i - 1]["t"]) <= 0.5:
            out[i - 1]["t"] += out[i]["t"]
            out[i - 1]["e"] = out[i]["e"]
            out[i]["_drop"] = True
    out = [x for x in out if not x.get("_drop")]

    # 标题卡：人工行表覆盖（标题卡是「一句一停」的固定格式，交给机器切必然切碎）
    if args.intro:
        intro = json.load(open(args.intro))
        end = max(x["e"] for x in intro)
        out = [x for x in out if x["s"] >= end - 0.01] + [
            {"s": round(x["s"], 2), "e": round(x["e"], 2), "t": x["t"]} for x in intro]
        out.sort(key=lambda x: x["s"])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"cues": out, "n_cues": len(out)}, f, ensure_ascii=False, indent=1)

    if args.report:
        durs = [c["e"] - c["s"] for c in out]
        lens = [len(c["t"]) for c in out]
        gaps = [out[i]["s"] - out[i - 1]["e"] for i in range(1, len(out))]

        def stat(name, v):
            v = sorted(v)
            if not v:
                return
            print("  %-5s n=%4d min=%.2f p25=%.2f med=%.2f p75=%.2f p90=%.2f max=%.2f"
                  % (name, len(v), v[0], v[len(v)//4], v[len(v)//2], v[len(v)*3//4],
                     v[int(len(v)*0.9)], v[-1]))
        print("== 对照已发布：中位时长3.80 行距1.88 字数15 密度0.155/s ==")
        stat("时长", durs)
        stat("行距", gaps)
        stat("字数", lens)
        total = out[-1]["e"] if out else 1
        print("  密度 %.3f 行/秒（%d 行 / %.0fs）" % (len(out) / total, len(out), total))
    print("✅ %d 行 → %s" % (len(out), args.out), file=sys.stderr)


if __name__ == "__main__":
    main()
