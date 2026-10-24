#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update.py —— 编排：抓新一期 + 对齐 + 派翻译单 / 合并构建

用法：
    python3 update.py --all      # ①抓新一期 ②ASR 对齐 ③打印待翻译工作单
    python3 update.py --build    # ④合并 + 构建 + 拷贝音频（**只收翻译完整的期**）
    python3 update.py --status   # 现在有几期、哪些待翻译
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")
SITE = os.path.join(ROOT, "site", "app")
PY = sys.executable


def sh(*args, **kw):
    print("  $ %s" % " ".join(str(a) for a in args))
    return subprocess.run([str(a) for a in args], cwd=ROOT, **kw).returncode


def known_ids():
    p = os.path.join(SITE, "data", "episodes.js")
    if not os.path.exists(p):
        return set()
    s = open(p, encoding="utf-8").read()
    import re
    return set(re.findall(r'"id"\s*:\s*"([^"]+)"', s))


def cmd_all(args):
    print("== ① 抓新一期 ==")
    sh(PY, os.path.join(ROOT, "scripts", "fetch_nhk.py"), "--all")
    new = [i for i in sorted(os.listdir(os.path.join(DATA, "episodes")))
           if os.path.isdir(os.path.join(DATA, "episodes", i))] if \
        os.path.isdir(os.path.join(DATA, "episodes")) else []
    new = [i for i in new if i not in known_ids()]
    if not new:
        print("✅ 没有新的一期（NHK やさしいことばニュース 只工作日播出）")
        return 0
    print("== ② 音频 + ASR 对齐 ==")
    for eid in new:
        audio = os.path.join(DATA, "audio", "%s.m4a" % eid)
        if not os.path.exists(audio):
            print("  ⚠️ %s 没有慢速音（%s）" % (eid, "授权链未通/未抓" if not args.force else "强制"))
            if not args.force:
                continue
        ep = json.load(open(os.path.join(DATA, "episodes", eid, "episode.json"), encoding="utf-8"))
        easy_txt = os.path.join(WORK, "raw", "%s_easy.txt" % eid)
        os.makedirs(os.path.dirname(easy_txt), exist_ok=True)
        with open(easy_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(ep.get("easy_text") or []))
        sh(PY, os.path.join(ROOT, "scripts", "asr_align.py"),
           "--audio", audio, "--text", easy_txt,
           "--out", os.path.join(WORK, "asr", "%s_easy.json" % eid), "--track", "easy")
        fast = os.path.join(DATA, "audio", "%s.fast.m4a" % eid)
        if os.path.exists(fast):
            sh(PY, os.path.join(ROOT, "scripts", "asr_align.py"),
               "--audio", fast, "--text", "",
               "--out", os.path.join(WORK, "asr", "%s_sp.json" % eid),
               "--track", "sp", "--asr-only")
    print("== ③ 待翻译工作单 ==")
    todo = []
    for eid in new:
        for track in ("easy", "sp"):
            c = os.path.join(WORK, "asr", "%s_%s_cues.json" % (eid, track))
            if not os.path.exists(c):
                continue
            cues = json.load(open(c, encoding="utf-8"))["cues"]
            zh = os.path.join(WORK, "zh", "%s_%s.json" % (eid, track))
            have = set(json.load(open(zh, encoding="utf-8")).keys()) if os.path.exists(zh) else set()
            missing = [i for i in range(len(cues)) if str(i) not in have]
            if missing:
                todo.append((eid, track, len(cues), len(missing)))
    if not todo:
        print("  （没有待翻译的句子）")
        return 0
    for eid, track, tot, miss in todo:
        print("  %s / %s：共 %d 句，待翻 %d 句" % (eid, track, tot, miss))
        print("     派单契约：work/AGENT_TASK.md（句子）/ work/SYN_TASK.md（成分块中文）")
        print("     输入：work/asr/%s_%s_cues.json" % (eid, track))
        print("     输出：work/zh/%s_%s.json  +  work/syn_out/%s_%s.json" % (eid, track, eid, track))
    return 0


def cmd_build(args):
    print("== ④ 合并 + 构建 ==")
    rc = sh(PY, os.path.join(ROOT, "scripts", "build_site.py"), *args.ids)
    return rc


def cmd_status(args):
    ids = sorted(known_ids())
    print("站点已有 %d 期：%s … %s" % (len(ids), ids[0] if ids else "-", ids[-1] if ids else "-"))
    pend = []
    for eid in ids:
        for track in ("easy", "sp"):
            c = os.path.join(WORK, "asr", "%s_%s_cues.json" % (eid, track))
            if not os.path.exists(c):
                continue
            cues = json.load(open(c, encoding="utf-8"))["cues"]
            zh = os.path.join(WORK, "zh", "%s_%s.json" % (eid, track))
            have = set(json.load(open(zh, encoding="utf-8")).keys()) if os.path.exists(zh) else set()
            if len(have) < len(cues):
                pend.append("%s/%s(%d/%d)" % (eid, track, len(have), len(cues)))
    print("待翻译：%s" % (", ".join(pend) if pend else "无"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("ids", nargs="*")
    a = ap.parse_args()
    if a.all:
        return cmd_all(a)
    if a.build:
        return cmd_build(a)
    return cmd_status(a)


if __name__ == "__main__":
    sys.exit(main())
