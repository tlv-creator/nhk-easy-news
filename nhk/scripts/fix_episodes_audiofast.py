#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 修 episodes.js 里的 audioFast：**有文件才写，没文件就删**（2026-10-24）

为什么要有：
    build_data.py 原来**无条件**写 `"audioFast": "media/fast/<id>.m4a"`，
    哪怕那一期根本没有原速音 ⇒ episodes.js 里挂着 23 个**指向不存在文件**的指针。
    ⇒ 前端不读这个字段（它从 cdn.js 取地址）⇒ **不会报错**，
      但数据在骗人：看 episodes.js 以为有原速音，实际没有。
    ⇒ 而且 build_data.py 已经改成「有才写」⇒ 这个脚本把**存量**也对齐。

判据落在事实量上：**文件在不在**（不是「durFast 是不是 0」）。
用法： python3 scripts/fix_episodes_audiofast.py [--app site/app]
"""
import argparse
import json
import os
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "site", "app"))
    a = ap.parse_args()
    app = os.path.normpath(a.app)
    p = os.path.join(app, "data", "episodes.js")
    raw = open(p, encoding="utf-8").read()
    m = re.search(r"=\s*(\[.*?\]);", raw, re.S)
    if not m:
        sys.exit("❌ 读不出 data/episodes.js 里的数组")
    eps = json.loads(m.group(1))

    added, removed, kept = [], [], 0
    for e in eps:
        rel = "media/fast/%s.m4a" % e["id"]
        exists = os.path.exists(os.path.join(app, rel))
        has = "audioFast" in e
        if exists and not has:
            e["audioFast"] = rel
            added.append(e["id"])
        elif not exists and has:
            e.pop("audioFast", None)
            removed.append(e["id"])
        else:
            kept += 1
    if added or removed:
        head = raw[:m.start(1)]
        tail = raw[m.end(1):]
        out = head + json.dumps(eps, ensure_ascii=False, indent=1) + tail
        open(p, "w", encoding="utf-8").write(out)
    print("✅ audioFast 对齐：新增 %d 期、删除 %d 期、不变 %d 期（共 %d 期）"
          % (len(added), len(removed), kept, len(eps)))
    if added:
        print("   新增：", added)
    if removed:
        print("   删除：", removed)


if __name__ == "__main__":
    main()
