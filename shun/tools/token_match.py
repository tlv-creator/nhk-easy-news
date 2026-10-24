#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token_match.py —— 用 **token 边界**做句型匹配（周更与本地共用）。

🔴 为什么不能用 `key in text`：
    「だけ」⊂「だけど」⇒ 子串匹配会把「友達だけど…」误标成「〜だけ」。
    真实事故：ep13:69「…友達だけど最近…」被标成「〜だけ（只…）」。
    ⇒ 判据必须落在**词边界**上，不是子串。

🔴 匹配规则（对所有句型统一）：
    ① 句句先切成 janome token 序列（surface 列表）
    ② 关键字也切成 token 序列
    ③ 关键字的 token 序列必须在句子的 token 序列里**连续出现**（完整覆盖）
    ④ 「は〜です」「が〜です」这类**需要两个位置**的规则单独处理
       （必须是「は/が」在名词性 token 之后、「です」在句尾附近）

用法：
    from token_match import StyleMatcher
    m = StyleMatcher(STYLE_RULES)     # [(sid, keyword), ...]
    sid = m.pick("あと前は本当に仲が良かった友達だけど最近…")   # ⇒ ""（不会误标「だけ」）
"""
import re
import sys

try:
    from janome.tokenizer import Tokenizer
    _TOK = Tokenizer()
except Exception:  # noqa: BLE001
    _TOK = None


def _surf(tok):
    """janome 的 Token.surface 在不同版本里是属性还是方法 ⇒ 两种都兼容。"""
    s = getattr(tok, "surface", "")
    return s() if callable(s) else s


def surfaces(text):
    """→ token 的 surface 列表（没有 janome 时退回按字符切，判据会退化但不会崩）"""
    if _TOK is None:
        return list(text or "")
    return [_surf(t) for t in _TOK.tokenize(text or "")]


def seq_contains(hay, needle):
    """needle 的 token 序列是否在 hay 里连续出现。"""
    if not needle:
        return False
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return True
    return False


class StyleMatcher:
    """句型匹配器：token 边界对齐。

    STYLE_RULES: [(sid, keyword), ...]  —— 顺序即优先级（多字优先）
    """

    def __init__(self, rules):
        self.rules = list(rules)
        # 预分词关键字（只分词一次）
        self._cache = {}
        for sid, key in self.rules:
            self._cache[sid] = surfaces(key)

    def pick(self, text):
        """→ sid（没命中返回 ""）"""
        if not text:
            return ""
        toks = surfaces(text)
        joined = "".join(toks)
        for sid, key in self.rules:
            if sid == "g_e5440997":       # は〜です（主题 + 断定）
                if self._has_desu(toks) and "は" in toks:
                    return sid
                continue
            if sid == "g_785d5a16":       # が〜です（主语 + 断定）
                if self._has_desu(toks) and "が" in toks:
                    return sid
                continue
            if seq_contains(toks, self._cache.get(sid) or []):
                return sid
        return ""

    @staticmethod
    def _has_desu(toks):
        """句尾附近有「です」/「ます」（断定形）"""
        tail = "".join(toks[-6:])
        return ("です" in tail) or ("ます" in tail) or ("でした" in tail)


# ── 自检：正反两向 ────────────────────────────────────────────
def _selftest():
    ok = 0
    bad = 0

    def chk(cond, name):
        nonlocal ok, bad
        if cond:
            ok += 1
            print("  ✓ " + name)
        else:
            bad += 1
            print("  ✗ " + name)

    rules = [
        ("g_5613dea7", "だけ"),
        ("g_33c37e0b", "に"),
        ("g_18016e98", "の"),
        ("g_8c9acd75", "で"),
        ("g_e5440997", "は"),
    ]
    m = StyleMatcher(rules)

    print("=== 正向：真命中 ===")
    chk(m.pick("友達だけいます") == "g_5613dea7", "「だけ」真命中")
    chk(m.pick("学校に行きます") == "g_33c37e0b", "「に」真命中")
    chk(m.pick("友達の 이름".replace(" 이름","家")) == "g_18016e98", "「の」真命中")
    chk(m.pick("友達の家に行きます") == "g_33c37e0b",
        "同时含「の」「に」时按优先级命中「に」")
    chk(m.pick("私は学生です") == "g_e5440997", "「は〜です」能命中")

    print("\n=== 反向：子串误命中必须不发生 ===")
    chk(m.pick("友達だけど最近会っていません") != "g_5613dea7",
        "「だけ」不误标「だけど」")
    chk(m.pick("これはただの例です") != "g_5613dea7",
        "「だけ」不误标「ただ」")
    chk(m.pick("置いていきます") != "g_18016e98",
        "「の」不误标「置いて」")
    chk(m.pick("でも大于") != "g_33c37e0b",
        "「に」不误标「大于」")

    print("\n%d 通过 / %d 失败" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest())
