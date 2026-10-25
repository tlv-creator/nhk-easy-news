#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 `sp` 轨（电视播报稿）的 ASR —— 普通版「跟音」模式的数据源

为什么需要这条轨（2026-10-24）
------------------------------------------------------------------------------
普通版（普通语速）页面上的**文字**是 NHK 的**报纸原文**（书面语），
而**普通版的音**是电视播报稿（口语）—— 同一件事的两个版本，字面不一样：

    音（播报稿）：「まるで季節が逆戻りしたような暑さになりました…」
    字（报纸原文）：「21日は東日本や西日本を中心に季節外れの暑さとなり…」

⇒ 拿报纸原文去套播报音的时间轴 ⇒ 字幕**跳句**（一句还没念完就跳下一句）。
⇒ 所以普通版必须**存两套句子**：
      ① text 轨 = 报纸原文（阅读用，默认）
      ② sp   轨 = 播报稿（跟音用）—— **必须来自该期原速音的真实 ASR**

🔴 纪律：
  · `ja` **必须是 ASR 的真实输出**（不能编、不能用报纸原文冒充）
  · 时间轴**必须来自同一份原速音**（用户听到的就是这一份）⇒ ASR 的输入
    用 **交付出去的那份** site/app/media/fast/<id>.m4a，不是抓回来的源文件
    （源文件会被重新编码 ⇒ 时长/内容可能差几毫秒 ⇒ 高亮会漂）
  · 找不到原速音的期 ⇒ **不产出 sp 轨**（绝不拿慢速音凑）

产出： data/sp/<id>.json = {"dur": 秒, "sents": [{"ja":…, "s": 毫秒, "e": 毫秒}]}
用法： python3 scripts/asr_sp.py [--ids a,b,c] [--app site/app] [--out data/sp]
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 🔴 句末标点（切句用）
SENT_END = "。！？"
# 🔴 停顿超过这个秒数就切一刀（自然停顿 = 句界）
GAP_SPLIT = 0.55
# 🔴 一句最长多少字（超了强制切，避免一行挤太长）
MAX_CHARS = 60


def to_wav(src, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    import subprocess
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0 or not os.path.exists(dst):
        return None
    return dst


def clean(txt):
    t = txt.strip()
    t = re.sub(r"\s+", "", t)
    return t


def split_words(words):
    """把 whisper 的词序列切成句子：按句末标点 + 自然停顿 + 长度上限
    返回 [(ja, s_ms, e_ms)]"""
    sents = []
    buf = []          # 当前句的词
    buf_txt = []
    prev_end = None

    def flush():
        if not buf:
            return
        ja = clean("".join(buf_txt))
        if len(ja) >= 3:
            sents.append((ja, buf[0]["s"], buf[-1]["e"]))
        del buf[:]
        del buf_txt[:]

    for w in words:
        txt = w["w"]
        # 🔴 自然停顿 ⇒ 这里就是句界（播报稿的换气/断句）
        if buf and prev_end is not None and (w["s"] - prev_end) / 1000.0 >= GAP_SPLIT:
            flush()
        buf.append(w)
        buf_txt.append(txt)
        prev_end = w["e"]
        joined = clean("".join(buf_txt))
        # 句末标点 → 收句
        if any(ch in SENT_END for ch in txt):
            flush()
            continue
        # 太长也切一刀，防止一整段挤在一行
        if len(joined) >= MAX_CHARS:
            flush()
    flush()
    # 合并过短的碎片（< 6 字）到前一句
    merged = []
    for s in sents:
        if merged and len(s[0]) < 6:
            merged[-1] = (merged[-1][0] + s[0], merged[-1][1], s[2])
        else:
            merged.append(s)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(ROOT, "site", "app"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "sp"))
    ap.add_argument("--ids", default="")
    ap.add_argument("--model", default=os.environ.get("ASR_MODEL", "small"))
    a = ap.parse_args()
    app = os.path.normpath(a.app)
    os.makedirs(a.out, exist_ok=True)

    eps = json.loads(re.search(r"=\s*(\[.*?\]);",
                                open(os.path.join(app, "data", "episodes.js"),
                                     encoding="utf-8").read(), re.S).group(1))
    want = [x.strip() for x in a.ids.split(",") if x.strip()]
    todo = []
    for e in eps:
        eid = e["id"]
        if want and eid not in want:
            continue
        if (e.get("durFast") or 0) <= 1:
            continue          # 没有原速音 ⇒ 不做 sp 轨
        src = os.path.join(app, "media", "fast", eid + ".m4a")
        if not os.path.exists(src):
            print("  ! %s 没有原速音文件 ⇒ 跳过" % eid, flush=True)
            continue
        todo.append((eid, src))
    print("== 待做 %d 期（模型 %s）==" % (len(todo), a.model), flush=True)

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8",
                         download_root="/workspace/models")

    n = 0
    for eid, src in todo:
        outp = os.path.join(a.out, eid + ".json")
        if os.path.exists(outp):
            print("  [%2d] %s 已有 ⇒ 跳过" % (n + 1, eid), flush=True)
            n += 1
            continue
        t0 = time.time()
        wav = to_wav(src, "/tmp/_sp_%s.wav" % eid)
        if not wav:
            print("  ! %s 转 wav 失败 ⇒ 跳过" % eid, flush=True)
            continue
        try:
            segments, info = model.transcribe(
                wav, language="ja", beam_size=5, word_timestamps=True,
                vad_filter=True, condition_on_previous_text=False,
                initial_prompt="日本語のニュース原稿を読み上げている。")
            words = []
            for seg in segments:
                for w in (seg.words or []):
                    if w.start is None or w.end is None:
                        continue
                    words.append({"s": int(w.start * 1000), "e": int(w.end * 1000),
                                  "w": w.word})
            sents = split_words(words)
        except Exception as ex:
            print("  ! %s ASR 失败：%s" % (eid, ex), flush=True)
            continue
        finally:
            if os.path.exists("/tmp/_sp_%s.wav" % eid):
                os.remove("/tmp/_sp_%s.wav" % eid)
        if not sents:
            print("  ! %s ASR 没出句子 ⇒ 跳过" % eid, flush=True)
            continue
        dur = info.duration if info else 0
        json.dump({"id": eid, "dur": dur,
                   "sents": [{"ja": s[0], "s": s[1], "e": s[2]} for s in sents],
                   # 🔴 原始词级时间戳也存下来 ⇒ 以后改切句规则**不用重跑 ASR**
                   "words": words},
                  open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        n += 1
        print("  [%2d] %s  句=%d  时长=%.1fs  ASR=%.0fs"
              % (n, eid, len(sents), dur, time.time() - t0), flush=True)
    print("== sp 轨 ASR 完成 %d 期 ==" % n, flush=True)


if __name__ == "__main__":
    main()
