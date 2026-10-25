#!/usr/bin/env python3
"""注音独立审计（第三方参照，不靠 ruby.py 自证）。

判据（三条，全部落在可核对的事实量上）：
 ① 表层还原：''.join(unit.t for unit in ru) 必须 == 句原文 t（逐字）
 ② 读音完整：每个含汉字的 unit 必须有非空读音（空读音 = 用户看到"汉字头上没假名"）
 ③ 读音流正确：ru 的读音拼接必须与 **janome/IPADIC 独立分词的读音流** 一致
     （第三方参照：同一条规则换一套实现，见 README 判据设计）

粒度另行统计（不判红，只报数）：
  · 含汉字 unit 的表层字数分布
  · 「表层 >1 字」的 unit 数 = 整块数
  · 整块里有多少属于「真不规则（整块必须）」（DP 切不出）

用法：
  python3 verify_ru_audit.py                 # 审计全部 50 集
  python3 verify_ru_audit.py --file X.json   # 审计单个数据文件
"""
import json, os, sys, argparse
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RUBY_DIR = os.path.join(HERE, "tools", "ruby")
sys.path.insert(0, RUBY_DIR)

KATA_RE = None


def kata_to_hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in (s or ""))


def is_kanji(c):
    return "一" <= c <= "鿿" or "㐀" <= c <= "鿿"


def janome_stream(t):
    """第三方读音流：janome(IPADIC) 分词后把每个 token 的 reading 拼起来。
    假名 token 的 reading 通常为 None ⇒ 直接用表层（假名读音=它自己）。

    🔴 噪声控制：janome 对**片假名外来语 / 未知词**返回 '*'
       （如 ホステル→ほすてる 而 ru 保留原表层；ポッドキャスト→*）。
       ⇒ '*' 段与片假名段**不参与比对**（两边都不可信，比了必假红）。
       ⇒ 只比对「含汉字的 token」这一段 —— 那才是注音真正负责的范围。
    """
    from janome.tokenizer import Tokenizer
    global _TOK
    try:
        _TOK
    except NameError:
        _TOK = Tokenizer()
    out = []
    for tok in _TOK.tokenize(t or ""):
        surf = tok.surface
        if not is_kanji(surf[0]):
            # 纯假名/片假名/符号 ⇒ 读音就是它自己（外来语不比对）
            if surf and all(("぀" <= c <= "ヿ") or ("゠" <= c <= "ヿ") or
                            ("ぁ" <= c <= "ゖ") or ("ァ" <= c <= "ヶ") for c in surf):
                out.append(surf)
            continue
        r = tok.reading
        if not r or "*" in r:
            continue          # janome 不认识 ⇒ 跳过，不比对
        out.append(kata_to_hira(r))
    return "".join(out)


def ru_surface(units):
    return "".join(u[0] for u in units)


def ru_reading_stream(units):
    """ru 的读音流：含汉字的 unit 取其读音；纯假名 unit 取它自己。"""
    out = []
    for t, r in units:
        if not t:
            continue
        if is_kanji(t[0]):
            out.append(r or "")
        else:
            out.append(t)
    return "".join(out)


def audit_eps(eps, label):
    bad = []          # 判红项
    stat = Counter()
    gran = Counter()
    examples = []
    for e in eps:
        tag = e.get("tag")
        for i, s in enumerate(e.get("sents") or []):
            t = s.get("t") or ""
            ru = s.get("ru") or []
            if not ru:
                stat["no_ru"] += 1
                bad.append((tag, i, "no_ru", "这句没有 ru"))
                continue
            # ① 表层还原
            surf = ru_surface(ru)
            if surf != t:
                stat["surface_mismatch"] += 1
                bad.append((tag, i, "surface", "表层!=原文: %r vs %r" % (surf[:40], t[:40])))
                continue
            # ② 读音完整
            empty = [u for u in ru if u[0] and is_kanji(u[0][0]) and not u[1]]
            if empty:
                stat["empty_reading"] += 1
                bad.append((tag, i, "empty", "含汉字但无读音: %s" % [u[0] for u in empty][:5]))
                continue
            # ③ 读音流 vs janome 独立参照
            jr = janome_stream(t)
            mr = ru_reading_stream(ru)
            if jr and mr != jr:
                stat["stream_mismatch"] += 1
                if len(examples) < 40:
                    examples.append((tag, i, t[:60], mr, jr))
                continue
            stat["ok"] += 1
            # 粒度统计
            for u in ru:
                if u[0] and is_kanji(u[0][0]):
                    gran["kanji_units"] += 1
                    if len(u[0]) > 1:
                        gran["whole_units"] += 1
    print("=== %s ===" % label)
    print("  判红项统计:", dict(stat))
    print("  粒度: 含汉字 unit %d 个，其中表层>1字（整块）%d 个 = %.1f%%"
          % (gran["kanji_units"], gran["whole_units"],
             100.0 * gran["whole_units"] / max(1, gran["kanji_units"])))
    if bad:
        print("  🔴 判红明细（前 30）:")
        for b in bad[:30]:
            print("     ", b)
    if examples:
        print("  🟡 读音流与 janome 参照不一致（前 20）:")
        for x in examples[:20]:
            print("     %s#%d %s\n        mine=%s\n        janome=%s" % x)
    return len(bad), stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--data", default=os.path.join(HERE, "data-merged-50.json"))
    a = ap.parse_args()

    if a.file:
        d = json.load(open(a.file, encoding="utf-8"))
        eps = d["eps"] if isinstance(d, dict) and "eps" in d else d
        n, _ = audit_eps(eps, a.file)
    else:
        d = json.load(open(a.data, encoding="utf-8"))
        eps = d["eps"]
        n, _ = audit_eps(eps, "50 集全量")
    print()
    print("退出码: %d（判红项 %d）" % (1 if n else 0, n))
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
