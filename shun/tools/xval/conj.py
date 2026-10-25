#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
形态链: 辞書形 → て形 → 意志形 (+ ば形)
依据 IPADIC 的活用型推导; 不规则动词单独列表。
"""

# 五段: 词尾 → (て形词尾, 意志形词尾, ば形词尾)
GODAN = {
    'う': ('って', 'おう', 'えば'),   # 買う → 買って/買おう/買えば
    'つ': ('って', 'とう', 'てば'),   # 待つ → 待って/待とう/待てば
    'る': ('って', 'ろう', 'れば'),   # 取る → 取って/取ろう/取れば
    'ぬ': ('んで', 'のう', 'ねば'),   # 死ぬ → 死んで/死のう/死ねば
    'ぶ': ('んで', 'ぼう', 'べば'),   # 飛ぶ → 飛んで/飛ぼう/飛べば
    'む': ('んで', 'もう', 'めば'),   # 読む → 読んで/読もう/読めば
    'く': ('いて', 'こう', 'けば'),   # 書く → 書いて/書こう/書けば
    'ぐ': ('いで', 'ごう', 'げば'),   # 泳ぐ → 泳いで/泳ごう/泳げば
    'す': ('して', 'そう', 'せば'),   # 話す → 話して/話そう/話せば
}

# 下二 (言う/帰る/死ぬ/作る/…)
KIDAN = {
    'う': ('って', 'おう', 'えば'),   # 言う → 言って/言おう/言えば
    'る': ('って', 'ろう', 'れば'),   # 帰る → 帰って/帰ろう/帰れば
    'く': ('いて', 'こう', 'けば'),   # 聞く → 聞いて/聞こう/聞けば
}

# 不规则 / 特殊 / 例外
IRREG = {
    '行く':   ('行って', '行こう', '行けば'),
    'する':   ('して',   'しよう', 'すれば'),
    '来る':   ('来て',   '来よう', '来れば'),
    'した':   ('して',   'しよう', 'すれば'),
    'いい':   ('よくて', 'よかろう', 'よければ'),
    '良い':   ('よくて', 'よかろう', 'よければ'),
    '無い':   ('なくて', 'なかろう', 'なければ'),
    'ない':   ('なくて', 'なかろう', 'なければ'),
    # 上一段「る」行例外: て形是「〜って」而不是「〜て」
    '帰る':   ('帰って', '帰ろう', '帰れば'),
    '入る':   ('入って', '入ろう', '入れば'),
    '要る':   ('要って', '要ろう', '要れば'),
    '送る':   ('送って', '送ろう', '送れば'),
    '得る':   ('得て',   '得よう', '得れば'),
    '減る':   ('減って', '減ろう', '減れば'),
}


def conjugate(base, ct):
    """返回 (て形, 意志形, ば形) 或 None"""
    if not base or base == '*':
        return None
    if base in IRREG:
        return IRREG[base]
    if ct.startswith('形容詞'):
        # 高い → 高くて / 高かろう / 高ければ
        # 大きい → 大きくて / 大きかろう / 大きければ
        if len(base) < 2:
            return None
        return (base[:-1] + 'くて', base[:-1] + 'かろう', base[:-1] + 'ければ')
    if ct.startswith('不変化'):
        return (base + 'て', base + 'よう', base + 'ば')
    if ct.startswith('サ変'):
        if len(base) < 2:
            return None
        return (base[:-1] + 'て', base[:-1] + 'よう', base[:-1] + 'れば')
    if ct.startswith('一段') or ct.startswith('上一段'):
        if len(base) < 2:
            return None
        return (base[:-1] + 'て', base[:-1] + 'よう', base[:-1] + 'れば')
    if ct.startswith('五段'):
        last = base[-1]
        if last not in GODAN:
            return None
        te, vol, ba = GODAN[last]
        stem = base[:-1]
        return (stem + te, stem + vol, stem + ba)
    if ct.startswith('下二'):
        last = base[-1]
        if last not in KIDAN:
            return None
        te, vol, ba = KIDAN[last]
        stem = base[:-1]
        return (stem + te, stem + vol, stem + ba)
    if ct.startswith('カ変'):
        if base.endswith('来る'):
            return ('来て', '来よう', '来れば')
        return None
    return None


def chain(base, ct):
    """4 步: 辞書形 → て形 → 意志形 → ば形"""
    c = conjugate(base, ct)
    if not c:
        return None
    te, vol, ba = c
    return [base, te, vol, ba]
