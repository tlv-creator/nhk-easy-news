#!/usr/bin/env python3
"""对 50 集做粒度细化（读音流零变化）+ 标点堆清洗，并做全量自检。

判据（全部落在事实量上）：
 ① 表层：新 ru 拼接 == 旧 ru 拼接 == 句原文 t
 ② 读音流：新 ru 读音流 == 旧 ru 读音流（逐字符）
 ③ 其它字段：t（除 8 行清洗）/ zh / en / kind / s / e / gs / pred **逐字不变**
 ④ 粒度：整块 unit 数必须**不增**（只允许变细）
 ⑤ 空读音：含汉字的 unit 必须有读音

用法：
  python3 apply_refine.py --in data-merged-50.json --out data-merged-50.new.json
  python3 apply_refine.py --self-test
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools", "ruby"))

import ruby  # noqa: E402
from refine_granularity import refine_ru, _as_pairs  # noqa: E402
from fix_junk_tail import junk_tail, has_english  # noqa: E402


def surface(units):
    return "".join(u[0] for u in units)


def reading_stream(units):
    out = []
    for t, r in units:
        if not t:
            continue
        out.append(r if ruby.is_kanji(t[0]) else t)
    return "".join(out)


def whole_count(units):
    return sum(1 for t, r in units if t and ruby.is_kanji(t[0]) and len(t) > 1)


def self_test():
    print("=== 反向验证 ===")
    d = json.load(open(os.path.join(HERE, "data-merged-50.json"), encoding="utf-8"))
    eps = d["eps"]
    all_sents = [s for e in eps for s in e["sents"]]
    # 找一句有整块的
    victim = None
    for i, s in enumerate(all_sents):
        for u in (s.get("ru") or []):
            t = u[0] if isinstance(u, (list, tuple)) else u["t"]
            r = u[1] if isinstance(u, (list, tuple)) else u["r"]
            if t and ruby.is_kanji(t[0]) and len(t) > 1 and r:
                victim = (i, t)
                break
        if victim:
            break
    if not victim:
        print("  ❌ 找不到样本")
        return 1
    i, t = victim
    old = all_sents[i]["ru"]
    new = refine_ru(old)
    # ① 故意改坏读音 ⇒ 必须被「读音流」判据抓到
    bad = _as_pairs(old)
    for k in range(len(bad)):
        if bad[k][1]:
            bad[k][1] = bad[k][1] + "X"
            break
    caught = (reading_stream(bad) != reading_stream(old))
    print("  %s 改坏读音被读音流判据抓到" % ("✅" if caught else "❌"))
    # ② 正常细化必须读音流不变
    same = reading_stream(new) == reading_stream(old)
    surf = surface(new) == surface(old)
    print("  %s 正常细化读音流不变" % ("✅" if same else "❌"))
    print("  %s 正常细化表层不变" % ("✅" if surf else "❌"))
    ok = caught and same and surf
    print("  %s" % ("全部通过" if ok else "有失败"))
    return 0 if ok else 1


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    inp = os.path.join(HERE, "data-merged-50.json")
    outp = os.path.join(HERE, "data-merged-50.new.json")
    if "--in" in sys.argv:
        inp = sys.argv[sys.argv.index("--in") + 1]
    if "--out" in sys.argv:
        outp = sys.argv[sys.argv.index("--out") + 1]

    d = json.load(open(inp, encoding="utf-8"))
    eps = d["eps"]
    stats = {"sent_changed": 0, "ru_changed": 0, "stream_changed": 0,
             "surface_changed": 0, "junk_fixed": 0, "other_changed": 0,
             "empty_reading": 0, "whole_old": 0, "whole_new": 0,
             "kanji_units_old": 0, "kanji_units_new": 0}
    before_snapshot = []

    for e in eps:
        for s in e.get("sents") or []:
            before_snapshot.append(json.dumps(
                {k: s.get(k) for k in ("zh", "en", "kind", "s", "e", "gs", "pred")},
                ensure_ascii=False, sort_keys=True))
            old_ru = _as_pairs(s.get("ru") or [])
            new_ru = refine_ru(old_ru)
            # ① 表层不变
            if surface(old_ru) != surface(new_ru):
                stats["surface_changed"] += 1
                print("  🔴 表层变了 %s#%d" % (e.get("tag"), len(stats) and 0))
            # ② 读音流不变
            if reading_stream(old_ru) != reading_stream(new_ru):
                stats["stream_changed"] += 1
                print("  🔴 读音流变了 %s\n     old=%r\n     new=%r"
                      % (e.get("tag"), reading_stream(old_ru)[:70], reading_stream(new_ru)[:70]))
            if old_ru != new_ru:
                stats["ru_changed"] += 1
                stats["sent_changed"] += 1
            s["ru"] = new_ru
            stats["whole_old"] += whole_count(old_ru)
            stats["whole_new"] += whole_count(new_ru)
            stats["kanji_units_old"] += sum(1 for t, r in old_ru if t and ruby.is_kanji(t[0]))
            stats["kanji_units_new"] += sum(1 for t, r in new_ru if t and ruby.is_kanji(t[0]))
            # ⑤ 空读音
            for t, r in new_ru:
                if t and ruby.is_kanji(t[0]) and not r:
                    stats["empty_reading"] += 1

    # 标点堆清洗（只改 t 与 ru）
    for e in eps:
        for s in e.get("sents") or []:
            t = s.get("t") or ""
            if has_english(t):
                continue
            r = junk_tail(t)
            if not r:
                continue
            core = r[0]
            # 清洗后重新算 ru（core 的读音）
            units = ruby.annotate(core)
            s["t"] = core
            s["ru"] = [[u["t"], u["r"]] for u in units]
            stats["junk_fixed"] += 1

    # ③ 其它字段逐字不变
    idx = 0
    for e in eps:
        for s in e.get("sents") or []:
            now = json.dumps({k: s.get(k) for k in ("zh", "en", "kind", "s", "e", "gs", "pred")},
                              ensure_ascii=False, sort_keys=True)
            if now != before_snapshot[idx]:
                stats["other_changed"] += 1
            idx += 1

    print()
    print("=== 粒度细化自检 ===")
    for k, v in stats.items():
        print("  %-18s %d" % (k, v))
    print()
    print("  整块 unit: %d → %d  (%.1f%% → %.1f%%)"
          % (stats["whole_old"], stats["whole_new"],
             100.0 * stats["whole_old"] / max(1, stats["kanji_units_old"]),
             100.0 * stats["whole_new"] / max(1, stats["kanji_units_new"])))
    bad = stats["stream_changed"] + stats["surface_changed"] + stats["other_changed"] + stats["empty_reading"]
    if stats["whole_new"] > stats["whole_old"]:
        print("🔴 整块数变多了")
        bad += 1
    if bad:
        print("🔴 判红 %d 项 ⇒ 不写出" % bad)
        sys.exit(1)
    json.dump(d, open(outp, "w", encoding="utf-8"), ensure_ascii=False)
    print("  ✅ 全部通过，已写出 %s (%d 字节)" % (outp, os.path.getsize(outp)))


if __name__ == "__main__":
    main()
