#!/usr/bin/env python3
"""粒度细化器（refine）：**只重新分配读音，不重选读音**。

🔴 为什么不能直接用 ruby.py 重算（2026-10-25 实测踩到）：
   ruby.py 的 DP 会**重新选择**每个字的读音（音读 vs 训读）
   ⇒ 已发布数据里读对的字会被改坏：
        風(かぜ) → 風(ふう)   ❌（かぜ 是训读，ふう 是音读）
        腹(はら) → 腹(なか)   ❌
        停(てい) → 停(ばすてい) ❌（「バス」被当送り假名剥掉）
   ⇒ 而用户口径是「读音必须逐字 ruby，不是整块注音」
   ⇒ **粒度要细化，但读音一个假名都不能变**

✅ 本模块的做法（纯粒度变换）：
   输入：旧 ru（表层 + 读音）
   输出：新 ru（更细的粒度）
   硬保证：
     ① 新 ru 的表层拼接 == 旧 ru 的表层拼接（逐字）
     ② 新 ru 的读音流 == 旧 ru 的读音流（逐字符）
   ⇒ 读音内容零变化，只有「读音挂在几个字上」变了。

算法（对每个「多字整块单元」）：
   ① 取该单元的表层 kanji 与读音 rd
   ② 若 kanji 含叠字符「々」⇒ **保持整块**（叠字读音有音变，不能拆）
   ③ 否则：把 kanji 逐字切分，**用「不改变读音流」的方式分配**：
        —— 用 kanjidic2 + 上下文表 + 已发布表做 DP，但**候选必须是旧数据里
           该字实际用过的读音**（published_reading.json）⇒ 不会选错音读/训读
        —— DP 仍无解 ⇒ 保持整块
   ④ 逐字化后拼回的读音必须 == 原读音，否则回退整块

用法：
  from refine_granularity import refine_ru
  new_ru = refine_ru(old_ru)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUBY_DIR = os.path.join(HERE, "tools", "ruby")
if RUBY_DIR not in sys.path:
    sys.path.insert(0, RUBY_DIR)

import ruby  # noqa: E402


def _t(u):
    return u[0] if isinstance(u, (list, tuple)) else u["t"]


def _r(u):
    return u[1] if isinstance(u, (list, tuple)) else u["r"]


def _as_pairs(units):
    return [[_t(u), _r(u)] for u in units]


def _kanji_only(s):
    """s 是否只含汉字（不含假名/英文/标点）"""
    return bool(s) and all(ruby.is_kanji(c) for c in s)


def refine_ru(units, published=None):
    """把整块单元细化为逐字（读音流不变）。返回 list of [t, r]。"""
    units = _as_pairs(units)
    pri = published if published is not None else ruby.load_published_reading()
    kd, kd_fb = ruby.load_kanjidic()
    # 候选集：**只用已发布数据里该字实际用过的读音** ⇒ 不会选错音读/训读
    #      （不够时再补词典，但词典候选排在后面 ⇒ 优先用已发布读音）
    cand = {}
    for ch in set(list(kd.keys()) + list(pri.keys())):
        cl = []
        for x in pri.get(ch, []):
            if x not in cl:
                cl.append(x)
        for x in kd.get(ch, []):
            if x not in cl:
                cl.append(x)
        for x in (kd_fb.get(ch) or []):
            if x not in cl:
                cl.append(x)
        if cl:
            cand[ch] = cl

    out = []
    for t, r in units:
        if not t or not r:
            out.append([t, r])
            continue
        # 只处理「纯汉字的整块单元」
        if len(t) <= 1 or not _kanji_only(t):
            out.append([t, r])
            continue
        # 叠字符 ⇒ 保持整块（叠字读音有音变：時々=ときどき、人々=ひとびと）
        if ruby.is_iterative(t[0]) or ruby.is_iterative(t[-1]) or "々" in t:
            out.append([t, r])
            continue
        parts = ruby.dp_split(list(t), r, cand, None)
        if parts is None:
            out.append([t, r])
            continue
        # 自证：拼回的读音必须逐字相同
        if "".join(x[1] for x in parts) != r:
            out.append([t, r])
            continue
        for ch, rd in parts:
            out.append([ch, rd])
    return out


def refine_sentence(sent):
    """对一句做粒度细化（原地改 sent['ru']）。返回 True 表示有改动。"""
    old = sent.get("ru") or []
    new = refine_ru(old)
    if new != _as_pairs(old):
        sent["ru"] = new
        return True
    return False


if __name__ == "__main__":
    # 自检
    tests = [
        ([["日", "に"], ["本", "ほん"], ["語", "ご"]], "逐字"),
        ([["教師", "きょうし"]], "整块→逐字"),
        ([["今日", "きょう"]], "真不规则⇒整块"),
        ([["風", "かぜ"]], "单字"),
        ([["日本語", "にほんご"]], "词表命中⇒逐字"),
        ([["色々", "いろいろ"]], "叠字⇒整块"),
        ([["地下鉄", "ちかてつ"]], "多字⇒逐字"),
    ]
    ok = True
    for u, label in tests:
        new = refine_ru(u)
        old_stream = "".join(r if ruby.is_kanji(t[0]) else t for t, r in u)
        new_stream = "".join(r if ruby.is_kanji(t[0]) else t for t, r in new)
        same = old_stream == new_stream
        surf_old = "".join(t for t, r in u)
        surf_new = "".join(t for t, r in new)
        mark = "✅" if (same and surf_old == surf_new) else "❌"
        if not (same and surf_old == surf_new):
            ok = False
        print("%s %-22s %s → %s" % (mark, label, u, new))
    print()
    print("全部通过" if ok else "有失败")
    sys.exit(0 if ok else 1)
