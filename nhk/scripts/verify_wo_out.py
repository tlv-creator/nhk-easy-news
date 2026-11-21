#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_wo_out.py —— 校验 subagent 的译文产出（work/wo ↔ work/zh ↔ work/syn_out）

判据（照 WO_TASK.md 的硬性纪律）：
  ① work/zh/<epid>_<track>.json 的键集合 == 0..n-1（n = 工作单里该轨句数）
  ② 每个译文非空
  ③ work/syn_out/<epid>_easy.json 每个值的**长度** == 该句 blocks[] 的长度
  ④ web 轨不该有 syn_out（不是错，只是提醒）

用法：
    python3 scripts/verify_wo_out.py            # 校验全部期
    python3 scripts/verify_wo_out.py <epid> ... # 只校验指定期
退出码：0 = 全绿；1 = 有错（逐条打印）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WO = os.path.join(ROOT, "work", "wo")
ZH = os.path.join(ROOT, "work", "zh")
SYN = os.path.join(ROOT, "work", "syn_out")


def load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def main():
    ids = sys.argv[1:]
    if not ids:
        ids = sorted(f[:-5] for f in os.listdir(WO)
                     if f.endswith(".json") and not f.startswith("_"))
    bad = []
    warn = []
    for eid in ids:
        wo = load(os.path.join(WO, eid + ".json"))
        if not wo:
            bad.append("%s: 没有工作单" % eid)
            continue
        for track in ("easy", "web"):
            items = ((wo.get("tracks") or {}).get(track)) or []
            if not items:
                continue
            n = len(items)
            z = load(os.path.join(ZH, "%s_%s.json" % (eid, track)))
            if z is None:
                bad.append("%s/%s: 缺 work/zh/%s_%s.json" % (eid, track, eid, track))
                continue
            keys = set(str(k) for k in z.keys())
            want = set(str(i) for i in range(n))
            miss = sorted(want - keys, key=int)
            extra = sorted(keys - want, key=int)
            if miss:
                bad.append("%s/%s: 缺 %d 句（下标 %s）" % (eid, track, len(miss), miss[:12]))
            if extra:
                bad.append("%s/%s: 多 %d 个下标（%s）" % (eid, track, len(extra), extra[:12]))
            empty = [k for k, v in z.items() if not str(v).strip()]
            if empty:
                bad.append("%s/%s: %d 句译文为空（下标 %s）" % (eid, track, len(empty), empty[:12]))
            if track == "easy":
                s = load(os.path.join(SYN, "%s_easy.json" % eid))
                if s is None:
                    bad.append("%s/easy: 缺 work/syn_out/%s_easy.json" % (eid, eid))
                    continue
                for it in items:
                    i = str(it.get("i"))
                    nb = len(it.get("blocks") or [])
                    v = s.get(i)
                    if v is None:
                        bad.append("%s/easy: syn_out 缺下标 %s" % (eid, i))
                        break
                    if not isinstance(v, list):
                        bad.append("%s/easy: syn_out[%s] 不是数组" % (eid, i))
                        break
                    if len(v) != nb:
                        # 🔴 空数组 = 「这句放弃成分块中文」的**显式降级**（整句中文仍要）
                        #    （build_inc 会把长度对不上的 yb 置空 ⇒ 面板里不显示半空的中文块）
                        if v == []:
                            warn.append("%s/easy: 第 %s 句没有成分块中文（已降级，整句中文仍在）"
                                        % (eid, i))
                        else:
                            bad.append("%s/easy: syn_out[%s] 块数 %d != 工作单 %d"
                                       % (eid, i, len(v), nb))
                        break
    for b in warn:
        print("⚠️", b)
    for b in bad:
        print("❌", b)
    if not bad:
        print("✅ 全部期校验通过（键集合 / 非空 / 成分块数）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
