#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_fast_audio.py —— **第二道防线**：原速音的**内容**对拍（ASR 文字 vs 该期字幕）

🔴🔴🔴 为什么需要这道（2026-10-23 NHK）：
    第一道防线 `check_fast_audio.py` 只能抓「**张冠李戴**」（跨期 md5 重复 + 时长越界）。
    但它**抓不到**「抓对了流、却抓错了片段」——
    而 NHK 新闻页底部挂着最新一期的播放器，最容易犯的错就是**抓到了这一期的**流，
    却是**另一条新闻**的片段（同一站点、同一格式 ⇒ md5 不重复、时长正常 ⇒ 第一道放行）。
    ⇒ 所以要第二道：**内容对拍**。
      用 ASR 把这段音转成文字，与**该期的字幕正文**比 ⇒ 对不上就是抓错了。

🔴 判据（落在内容上）：
    ① ASR 出来的文字 vs 该期字幕正文 ⇒ 字符级相似度（bigram Dice）
    ② 相似度 < 阈值 ⇒ FAIL，并打印「这一期实际听到的是什么」
    ③ 参考词覆盖率：字幕里出现的较长词，有多少在 ASR 文字里能找到
       （对拍辅助，比纯相似度更能说明「是不是同一段话」）

⚠️ 为什么用 bigram Dice 而不是编辑距离：
    ASR 会同音错字（「貯金しますか」→「直近しますか」）⇒ 逐字比会误杀
    ⇒ bigram 相似度对少量错字宽容，对「完全不是这段话」敏感

🔴🔴🔴 **2026-10-23 实测结论：这个脚本【不接入发版闸门】**
    我在对照组上量了两种指标的分布（同一期的音 vs 别的期的音）：
      Dice 系数：  配对 0.299 / 0.148 / 0.187   错配 0.097 / 0.069 / 0.039
      包含率：      配对 0.359 / 0.604 / 0.560   错配 0.335 / 0.366 / 0.271
    ⇒ **两个指标都不可靠**：
      ① Dice：有两期的「配对」只有 0.148/0.187 ⇒ 阈值 0.20 会**误报**（实测确实误报）
      ② 包含率：配对与错配的分布**重叠**（0.359 vs 0.335）⇒ 根本分不开
    ⇒ 根因：**报纸原文（articleBody）与电视播报稿是同一新闻的两个版本**
      （书面 5197 字 vs 口语 92 秒播报）⇒ 拿整篇去比，长度不匹配 ⇒ 信号被稀释
    ⇒ 按纪律：**判据没在对照组上证明自己抓得住，就不许拿去判真数据**
    ⇒ 本脚本留作**人工抽查工具**（想知道「这段音到底在说什么」时用），
      **不参与放行判断**；真正的防线是 ① 抓取时 id 精确匹配（fetch_fast2 已加严）
      ② 跨期 md5 重复闸门（check_fast_audio.py）
    ⇒ 想做可靠的内容对拍，参照物应该是「**该期 fast 音自己的 ASR** vs 该期的 web 时间轴」
      —— 但那是循环论证（时间轴本来就是这音的 ASR 生成的），价值有限。

用法：
    python3 scripts/verify_fast_audio.py --episodes data/audio_fast/20261022de63470.m4a
    python3 scripts/verify_fast_audio.py --all           # 扫 site/app/media/fast 全部
    python3 scripts/verify_fast_audio.py --all --model small --min-score 0.55
