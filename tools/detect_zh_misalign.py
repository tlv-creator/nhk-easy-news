#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""detect_zh_misalign.py —— 一次模型调用同时判定**三类**日中错位。

🔴 背景（2026-10-24）：Shun 的翻译是**按音频的整句**做的，而切行把一句切成两行
   ⇒ 译文按行分配时集体错位（ep13 实测 25/124 句不对应）。

🔴🔴 **关键设计：让模型在「候选中文」里挑，而不是只问「对不对应」。**
   原因：只问「对应/不对应」只能得到一个布尔值，**修的时候不知道该用哪句中文**。
   ⇒ 改成：给模型这一行的日文 + 前后若干行的中文，问「**哪一行**的中文是它的翻译」
   ⇒ 差值直接给出错位类型，且**自带修复依据**。

🔴 **三类标记**（判定落在「行号差」这个事实量上）：
   ┌ pick == i        ⇒ 正常
   │ pick == i±1      ⇒ **SPLIT**（切行碎裂：这两行本是同一句 ⇒ 该合并）
   │ pick == i±2..3   ⇒ **SHIFT**（纯错位：t 完整，但 zh 串到别处 ⇒ 该重排）
   └ pick == null     ⇒ **NO_MATCH**（没有对应中文 ⇒ 多半是**转写缺内容**）

🔴 输出：
   work/zh_flags.json   ⇒ {"ep13": {"split":[...], "shift":[...], "no_match":[...]}}
   （另给一份扁平版 all[] = 所有有问题的行号，便于按「集+行号」查）

用法：
    python3 tools/detect_zh_misalign.py --ep all --workers 4
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-Text-01"

SYSTEM = "你是日语精读校对员。只输出 JSON，不要解释文字。"

TMPL = """下面是日语精读 App 的相邻几行「日文原句 + 中文翻译」。
**切行时句子被切碎过**，所以中文可能配到了别的行上。请你为**每一行日文**选出
**真正对应它的那一行中文**。

{items}

候选中文的编号就是它在上面清单里的行号（用 "j<n>" 标的那种）。

对每行日文输出（数组，长度 = 输入行数）：
{{"i": <日文行号>, "pick": <对应中文的行号 或 null>}}

规则：
- 选**语义真正对应**的那一行中文（不是「看起来像」的那行）
- 实在没有对应的中文 ⇒ pick = null
- 只能从给出的候选里选，不能自己编
"""

CAND = 3          # 前后各看几行中文
BATCH = 6         # 每次问几行


def call(user, timeout=180, tries=4):
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user}]}).encode("utf-8")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                API, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            br = d.get("base_resp") or {}
            if br.get("status_code") not in (0, None):
                last = RuntimeError("minimax %s" % br.get("status_code"))
                time.sleep(8 * (i + 1))
                continue
            return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise last


def extract_json(t):
    t = (t or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("["), t.rfind("]")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def scan_ep(ep, workers=4, batch=BATCH):
    p = os.path.join(WS, "web", "data", "eps", ep + ".json")
    d = json.load(open(p, encoding="utf-8"))
    cues = d.get("cues") or []
    rows = []
    for c in cues:
        r = (c.get("rows") or [{}])[0]
        rows.append({"t": (r.get("t") or "").strip(),
                     "zh": (r.get("zh") or "").strip()})
    n = len(rows)
    picks = {}          # i -> pick（候选的行号）
    tasks = list(range(0, n, batch))
    lock = __import__("threading").Lock()

    def do(start):
        idxs = list(range(start, min(start + batch, n)))
        items = []
        for i in idxs:
            cands = []
            for j in range(max(0, i - CAND), min(n, i + CAND + 1)):
                if rows[j]["zh"]:
                    cands.append("  j%d: %s" % (j, rows[j]["zh"]))
            items.append("i%d: %s" % (i, rows[i]["t"]))
            items.append("   候选中文：")
            items.extend(cands)
            items.append("")
        try:
            arr = extract_json(call(TMPL.format(items="\n".join(items))))
        except Exception as e:  # noqa: BLE001
            with lock:
                print("   %s 批次 %d 失败：%s" % (ep, start, e))
            return
        def as_int(v):
            """模型可能返回 3 / "3" / "i3" / "j3" ⇒ 统一成整数。"""
            if isinstance(v, bool):
                return None
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                t = v.strip().lower()
                if t in ("null", "none", "", "-"):
                    return None
                t = re.sub(r"^[ij]", "", t)
                try:
                    return int(t)
                except ValueError:
                    return None
            return None

        for a in arr:
            if not isinstance(a, dict):
                continue
            i = as_int(a.get("i"))
            pk = as_int(a.get("pick"))
            if i is None or i < 0 or i >= n:
                continue
            if pk is not None and not (0 <= pk < n):
                pk = None
            with lock:
                picks[i] = pk

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do, tasks))

    split, shift, nomatch, ok = [], [], [], 0
    for i in range(n):
        pk = picks.get(i)
        if pk is None and i not in picks:
            continue                    # 这行没判成（批次失败）⇒ 不算
        if pk is None:
            nomatch.append(i)
        elif pk == i:
            ok += 1
        elif abs(pk - i) == 1:
            split.append((min(i, pk), max(i, pk)))
        else:
            shift.append((i, pk))
    # split 去重
    sset = sorted(set(split))
    return {"n": n, "ok": ok, "split": sset,
            "shift": sorted(set(shift)), "no_match": sorted(set(nomatch)),
            "picks": picks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--out", default=os.path.join(WS, "work", "zh_flags.json"))
    args = ap.parse_args()
    eps = ([f"ep{i:02d}" for i in range(1, 14)] if args.ep == "all"
           else args.ep.split(","))
    res = {}
    for ep in eps:
        p = os.path.join(WS, "web", "data", "eps", ep + ".json")
        if not os.path.exists(p):
            continue
        try:
            r = scan_ep(ep, args.workers, args.batch)
        except Exception as e:  # noqa: BLE001
            print("%s 失败：%s" % (ep, e))
            continue
        res[ep] = r
        print("%s: %d 行 | 正常 %d | SPLIT %d | SHIFT %d | NO_MATCH %d" %
              (ep, r["n"], r["ok"], len(r["split"]), len(r["shift"]), len(r["no_match"])))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    flags = {}
    for ep, r in res.items():
        flags[ep] = {
            "split": [a for a, _ in r["split"]],
            "shift": sorted(set([a for a, _ in r["shift"]])),
            "no_match": r["no_match"],
            "all": sorted(set([a for a, _ in r["split"]] +
                              [a for a, _ in r["shift"]] + list(r["no_match"]))),
        }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(flags, f, ensure_ascii=False, indent=1)
    print("\n清单 → %s" % args.out)
    tot = sum(len(v["all"]) for v in flags.values())
    print("合计 %d 句有问题" % tot)


if __name__ == "__main__":
    main()
