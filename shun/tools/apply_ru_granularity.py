#!/usr/bin/env python3
"""分片重算 ru（可断点续跑）。

🔴 为什么分片：这个沙箱里 **background 进程会在下一次 tool 调用时被回收**
   （实测：进程 ELAPSED 只有几十秒就被杀，日志停在半路）
   ⇒ 长任务必须**分片落盘**，每片独立可重跑，进度不丢。

用法：
  # 第 k 片（k 从 0 开始，共 NBLK 片）
  python3 apply_ru_granularity.py --shard 0 --nblk 24
  # 合并
  python3 apply_ru_granularity.py --merge

每片产出 /workspace/shun/.rushards/shard_<k>.json
"""
import json, os, sys, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools", "ruby"))
sys.path.insert(0, HERE)

from fix_junk_tail import junk_tail, has_english

LONG_SENT_JA_LIMIT = 20
SHARD_DIR = os.path.join(HERE, ".rushards")


def is_kanji(c):
    return "一" <= c <= "鿿"


def _surface(units):
    return "".join((u[0] if isinstance(u, (list, tuple)) else u["t"]) for u in units)


def _stream(units):
    out = []
    for u in units:
        t, r = (u[0], u[1]) if isinstance(u, (list, tuple)) else (u["t"], u["r"])
        if not t:
            continue
        out.append(r if is_kanji(t[0]) else t)
    return "".join(out)


def _to_list(pairs):
    return [[u["t"], u["r"]] for u in pairs]


def do_shard(k, nblk, inp):
    import ruby
    d = json.load(open(inp, encoding="utf-8"))
    all_sents = []
    for e in d["eps"]:
        for s in e.get("sents") or []:
            all_sents.append(s)
    n = len(all_sents)
    per = (n + nblk - 1) // nblk
    lo, hi = k * per, min((k + 1) * per, n)
    out = {}
    skipped = []
    t0 = time.time()
    for i in range(lo, hi):
        s = all_sents[i]
        t = s.get("t") or ""
        ja = sum(1 for c in t if is_kanji(c))
        if ja > LONG_SENT_JA_LIMIT:
            out[str(i)] = None          # 沿用原 ru
            skipped.append(i)
            continue
        out[str(i)] = _to_list(ruby.annotate(t))
    os.makedirs(SHARD_DIR, exist_ok=True)
    p = os.path.join(SHARD_DIR, "shard_%02d.json" % k)
    json.dump({"lo": lo, "hi": hi, "ru": out, "skipped": skipped},
              open(p, "w", encoding="utf-8"), ensure_ascii=False)
    print("shard %2d/%d  [%d..%d]  %d 句  %.1fs  跳过超长 %d ⇒ %s"
          % (k, nblk, lo, hi, hi - lo, time.time() - t0, len(skipped), os.path.basename(p)))


def do_merge(inp, outp):
    import ruby
    d = json.load(open(inp, encoding="utf-8"))
    eps = d["eps"]
    all_sents = []
    for e in eps:
        for s in e.get("sents") or []:
            all_sents.append(s)
    n = len(all_sents)
    new_ru = {}
    files = sorted(f for f in os.listdir(SHARD_DIR) if f.startswith("shard_"))
    print("合并 %d 个分片" % len(files))
    for f in files:
        dsh = json.load(open(os.path.join(SHARD_DIR, f), encoding="utf-8"))
        for k, v in dsh["ru"].items():
            new_ru[int(k)] = v
    missing = [i for i in range(n) if i not in new_ru]
    if missing:
        print("🔴 缺 %d 句（分片不全）⇒ 不合并" % len(missing))
        print("   缺的前 10:", missing[:10])
        sys.exit(1)
    stats = {"ru_changed": 0, "stream_changed": 0, "surface_changed": 0,
             "junk_fixed": 0, "skipped_long": 0}
    idx = 0
    for e in eps:
        for s in e.get("sents") or []:
            old_ru = s.get("ru") or []
            nr = new_ru[idx]
            t = s.get("t") or ""
            if nr is None:
                stats["skipped_long"] += 1
                nr = old_ru
            else:
                if _stream(old_ru) != _stream(nr):
                    stats["stream_changed"] += 1
                    print("  🔴 读音流变了 idx=%d\n     old=%r\n     new=%r"
                          % (idx, _stream(old_ru)[:60], _stream(nr)[:60]))
                if _surface(old_ru) != _surface(nr):
                    stats["surface_changed"] += 1
                    print("  🔴 表层变了 idx=%d" % idx)
            r = junk_tail(t) if not has_english(t) else None
            if r:
                core = r[0]
                nr = _to_list(ruby.annotate(core))
                stats["junk_fixed"] += 1
                s["t"] = core
                if _surface(nr) != core:
                    print("  🔴 清洗后表层!=core idx=%d" % idx)
                    sys.exit(1)
            if old_ru != nr:
                stats["ru_changed"] += 1
            s["ru"] = nr
            idx += 1
    print()
    print("=== 合并自检 ===")
    for k, v in stats.items():
        print("  %-16s %d" % (k, v))
    if stats["stream_changed"] or stats["surface_changed"]:
        print("🔴 存在读音流/表层变化 ⇒ 不写出")
        sys.exit(1)
    json.dump(d, open(outp, "w", encoding="utf-8"), ensure_ascii=False)
    print("  已写出 %s (%d 字节)" % (outp, os.path.getsize(outp)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int)
    ap.add_argument("--nblk", type=int, default=24)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--in", dest="inp", default=os.path.join(HERE, "data-merged-50.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "data-merged-50.new.json"))
    a = ap.parse_args()
    if a.merge:
        do_merge(a.inp, a.out)
    else:
        do_shard(a.shard, a.nblk, a.inp)


if __name__ == "__main__":
    main()
