#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日语泛读：书面句子 ↔ 音频真值的**字符流全局对齐**，重算时间轴。

为什么不用「按序号硬配」：
  ASR 切句粒度 ≠ 书面切句粒度（一句被拆成 3 行，一行也可能含 2 句）⇒ 序号对不上。
为什么不用「整句相似度」：
  朗読版与教材书面版用词不同（和尚/おしょうさん、家/うち、皮/顔）⇒ ratio 被拉低 ⇒ 误判成没对上。
为什么不用「滑动窗口 + 停顿分段」：
  ASR 会在一句话中间换行（【9】1394年、/【12】京都に/【13】一人…）⇒ 按停顿分段会把一句切碎 ⇒ 匹配散掉。

本器口径（实测校准过）：
  覆盖率 = 字符级**最长公共子串匹配块**总长 / 书面句长度
  ⇒ 「おしょうさんの大切な茶碗を割りました」vs「和尚様の大切な茶碗を割りました」
     匹配块 =「の大切な茶碗を割りました」⇒ 覆盖 ≈ 0.6 ⇒ 判「有对应」✅
  ⇒ 「一休さんは、とても頭のいいお坊さんです。」vs「一休さん」⇒ 0.22 ⇒ 判「无对应」✅（音频开头是书名页）
  ⇒ 「一休さんはどうするのでしょうか。」vs「一休さんはどんなことをしたり…」⇒ 0.00 ⇒ 真·无对应 ✅

时间轴来源：**音频真值**（平台 ASR 逐段转写的时间戳），不是原来的估算值。

🔴 **三关全过才算「这句有音」**（2026-10-25 补，第 3 关是新加的）：
  ① 文本能对上（字符流覆盖率 ≥ 0.50）
  ② **顺序能对上**（这句在音频里的位置不早于上一句）—— 见 apply_order_check()
  ③ 时间轴单调、不重叠（rebuild_data.py 的 check()，不满足 exit 1）
  ⚠️ 只过 ① 会产出**「端到端 ASR 能命中、但高亮跳错行」**的假正确
     （书上顺序 ≠ 音频顺序的书上，文本确实在音频里，但时间轴会非单调）
