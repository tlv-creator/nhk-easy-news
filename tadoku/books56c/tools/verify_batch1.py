#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次 1（12 本 L0）独立验收：
  ① 命中行时间窗体检（不需要 ASR，纯算）
  ② 端到端：有音句切音频 → ASR → 比文字（正向）
  ③ 端到端：B 类哑句在「音频里真实存在的位置」切一刀 → ASR → 应当命中（证明「音频有、位置被占」）
  ④ 反向对照：在故意错位的位置切 → 应当**不**命中（证明判据有鉴别力，不是恒绿）
"""
import json, os, re, subprocess, sys, time, unicodedata
from concurrent.futures import ThreadPoolExecutor

# 🔴 路径可移植：先看环境变量，再退到「脚本所在目录」（仓库布局 books56c/tools/），最后才用本地默认
#    ⇒ **别人从仓库拉下来也能跑**（同族：交付出去的脚本不能硬编码绝对路径）
B1 = os.environ.get("TD_BATCH1") or "/workspace/work/batch1"
for _cand in (os.path.dirname(os.path.abspath(__file__)), os.path.join(B1, "tools")):
    if os.path.exists(os.path.join(_cand, "audio_cut.py")):
        sys.path.insert(0, _cand)
        break
else:
    sys.path.insert(0, "/workspace/work/e2e_v120")
try:
    from audio_cut import cut_with_pad, clip_dur      # 共用切段实现（上界用绝对位置 + 空切片不许计入）
except ImportError:
    print("❌ 环境无效（exit 9）：找不到 audio_cut.py（切段实现）—— 须与本脚本同目录")
    sys.exit(9)

# 🔴 端到端判据阈值 = 0.6（不是 0.5）
#   实测（2026-10-26 复核）：**短句 + 常见词在 0.5 会假阳**
#   例：负样本「女の人が来ました」vs ASR「女の人は走ります」⇒ 只匹配到「女の人 + ま」= 0.50 ⇒ 被判成命中
#   ⇒ 阈值 0.5：负样本假阳 1/6；改 0.6：**0/6**，且正向样本无一被误伤（正向最低 0.75）
THR = 0.6

PROMPT = ("这是一本日语有声读物。**严格逐字转写**这段音频的日文内容："
          "①保留原有标点；②不要添加原文没有的词，不要翻译、不要概括、不要补全；"
          "③听不清用 □；④按听到顺序逐句输出，每句前标【秒】=该句在这段音频里的起始秒数"
          "（整数，本段开头为 0）。")
PUNCT = set("、。「」『』（）()【】［］〈〉<>〔〕・…―－ー～〜!\"#$%&'’‘“”，,.:;?/\\|＝=＋+-×・　 ")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(c for c in s if c not in PUNCT)


def cov_of(text, asr_text):
    """文本在 ASR 文本里的字符流覆盖率（最长公共子串块总长 / 文本长度）"""
    a, b = norm(text), norm(asr_text)
    if not a:
        return 0.0
    import difflib
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    n = sum(x.size for x in sm.get_matching_blocks())
    return n / len(a)


def asr_of(path, tag):
    up = json.loads(subprocess.run(["mcode-tools", "upload_temp_url", path],
                                    capture_output=True, text=True).stdout)["temp_url"]
    ap = f"{B1}/v_args/{tag}.json"
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    json.dump({"audio_info": [{"url": up, "mime_type": "audio/mpeg", "prompt": PROMPT}]},
              open(ap, "w"), ensure_ascii=False)
    for i in range(3):
        r = subprocess.run(["mcode-tools", "connector", "call",
                            "connector__matrix__audios_understand", "--args-file", ap],
                           capture_output=True, text=True, timeout=600)
        try:
            d = json.loads(r.stdout)
        except Exception:
            d = None
        if d and d.get("results"):
            res = d["results"][0]
            if res.get("success") and (res.get("description") or "").strip():
                return res["description"]
        time.sleep(4 * (i + 1))
    return ""


def preflight():
    """🔴 环境预检：依赖不全 ⇒ **exit 9（环境失败）**，不许伪装成「判据红」
       （同族：基线为空 / 找不到文件 ⇒ 「跑前 == 跑后」是空语句假绿）"""
    need = [f"{B1}/peer/books", f"{B1}/peer/asr_truth_bytag", f"{B1}/peer/units_batch1.json",
            f"{B1}/aligned", f"{B1}/out", f"{B1}/no_audio_reason.json"]
    miss = [p for p in need if not os.path.exists(p)]
    if miss:
        print("❌ 环境无效（exit 9）：缺以下输入，跑不了（不是判据红）")
        for p in miss:
            print(f"   - {p}")
        print("   ⇒ 用 TD_BATCH1=<工作目录> 指定；音频（ext/*.mp3）与 asr_truth_bytag 是本地工作产物")
        sys.exit(9)


def main():
    preflight()
    books = sorted(os.listdir(f"{B1}/peer/books"))
    units = json.load(open(f"{B1}/peer/units_batch1.json"))
    truth_dir = f"{B1}/peer/asr_truth_bytag"
    out_dir = f"{B1}/out"
    os.makedirs(f"{B1}/v_clip", exist_ok=True)

    # ---------- ① 命中行时间窗体检 ----------
    print("=== ① 命中行时间窗体检（有音句的 s 是否落在某条 ASR 行的 [start, 下一行 start) 里）===")
    tot_live = tot_in = 0
    for f in books:
        tag = f[:-5]
        truth = json.load(open(f"{truth_dir}/{f}"))
        al = json.load(open(f"{B1}/aligned/{f}"))
        starts = [r["start"] for r in truth]
        nxt = {i: (starts[i + 1] if i + 1 < len(starts) else 1e9) for i in range(len(starts))}
        live = inwin = 0
        bad = []
        for c in al["cues"]:
            if c.get("no_audio"):
                continue
            live += 1
            s = c.get("s", 0)
            hit = any(starts[i] - 0.5 <= s < nxt[i] for i in range(len(starts)))
            if hit:
                inwin += 1
            else:
                bad.append((s, "".join(r.get("t", "") for r in c.get("rows", []))[:20]))
        tot_live += live
        tot_in += inwin
        print(f"  {tag:26} 有音 {live:3} 落在命中行窗口内 {inwin:3} ({inwin/max(1,live)*100:.1f}%)"
              + (f"  ⚠️{bad[:2]}" if bad else ""))
    print(f"  合计 {tot_in}/{tot_live} = {tot_in/max(1,tot_live)*100:.1f}%")

    # ---------- 采样 ----------
    def book_text(cue):
        return "".join(r.get("t", "") for r in cue.get("rows", []))

    pos_tasks, neg_tasks, b_tasks = [], [], []
    for f in books:
        tag = f[:-5]
        bk = json.load(open(f"{B1}/peer/books/{f}"))
        al = json.load(open(f"{B1}/aligned/{f}"))
        truth = json.load(open(f"{truth_dir}/{f}"))
        live = [(i, c) for i, c in enumerate(al["cues"]) if not c.get("no_audio")]
        # 正向：每本均匀取 3 句
        if live:
            for j in (0, len(live) // 2, len(live) - 1):
                i, c = live[j]
                pos_tasks.append((f"{tag}_p{j}", tag, c["s"], c["e"], book_text(bk["cues"][i])))
        # 反向对照：故意往后错 30 秒（且不越过轨长）
        if live:
            i, c = live[0]
            dur = bk.get("dur", 0)
            s2 = min(c["s"] + 30, max(0, dur - 3))
            neg_tasks.append((f"{tag}_neg", tag, s2, s2 + 2.0, book_text(bk["cues"][i])))
    # B 类：B 类的哑句在音频里的真实位置 = 分类表里记录的 asr_start
    cls = json.load(open(f"{B1}/no_audio_reason.json"))
    for r in cls:
        if r["kind"].startswith("B") and r.get("asr_start") is not None:
            bk = json.load(open(f"{B1}/peer/books/{r['book']}.json"))
            idx = [i for i, c in enumerate(bk["cues"]) if i == r["i"]][0]
            t = r["text"]
            b_tasks.append((f"{r['book']}_b{r['i']}", r["book"], r["asr_start"], r["asr_start"] + 2.5, t))

    def run_cut(task):
        key, tag, s, e, text = task
        out = f"{B1}/v_clip/{key}.mp3"
        p, ok, why = cut_with_pad(units, tag, s, e, out, force=True)
        if not ok:
            return (key, tag, None, why)
        desc = asr_of(p, key)
        c = cov_of(text, desc)
        return (key, tag, c, desc[:200])

    allt = pos_tasks + neg_tasks + b_tasks
    print(f"\n=== ② 端到端：{len(pos_tasks)} 正向 + {len(b_tasks)} B类反证 + {len(neg_tasks)} 反向对照 ===")
    res = []
    with ThreadPoolExecutor(3) as ex:
        for r in ex.map(run_cut, allt):
            res.append(r)
            if r[2] is None:
                print(f"  ⏭ {r[0]:34} 切片失败：{r[3]}")
    json.dump([{"key": k, "tag": t, "cov": c, "desc": d} for k, t, c, d in res],
              open(f"{B1}/e2e_result.json", "w"), ensure_ascii=False, indent=1)

    pos = [r for r in res if r[0].endswith(tuple(f"_p{j}" for j in (0, 1, 2)))]
    neg = [r for r in res if "_neg" in r[0]]
    bb = [r for r in res if "_b" in r[0] and "_neg" not in r[0] and "_p" not in r[0]]
    for name, group, thr in (("② 正向（有音句）", pos, THR), ("③ B类反证（哑句在音频里的真实位置）", bb, THR)):
        ok = [r for r in group if r[2] is not None and r[2] >= thr]
        fail = [r for r in group if r[2] is None or r[2] < thr]
        print(f"\n{name}：命中 {len(ok)}/{len(group)}（阈值 {thr}）")
        for r in fail[:8]:
            print(f"   ❌ {r[0]:34} cov={r[2]}  {r[3]}")
    print(f"\n④ 反向对照（故意错位 30 秒）：命中 {sum(1 for r in neg if r[2] and r[2] >= THR)}/{len(neg)}"
          f" ⇒ 应当 **0**（命中就说明判据恒绿）")
    for r in neg:
        if r[2] and r[2] >= THR:
            print(f"   ❌ {r[0]:34} cov={r[2]:.2f}  {r[3]}")


if __name__ == "__main__":
    main()
