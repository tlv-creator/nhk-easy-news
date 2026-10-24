#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_zh_fix.py —— 按**人工确认的清单**修数据层：合并碎行 + 重排错位译文。

🔴 定位（2026-10-24）：模型只做**候选**，最终判定由**人工确认的清单**决定
   （清单见 tools/confirmed_splits.json）⇒ 判定可复现，不依赖模型每次判一样。

🔴 三类操作：
   ① **合并**（SPLIT）：相邻两行本是同一句被切开
      → t 拼接、ru/w/blk 合并、时间轴取并集
      → **zh 取「覆盖合并后内容」的那一句**（通常是前一行或后一行，看清单指定）
   ② **重排**（SHIFT）：t 完整，但 zh 串到了别的行
      → 把 pick 指向的那行的 zh 换给本行（**搬走**：被搬的那行会空出来）
   ③ **标记**（NO_MATCH）：无对应中文（转写缺内容）
      → **不改正文**，只写进 work/zh_flags.json 供站点端打标

🔴🔴 **不碰音频与时间轴语义**：
   合并只是把两段时间轴并成一段（s=min, e=max）⇒ 播放仍逐句对得上。

🔴 用法：
    python3 tools/apply_zh_fix.py --ep ep13 --dry-run
    python3 tools/apply_zh_fix.py --ep ep13 --apply
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(WS, "tools"))
from gs_text import clean_zh_marks as _clean_zh_marks  # noqa: E402
CONF = os.path.join(HERE, "confirmed_splits.json")


def load_conf():
    if not os.path.exists(CONF):
        return {}
    return json.load(open(CONF, encoding="utf-8"))


def merge_two(a, b, zh_from, extra_rows=None, group_zh=None):
    """extra_rows: 组内更多行（组长 >2 时用）"""
    ra = (a.get("rows") or [{}])[0]
    rb = (b.get("rows") or [{}])[0]
    if group_zh:
        z = group_zh
    else:
        z = ra.get("zh") if zh_from == "a" else rb.get("zh")
        if not z:
            z = rb.get("zh") or ra.get("zh") or ""
    ru = list(ra.get("ru") or []) + list(rb.get("ru") or [])
    w = list(ra.get("w") or []) + list(rb.get("w") or [])
    blk = list(ra.get("blk") or []) + list(rb.get("blk") or [])
    st = ra.get("st") or rb.get("st") or ""
    for ex in (extra_rows or []):
        ru += list(ex.get("ru") or [])
        w += list(ex.get("w") or [])
        blk += list(ex.get("blk") or [])
    return {
        "s": min(a.get("s", 0), b.get("s", 0)),
        "e": max(a.get("e", 0), b.get("e", 0)),
        "rows": [{
            "t": (ra.get("t") or "") + (rb.get("t") or ""),
            "ru": ru,
            "zh": z,
            "w": w,
            "st": st,
            "blk": blk,
        }],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", default="all")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--conf", default=CONF)
    args = ap.parse_args()
    conf = load_conf() if args.conf == CONF else json.load(
        open(args.conf, encoding="utf-8"))
    eps = ([f"ep{i:02d}" for i in range(1, 14)] if args.ep == "all"
           else args.ep.split(","))
    for ep in eps:
        c = conf.get(ep)
        if not c:
            print("%s: 无确认清单 ⇒ 跳过" % ep)
            continue
        merges = c.get("merge") or []      # [[i, j, "a"|"b"], ...]  j=i+1
        shifts = c.get("shift") or {}      # {"行号": "来源行号"}
        p = os.path.join(WS, "web", "data", "eps", ep + ".json")
        d = json.load(open(p, encoding="utf-8"))
        cues = d.get("cues") or []

        # ① 先重排 zh（在合并之前 ⇒ 行号还是原始的）
        for i, src in shifts.items():
            i, src = int(i), int(src)
            if not (0 <= i < len(cues)) or not (0 <= src < len(cues)):
                print("   %s shift 越界：%d ← %d" % (ep, i, src))
                continue
            if i == src:
                continue
            ri = (cues[i].get("rows") or [{}])[0]
            rs = (cues[src].get("rows") or [{}])[0]
            z = rs.get("zh")
            if z:
                ri["zh"] = z

        # ② 合并（从后往前 ⇒ 索引不乱）
        #    译文来源优先级：**组级对齐结果**（模型重建）> 原行 zh
        gz = {}
        gp = os.path.join(WS, "work", "group_zh_%s.json" % ep)
        if os.path.exists(gp):
            gz = json.load(open(gp, encoding="utf-8"))
        merges = sorted(merges, key=lambda x: -int(x[0]))
        n_m = 0
        n_auto = 0
        for item in merges:
            i = int(item[0])
            j = int(item[1]) if len(item) > 1 else i + 1
            src = item[2] if len(item) > 2 else "a"
            if not (0 <= i < j < len(cues)):
                print("   %s merge 越界：%d,%d" % (ep, i, j))
                continue
            g = gz.get("%d-%d" % (i, j)) or {}
            gzh = g.get("zh") if g.get("reorder_only") else None
            if gzh:
                # 🔴 去掉模型写进译文的候选编号（[2] [3]）—— 那只是格式，不是内容
                gzh = _clean_zh_marks(gzh)
            if gzh:
                n_auto += 1
            extra = []
            if j - i > 1:
                # 组长 >2：把中间的 rows 也并进来
                for k in range(i + 1, j):
                    extra.append((cues[k].get("rows") or [{}])[0])
            merged = merge_two(cues[i], cues[j], src,
                               extra_rows=extra,
                               group_zh=gzh)
            if j - i > 1:
                # 中间行的 ru/w/blk 要接在 b 之前
                mrow = merged["rows"][0]
                mrow["ru"] = ((cues[i].get("rows") or [{}])[0].get("ru") or []) + \
                             [x for e in extra for x in (e.get("ru") or [])] + \
                             ((cues[j].get("rows") or [{}])[0].get("ru") or [])
                mrow["w"] = ((cues[i].get("rows") or [{}])[0].get("w") or []) + \
                            [x for e in extra for x in (e.get("w") or [])] + \
                            ((cues[j].get("rows") or [{}])[0].get("w") or [])
                mrow["blk"] = ((cues[i].get("rows") or [{}])[0].get("blk") or []) + \
                              [x for e in extra for x in (e.get("blk") or [])] + \
                              ((cues[j].get("rows") or [{}])[0].get("blk") or [])
            cues[i:j + 1] = [merged]
            n_m += 1
        d["cues"] = cues
        # 🔴🔴🔴 **合并会改行号 ⇒ 旧的 gs 全部索引错位 ⇒ 必须清空重生成**
        #   （gs 里带「字符位置」+「行内结构」⇒ 行号一变就是垃圾数据）
        #   ⇒ 清空 work/gs/<ep>.json 缓存 + 这里的 gs 字段
        n_gs = 0
        for c in cues:
            for r in (c.get("rows") or []):
                if r.get("gs"):
                    r["gs"] = None
                    n_gs += 1
        # 🔴 缓存 key 是「行号」⇒ 行号变了 ⇒ 旧缓存全是垃圾 ⇒ 必须删
        cp = os.path.join(WS, "work", "gs", ep + ".json")
        if os.path.exists(cp):
            os.remove(cp)
        print("%s: 合并 %d 处（组级译文 %d 处）→ %d 行（清空 gs %d 条）" %
              (ep, n_m, n_auto, len(cues), n_gs))
        if args.apply:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
