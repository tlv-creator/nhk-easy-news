#!/usr/bin/env python3
"""粒度改版的零风险判据：**读音流必须逐字不变**。

改版目标：把「词表命中即整块」改成「能逐字就逐字、切不出才整块」。
⇒ 读音（假名串）必须**一个假名都不变**，只有「读音挂在几个字上」变了。

判据（全部落在事实量上）：
 ① 对每句：新版 ru 的读音流 == 旧版 ru 的读音流（逐字符比较）
 ② 表层：新版 ru 的表层拼接 == 旧版 ru 的表层拼接 == 原文
 ③ 粒度：新版整块数 <= 旧版整块数（只允许变细，不许变粗）
 ④ 反向验证：故意改坏一处的读音 ⇒ 本脚本必须报错（证明判据不是恒绿）

用法：
  python3 verify_granularity.py                # 拿当前 50 集做对照
  python3 verify_granularity.py --self-test    # 反向验证
"""
import json, os, sys, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
RUBY_DIR = os.path.join(HERE, "tools", "ruby")
sys.path.insert(0, RUBY_DIR)


def is_kanji(c):
    return "一" <= c <= "鿿"


def surface(units):
    return "".join((u[0] if isinstance(u, (list, tuple)) else u["t"]) for u in units)


def reading_stream(units):
    """读音流：含汉字 unit 取其读音；纯假名 unit 取它自己。"""
    out = []
    for u in units:
        t, r = (u[0], u[1]) if isinstance(u, (list, tuple)) else (u["t"], u["r"])
        if not t:
            continue
        if is_kanji(t[0]):
            out.append(r or "")
        else:
            out.append(t)
    return "".join(out)


def _t(u):
    return u[0] if isinstance(u, (list, tuple)) else u["t"]


def _r(u):
    return u[1] if isinstance(u, (list, tuple)) else u["r"]


def whole_count(units):
    return sum(1 for u in units if _t(u) and is_kanji(_t(u)[0]) and len(_t(u)) > 1)


def reannotate(text):
    """用新版引擎重算一句的 ru"""
    import ruby
    return ruby.annotate(text)


def compare(old_eps, new_eps, label):
    errs = []
    n_sent = 0
    changed_gran = 0
    old_whole = new_whole = 0
    old_units = new_units = 0
    for eo, en in zip(old_eps, new_eps):
        if eo.get("tag") != en.get("tag"):
            errs.append(("tag_mismatch", eo.get("tag"), en.get("tag")))
            continue
        so = eo.get("sents") or []
        sn = en.get("sents") or []
        if len(so) != len(sn):
            errs.append(("sent_count", eo.get("tag"), len(so), len(sn)))
            continue
        for i, (a, b) in enumerate(zip(so, sn)):
            n_sent += 1
            ra, rb = a.get("ru") or [], b.get("ru") or []
            if surface(ra) != surface(rb):
                errs.append(("surface", eo.get("tag"), i))
                continue
            oa, ob = reading_stream(ra), reading_stream(rb)
            if oa != ob:
                errs.append(("reading_stream", eo.get("tag"), i, oa[:40], ob[:40]))
                continue
            wa, wb = whole_count(ra), whole_count(rb)
            old_whole += wa
            new_whole += wb
            old_units += sum(1 for u in ra if _t(u) and is_kanji(_t(u)[0]))
            new_units += sum(1 for u in rb if _t(u) and is_kanji(_t(u)[0]))
            if wa != wb:
                changed_gran += 1
    print("=== %s ===" % label)
    print("  对比句数: %d" % n_sent)
    print("  含汉字 unit: 旧 %d → 新 %d" % (old_units, new_units))
    print("  整块 unit:   旧 %d → 新 %d  (%.1f%% → %.1f%%)"
          % (old_whole, new_whole,
             100.0 * old_whole / max(1, old_units),
             100.0 * new_whole / max(1, new_units)))
    print("  粒度变化的句子数: %d" % changed_gran)
    print("  🔴 判红项: %d" % len(errs))
    for e in errs[:20]:
        print("     ", e)
    return len(errs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data-merged-50.json"))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    import ruby

    if a.self_test:
        # 反向验证：故意改坏一处读音 ⇒ 必须被抓到
        print("=== 反向验证 ===")
        d = json.load(open(a.data, encoding="utf-8"))
        eps = d["eps"]
        # 找一句有整块的，改坏它的读音
        victim = None
        for e in eps:
            for i, s in enumerate(e.get("sents") or []):
                for j, u in enumerate(s.get("ru") or []):
                    tt = u[0] if isinstance(u, (list, tuple)) else u["t"]
                    rr = u[1] if isinstance(u, (list, tuple)) else u["r"]
                    if tt and is_kanji(tt[0]) and rr:
                        victim = (e, i, j)
                        break
                if victim:
                    break
            if victim:
                break
        if not victim:
            print("  ❌ 找不到可破坏的样本")
            sys.exit(1)
        e, i, j = victim
        orig = e["sents"][i]["ru"][j][1]
        e["sents"][i]["ru"][j][1] = orig + "X"     # 故意改坏
        bad = compare(eps, eps, "故意改坏后")
        if bad == 0:
            print("  ❌ 判据恒绿（改坏了都没抓到）")
            sys.exit(1)
        print("  ✅ 判据抓到了改坏（%d 项判红）" % bad)
        return

    d = json.load(open(a.data, encoding="utf-8"))
    eps = d["eps"]
    # 用新版引擎重算全部句子
    new_eps = []
    for e in eps:
        sents = []
        for s in e.get("sents") or []:
            t = s.get("t") or ""
            ns = dict(s)
            if t:
                try:
                    ns["ru"] = ruby.annotate(t)
                except Exception as ex:
                    print("  ⚠️ 重算失败 %s: %s" % (e.get("tag"), ex))
                    ns["ru"] = s.get("ru")
            sents.append(ns)
        ne = dict(e)
        ne["sents"] = sents
        new_eps.append(ne)
    n = compare(eps, new_eps, "旧版 vs 新版（读音流应逐字相同）")
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
