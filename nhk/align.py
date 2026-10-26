#!/usr/bin/env python3
"""
用 Whisper 做时间轴：把官方文本对齐到音频上。

做法：
  1) faster-whisper 转写（ja, word_timestamps=True）
  2) 归一化 ASR 文本 与 官方句子文本（去标点/空白/小假名）
  3) 用 difflib.SequenceMatcher 找最长公共块，把 ASR 的词时间戳映射到每句
  4) 每句取所覆盖 ASR 词的最早 start 与最晚 end；匹配不上的句子按邻居插值

产出：[(start_ms, end_ms), ...] 与句子一一对应
"""
import difflib
import json
import os
import re
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

_MODEL = None
MODEL_DIR = "/workspace/models"


def get_model(size="small"):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8",
                               download_root=MODEL_DIR)
    return _MODEL


_norm_re = re.compile(r"[^ぁ-んァ-ヶ一-鿿0-9A-Za-z]")


def norm(s: str) -> str:
    return _norm_re.sub("", s)


def transcribe(audio_path, model=None):
    model = model or get_model()
    segments, info = model.transcribe(
        audio_path, language="ja", beam_size=5, word_timestamps=True,
        vad_filter=True, condition_on_previous_text=False,
        initial_prompt="日本語のニュース原稿を読み上げている。",
    )
    words = []
    for seg in segments:
        for w in (seg.words or []):
            if w.start is None or w.end is None:
                continue
            words.append({"s": int(w.start * 1000), "e": int(w.end * 1000),
                          "w": w.word})
    return words, (info.duration if info else 0)


def align(words, sentences):
    """返回每句的 (start_ms, end_ms)"""
    n = len(sentences)
    if n == 0:
        return []
    if not words:
        return [(0, 0)] * n

    # 拼出 ASR 全文 + 每个字符对应的词索引
    asr_chars, asr_word_of = [], []
    for i, w in enumerate(words):
        for ch in w["w"]:
            asr_chars.append(ch)
            asr_word_of.append(i)
    asr_txt = "".join(asr_chars)
    asr_norm = norm(asr_txt)
    # norm 之后索引会变，重新映射
    norm_chars, norm_word_of = [], []
    for idx, ch in enumerate(asr_txt):
        c = norm(ch)
        if not c:
            continue
        for cc in c:
            norm_chars.append(cc)
            norm_word_of.append(asr_word_of[idx])
    asr_norm = "".join(norm_chars)

    sent_norms = [norm(s) for s in sentences]
    sm = difflib.SequenceMatcher(None, asr_norm, "".join(sent_norms), autojunk=False)

    # 把每个官方字符映射到 ASR 字符下标
    sent_char_ranges = []
    pos = 0
    for sn in sent_norms:
        sent_char_ranges.append((pos, pos + len(sn)))
        pos += len(sn)
    full_official = "".join(sent_norms)

    spans = [[] for _ in range(n)]
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            gi = a + k
            oi = b + k
            # 找到这个官方字符属于第几句
            for si in range(n):
                s0, s1 = sent_char_ranges[si]
                if s0 <= oi < s1:
                    spans[si].append(norm_word_of[gi])
                    break

    times = []
    for si in range(n):
        idxs = spans[si]
        if idxs:
            s = min(words[i]["s"] for i in idxs)
            e = max(words[i]["e"] for i in idxs)
            times.append([s, e])
        else:
            times.append(None)

    # 插值补空
    known = [i for i, t in enumerate(times) if t]
    if not known:
        return [(0, 0)] * n
    for i in range(n):
        if times[i]:
            continue
        prev = [j for j in known if j < i]
        nxt = [j for j in known if j > i]
        if prev and nxt:
            p, q = prev[-1], nxt[0]
            t = (times[p][1] + times[q][0]) // 2
            times[i] = [t, t]
        elif prev:
            times[i] = [times[prev[-1]][1], times[prev[-1]][1]]
        else:
            times[i] = [times[nxt[0]][0], times[nxt[0]][0]]
    # 保证单调
    for i in range(1, n):
        if times[i][0] < times[i - 1][1]:
            times[i] = [times[i - 1][1], max(times[i][1], times[i - 1][1] + 120)]
    return [(int(a), int(b)) for a, b in times]


def run(audio_path, sentences, model=None, size="small"):
    words, dur = transcribe(audio_path, model or get_model(size))
    times = align(words, sentences)
    return times, dur, words


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("sentences_json")
    ap.add_argument("--model", default="small")
    a = ap.parse_args()
    sents = json.load(open(a.sentences_json))
    if isinstance(sents, dict):
        sents = sents.get("sentences", [])
    t0 = __import__("time").time()
    times, dur, words = run(a.audio, sents, size=a.model)
    print("音频时长 %.1fs  ASR 用时 %.1fs  词数 %d" % (dur, __import__("time").time() - t0, len(words)))
    for s, (a1, b1) in zip(sents, times):
        print("  %6.2f - %6.2f  %s" % (a1 / 1000, b1 / 1000, s[:60]))
