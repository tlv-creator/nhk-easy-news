"""
修 fix_okurigana 的误伤。

原判据：段i读音末尾假名 == 下一段表层首假名 且 下一段无读音 ⇒ 削掉段i读音末字
误伤  ：汉字读音本身恰好以同音假名结尾（如 言う=い/う ⇒ 段i读音「い」，
        下一段表层「い」无读音 ⇒ 被当成「送り假名重复计入」⇒ 汉字读音被削空）

正确判据：**只有当段i是「汉字+送り假名」被拆开时**才可能重复计入。
  ⇒ 真正的重复计入形态：段i 末字是假名、且段i 的表层末字也是那个假名（即送り假名
    已经作为独立段出现），而段i 的读音还带着它。
  ⇒ 反例（本案）：段i 表层是「言」（汉字，无假名尾），读音「い」以假名结尾，
    但读音里的「い」是**汉字自己的读音**（言う=いう），不是重复计入的送り假名。

判据落点：**「被削的那个字」是不是 kanji** ⇒ 是 kanji 就不能削（它的读音是汉字的）
        ⇒ 只有纯假名段的读音末尾才允许被削。
"""
import re

KANA_END = re.compile(r'[ぁ-ゖ]$')
KANA_ONLY = re.compile(r'^[ぁ-ゖー]+$')

def is_kanji_only(s):
    return bool(s) and not KANA_ONLY.match(s)

def fix_okurigana_v2(tokens):
    """修好版：只削「纯假名段」的读音末字；汉字段的读音不动。"""
    for i in range(len(tokens) - 1):
        r = tokens[i]["r"]
        nxt = tokens[i + 1]
        if not r or not nxt["t"] or nxt["r"]:
            continue
        if not KANA_END.search(r):
            continue
        if nxt["t"][0] != r[-1]:
            continue
        # 🔴 关键修正：若本段表层**含汉字** ⇒ 它的读音是汉字自己的读音，
        #    不能因为末尾碰巧与下一段首字同音就削掉
        if is_kanji_only(tokens[i]["t"]):
            continue
        tokens[i]["r"] = r[:-1]
    return tokens
