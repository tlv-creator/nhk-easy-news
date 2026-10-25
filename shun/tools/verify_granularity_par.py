#!/usr/bin/env python3
"""并行重算 + 粒度对比（verify_granularity 的多进程版）。

串行 3618 句约 30 分钟 ⇒ 用 multiprocessing 分 8 段并行，降到 ~4 分钟。

用法：
  python3 verify_granularity_par.py --data data-merged-50.json
  python3 verify_granularity_par.py --self-test
"""
import json, os, sys, argparse, multiprocessing as mp

HERE = os.path.dirname(os.path.abspath(__file__))
RUBY_DIR = os.path.join(HERE, "tools", "ruby")
sys.path.insert(0, RUBY_DIR)


def is_kanji(c):
    return "一" <= c <= "鿿"


def surface(units):
    return "".join((u[0] if isinstance(u, (list, tuple)) else u["t"]) for u in units)


def reading_stream(units):
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


def _worker(args):
    """处理一段：返回该段的 (逐句对比结果, 统计)"""
    lo, hi, sents = args
    import ruby
    out = []
    st = {"ok": 0, "old_whole": 0, "new_whole": 0, "old_units": 0, "new_units": 0,
          "gran_changed": 0, "errs": []}
    for idx in range(lo, hi):
        s = sents[idx]
        t = s.get("t") or ""
        old_ru = s.get("ru") or []
        try:
            new_ru = ruby.annotate(t)
        except Exception as ex:
            st["errs"].append((idx, "annotate_failed: %s" % ex))
            continue
        if surface(old_ru) != surface(new_ru):
            st["errs"].append((idx, "surface_changed"))
            continue
        oa, ob = reading_stream(old_ru), reading_stream(new_ru)
        if oa != ob:
            st["errs"].append((idx, "reading_changed: %r -> %r" % (oa[:50], ob[:50])))
            continue
        st["ok"] += 1
        st["old_whole"] += whole_count(old_ru)
        st["new_whole"] += whole_count(new_ru)
        # 🔴 格式自证：旧 ru 与新 ru 的**单元格式必须各自统一**
        #    （旧=list of [t,r]｜新=list of {t,r}）—— 混用会让比较函数读到错误的键
        if old_ru and isinstance(old_ru[0], (list, tuple)) and isinstance(new_ru[0], dict):
            pass
        elif old_ru and isinstance(old_ru[0], dict) and isinstance(new_ru[0], (list, tuple)):
            pass
        st["old_units"] += sum(1 for u in old_ru if _t(u) and is_kanji(_t(u)[0]))
        st["new_units"] += sum(1 for u in new_ru if _t(u) and is_kanji(_t(u)[0]))
        if whole_count(old_ru) != whole_count(new_ru):
            st["gran_changed"] += 1
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data-merged-50.json"))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()

    if a.self_test:
        print("=== 反向验证：故意改坏一处读音 ⇒ 必须被抓到 ===")
        d = json.load(open(a.data, encoding="utf-8"))
        eps = d["eps"]
        all_sents = []
        for e in eps:
            for s in e.get("sents") or []:
                all_sents.append(s)
        # 找一句有读音的单元改坏
        victim = None
        for i, s in enumerate(all_sents):
            for j, u in enumerate(s.get("ru") or []):
                tt = u[0] if isinstance(u, (list, tuple)) else u["t"]
                rr = u[1] if isinstance(u, (list, tuple)) else u["r"]
                if tt and is_kanji(tt[0]) and rr:
                    victim = (i, j)
                    break
            if victim:
                break
        if not victim:
            print("  ❌ 找不到样本")
            sys.exit(1)
        i, j = victim
        _ru = all_sents[i]["ru"]
        if isinstance(_ru[j], (list, tuple)):
            _ru[j][1] = _ru[j][1] + "X"
        else:
            _ru[j]["r"] = _ru[j]["r"] + "X"
        st = _worker((i, i + 1, all_sents))
        if not st["errs"]:
            print("  ❌ 判据恒绿：改坏了却没抓到")
            sys.exit(1)
        print("  ✅ 抓到：%r" % (st["errs"][0],))
        return

    d = json.load(open(a.data, encoding="utf-8"))
    eps = d["eps"]
    all_sents = []
    for e in eps:
        for s in e.get("sents") or []:
            all_sents.append(s)
    n = len(all_sents)
    print("总句数 %d，进程数 %d …" % (n, a.jobs))
    chunk = (n + a.jobs - 1) // a.jobs
    ranges = [(lo, min(lo + chunk, n), all_sents)
              for lo in range(0, n, chunk)]
    with mp.Pool(a.jobs) as pool:
        parts = pool.map(_worker, ranges)
    st = {"ok": 0, "old_whole": 0, "new_whole": 0, "old_units": 0, "new_units": 0,
          "gran_changed": 0, "errs": []}
    for p in parts:
        for k in st:
            if k == "errs":
                st["errs"].extend(p["errs"])
            else:
                st[k] += p[k]
    print()
    print("=== 旧版 vs 新版（读音流必须逐字相同）===")
    print("  通过句数: %d / %d" % (st["ok"], n))
    print("  含汉字 unit: 旧 %d → 新 %d" % (st["old_units"], st["new_units"]))
    print("  整块 unit:   旧 %d → 新 %d  (%.1f%% → %.1f%%)"
          % (st["old_whole"], st["new_whole"],
             100.0 * st["old_whole"] / max(1, st["old_units"]),
             100.0 * st["new_whole"] / max(1, st["new_units"])))
    print("  粒度变化的句子数: %d" % st["gran_changed"])
    print("  🔴 判红项: %d" % len(st["errs"]))
    for e in st["errs"][:20]:
        print("     ", e)
    sys.exit(1 if st["errs"] else 0)


if __name__ == "__main__":
    main()