"""
import json, os, re, sys, unicodedata
from difflib import SequenceMatcher

ROOT = "/workspace/work/tadoku"
TRUTH_DIR = f"{ROOT}/asr_truth"
COV_THRESHOLD = 0.50

# ---------- 归一化（只用于比较，不改数据）----------
CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7",
           "八": "8", "九": "9"}


def cn2num(s):
    def repl(m):
        t = m.group(0)
        if all(c in CN_NUM for c in t):
            if len(t) == 1:
                return CN_NUM[t]
            if "十" in t:
                a, _, b = t.partition("十")
                return str(int(CN_NUM.get(a, 1)) * 10 + (int(CN_NUM[b]) if b else 0))
            return t
        return t
    return re.sub(r"[一二三四五六七八九十]+", repl, s)


PUNCT = set("、。「」『』（）()【】［］〈〉<>〔〕・…―－ー～〜!\"#$%&'’‘“”，,.:;?/\\|＝=＋+-×・　 ")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = cn2num(s)
    s = "".join(c for c in s if c not in PUNCT)
    return s


KANA_RE = re.compile(r"[ぁ-ゖァ-ヺー]")

# 顺序检查的统计（每本书跑完由 main() 取走并清零）
ORDER_STATS = {"out_of_order": 0, "channel_switch": 0}

# 🔴 **顺序检查的容差（秒）**
#   ASR 的行时间戳粒度是 2~5 秒 ⇒ 相邻两句被插值算出的起点可能差零点几秒到一秒
#   ⇒ 用「严格早于」判乱序 ⇒ 会把**插值噪声**当成真乱序 ⇒ 过度降级（实测多降 27 句，
#      而音频端到端确认其中 27 句确实念了）
#   ⇒ 容差取 **1.5 秒**（大于 ASR 行粒度的噪声，小于真实乱序的幅度
#      ——实测真实乱序的幅度是**几十秒**级，如地震 #37 在 330 秒而书中更早的 #41 在 289 秒）
ORDER_TOL = float(os.environ.get("TD_ORDER_TOL", "1.5"))
NO_ORDER_CHECK = os.environ.get("TD_NO_ORDER_CHECK") == "1"

# 汉字/数字/字母（去掉假名后剩下的「骨架」）
def skeleton(s):
    """只留汉字/数字/英文字母（去掉假名与标点）
    用途：别册非虚构类书的 OCR 把振假名并进了正文（「これは、むかし昔」）
        ⇒ 整句比对会因为多了注音假名而对不上 ⇒ 误判成哑句
        ⇒ 汉字骨架比对不受影响（假名被去掉了）
    """
    return "".join(c for c in s if not KANA_RE.match(c))


SPEECH_END = {}      # tag -> 实测「最后说话时刻」（measure_speech_end.py 测得；能量分析，不是猜）
_CTX = {}            # 当前处理的 tag


def align(book_cues, asr_lines, dur, transform=None):
    """
    返回 (new_cues, info)
      new_cues[i].s/e   = 音频里的真实时间（秒）
      new_cues[i].no_audio = 1 ⇒ 这句在音频里没有对应
      new_cues[i].cov   = 覆盖率
      new_cues[i]._n    = 本次比对用的字符数（合并双通道时要用）
    transform: 归一化函数，默认 norm；传 skeleton_norm 则走「只比汉字」通道
    """
    tr = transform or norm
    # ---- A：书面字符流（记每句区间）----
    A, a_len = [], []
    for ci, c in enumerate(book_cues):
        t = "".join(r.get("t", "") for r in c.get("rows", []))
        n = tr(t)
        a_len.append(len(n))
        for ch in n:
            A.append((ch, ci))

    # ---- B：ASR 字符流（记每行区间 + 每行起点秒）----
    B, b_len, b_start = [], [], []
    for li, l in enumerate(asr_lines):
        n = tr(l["text"])
        b_len.append(len(n))
        b_start.append(l["start"])
        for ch in n:
            B.append((ch, li))

    cov = [0] * len(book_cues)
    first_b = [None] * len(book_cues)   # 该句首个匹配字符在 B 中的位置
    last_b = [None] * len(book_cues)

    if A and B:
        sa = "".join(x[0] for x in A)
        sb = "".join(x[0] for x in B)
        sm = SequenceMatcher(None, sa, sb, autojunk=False)
        for bi, bj, n in sm.get_matching_blocks():
            if n == 0:
                continue
            for k in range(bi, bi + n):
                ci = A[k][1]
                cov[ci] += 1
                bpos = bj + (k - bi)          # A 的第 k 个字符 ↔ B 的第 bj+(k-bi) 个字符
                if first_b[ci] is None:
                    first_b[ci] = bpos
                last_b[ci] = bpos

    # ---- B 位置 → 时间 ----
    # 行内比例 → 起点 + frac×(下一行起点 - 本行起点)
    line_end_time = []
    for li in range(len(b_start)):
        nxt = b_start[li + 1] if li + 1 < len(b_start) else (dur or (b_start[li] + 2))
        line_end_time.append(nxt)

    # 🔴🔴 **最后一行的结束时间不能用「轨长」**（2026-10-25 批次 1 实测踩到）
    #   问题：末行若按「延伸到整轨末尾」算 span ⇒ 行内比例被放大到几十秒
    #         ⇒ 落在末行后段的句子被安到**音频已经结束之后的静音区**（假时间）
    #   实测三例：kekkon「あーっ！！！」(186→186.4，音频实为 192 内但只有 0.4 秒窗)
    #             otoshimono 的回环句第二次出现（164.6/172.2，音频在 160 就念完了）
    #             ryosan-christmas 末句（147.8→159.4，实际音频 136~144）
    #   端到端验证直接把这三句判成「切出来念的不是这句」⇒ 端到端有鉴别力，抓到了真问题
    #   修法：用**实测语速**估末行长度 —— rate = 各行「字符数 / 行时长」的中位数
    #        末行结束 ≈ 末行起点 + 末行字符数 / rate（再与轨长取小）
    if len(b_start) >= 2:
        rates = []
        for li in range(len(b_start) - 1):
            span = b_start[li + 1] - b_start[li]
            if span > 0.2 and b_len[li] > 0:
                rates.append(b_len[li] / span)
        if rates:
            rates.sort()
            rate = rates[len(rates) // 2]          # 全书中位数（兜底）
            # 🔴 **优先用「末尾几行」的局部语速**：末行常是长句/回环句，
            #    全书中位数会被「短句 + 长停顿」拉低 ⇒ 末行结束被高估 ⇒
            #    末句时间被拖到音频已经结束之后（实测 otoshimono #41 偏 3 秒、ryosan #21 偏 3 秒）
            # ⚠️ 曾试过「末尾几行的局部语速」⇒ **实测更差**（ryosan 末句被拉到 159s）
            #    局部窗口若多为「短句 + 长停顿」，语速估得更慢 ⇒ 末行结束被高估 ⇒ 末句落进静音
            # ⇒ 保留全书中位数；再用**实测 speech_end**（能量分析）当硬上限兜底
            est = b_start[-1] + (b_len[-1] / max(0.5, rate))
            est = max(b_start[-1] + 1.0, est)
            se = SPEECH_END.get(_CTX.get("tag", ""))
            if se:
                est = min(est, se)          # 🔴 硬上限：不能超过「实测的最后说话时刻」
            line_end_time[-1] = min(dur, est) if dur else est

    def bpos2time(pos):
        if pos is None or not B:
            return None
        li = B[pos][1]
        if b_len[li] <= 0:
            return b_start[li]
        # 该行内第几个字符
        # 找该行首字符在 B 中的位置
        lo = 0
        # 线性扫（B 不大，缓存）
        return b_start[li], li

    # 预先算每个行首字符在 B 中的索引
    line_first_idx = {}
    for idx, (ch, li) in enumerate(B):
        if li not in line_first_idx:
            line_first_idx[li] = idx

    def pos2time(pos):
        if pos is None or not B:
            return None
        li = B[pos][1]
        first = line_first_idx[li]
        frac = (pos - first) / max(1, b_len[li])
        span = max(0.1, line_end_time[li] - b_start[li])
        return b_start[li] + frac * span

    # ---- 输出 ----
    out = []
    prev_end = 0.0
    for i, cue in enumerate(book_cues):
        rec = dict(cue)
        L = max(1, a_len[i])
        c = cov[i] / L
        rec["cov"] = round(c, 3)
        rec["_n"] = a_len[i]
        if c >= COV_THRESHOLD and first_b[i] is not None:
            s = pos2time(first_b[i])
            e = pos2time(last_b[i])
            if s is None:
                s = cue.get("s", 0)
            if e is None or e <= s:
                e = s + max(0.4, (e or s) - s + 0.4)
            rec["s"] = round(s, 2)
            rec["e"] = round(e, 2)
            rec["no_audio"] = 0
            prev_end = rec["e"]
        else:
            rec["no_audio"] = 1
        # 记下「这句在音频字符流里的位置」⇒ 供顺序检查用（第三关）
        # 🔴 只有**真的匹配上**（cov ≥ 阈值）的句子才有可用坐标；
        #    未匹配的句子若也带上坐标，顺序检查会拿它的「旧估值时间」当候选
        #    ⇒ 会把好句子误判成乱序 ⇒ 换到失败的通道 ⇒ 拿到过期的时间轴
        if not rec.get("no_audio"):
            rec["_b0"] = first_b[i]
            rec["_b1"] = last_b[i]
        else:
            rec["_b0"] = None
            rec["_b1"] = None
        out.append(rec)
    return out


def skeleton_norm(s):
    return skeleton(norm(s))


def apply_order_check(cues):
    """
    🔴 **第三关：顺序检查**（2026-10-25 补，跨项目通则）

    「文本能对上」≠「这句该标成有音句」——还得过**顺序**这一关。

    根因：单通道的全局匹配块天然单调（SequenceMatcher 的匹配块在 A、B 两侧都递增）
          ⇒ 单通道**不会**产出逆序
          ⇒ **真正的乱序来自双通道合并**：
             整句通道与汉字骨架通道各算一遍，两条位置序列可能互相穿越
             （整句通道把第 i 句放到 330s，骨架通道把第 i+1 句放到 289s）
          ⇒ 合并后时间轴非单调 ⇒ 前端 binarySearch 高亮**跳错行**
          ⇒ 端到端 ASR 验证会「命中」（文本确实在音频里）⇒ 判据没有鉴别力

    处置（宁可不高亮也不错位）：
      句子的**音频起点**早于上一句已接受的起点 ⇒ 书上顺序 ≠ 音频顺序
      ⇒ 标成哑句（no_audio）⇒ 前端不点亮、不跳转

    每句有两个候选（整句通道 / 汉字骨架通道）：
      · 原选通道若顺序合法 ⇒ 保留
      · 原选通道乱序但另一通道合法 ⇒ 改用另一通道（并记下换通道）
      · 两个都乱序 ⇒ 哑句

    🔴🔴 **判据必须用「时间」，不能用「字符坐标」**（2026-10-25 实测踩到）：
       整句通道的字符流含假名、汉字骨架通道不含 ⇒ **两套坐标根本不是同一个坐标系**
       ⇒ 拿骨架通道的坐标去和整句通道的坐标比大小 ⇒ 必然得出「乱序」的假结论
       ⇒ 会把好句子换成失败的通道 ⇒ 拿到书文件里的旧估值时间
       ⇒ 实测：地震 #34 的骨架坐标 330 被整句坐标 800 判成乱序
                ⇒ 换到 cov=0.467 的失败通道 ⇒ s 从正确的 278.0 变成错误的 277.0
       ⇒ **时间（秒）是两条通道共用的坐标系** ⇒ 只比时间
    🔴 **候选只能是「真的匹配上」的通道**（cov ≥ 阈值）
       ⇒ 未匹配的通道 s/e 是书文件里的旧值 ⇒ 拿它当候选 = 拿过期数据当真值
    """
    n_ord = 0            # 因乱序被降级成哑句的数量
    n_switch = 0         # 因原通道乱序、改用另一通道的数量
    last_s = None        # **最后一个被接受**的有音句的音频起点（秒）
    for rec in cues:
        if rec.get("no_audio"):
            continue
        # 候选 = [起始秒, 结束秒, 覆盖率, 通道名]；只收「真的匹配上」的
        cands = [c for c in (rec.get("_cands") or []) if c[0] is not None]
        if not cands:
            rec["no_audio"] = 1
            continue
        prefer = rec.get("_ch")          # 原选通道
        # 🔴🔴 参照点是**最后一个被接受的句**（不是紧邻的上一句）
        #   · 用「最后一个被接受的」⇒ **保证整条时间轴单调**（前端 binarySearch 的前提）
        #     实测：若改用「紧邻上一句」，拉面 #115 会因为 #114 被降级而逃过检查
        #           ⇒ 被留在 813 秒，而它前面的 #113 在 875 秒 ⇒ 时间轴倒序 ⇒ 闸门 exit 1
        #   · 降级时**不清空**参照点（清空 = 让后面的句逃逸）
        #   · 容差 ORDER_TOL=1.5s ⇒ 不会把「ASR 行粒度造成的零点几秒噪声」当成乱序
        #     （实测真乱序的幅度是**几十秒**级，如地震 #37 在 330 秒 / 书中更早的 #41 在 289 秒）
        ref = last_s
        ordered = [c for c in cands if ref is None or c[0] >= ref - ORDER_TOL]
        if not ordered:
            # 两个通道都排在最后被接受的句**之前**超过容差 ⇒ 真乱序 ⇒ 哑句
            rec["no_audio"] = 1
            rec["_ord"] = 1
            n_ord += 1
            continue
        pick = None
        for c in ordered:
            if c[3] == prefer:
                pick = c
                break
        if pick is None:
            pick = ordered[0]
            rec["_ch"] = pick[3]
            n_switch += 1
        rec["s"], rec["e"] = pick[0], pick[1]
        last_s = pick[0]
    ORDER_STATS.update({"out_of_order": n_ord, "channel_switch": n_switch})
    return cues


def align_dual(book_cues, asr_lines, dur):
    """
    **双通道比对**：整句比对 vs 只比汉字骨架，取覆盖更高者。

    为什么需要：别册非虚构类书的 OCR 把振假名并进了正文
    （「これは、むかし昔」里的「むかし」是「昔」的注音）
    ⇒ 整句比对会因为多出来的注音假名而覆盖率不足 ⇒ **整句明明在音频里，却被判成哑句**
    ⇒ 汉字骨架通道（去掉所有假名）不受注音污染影响

    🔴 不用「删掉被并进来的假名」的做法：
       真例（むかし+昔）和误伤（喜んで+出(で)，「で」是「喜」的送り仮名）
       在 ru 结构上**完全无法区分** ⇒ 改用户正文的风险不可接受
    ⇒ 改成「两个假设各算一遍覆盖率，让音频来裁决哪个对」

    汉字骨架太短（<3 字）时不采信 ⇒ 避免短句靠巧合得高分
    """
    full = align(book_cues, asr_lines, dur)
    skel = align(book_cues, asr_lines, dur, transform=skeleton_norm)
    out = []
    for f, s in zip(full, skel):
        pick, ch = f, "full"
        if s.get("_n", 0) >= 3 and s["cov"] > f["cov"]:
            pick, ch = s, "skel"
        rec = dict(pick)
        rec["_ch"] = ch
        # 两个通道各自的候选 ⇒ 顺序检查时可换通道
        # 候选 = [起始秒, 结束秒, 覆盖率, 通道名]；**只收真的匹配上的通道**
        # （未匹配的通道 s/e 是书文件里的旧估值 ⇒ 拿它当候选 = 拿过期数据当真值）
        rec["_cands"] = []
        if not f.get("no_audio"):
            rec["_cands"].append([f.get("s"), f.get("e"), f.get("cov", 0), "full"])
        if not s.get("no_audio"):
            rec["_cands"].append([s.get("s"), s.get("e"), s.get("cov", 0), "skel"])
        out.append(rec)
    if not NO_ORDER_CHECK:
        apply_order_check(out)
    return out


def main():
    """
    参数化（默认参数 = 旧 34 本的路径 ⇒ 旧流程可直接回归验证）
      --books-dir / --truth-dir / --out-dir / --report
      --tags   逗号分隔的 tag 列表；不给就按 books 目录里所有同名文件
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--books-dir", default=f"{ROOT}/books")
    ap.add_argument("--truth-dir", default=TRUTH_DIR)
    ap.add_argument("--out-dir", default=f"{ROOT}/aligned")
    ap.add_argument("--report", default=f"{ROOT}/align_report.json")
    ap.add_argument("--tags", default="")
    ap.add_argument("--speech-end", default="",
                    help="JSON：{tag: {speech_end, dur}} ⇒ 末行结束时间的**实测**硬上限")
    ap.add_argument("--dual", action="store_true",
                    help="双通道比对（整句 + 汉字骨架）⇒ 用于正文被并了振假名的书")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    if a.tags:
        tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    else:
        tags = sorted(os.path.splitext(f)[0] for f in os.listdir(a.books_dir) if f.endswith(".json"))
    if a.speech_end and os.path.exists(a.speech_end):
        for k, v in json.load(open(a.speech_end)).items():
            SPEECH_END[k] = v.get("speech_end") or 0
        print(f"载入实测 speech_end：{len(SPEECH_END)} 本")
    report = []
    for tag in tags:
        _CTX["tag"] = tag
        tp = f"{a.truth_dir}/{tag}.json"
        bp = f"{a.books_dir}/{tag}.json"
        if not os.path.exists(tp) or not os.path.exists(bp):
            print(f"  ⏭ {tag}（缺 ASR 真值或书文件）")
            continue
        truth = json.load(open(tp))
        book = json.load(open(bp))
        dur = book.get("dur") or 0
        ORDER_STATS.update({"out_of_order": 0, "channel_switch": 0})
        if a.dual:
            new = align_dual(book["cues"], truth, dur)
        else:
            new = align(book["cues"], truth, dur)
            # 单通道：全局匹配块天然单调 ⇒ 顺序检查应当是空操作；
            # 照样跑一遍，保证「三关」在任何模式下都成立（回归时应当 0 变化）
            for c in new:
                c["_ch"] = "full"
                if not c.get("no_audio"):
                    c["_cands"] = [[c.get("s"), c.get("e"), c.get("cov", 0), "full"]]
            if not NO_ORDER_CHECK:
                apply_order_check(new)
        stats = dict(ORDER_STATS)
        json.dump({"tag": tag, "dur": dur, "cues": new},
                  open(f"{a.out_dir}/{tag}.json", "w"), ensure_ascii=False)
        na = sum(1 for c in new if c.get("no_audio"))
        covs = [c.get("cov", 0) for c in new]
        nskel = sum(1 for c in new if c.get("_ch") == "skel")
        report.append({"tag": tag, "title": book.get("title"), "level": book.get("level"),
                       "cues": len(new), "no_audio": na, "n_skel": nskel,
                       "out_of_order": stats["out_of_order"],
                       "channel_switch": stats["channel_switch"],
                       "avg_cov": round(sum(covs) / max(1, len(covs)), 3),
                       "min_cov": round(min(covs), 3) if covs else 0,
                       "asr_lines": len(truth)})
        print(f"  {tag:44} {len(new):4}句  无对应 {na:3}  乱序降级 {stats['out_of_order']:3}"
              f"  换通道 {stats['channel_switch']:3}  平均覆盖 {report[-1]['avg_cov']}")
    json.dump(report, open(a.report, "w"), ensure_ascii=False)
    tot = sum(r["cues"] for r in report)
    na = sum(r["no_audio"] for r in report)
    oo = sum(r.get("out_of_order", 0) for r in report)
    print(f"共 {len(report)} 本 / {tot} 句 / 无对应 {na} 句 ({na/max(1,tot)*100:.1f}%)"
          f" / 乱序降级 {oo} 句")


if __name__ == "__main__":
    main()
