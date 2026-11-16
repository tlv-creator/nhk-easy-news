#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_run.py —— 给新的一期跑 ASR 对齐（**先转 WAV**，用 align.py 的词级对齐）

🔴🔴 为什么要单独写这个（2026-11-16 实测，今天真踩到）：
    ① **必须先转 WAV**：NHK 慢速音是 HLS 的 TS 分片拼出来的 ⇒ 末尾 AAC 流是坏的
       （`TYPE_FIL: Input buffer exhausted before END element found`）：
         · ffmpeg 能容忍 ⇒ 照样解出 66.8s（容器标 67.8s，尾巴丢约 1s）
         · 但 faster-whisper 用的 **PyAV 遇到第一个错就停** ⇒ 只解出 **6s**
           ⇒ 时间轴只有开头 6 秒 ⇒ 后面全是 (0,0) ⇒ 字幕跟音直接废掉
       ⇒ 所以：**先用 ffmpeg 转 16k 单声道 WAV，再喂 whisper**
         （`run_asr.py` 一直就是这么干的）
    ② **用 align.py 的对齐**（不是 scripts/asr_align.py）：
       · align.py 用**词级时间戳** + **缺口插值** + **单调性保证**
       · scripts/asr_align.py 用**段级**时间戳、**不插值**
         ⇒ 识别不出来的句子会拿到 (0,0) ⇒ 字幕永远高亮不到（实测 8 句里 2 句是 0）
       ⇒ 线上已发布产物就是 align.py 这条链路产出的 ⇒ 保持一致

🔴 原文从 **easy.html** 解析（`parse_text.parse_easy`）：
    🔴 **不要用 scripts/fetch_nhk.py 的 easy_text** —— 那个爬法今天只能拿到 2~3 句
       （NHK 页面结构变了）⇒ 实测 parse_easy 每期 6~8 句（与线上已发布产物一致）

用法：
    python3 scripts/asr_run.py --all-new            # 站点里没有的期
    python3 scripts/asr_run.py --ids a,b,c
    python3 scripts/asr_run.py --track sp --ids ...  # 原速音（sp）轨
产出：
    work/raw/<id>_easy.txt          原文（每行一句）
    work/asr/<id>_easy.json         词级 ASR
    work/asr/<id>_easy_cues.json    句级时间轴 {"cues":[{ja,s,e},…]}
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import align as AL  # noqa: E402
import parse_text as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")
SITE = os.path.join(ROOT, "site", "app")


def to_wav(src, dst):
    """16k 单声道 WAV（whisper 的输入口径；也是绕开 PyAV 截断的关键一步）"""
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    p = subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", src,
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0 or not os.path.exists(dst):
        return None
    return dst


def published_ids():
    p = os.path.join(SITE, "data", "episodes.js")
    if not os.path.exists(p):
        return set()
    import re
    s = open(p, encoding="utf-8").read()
    return set(re.findall(r'"id"\s*:\s*"([^"]+)"', s))


def all_new():
    d = os.path.join(DATA, "episodes")
    if not os.path.isdir(d):
        return []
    pub = published_ids()
    return sorted([e for e in os.listdir(d)
                   if os.path.isdir(os.path.join(d, e)) and e not in pub], reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--all-new", action="store_true")
    ap.add_argument("--track", default="easy", choices=["easy", "sp"])
    ap.add_argument("--model", default=os.environ.get("ASR_MODEL", "small"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.ids:
        ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    elif a.all_new:
        ids = all_new()
    else:
        print("需要 --ids 或 --all-new")
        return 2
    print("== 待处理 %d 期（track=%s, model=%s）==" % (len(ids), a.track, a.model), flush=True)

    for d in ("raw", "asr", "wav"):
        os.makedirs(os.path.join(WORK, d), exist_ok=True)

    model = AL.get_model(a.model)
    print("   model ready (%s)" % a.model, flush=True)

    n = 0
    for eid in ids:
        d = os.path.join(DATA, "episodes", eid)
        out_cues = os.path.join(WORK, "asr", "%s_%s_cues.json" % (eid, a.track))
        if os.path.exists(out_cues) and not a.force:
            print("  · %s/%s 已有 ⇒ 跳过" % (eid, a.track), flush=True)
            continue
        if a.track == "easy":
            easy_p = os.path.join(d, "easy.html")
            if not os.path.exists(easy_p):
                print("  ! %s 没有 easy.html ⇒ 跳过" % eid, flush=True)
                continue
            sents = P.parse_easy(easy_p)
            if not sents:
                print("  ! %s easy 解析出 0 句 ⇒ 跳过" % eid, flush=True)
                continue
            plains = [s["plain"] for s in sents]
            with open(os.path.join(WORK, "raw", "%s_easy.txt" % eid), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(plains))
            src = os.path.join(DATA, "audio", "%s.m4a" % eid)
        else:
            plains = None
            src = os.path.join(DATA, "audio_fast", "%s.m4a" % eid)
        if not os.path.exists(src):
            print("  ! %s 没有 %s 音频 ⇒ 跳过" % (eid, a.track), flush=True)
            continue
        wav = to_wav(src, os.path.join(WORK, "wav", "%s_%s.wav" % (eid, a.track)))
        if not wav:
            print("  ! %s 转 WAV 失败 ⇒ 跳过" % eid, flush=True)
            continue
        words, dur = AL.transcribe(wav, model)
        if plains is not None:
            times = AL.align(words, plains)
            cues = [{"ja": p, "s": int(t[0]), "e": int(t[1])}
                    for p, t in zip(plains, times)]
        else:
            # sp 轨：原文未知 ⇒ ASR 的分段就是句子（逐段去空）
            segs = AL.transcribe_segments(wav, model) if hasattr(AL, "transcribe_segments") else None
            cues = []
            if segs is None:
                # align.transcribe 只给词级 ⇒ 按词的时间聚成段（用词之间的空隙切段）
                segs = []
                cur = []
                for w in words:
                    if cur and w["s"] - cur[-1]["e"] > 400:
                        segs.append(cur)
                        cur = []
                    cur.append(w)
                if cur:
                    segs.append(cur)
            for sg in segs:
                txt = "".join(w["w"] for w in sg).strip()
                if len(txt) < 2:
                    continue
                cues.append({"ja": txt, "s": int(sg[0]["s"]), "e": int(sg[-1]["e"])})
        with open(os.path.join(WORK, "asr", "%s_%s.json" % (eid, a.track)), "w",
                  encoding="utf-8") as f:
            json.dump({"audio": src, "duration": dur, "words": words}, f,
                      ensure_ascii=False, indent=1)
        with open(out_cues, "w", encoding="utf-8") as f:
            json.dump({"cues": cues}, f, ensure_ascii=False, indent=1)
        zero = sum(1 for c in cues if c["e"] <= c["s"])
        print("  ✅ %s/%s  %d 句（音频 %.1fs，零长 %d 句）"
              % (eid, a.track, len(cues), dur, zero), flush=True)
        n += 1
    print("== 完成 %d 期 ==" % n, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