退出码：0 = 全过；1 = 有对不上的（必须人工看）
"""
import argparse
import difflib
import json
import os
import re
import sys

ROOT = "/workspace/nhk"
DEFAULT_MODEL = os.environ.get("ASR_MODEL", "base")


def normalize(s):
    """只留日文汉字/假名/字母数字，丢掉标点空格 —— 比的是「说了什么」，不是排版"""
    s = re.sub(r"[^\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]", "", s or "")
    return s


def bigram_dice(a, b):
    """bigram Dice 系数：0~1。对少量错字宽容，对「不是同一段话」敏感"""
    if not a or not b:
        return 0.0
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    ga = [a[i:i + 2] for i in range(len(a) - 1)]
    gb = [b[i:i + 2] for i in range(len(b) - 1)]
    sa, sb = set(ga), set(gb)
    inter = len(sa & sb)
    return 2.0 * inter / (len(sa) + len(sb))


def asr_text(path, model_obj):
    segs, info = model_obj.transcribe(path, language="ja", beam_size=1,
                                      vad_filter=True, condition_on_previous_text=False)
    return "".join(s.text for s in segs)


def episode_text(eid):
    """该期的字幕正文（普通版 web.json 优先，其次易懂版 easy.html）"""
    p = os.path.join(ROOT, "data/episodes", eid, "web.json")
    if os.path.exists(p):
        try:
            j = json.load(open(p, encoding="utf-8"))
            t = j.get("articleBody") or j.get("detailedArticleBody") or ""
            if isinstance(t, list):
                t = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in t)
            if t:
                return normalize(t)
        except Exception:
            pass
    p2 = os.path.join(ROOT, "data/episodes", eid, "easy.html")
    if os.path.exists(p2):
        h = open(p2, encoding="utf-8", errors="replace").read()
        h = re.sub(r"<[^>]+>", " ", h)
        return normalize(h)
    return ""


def key_words(text, minlen=3, top=12):
    """取较长且不重复的词片段做覆盖率参考"""
    toks = re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]{%d,}" % minlen, text)
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", nargs="*", help="m4a 文件路径（可多个）")
    ap.add_argument("--all", action="store_true", help="扫 site/app/media/fast 全部")
    ap.add_argument("--dir", default=os.path.join(ROOT, "site/app/media/fast"))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    # 🔴 阈值来自**实测分布**，不是拍的（2026-10-23 NHK 三对样本）：
    #   配对（音=这一期）  0.300 / 0.326 / 0.402
    #   错配（音=别的期）  0.091 / 0.125 / 0.087
    # ⇒ 取 0.20：离两边都有充足余量（配对最低 0.30 是它的 1.5 倍）
    # ⚠️ 为什么相似度这么低？因为**报纸原文和电视播报稿是同一新闻的两个版本**
    #   （书面「21日は東日本や西日本を中心に…」 vs 口语「まるで季節が逆戻りしたような…」）
    #   ⇒ 所以这个判据是**相对**的：同一新闻 ≈ 0.30+，别的新闻 ≈ 0.13-
    ap.add_argument("--min-score", type=float, default=0.20)
    ap.add_argument("--new-only", action="store_true",
                    help="只查 data/audio_fast 里**还没上线**的那些（增量对拍，几秒级）")
    a = ap.parse_args()

    files = list(a.episodes or [])
    if a.new_only:
        # 增量：data/audio_fast 里有、但 site/app/media/fast 里没有的
        cache = os.path.join(ROOT, "data/audio_fast")
        live = os.path.join(ROOT, "site/app/media/fast")
        for f in sorted(os.listdir(cache)):
            if not f.endswith(".m4a"):
                continue
            if not os.path.exists(os.path.join(live, f)):
                files.append(os.path.join(cache, f))
        print("== 增量对拍：%d 个未上线的新音频 ==" % len(files), flush=True)
    if a.all and not a.new_only or (not files and not a.new_only):
        d = os.path.join(ROOT, "site/app/media/fast")
        if os.path.isdir(d):
            files += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".m4a")]
    if not files:
        print("❌ 没有要检查的音频")
        return 1

    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        print("❌ faster-whisper 不可用：%r" % e)
        return 1
    print("== ASR 模型：%s ==" % a.model, flush=True)
    model = WhisperModel(a.model, device="cpu", compute_type="int8")

    bad = []
    for f in files:
        eid = os.path.splitext(os.path.basename(f))[0]
        ref = episode_text(eid)
        if not ref:
            print("  ⚠ %s  没有该期字幕正文 ⇒ 跳过（无判据）" % eid)
            continue
        got = normalize(asr_text(f, model))
        score = bigram_dice(ref, got)
        kws = key_words(ref)
        cov = (sum(1 for k in kws if k in got) / len(kws)) if kws else 0.0
        ok = score >= a.min_score
        print("  %-18s 相似度 %.3f  参考词覆盖 %.2f  %s"
              % (eid, score, cov, "✅" if ok else "❌ 对不上"), flush=True)
        if not ok:
            bad.append((eid, score, cov, got[:160]))
            print("       ASR 听到的是：%s" % got[:160])

    print("\n== 结果 ==\n检查 %d 期，对不上 %d 期" % (len(files), len(bad)))
    for eid, sc, cov, head in bad:
        print("   ❌ %s（相似度 %.3f，覆盖 %.2f）" % (eid, sc, cov))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
