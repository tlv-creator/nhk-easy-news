#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_align.py —— 本地 ASR（faster-whisper）+ 与已知原文对齐 ⇒ 句级时间轴

契约（照线上已发布产物反推）：
    输入：work/asr/<epid>_<track>.json  ← 段级 ASR
    输出：work/asr/<epid>_<track>_cues.json = {"cues":[{ja,s,e},…]}   （s/e = 毫秒）

🔴 两条轨：
   · easy 轨：原文已知（NHK 的易懂版正文）⇒ ASR 出来的字面会有出入
     ⇒ 用 difflib 把「已知原文」逐字对齐到「ASR 文本」，再把每句的时间从 ASR 段借过来。
   · sp   轨：原文**未知**（原速音的电视播报稿）⇒ 直接用 ASR 的分段当句子。

🔴 为什么自己写：原脚本随沙箱重置丢失，本脚本按**已发布数据的契约**重建。

用法：
    python3 scripts/asr_align.py --audio <m4a> --text <txt|json> --out <json> [--track easy|sp]
"""
import argparse
import difflib
import json
import os
import re
import sys

MODEL_DIR = os.environ.get("FW_MODEL", "/workspace/models/fw-base")
# 🔴 送进 ASR 的「提示」用原文前 200 字 ⇒ 专有名词/数字不容易被听错
PROBE = 0


def load_text(p):
    if p.endswith(".json"):
        d = json.load(open(p, encoding="utf-8"))
        if isinstance(d, list):
            return [x if isinstance(x, str) else x.get("ja", "") for x in d]
        for k in ("easy_text", "lines", "text", "cues"):
            if k in d:
                v = d[k]
                return v if isinstance(v, list) else [v]
        return []
    raw = open(p, encoding="utf-8").read()
    return [l.strip() for l in raw.splitlines() if l.strip()]


def norm(s):
    """对齐用的归一化：去掉标点/空白/全半角差异，只留文字"""
    s = re.sub(r"[\s\u3000]", "", s)
    s = re.sub(r"[、。．，,．「」『』“”\"'（）()【】\[\]！？!?：:；;・…\-ー〜~%％℃#＃]", "", s)
    return s


def asr(audio, prompt=""):
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8", cpu_threads=2)
    segs, info = model.transcribe(
        audio, language="ja", task="transcribe", beam_size=5,
        word_timestamps=True, vad_filter=False, condition_on_previous_text=False,
        initial_prompt=prompt or None)
    out = []
    for s in segs:
        words = [{"s": round(float(w.start), 3), "e": round(float(w.end), 3),
                  "w": (w.word or "").strip()} for w in (s.words or [])]
        out.append({"start": round(float(s.start), 3), "end": round(float(s.end), 3),
                    "text": (s.text or "").strip(), "words": words})
        print("  [%7.2f - %7.2f] %s" % (s.start, s.end, (s.text or "").strip()),
              file=sys.stderr)
    return out, float(info.duration)


def align_text_to_asr(sentences, segs):
    """把已知句子逐字对齐到 ASR 文本 ⇒ 每句拿到 (start,end) 毫秒

    做法：把 ASR 段拼成一条带字符→时间映射的串，再用 difflib 的匹配块
         把「已知句子的归一化字符」映射回 ASR 的字符位置。
    """
    # 1) ASR 侧：字符 → 时间
    chars, ctime = [], []
    for seg in segs:
        t = seg["text"]
        for ch in t:
            n = norm(ch)
            if not n:
                continue
            chars.append(n)
            ctime.append((seg["start"], seg["end"]))
    asr_txt = "".join(chars)
    # 2) 已知侧：句子 → 归一化字符区间
    known, spans = "", []
    for s in sentences:
        n = norm(s)
        if not n:
            continue
        spans.append((len(known), len(known) + len(n), s))
        known += n
    if not known or not asr_txt:
        return [(0, 0, s) for s in sentences]
    # 3) 匹配
    sm = difflib.SequenceMatcher(None, known, asr_txt, autojunk=False)
    # char_index(known位置) -> asr位置
    kmap = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            kmap[a + k] = b + k
    out = []
    for ks, ke, s in spans:
        pos = [kmap[i] for i in range(ks, ke) if i in kmap]
        if not pos:
            out.append((0, 0, s))
            continue
        st = min(ctime[p][0] for p in pos)
        en = max(ctime[p][1] for p in pos)
        out.append((round(st * 1000), round(en * 1000), s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--text", required=True, help="原文：txt 行分隔 或 json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--track", default="easy", choices=["easy", "sp"])
    ap.add_argument("--asr-only", action="store_true", help="只存 ASR 段，不对齐")
    args = ap.parse_args()

    sentences = load_text(args.text)
    prompt = "".join(sentences)[:200] if sentences else ""
    segs, dur = asr(args.audio, prompt)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"audio": args.audio, "duration": dur, "segments": segs}, f,
                  ensure_ascii=False, indent=1)

    if args.asr_only or args.track == "sp":
        # sp 轨：原文未知 ⇒ 直接用 ASR 分段当句子
        cues = []
        for s in segs:
            t = s["text"].strip()
            if len(t) < 2:
                continue
            cues.append({"ja": t, "s": int(round(s["start"] * 1000)),
                         "e": int(round(s["end"] * 1000))})
    else:
        raw = align_text_to_asr(sentences, segs)
        cues = [{"ja": s, "s": a, "e": b} for a, b, s in raw]
        # 单调化：下一句的 s 不得早于上一句的 e
        for i in range(1, len(cues)):
            if cues[i]["s"] < cues[i - 1]["e"]:
                cues[i]["s"] = cues[i - 1]["e"]
    with open(args.out.replace(".json", "_cues.json"), "w", encoding="utf-8") as f:
        json.dump({"cues": cues}, f, ensure_ascii=False, indent=1)
    print("✅ %d 句 → %s_cues.json（音频 %.1fs）" % (len(cues), args.out, dur),
          file=sys.stderr)


if __name__ == "__main__":
    main()
