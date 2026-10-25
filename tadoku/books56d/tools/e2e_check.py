#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 五本的端到端抽验（复用批次 1 已入库的切段/ASR/覆盖率实现，不重新实现）：
  正向：有音句按本次对齐的时间窗切一刀 → ASR → 应当命中（覆盖率 ≥ 0.6）
  反向对照：故意往后错 30 秒切 → 应当**不**命中（证明判据不是恒绿）
  🔴 覆盖率阈值 **0.6**（0.5 对短句会假阳，见记忆）
  🔴 反向对照的样本要取「在 ASR 真值里只出现一次」的句子 ⇒ 否则撞回环结构
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor

B1 = "/workspace/work/batch1"
sys.path.insert(0, f"{B1}/tools")
sys.path.insert(0, B1)
from audio_cut import cut_with_pad          # noqa: E402
from verify_batch1 import asr_of, cov_of    # noqa: E402

L3 = "/workspace/work/l3fix"
units = json.load(open(f"{L3}/units.json"))
ALIGNED = f"{L3}/aligned"
THRESH = 0.6

MAP = {u["tag"]: u for u in units}


def book_text(cue):
    return "".join(r.get("t", "") for r in cue.get("rows", []))


def pick_unique(tag, idx, al, truth):
    """反向对照用：取一句在 ASR 真值里**只出现一次**的（避免回环结构让判据失效）"""
    from collections import Counter
    norm = lambda s: "".join(ch for ch in (s or "") if ch not in "、。「」『』（）()【】［］〈〉<>〔〕・…―－ー～〜!\"#$%&'’‘“”，,.:;?/\\|＝=＋+-×・　 ")
    cnt = Counter(norm(l["text"]) for l in truth)
    for j in range(len(al["cues"]) - 1, -1, -1):
        c = al["cues"][j]
        if c.get("no_audio"):
            continue
        t = norm(book_text(json.load(open(f"{L3}/books/{tag}.json"))["cues"][j]))
        if len(t) >= 8 and cnt.get(t, 0) == 1:
            return j, c
    return None, None


tasks = []
for tag, u in MAP.items():
    al = json.load(open(f"{ALIGNED}/{tag}.json"))
    bk = json.load(open(f"{L3}/books/{tag}.json"))
    truth = json.load(open(f"{L3}/asr_truth/{tag}.json"))
    live = [(i, c) for i, c in enumerate(al["cues"]) if not c.get("no_audio")]
    if not live:
        print(f"  ⚠️ {tag} 无有音句")
        continue
    # 🔴 跳过「印刷结构元素」（章节号/单字标题）：朗読念法可能不同
    #    （实测 野菊の墓 #0「一」= 章节号，朗読念「1」⇒ 汉字数字 vs 阿拉伯数字 ⇒ 假失败；时间点本身是对的）
    import re as _re
    def _short(t):
        t = "".join(ch for ch in (t or "") if ch not in "、。「」『』（）()【】［］〈〉<>〔〕・…―－ー～〜!\"#$%&'’‘“”，,.:;?/\\|＝=＋+-×・　 ")
        return len(t) < 4
    live2 = [(i, c) for i, c in live if not _short(book_text(bk["cues"][i]))]
    if not live2:
        continue
    for j in (0, len(live2) // 2, len(live2) - 1):
        i, c = live2[j]
        tasks.append((f"{tag}_p{j}", tag, c["s"], c["e"], book_text(bk["cues"][i]), "pos"))
    j, c = pick_unique(tag, None, al, truth)
    if c:
        dur = bk.get("dur", 0)
        s2 = min(c["s"] + 30, max(0.0, dur - 3.0))
        tasks.append((f"{tag}_neg", tag, s2, s2 + 2.0, book_text(bk["cues"][j]), "neg"))
    print(f"  {tag[:50]:52} 有音 {len(live)}/{len(al['cues'])}")

os.makedirs(f"{L3}/v_clip", exist_ok=True)


def run_cut(task):
    key, tag, s, e, text, kind = task
    out = f"{L3}/v_clip/{key}.mp3"
    p, ok, why = cut_with_pad(units, tag, s, e, out, force=True)
    if not ok:
        return (key, kind, None, f"切片失败：{why}")
    desc = asr_of(p, key)
    if not desc:
        return (key, kind, None, "ASR 空")
    return (key, kind, cov_of(text, desc), desc[:60])


print(f"\n共 {len(tasks)} 个样本（正向 {sum(1 for t in tasks if t[5]=='pos')} / 反向 {sum(1 for t in tasks if t[5]=='neg')}）")
res = []
with ThreadPoolExecutor(3) as ex:
    res = list(ex.map(run_cut, tasks))

pos = [r for r in res if r[1] == "pos"]
neg = [r for r in res if r[1] == "neg"]
fails = [r for r in pos if r[2] is None or r[2] < THRESH]
falsepos = [r for r in neg if r[2] is not None and r[2] >= THRESH]
print(f"\n正向：{len(pos)-len(fails)}/{len(pos)} 命中（阈值 {THRESH}）")
for r in fails:
    print(f"  ❌ {r[0]}  cov={r[2]}  {r[3]}")
print(f"反向对照：{len(neg)-len(falsepos)}/{len(neg)} 未命中（判据有鉴别力）")
for r in falsepos:
    print(f"  ❌ 假阳 {r[0]}  cov={r[2]}  {r[3]}")
json.dump([{"key": r[0], "kind": r[1], "cov": r[2], "head": r[3]} for r in res],
          open(f"{L3}/e2e_result.json", "w"), ensure_ascii=False, indent=1)
print("\n" + ("✅ 全绿" if not fails and not falsepos else "⚠️ 有失败，见上"))
