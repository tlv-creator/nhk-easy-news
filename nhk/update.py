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
    rc = subprocess.run([str(a) for a in args], cwd=ROOT, **kw).returncode
    # 🔴 2026-11-24：子命令失败**必须打印出来**
    #    （原来只返回 rc、调用方常常不查 ⇒ ASR 挂了整轮照样「成功」⇒ 静默无更新）
    if rc:
        print("  ❌ 子命令失败（rc=%s）：%s" % (rc, " ".join(str(a) for a in args)))
    return rc


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
    # 🔴🔴🔴 2026-11-24 修（今天实测踩到）：**ASR 必须用 venv 的 python + scripts/asr_run.py**
    #    坑 ①（今天真踩）：faster-whisper **只装在 /workspace/venv 里**（系统 python3 没有）
    #       ⇒ 原来用 sys.executable 跑 ASR ⇒ ModuleNotFoundError
    #       ⇒ 而 sh() 不看返回码 ⇒ **整轮静默跳过 ASR** ⇒ 后面 build 时该期「没有时间轴 ⇒ 跳过」
    #       ⇒ 用户看到的是「今天没更新」，**全程不报错**（静默失败族）
    #    坑 ②：scripts/asr_align.py 是**段级、不插值** ⇒ 识别不出的句子拿 (0,0)
    #       ⇒ 字幕永远高亮不到 ⇒ 正解 = scripts/asr_run.py
    #       （**先转 16k WAV** + align.py 的**词级对齐 + 缺口插值 + 单调性保证**；手册 §35.2：
    #        PyAV 遇到 NHK 那条坏 AAC 流会只解出前 6 秒 ⇒ 必须先转 WAV）
    #    🔴 ASR 失败必须**大声失败**（不能静默继续）⇒ 下面直接 return rc
    ASR_PY = "/workspace/venv/bin/python"
    if not os.path.exists(ASR_PY):
        ASR_PY = PY
        print("  ⚠️ 没有 /workspace/venv ⇒ 用当前 python 跑 ASR（可能缺 faster_whisper）")
    rc = sh(ASR_PY, os.path.join(ROOT, "scripts", "asr_run.py"),
            "--ids", ",".join(new), "--track", "easy")
    if rc:
        print("  ❌ ASR(easy) 失败（rc=%s）⇒ 今天的期会没有时间轴 ⇒ 停" % rc)
        return rc
    spids = [e for e in new
             if os.path.exists(os.path.join(DATA, "audio_fast", e + ".m4a"))]
    if spids:
        rc = sh(ASR_PY, os.path.join(ROOT, "scripts", "asr_run.py"),
                "--ids", ",".join(spids), "--track", "sp")
        if rc:
            print("  ❌ ASR(sp) 失败（rc=%s）⇒ 停" % rc)
            return rc
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
    # 🔴🔴 2026-11-24 修：asr_sp.py 也要 **venv 的 python**（它 import faster_whisper）
    #    ⇒ 否则 ModuleNotFoundError ⇒ sp 轨**静默缺失**（普通版就退化成纯阅读，用户看不出来）
    ASR_PY = "/workspace/venv/bin/python"
    if not os.path.exists(ASR_PY):
        ASR_PY = PY
        print("  ⚠️ 没有 /workspace/venv ⇒ 用当前 python 跑 ASR（可能缺 faster_whisper）")
    spids = [i for i in ids if os.path.exists(os.path.join(DATA, "audio_fast", i + ".m4a"))]
    if spids:
        sh(ASR_PY, os.path.join(ROOT, "scripts", "asr_sp.py"), "--ids", ",".join(spids),
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
