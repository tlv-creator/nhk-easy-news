#!/usr/bin/env python3
"""清洗「不成句标点堆」行（平台切句残留）。

判据（只删**确定不成句**的尾部标点堆，不碰任何语言内容）：
  ① 尾部 = 标点/空白组成，且**长度 ≥ 2**
  ② 尾部**同时含逗号类**（, ， 、）**与句号类**（。 .）
     ⇒ 单个句号 = 正常句末（不动）；单个逗号 = 正常停顿（不动）
  ③ 删完后**必须仍有语言内容**（core 非空）
  ④ **绝不碰含英文对照的行**（Beginners / close / Genki 1 level …）
     ⇒ 那些是 13 集原作者的词义注释（已确认不是污染）

同时同步删 ru 里对应的尾部标点单元（表层与读音一起删，保持表层还原=原文）。

用法：
  python3 fix_junk_tail.py --in X.json --out Y.json       # 写新文件
  python3 fix_junk_tail.py --in X.json --check             # 只报告
  python3 fix_junk_tail.py --self-test                     # 反向验证
"""
import json, os, re, sys, argparse, copy

PUNC = "、，,。．.!！?？:：;；"
TAIL_RE = re.compile(r"[\s、，,。．.!！?？:：;；「」『』（）()【】…・－ー]+$")
ASCII_WORD = re.compile(r"[A-Za-z]")


def junk_tail(t):
    """返回 (core, tail) 或 None。core 为清洗后的日文部分。"""
    if not t:
        return None
    core = TAIL_RE.sub("", t)
    tail = t[len(core):]
    if len(tail) < 2:
        return None
    if not all(c in PUNC + " \t　" for c in tail):
        return None
    has_comma = any(c in tail for c in ",，、")
    has_period = any(c in tail for c in "。.")
    if not (has_comma and has_period):
        return None
    if not core:
        return None
    return core, tail


def has_english(t):
    return bool(ASCII_WORD.search(t or ""))


def fix_sentence(s):
    """返回 (新句, 说明) 或 None。"""
    t = s.get("t") or ""
    if has_english(t):
        return None                      # 英文对照行 ⇒ 不动
    r = junk_tail(t)
    if not r:
        return None
    core, tail = r
    ns = copy.deepcopy(s)
    ns["t"] = core
    # 同步裁 ru：删掉尾部那些「只含标点/空白」的单元
    ru = s.get("ru") or []
    keep = []
    for u in ru:
        if u[0] and all(c in PUNC + " \t　" for c in u[0]):
            continue
        keep.append(list(u))
    ns["ru"] = keep
    # 自证：表层必须还原成 core
    surf = "".join(u[0] for u in keep)
    if surf != core:
        return ("ERR", "表层裁剪后 != core: %r vs %r" % (surf, core))
    return ns, (tag_note := "删尾部 %r" % tail)


def process(eps, apply=True):
    changed = []
    for e in eps:
        for i, s in enumerate(e.get("sents") or []):
            out = fix_sentence(s)
            if not out:
                continue
            if out[0] == "ERR":
                print("  🔴", e.get("tag"), i, out[1])
                raise SystemExit(1)
            ns, note = out
            changed.append((e.get("tag"), i, s.get("t"), ns["t"], note))
            if apply:
                e["sents"][i] = ns
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data-merged-50.json")
    inp = a.inp or default

    if a.self_test:
        print("=== 反向验证 ===")
        cases = [
            ("仲がいい、 , 。", "仲がいい", True),
            ("経験する、 。", "経験する", True),
            ("1日のルーティン、 。", "1日のルーティン", True),
            ("普通文。", "普通文。", False),          # 单句号 ⇒ 不动
            ("普通文、", "普通文、", False),           # 单逗号 ⇒ 不动
            ("初心者、Beginners、Genki 1 level、", None, False),  # 英文对照 ⇒ 不动
            ("ねえ、そう。", "ねえ、そう。", False),   # 正常句末
        ]
        ok = True
        for t, want, should in cases:
            got = junk_tail(t)
            g = got[0] if got else None
            hit = g is not None and has_english(t) is False
            if should and hit and g != want:
                print("  ❌ %r 期望 %r 得到 %r" % (t, want, g)); ok = False
            elif not should and hit:
                print("  ❌ %r 不该改却改了 => %r" % (t, g)); ok = False
            else:
                print("  ✅ %r" % t)
        print("  %s" % ("全部通过" if ok else "有失败"))
        sys.exit(0 if ok else 1)

    d = json.load(open(inp, encoding="utf-8"))
    eps = d["eps"]
    changed = process(eps, apply=not a.check)
    print("=== 清洗「不成句标点堆」===")
    print("  命中行数: %d" % len(changed))
    for c in changed:
        print("   %s#%d %r → %r  (%s)" % c)
    if not a.check and a.out:
        json.dump(d, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
        print("  已写出 %s (%d 字节)" % (a.out, os.path.getsize(a.out)))
    elif not a.check:
        print("  (未指定 --out，只报告不改文件)")


if __name__ == "__main__":
    main()
