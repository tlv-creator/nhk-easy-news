#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 「哪些词不值得挂在句子上」的**唯一权威**（2026-10-24 抽出来）

为什么抽出来：
    原来这套规则写在 `build_data.py` 里，`scripts/build_sp.py` 要用就得 import 它
    ⇒ 而 build_data.py 在 **import 时**就读 `sys.argv[1]`（`NEP = int(sys.argv[1])`）
    ⇒ 别的脚本一 import 就炸（`invalid literal for int() with base 10: '--ids'`）
    ⇒ 规则也就没法被第二个脚本复用。
    ⇒ 现在：规则住在这里，build_data.py 和 build_sp.py **都** import 它
    ⇒ **同一份配方只写一遍**。

判据（落在事实量上）：
    · 超高频基础词（する/てる/こと/はい…）→ 不挂
    · 纯功能词性（助词/助动词/接续词…）→ 不挂
    · 出现期数占比 ≥ FREQ_RATIO 的高频词 → 不挂（避免刷屏）
"""

# 不值得挂在句子上刷屏的词（超高频基础词）
SKIP_WORDS = set("""
する した して してる します しました される してる てる あった ある いた いて いる いない
なる なった なると なれば なった してる
こと もの とき ところ これ それ あれ この その あの ここ そこ あそこ こちら どこ どれ
私 僕 わたし あなた あなた方 皆さん 各国
はい いいえ うん そう まあ ねえ ね よ ええ
です ます ました でした ません だった であった だ である な ない
ください どうぞ ありがとう すみません ごめんなさい
人 人々 日本 世界 場合
""".split())

import re as _re
SKIP_WORDS = set(w for w in SKIP_WORDS if _re.match(r"^[ぁ-んァ-ヶ一-鿿]+$", w))

# 纯功能词性的词不作为「生词」标记
SKIP_POS = ("助詞", "助動詞", "接続詞", "感動詞", "フィラー", "記号", "判定詞", "接頭詞")

# 出现期数占比 ≥ 此值的高频词不挂到句子上（避免刷屏）
FREQ_RATIO = 0.45


def is_skippable(w, pos, freq):
    if w in SKIP_WORDS:
        return True
    p0 = (pos or "").split(",")[0]
    if p0 in SKIP_POS:
        return True
    if freq.get(w, 0) >= FREQ_RATIO * freq.get("__total__", 1):
        return True
    return False
