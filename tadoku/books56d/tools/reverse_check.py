#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 反向检查：ASR 真值里有、我们书里**找不到**的句子
（构建会话 2026-10-26 建议的口径：ASR 是纯文本无注音 ⇒ 比 OCR 干净得多）

做法：对每条 ASR 行，算它在「书的归一化全文流」里的**最佳覆盖率**
      （最长公共子串匹配块总长 / 该行长度）⇒ 低于阈值 ⇒ 这段话书里没有 ⇒ 候选缺失
输出：每条轨的候选行 + 覆盖率 ⇒ 人工过一遍。
"""
import json, os, sys, unicodedata, difflib

L3 = "/workspace/work/l3fix"
PUNCT = set("、。「」『』（）()【】［］〈〉<>〔〕・…―－ー～〜!\"#$%&'’‘“”，,.:;?/\\|＝=＋+-×・　 ")
THRESH = 0.6


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(c for c in s if c not in PUNCT)


def best_cov(line, book_flow):
    """该 ASR 行在书流里的最佳覆盖率"""
    a = norm(line)
    if len(a) < 5:
        return None
    sm = difflib.SequenceMatcher(None, a, book_flow, autojunk=False)
    n = sum(x.size for x in sm.get_matching_blocks())
    return n / len(a)


def main():
    # 轨 → 书
    MAP = {
        "9784866397306-track-001": "level3-koizumi-yakumo-s-scary-tales-mujina-and-ghost-falls",
        "9784866397306-track-002": "level3-koizumi-yakumo-s-scary-tales-mujina-and-ghost-falls",
        "9784866397306-track-003": "level3-the-restaurant-of-many-orders",
        "9784866397306-track-004": "level3-who-are-these-people-the-story-of-japanese-banknotes",
        "9784866397306-track-005": "level3-princess-kaguya",
        "9784866397306-track-006": "level3-the-spider-s-thread-and-the-nose",
        "9784866397306-track-007": "level3-the-spider-s-thread-and-the-nose",
        "9784866396569-NogikunoHaka": "level4-the-grave-of-the-wild-chrysanthemums",
        "9784866396569-Toshishun": "level4-toshishun",
        "9784866396569-Yukionna": "level4-the-snow-woman",
        "9784866396569-Hashire_Merosu": "level4-run-melos",
        "9784866396569-NagaiTakashi": "level4-takashi-nagai-living-in-nagasaki-the-atomic-bomb-site",
        "9784866396583-l1-002": "level1-kimono",
        "9784866396583-l1-003": "level1-why-did-the-shrimp-s-body-bend-why-are-there-no-cat-year",
        "9784866396583-l1-004": "level1-why-did-the-shrimp-s-body-bend-why-are-there-no-cat-year",
    }
    out = {}
    total_flag = 0
    for track, tag in MAP.items():
        tp = f"{L3}/asr_truth_files/{track}.json"
        bp = f"{L3}/books/{tag}.json"
        if not (os.path.exists(tp) and os.path.exists(bp)):
            print(f"  ⏭ {track}（缺文件）")
            continue
        truth = json.load(open(tp))
        book = json.load(open(bp))
        flow = norm("".join("".join(r.get("t", "") for r in c.get("rows", [])) for c in book["cues"]))
        flagged = []
        for l in truth:
            c = best_cov(l["text"], flow)
            if c is None:
                continue
            if c < THRESH:
                flagged.append({"start": l["start"], "cov": round(c, 3), "text": l["text"]})
        out[track] = {"book": tag, "flagged": flagged}
        total_flag += len(flagged)
        print(f"  {track:34} → {tag[:42]:44} ASR {len(truth):4} 行  候选缺失 {len(flagged):3} 行")
    json.dump(out, open(f"{L3}/reverse_check.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n合计候选 {total_flag} 行 ⇒ 见 reverse_check.json")


if __name__ == "__main__":
    main()
