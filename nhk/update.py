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
    print("== ① 抓新一期（文本 + 慢速音）==")
    # 🔴🔴 2026-11-21 改：**先 fetch_batch（文本+慢速音），再 fetch_nhk（补普通版）**
    #    手册 §7 的硬要求：顺序反了就**没有音频**
    #    （fetch_batch 出 data/audio/<id>.m4a ⇒ build 才有得拷 ⇒ 否则「有字幕没声音」）
    sh(PY, os.path.join(ROOT, "fetch_batch.py"), "20", DATA)
    sh(PY, os.path.join(ROOT, "scripts", "fetch_nhk.py"), "--all")
    new = [i for i in sorted(os.listdir(os.path.join(DATA, "episodes")))
           if os.path.isdir(os.path.join(DATA, "episodes", i))] if \
        os.path.isdir(os.path.join(DATA, "episodes")) else []
    new = [i for i in new if i not in known_ids()]
    if not new:
        print("✅ 没有新的一期（NHK やさしいことばニュース 只工作日播出）")
        return 0
    print("== ①b 修坏帧（PCM 层）——**必须在 ASR 之前** ==")
    # 🔴 手册 §6.3：NHK 的慢速音是从 HLS 的 TS 分片拼出来的 ⇒ 含 51 带 AAC 帧
    #    ⇒ 解码出超满刻度垃圾样本 ⇒ ASR 直接报错/截断（实测音频只认出前 6 秒）
    sh(PY, os.path.join(ROOT, "scripts", "repair_all_audio.py"))
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
        # 🔴🔴 2026-11-21 修：**ASR 要对齐的句，必须和 build 时切出来的句完全一致**
        #    坑：`episode.json` 的 easy_text 是 fetch_nhk.py 抓 HTML 时**截断**的
        #    （实测 8 句只剩 2~3 句，末尾还带 "…"）
        #    ⇒ build_inc 用 `parse_easy(easy.html)` 切出 8 句，ASR 却只有 2 句
        #    ⇒ `len(asr_cues) != len(easy_sents)` ⇒ **整期被静默跳过**（不上线）
        #    ⇒ 唯一权威 = parse_text.parse_easy(easy.html)（build 就是用它切的）
        lines = []
        try:
            import parse_text as _PT
            _ep = os.path.join(DATA, "episodes", eid, "easy.html")
            if os.path.exists(_ep):
                lines = [s["plain"] for s in _PT.parse_easy(_ep)]
        except Exception as ex:
            print("  ⚠️ %s parse_easy 失败（%s）⇒ 退回 episode.json 的 easy_text" % (eid, ex))
        if not lines:
            lines = list(ep.get("easy_text") or [])
        with open(easy_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("  %s：easy 原文 %d 句" % (eid, len(lines)))
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
    # 🔴🔴 2026-11-21 改：**不再用 scripts/build_site.py**
    #    坑：build_site.py 是**从 data/episodes/ 全量重建**的旧版
    #    ⇒ ① 会把 `no` 重新编号（手册 §32.3：用户的生词靠 no 指回期数）
    #    ⇒ ② 不带 sp 轨、不带 blk ⇒ 重建等于把新功能抹掉
    #    ⇒ ③ `data/episodes/` 沙箱重置后只剩新抓的几期 ⇒ 重建会**抹掉已发布的期**
    #    ⇒ 现在：走**外科手术式**的 scripts/build_inc.py（已发布的期一个字都不动）
    ids = list(args.ids)
    if not ids:
        ids = [i for i in sorted(os.listdir(os.path.join(DATA, "episodes")))
               if os.path.isdir(os.path.join(DATA, "episodes", i))
               and i not in known_ids()]
    if not ids:
        print("✅ 没有新的一期可构建")
        return 0
    # ① 增量构建（easy + web）
    rc = sh(PY, os.path.join(ROOT, "scripts", "build_inc.py"), "--ids", ",".join(ids))
    if rc:
        return rc
    # ② 音频重编 96k（配方唯一权威 = scripts/audio_recipe.py）
    sh(PY, os.path.join(ROOT, "retranscode.py"), "--ids", ",".join(ids))
    # ③ sp 轨（电视播报稿，普通版「跟音」模式用）
    # 🔴 顺序：asr_sp.py 必须读**交付出去的那份** site/app/media/fast/<id>.m4a
    #    （用户听到的就是它 ⇒ 时间轴才对得上）⇒ 所以它在「重编之后」才跑
    spids = [i for i in ids if os.path.exists(os.path.join(DATA, "audio_fast", i + ".m4a"))]
    if spids:
        sh(PY, os.path.join(ROOT, "scripts", "asr_sp.py"), "--ids", ",".join(spids),
           "--model", os.environ.get("ASR_MODEL_PATH", "/workspace/models/fw-base"))
        # 没有译文的期 ⇒ build_sp 会跳过（不发布半成品）⇒ 这里明确提示
        todo = [i for i in spids
                if not os.path.exists(os.path.join(WORK, "sp", i + ".json"))]
        if todo:
            print("⚠️ sp 轨（电视播报稿）还没有译文 ⇒ 这一轮先不带 sp 轨上线：")
            print("   %s" % ", ".join(todo))
            print("   翻完放进 work/sp/<id>.json 后重跑：python3 update.py --build")
        sh(PY, os.path.join(ROOT, "scripts", "build_sp.py"), "--ids", ",".join(spids))
    # ④ blk（语法讲解新格式）
    sh(PY, os.path.join(ROOT, "scripts", "inject_blk.py"), "--ids", ",".join(ids))
    print("== ✅ 构建完成：%s ==" % ", ".join(ids))
    return 0


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
