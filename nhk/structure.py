#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整句成分分析：把日文句子切成成分块，标出角色。

角色 id 与前端 syntax.js 的 r 对应：
  0 主语  1 谓语  2 宾语  3 状语  4 修饰  5 从句  6 语气  7 其他
"""
import re
import sys

from janome.tokenizer import Tokenizer

_tok = Tokenizer()

NOMINALS = ("名詞", "代名詞", "数詞")
NP_TAIL = ("一般", "固有名詞", "組織", "人名", "地名", "副詞可能", "接尾", "数詞")


def _parts(pos):
    p = (pos or "").split(",")
    while len(p) < 2:
        p.append("*")
    return p[0], p[1], (p[2] if len(p) > 2 else "*")


def analyze(plain: str):
    """返回 [[start, len, role], ...]（相对 plain）"""
    toks = []
    pos = 0
    for t in _tok.tokenize(plain):
        surf = t.surface
        if not surf.strip():
            pos += len(surf)
            continue
        s, e = pos, pos + len(surf)
        pos = e
        toks.append((surf, t.part_of_speech, s, e))
    blocks = []
    i, n = 0, len(toks)
    while i < n:
        surf, pos_, s, e = toks[i]
        p0, p1, p2 = _parts(pos_)
        if p0 in NOMINALS:
            j = i
            while j + 1 < n:
                q0, q1, _ = _parts(toks[j + 1][1])
                if q0 in NOMINALS and q1 in NP_TAIL:
                    j += 1
                else:
                    break
            end = toks[j][3]
            role = "3"
            i = j + 1
            if i < n:
                r0, r1, r2 = _parts(toks[i][1])
                if r0 == "助詞" and r1 == "連体化":
                    role = "4"                       # 「〜の」= 修饰
                    end = toks[i][3]
                    i += 1
                elif r0 == "助詞" and r1 in ("格助詞", "係助詞"):
                    if toks[i][0] == "を":
                        role = "2"                   # 宾语
                    elif toks[i][0] in ("が", "は", "も"):
                        role = "0"                   # 主语
                    else:
                        role = "3"                   # 状语（に/で/へ…）
                    end = toks[i][3]
                    i += 1
            blocks.append([s, end - s, role])
            continue
        if p0 == "動詞":
            j = i
            while j + 1 < n and _parts(toks[j + 1][1])[0] == "助動詞":
                j += 1
            blocks.append([s, toks[j][3] - s, "1"])
            i = j + 1
            continue
        if p0 == "副詞":
            blocks.append([s, e - s, "3"])
            i += 1
            continue
        if p0 == "接続詞":
            blocks.append([s, e - s, "7"])
            i += 1
            continue
        if p0 in ("助詞", "助動詞", "判定詞"):
            blocks.append([s, e - s, "6"])
            i += 1
            continue
        if p0 == "形容詞":
            blocks.append([s, e - s, "4"])
            i += 1
            continue
        blocks.append([s, e - s, "7"])
        i += 1
    # 句末标点并入前一块
    if blocks and plain and plain.rstrip()[-1] in "。！？":
        pass
    out = []
    for b in blocks:
        if out and b[2] == "7" and b[1] <= 1 and plain[b[0]:b[0] + b[1]] in "。！？、，":
            out[-1][1] += b[1]
            continue
        out.append(b)
    return out


if __name__ == "__main__":
    for t in sys.argv[1:]:
        print(t)
        print("   ", analyze(t))
