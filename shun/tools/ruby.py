#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ruby.py —— 日文分词 + 振假名（读音）

契约（照已发布数据反推 + 实测校验）：
    输入：一行日文 t
    输出：[[表层, 读音], ...]，**完整覆盖 t**（逐字拼回去 == t）
          读音规则：
            · 只有含汉字的词才给读音（假名词给 ""）
            · 读音用**平假名**（已发布数据是平假名：きょう / ほん）
            · 复合词若「每个字都能单独读、且拼起来正好是这个词的读音」
              ⇒ **按字拆开**（已发布：本当 → 本(ほん)/当(とう)、友達 → 友(とも)/達(だち)）
              否则整词给读音（已发布：一緒 → いっしょ、今日 → きょう、日本 → にっぽん）

分词器：SudachiPy（sudachidict-core）
单字读音表：从已发布 12 集的 ru 数组里抽出来（438 个汉字）⇒ work/kanji_readings.json
"""
import re
import sys

_KANJI = re.compile(r"[一-鿿々〆ヵヶ]")

SENT_END = "。？！!?"


def has_kanji(s):
    return bool(_KANJI.search(s))


def katakana_to_hiragana(s):
    out = []
    for ch in s:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        elif code == 0x30FC:      # 长音 ー
            out.append("ー")
        else:
            out.append(ch)
    return "".join(out)


def morphemes(text, tokenizer):
    """→ [(表层, 读音(平假名), 词性), ...]"""
    out = []
    for tok in tokenizer.tokenize(text):
        surface = tok.surface()
        reading = katakana_to_hiragana(tok.reading_form() or surface)
        out.append((surface, reading, tok.part_of_speech()))
    return out


def _split_by_kanji(surface, reading, ktable):
    """复合词按字拆（条件：每个字都有单字读音，且拼起来 == 整词读音）"""
    ks = [c for c in surface if _KANJI.match(c)]
    if len(ks) < 2:
        return None
    parts, readings = [], []
    for c in surface:
        if _KANJI.match(c):
            r = ktable.get(c)
            if not r:
                return None
            parts.append(c)
            readings.append(r)
        else:
            parts.append(c)
            readings.append("")
    if "".join(readings) != reading:
        return None
    return [[p, r] for p, r in zip(parts, readings)]


def build_ru(text, tokenizer, ktable=None):
    """→ [[表层, 读音], ...]（读音只给含汉字的词；复合词按字拆）"""
    ktable = ktable or {}
    pairs = []
    for surface, reading, _pos in morphemes(text, tokenizer):
        if not surface:
            continue
        if not has_kanji(surface):
            pairs.append([surface, ""])
            continue
        if not reading or reading == surface:
            pairs.append([surface, ""])
            continue
        sp = _split_by_kanji(surface, reading, ktable)
        if sp:
            pairs.extend(sp)
        else:
            pairs.append([surface, reading])
    return pairs


def covers(pairs, text):
    """检查 pairs 是否完整覆盖 text（逐字拼回）"""
    return "".join(p[0] for p in pairs) == text


def get_tokenizer():
    from sudachipy import dictionary
    return dictionary.Dictionary().create()


if __name__ == "__main__":
    import json
    import os
    kt = {}
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "work", "kanji_readings.json")
    if os.path.exists(p):
        kt = json.load(open(p))
    tok = get_tokenizer()
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        pr = build_ru(line, tok, kt)
        ok = "OK " if covers(pr, line) else "❌ "
        print(ok, "".join((x[0] + ("(" + x[1] + ")" if x[1] else "")) for x in pr))
