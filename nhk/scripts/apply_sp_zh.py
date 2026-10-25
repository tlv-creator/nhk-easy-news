#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 把人工/模型写的 **sp 轨中文译文**落成 work/sp/<id>.json

为什么要有这个脚本：
    译文是**按句**的（一句一行），写成 JSON 容易出错（引号、换行、逗号），
    而且 326 句要人工过一遍 ⇒ 用**纯文本一行一句**最省事、最不容易错位。
    ⇒ 这个脚本负责：读纯文本 → 按行对上号 → 写 JSON。

用法：
    # 1) 打印待译的句子（每行一句，前面带序号）
    python3 scripts/apply_sp_zh.py --dump 20261021de62763
    # 2) 把译文写进一个 .txt（一行一句，**顺序必须一致**），然后：
    python3 scripts/apply_sp_zh.py --apply 20261021de62763 --zh /tmp/zh_xxx.txt
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+", help="期号")
    ap.add_argument("--dump", action="store_true", help="打印待译句子")
    ap.add_argument("--apply", action="store_true", help="写入 work/sp/<id>.json")
    ap.add_argument("--zh", default="", help="译文文件（--apply 时必填）")
    ap.add_argument("--sp", default=os.path.join(ROOT, "data", "sp"))
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "sp"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    for eid in a.ids:
        spf = os.path.join(a.sp, eid + ".json")
        if not os.path.exists(spf):
            sys.exit("❌ 没有 %s" % spf)
        sp = json.load(open(spf, encoding="utf-8"))
        sents = sp.get("sents") or []
        if a.dump:
            print("==== %s（%d 句）====" % (eid, len(sents)))
            for i, s in enumerate(sents):
                print("%d\t%s" % (i, s["ja"]))
        if a.apply:
            if not a.zh:
                sys.exit("❌ --apply 需要 --zh <文件>")
            lines = [x.rstrip("\n") for x in
                     open(a.zh, encoding="utf-8").read().split("\n")]
            while lines and lines[-1] == "":
                lines.pop()
            if len(lines) != len(sents):
                sys.exit("❌ %s 译文行数 %d ≠ 句子数 %d ⇒ 拒绝写入"
                         % (eid, len(lines), len(sents)))
            items = [{"ja": s["ja"], "zh": lines[i], "yb": []}
                     for i, s in enumerate(sents)]
            json.dump({"id": eid, "items": items},
                      open(os.path.join(a.out, eid + ".json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            print("✅ %s 写入 %d 句" % (eid, len(items)))


if __name__ == "__main__":
    main()
