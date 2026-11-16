#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_zh.py —— 把 TSV 译文落成 work/zh/*.json + work/syn_out/*.json（**带校验**）

输入（人工/模型写好的 TSV）：
    work/za/<epid>_<track>.tsv     每行 "<下标>\t<整句中文>"
    work/zb/<epid>_easy.tsv        每行 "<下标>\t<块1>|<块2>|…"

🔴 校验（不过就**拒绝写盘**）：
    ① 下标必须是 0..n-1 的完整集合（缺一句都不许）
    ② 每句译文非空
    ③ 成分块的个数必须 == 工作单里该句 blocks[] 的个数
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WO = os.path.join(ROOT, "work", "wo")
ZA = os.path.join(ROOT, "work", "za")
ZB = os.path.join(ROOT, "work", "zb")
ZC = os.path.join(ROOT, "work", "zc")
ZH = os.path.join(ROOT, "work", "zh")
SYN = os.path.join(ROOT, "work", "syn_out")


def load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def read_tsv(p):
    """→ {下标: 内容}（顺序保留）"""
    out = {}
    for ln, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
        if not line.strip():
            continue
        if "\t" not in line:
            raise ValueError("%s 第 %d 行没有 tab" % (p, ln))
        i, v = line.split("\t", 1)
        out[int(i)] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epids", required=True)
    a = ap.parse_args()
    eps = [x.strip() for x in a.epids.split(",") if x.strip()]
    os.makedirs(ZH, exist_ok=True)
    os.makedirs(SYN, exist_ok=True)
    rc = 0
    for eid in eps:
        wo = load(os.path.join(WO, "%s.json" % eid))
        if not wo:
            print("  ❌ %s 没有工作单" % eid)
            rc = 1
            continue
        for track in ("easy", "web"):
            items = wo["tracks"].get(track) or []
            tsv = os.path.join(ZA, "%s_%s.tsv" % (eid, track))
            if not items:
                continue
            if not os.path.exists(tsv):
                print("  ❌ %s/%s 缺译文 %s" % (eid, track, tsv))
                rc = 1
                continue
            rows = read_tsv(tsv)
            want = set(range(len(items)))
            if set(rows) != want:
                print("  ❌ %s/%s 下标对不上（多 %s / 少 %s）"
                      % (eid, track, sorted(set(rows) - want)[:5], sorted(want - set(rows))[:5]))
                rc = 1
                continue
            empty = [i for i, v in rows.items() if not v.strip()]
            if empty:
                print("  ❌ %s/%s 空译文：%s" % (eid, track, empty[:5]))
                rc = 1
                continue
            d = {str(i): rows[i] for i in range(len(items))}
            with open(os.path.join(ZH, "%s_%s.json" % (eid, track)), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            print("  ✅ %s/%s  %d 句" % (eid, track, len(items)))
        # 成分块
        items = wo["tracks"].get("easy") or []
        # ① 「词块 → 中文」字典模式（**推荐**）：长度天然对得上
        ctsv = os.path.join(ZC, "%s_easy.tsv" % eid)
        if items and os.path.exists(ctsv):
            gloss = {}
            for line in open(ctsv, encoding="utf-8").read().splitlines():
                if not line.strip():
                    continue
                if "\t" not in line:
                    print("  ❌ %s 词块表有一行没有 tab" % eid)
                    rc = 1
                    break
                k, v = line.split("\t", 1)
                gloss[k] = v
            out = {}
            miss = set()
            for it in items:
                vals = []
                for b in it["blocks"]:
                    if b["text"] in gloss:
                        vals.append(gloss[b["text"]])
                    else:
                        vals.append("")
                        miss.add(b["text"])
                out[str(it["i"])] = vals
            with open(os.path.join(SYN, "%s_easy.json" % eid), "w",
                      encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
            print("  ✅ %s/easy 成分块 %d 句（词块 %d 个%s）"
                  % (eid, len(out), len(gloss),
                     ("，**缺 %d 个**：%s" % (len(miss), list(miss)[:8])) if miss else ""))
            continue
        # ② 逐句列表模式（老的写法：长度必须手工对上）
        btsv = os.path.join(ZB, "%s_easy.tsv" % eid)
        if items and os.path.exists(btsv):
            rows = read_tsv(btsv)
            want = set(range(len(items)))
            if set(rows) != want:
                print("  ❌ %s 成分块下标对不上（多 %s / 少 %s）"
                      % (eid, sorted(set(rows) - want)[:5], sorted(want - set(rows))[:5]))
                rc = 1
            else:
                out = {}
                bad = False
                for it in items:
                    parts = rows[it["i"]].split("|")
                    if len(parts) != len(it["blocks"]):
                        print("  ❌ %s 第 %d 句成分块 %d 个 ≠ blocks %d 个"
                              % (eid, it["i"], len(parts), len(it["blocks"])))
                        bad = True
                        break
                    out[str(it["i"])] = parts
                if not bad:
                    with open(os.path.join(SYN, "%s_easy.json" % eid), "w",
                              encoding="utf-8") as f:
                        json.dump(out, f, ensure_ascii=False, indent=1)
                    print("  ✅ %s/easy 成分块 %d 句" % (eid, len(out)))
                else:
                    rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
