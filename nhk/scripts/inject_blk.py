#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 把 work/blk/*.json 的**语法讲解新格式**挂到句子上（site/app/subs/<id>.js）

做什么：
    work/blk/<id>.<track>.json = {"<句号>": {layers, blocks, skeleton, zh, …}}
    ⇒ 写进对应 cue 的 `blk` 字段 ⇒ 前端 openStudy 就会渲染新格式
    ⇒ **没有 blk 的句子照旧走旧三段式**（回落，不留空洞）

🔴 纪律：
    · 只**加** blk 字段，不动句子的其它字段（ja/zh/t/c/g/y/yb 一律原样）
    · 句号对不上（cue 数量变了）⇒ **拒绝注入**（宁可没有，也不要挂错句）
    · 注入后逐句回读校验：blk 的 text 必须 == 该句的 ja

用法： python3 scripts/inject_blk.py [--app site/app] [--ids a,b]
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(ROOT, "site", "app"))
    ap.add_argument("--blk", default=os.path.join(ROOT, "work", "blk"))
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    app = os.path.normpath(a.app)
    want = [x.strip() for x in a.ids.split(",") if x.strip()]
    if not os.path.isdir(a.blk):
        print("❌ 没有 %s ⇒ 先跑 scripts/gen_blk.py" % a.blk)
        return 1
    nfile = ncue = 0
    for f in sorted(os.listdir(a.blk)):
        if not f.endswith(".json"):
            continue
        stem = f[:-5]
        eid, _, track = stem.rpartition(".")
        if want and eid not in want:
            continue
        data = json.load(open(os.path.join(a.blk, f), encoding="utf-8"))
        if not data:
            continue
        subp = os.path.join(app, "subs", eid + ".js")
        if not os.path.exists(subp):
            print("  ! %s 没有字幕文件 ⇒ 跳过" % eid)
            continue
        raw = open(subp, encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["%s"\]\s*=\s*(\{.*\});?\s*$' % eid, raw, re.S)
        if not m:
            print("  ! %s 字幕解析失败 ⇒ 跳过" % eid)
            continue
        obj = json.loads(m.group(1))
        cues = obj.get(track)
        if not isinstance(cues, list):
            print("  ! %s 没有 %s 轨 ⇒ 跳过" % (eid, track))
            continue
        idxs = [int(k) for k in data.keys() if str(k).isdigit()]
        if max(idxs) >= len(cues):
            print("  ❌ %s.%s 句号越界（%d > %d）⇒ 拒绝注入" % (eid, track, max(idxs), len(cues)))
            continue
        hit = 0
        for k in idxs:
            blk = data[str(k)]
            c = cues[k]
            if (blk.get("text") or "") != (c.get("ja") or ""):
                print("  ❌ %s.%s 第 %d 句：blk 的 text 与句子不一致 ⇒ 跳过这一句" % (eid, track, k))
                continue
            c["blk"] = blk
            hit += 1
        if not hit:
            continue
        out = raw[:m.start(1)] + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + raw[m.end(1):]
        _atomic_write(subp, out)
        nfile += 1
        ncue += hit
        print("  ✅ %s.%s 注入 %d/%d 句" % (eid, track, hit, len(cues)))
    print("== 注入 %d 个文件 / %d 句 ==" % (nfile, ncue))
    return 0



def _atomic_write(path, text):
    """🔴 原子写：先写临时文件再 rename
    ⇒ 别的进程（fix_sp_asr / gen_blk）不会读到**半截**文件
    ⇒ 以前并发时 JSON 解析报「Invalid control character」（2026-10-25 踩到）"""
    import tempfile
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

if __name__ == "__main__":
    sys.exit(main())
