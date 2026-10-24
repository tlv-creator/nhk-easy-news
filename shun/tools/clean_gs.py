#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_gs.py —— 把已生成的 gs 按**最新**判据清洗一遍。

🔴 为什么需要它：判据是会改的（越改越准）。
   改完判据后，**已经生成**的旧数据里可能留着按老判据放过的坏数据
   （例如 chain 里的纯括号项「[]」）⇒ 必须重洗一遍，不能只管新生成的。

做法：
  ① 读 work/gs/epNN.json
  ② 每条重跑 check()（check 本身会做形状修复，比如剔除 chain 里的纯括号项）
  ③ 修完仍不合格 ⇒ gs 置空（宁可没有，也不要错的数据）
  ④ 写回 work/gs/ 和 web/data/eps/*.json

用法：
    python3 tools/clean_gs.py            # 只洗 work/gs/（dry）
    python3 tools/clean_gs.py --apply     # 洗 + 写回 web/data/eps/
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gen_gs  # noqa: E402


_PUNCT = set("、。！？!?，,．.「」『』（）()【】〔〕…—・:：;；\"' ")


def _core_blocks(frame):
    out = []
    for b in frame or []:
        t = (b.get("s") or "").strip()
        if not t:
            continue
        if all(ch in _PUNCT for ch in t):
            continue
        out.append(t)
    return out


def _mech_or_nonja(gs, ja):
    """返回 (不合格?, 原因)。机械切 / 非日语行 ⇒ 不合格。"""
    ja = ja or ""
    if not any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in ja):
        return True, "非日语行（无假名/汉字）⇒ 不生成结构"
    core = _core_blocks(gs.get("frame") or [])
    if len(core) >= 2 and all(8 <= len(x) <= 12 for x in core):
        return True, "机械切（块长全在 8~12 字：%s）" % ",".join(str(len(x)) for x in core)
    return False, ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 web/data/eps/*.json")
    ap.add_argument("--inp", default=os.path.join(WS, "work", "gs"))
    args = ap.parse_args()

    total = 0
    fixed = 0
    dropped = 0
    for fn in sorted(os.listdir(args.inp)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        ep = fn[:-5]
        p = os.path.join(args.inp, fn)
        with open(p, encoding="utf-8") as f:
            cache = json.load(f)
        changed = False
        for key, item in cache.items():
            gs = item.get("gs")
            if not gs:
                continue
            total += 1
            ja = item.get("ja") or ""
            before = json.dumps(gs, ensure_ascii=False, sort_keys=True)
            ok, reason = gen_gs.check(gs, ja)
            if not ok:
                # 再试一次形状修复（check 内部已修复过一次）
                gs2 = gen_gs.repair_shape(json.loads(json.dumps(gs)), ja)
                ok2, reason2 = gen_gs.check(gs2, ja)
                if ok2:
                    cache[key]["gs"] = gs2
                    fixed += 1
                    changed = True
                    continue
                cache[key]["gs"] = None
                cache[key]["err"] = "clean: " + str(reason2)
                dropped += 1
                changed = True
                continue
            # 🔴🔴 **额外判据**（2026-10-24 采纳主线反馈）：机械切 + 非日语行
            #   ① 机械切：块长全在 8~12 字且块数 ≥2 ⇒ 那不是结构，是**断字**
            #      ⚠️ 必须去掉**纯标点块**再算长度（句末「。」长度 1 ⇒ 不去掉就整条判据失灵）
            #   ② 非日语行：没有假名的行（如英文 Patreon 广告）⇒ 不给结构（前端降级纯文本）
            mech_bad, mech_why = _mech_or_nonja(gs, ja)
            if mech_bad:
                cache[key]["gs"] = None
                cache[key]["err"] = "clean: " + mech_why
                dropped += 1
                changed = True
                continue
            # 🔴 位置标注是**确定性计算**（不依赖模型）⇒ 每次清洗都重算一遍
            #    （新加的位置字段、加了位置标注的旧数据都能补上）
            gen_gs.annotate_positions(gs, ja)
            after = json.dumps(gs, ensure_ascii=False, sort_keys=True)
            if before != after:
                fixed += 1
                changed = True
        if changed:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        print("%s: 修复 %d / 丢弃 %d" % (ep, fixed, dropped))

    print("\n合计：检查 %d 条，修复 %d 条，丢弃 %d 条" % (total, fixed, dropped))

    if args.apply:
        for fn in sorted(os.listdir(args.inp)):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            ep = fn[:-5]
            with open(os.path.join(args.inp, fn), encoding="utf-8") as f:
                cache = json.load(f)
            rows, path = gen_gs.load_eps(ep)
            if rows is None:
                continue
            if os.sep + "eps" + os.sep not in path:
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            n = 0
            for i, c in enumerate(data.get("cues") or []):
                key = "%s:%d" % (ep, i)
                g = (cache.get(key) or {}).get("gs")
                if c.get("rows"):
                    c["rows"][0]["gs"] = g
                    n += 1 if g else 0
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            print("已写回 %s（%d 句带 gs）" % (os.path.basename(path), n))


if __name__ == "__main__":
    main()
