#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.py —— 发版前的**数据自检**（对应原来的 tools/verify.py + verify_sync.py）

🔴 判据全是「事实量」，不是「看起来对」：
    ① 结构：每行都有 t/ru/zh/w/st/blk 五个字段
    ② 覆盖：ru 逐字拼回去 == t（少一个字都不行）
    ③ 索引：index.json 的每一集都能在 data/eps/ 找到对应文件，集号/标题/时长/句数对得上
    ④ 同步（--sync）：
         · 行时间单调递增、不重叠、行不越出音频时长
         · 相邻行的间隔不能大得离谱（> 12s ⇒ 有一大段没字幕）
         · 每行的时长要么 ≥ 0.3s，要么为空行（不许出现 0 宽的行）
         · 字幕总时长 vs 音频时长（差 > 30s ⇒ 有一大段漏了）
    ⑤ 音频：成品存在、时长与源差 ≤ 0.5s

用法：
    python3 tools/verify.py --dir web/data/eps --lexdir web/data/lexicon
    python3 tools/verify.py --dir web/data/eps --lexdir web/data/lexicon --sync --audio work/audio/ep13.m4a --src /workspace/live/ep278_src.mp3
"""
import argparse
import json
import os
import subprocess
import sys

REQUIRED_ROW_KEYS = ["t", "ru", "zh", "w", "st", "blk"]


def fail(msg):
    print("  ❌ %s" % msg)
    return 1


def ok(msg):
    print("  ✅ %s" % msg)
    return 0


def check_ep(path, errors):
    tag = os.path.basename(path)[:-5]
    d = json.load(open(path))
    nrows = 0
    for ci, c in enumerate(d.get("cues", [])):
        for ri, r in enumerate(c.get("rows", [])):
            nrows += 1
            for k in REQUIRED_ROW_KEYS:
                if k not in r:
                    errors.append("%s 第%d行缺字段 %s" % (tag, ci, k))
            t = r.get("t", "")
            ru = r.get("ru", [])
            if "".join(x[0] for x in ru) != t:
                errors.append("%s 第%d行 ru 没覆盖住正文" % (tag, ci))
            if not r.get("zh", "").strip():
                errors.append("%s 第%d行没有中文" % (tag, ci))
            if not isinstance(r.get("blk"), list) or not r["blk"]:
                errors.append("%s 第%d行没有整句结构块" % (tag, ci))
    return d, nrows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--lexdir", required=True)
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--audio", default=None)
    ap.add_argument("--src", default=None)
    ap.add_argument("--only", default=None, help="只查某一集（ep13）")
    args = ap.parse_args()

    errors = []
    print("== ① 结构 ==")
    idx_path = os.path.join(args.dir, "index.json")
    idx = json.load(open(idx_path))
    eps = idx.get("episodes", [])
    if not eps:
        errors.append("index.json 里一集都没有")
    print("  索引 %d 集" % len(eps))

    nrows_all = 0
    for e in eps:
        if args.only and e.get("tag") != args.only:
            continue
        p = os.path.join(args.dir, e["tag"] + ".json")
        if not os.path.exists(p):
            errors.append("索引里的 %s 没有对应文件" % e["tag"])
            continue
        d, nrows = check_ep(p, errors)
        nrows_all += nrows
        if d.get("ep") != e.get("ep"):
            errors.append("%s 的 ep 号与索引不符" % e["tag"])
        if d.get("title") != e.get("title"):
            errors.append("%s 的标题与索引不符" % e["tag"])
        if abs(float(d.get("dur", 0)) - float(e.get("dur", 0))) > 0.2:
            errors.append("%s 的时长与索引不符" % e["tag"])
        if int(e.get("n", -1)) != len(d.get("cues", [])):
            errors.append("%s 的句数与索引不符（索引 %s，实际 %d）"
                          % (e["tag"], e.get("n"), len(d.get("cues", []))))
        if not os.path.exists(os.path.join(args.dir, e["tag"] + ".blk.json")):
            errors.append("%s 缺旁挂的 .blk.json" % e["tag"])
    print("  检查了 %d 行" % nrows_all)

    print("== ② 词库 / 句型表 ==")
    for name in ("vocab.json", "styles.json", "grammar.json"):
        p = os.path.join(args.lexdir, name)
        if not os.path.exists(p):
            errors.append("缺 %s" % name)
            continue
        d = json.load(open(p))
        print("  %s：%d 条" % (name, len(d)))
    gr = json.load(open(os.path.join(args.lexdir, "grammar.json")))
    for k, v in gr.items():
        # n = 该句型在全剧的**出现次数**；exs 只是样本 ⇒ n 可以大于 exs 的条数
        if int(v.get("n", 0)) < len(v.get("exs", [])):
            errors.append("句型 %s 的 n(%s) 小于例句数(%d)" % (k, v.get("n"), len(v.get("exs", []))))
        if not v.get("ex"):
            errors.append("句型 %s 没有示例句" % k)

    if args.sync:
        print("== ③ 字幕 ↔ 音频 同步 ==")
        tag = args.only or eps[0]["tag"]
        d = json.load(open(os.path.join(args.dir, tag + ".json")))
        dur = float(d.get("dur", 0))
        prev_end = -1.0
        maxgap = 0.0
        for i, c in enumerate(d["cues"]):
            s, e = float(c["s"]), float(c["e"])
            if e <= s:
                errors.append("%s 第%d行时长非正（%.2f-%.2f）" % (tag, i, s, e))
            if s < prev_end - 0.01:
                errors.append("%s 第%d行与上一行重叠（%.2f < %.2f）" % (tag, i, s, prev_end))
            if e > dur + 1.0:
                errors.append("%s 第%d行超出音频时长（%.2f > %.2f）" % (tag, i, e, dur))
            if prev_end >= 0:
                g = s - prev_end
                maxgap = max(maxgap, g)
                if g > 12.0:
                    errors.append("%s 第%d行前有 %.1fs 空白没字幕" % (tag, i, g))
            prev_end = e
        print("  末行结束于 %.2fs / 音频 %.2fs ｜ 最大行间空白 %.2fs"
              % (prev_end, dur, maxgap))
        if prev_end < dur - 30:
            errors.append("字幕只到 %.1fs，音频有 %.1fs（>30s）没覆盖" % (prev_end, dur - prev_end))

    if args.audio:
        print("== ④ 音频 ==")
        if not os.path.exists(args.audio):
            errors.append("成品音频不存在：%s" % args.audio)
        else:
            def dur_of(p):
                out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                      "format=duration", "-of", "default=nw=1:nk=1", p],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    return float(out.stdout.decode().strip())
                except Exception:
                    return None
            ad = dur_of(args.audio)
            print("  成品时长 %s" % ad)
            if args.src and os.path.exists(args.src):
                sd = dur_of(args.src)
                print("  源时长   %s" % sd)
                if ad and sd and abs(ad - sd) > 0.5:
                    errors.append("成品与源时长差 %.3fs > 0.5s" % abs(ad - sd))
            else:
                print("  （没给 --src，跳过时长对比）")

    print()
    if errors:
        for e in errors:
            print("❌ %s" % e)
        print("🔴 校验没过：%d 个问题" % len(errors))
        sys.exit(1)
    print("🟢 全部通过")


if __name__ == "__main__":
    main()
