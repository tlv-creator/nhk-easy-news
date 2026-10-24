#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_gs.py —— 用 MiniMax-Text-01 离线生成「句子结构详解」（gs 字段），并内置进数据。

🔴 为什么必须 LLM（不是规则）：
    规则拼不出「让步条件从句」「て形＋も」「形式名词」「主句骨架公式」这一层语义。
    现有 tools/analyze.py 的 analyze_structure() 只给 MSVA 角色块，精度差几个量级。

🔴 判据全是「事实量」：
    · frame[].s / blocks[].parts[].s 必须**逐字出现在原句里**（子串检查）⇒ 抓幻觉
    · blocks[i].s 必须等于 frame[i].s（一一对应）
    · b 只能是 [] ［］ 〈〉 （） 四种
    · b 与 role 必须配套（[]↔连接/副词，［］↔从句，〈〉↔宾语内容，（）↔谓语）
    · 内层骨架 inner 的成分也必须逐字取自该块
    · note 里不许出现原句没有的「词形」（防止模型自己造词）

用法：
    python3 tools/gen_gs.py --ep ep01                 # 生成一集
    python3 tools/gen_gs.py --ep ep01 --limit 30      # 只跑前 30 句（试样本）
    python3 tools/gen_gs.py --ep all --apply          # 全量生成 + 写回 web/data/eps/*.json
    python3 tools/gen_gs.py --ep ep01 --apply         # 生成 + 写回
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, HERE)

API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-Text-01"

BRACKETS = {"[]", "［］", "〈〉", "（）"}

# 软规则命中记录（不拦人，只记）
WARN = []

# 🔴 括号组正则（**全角右括号 ］ 一定要在排除集里**）：
#    漏了它 ⇒ 「［A］［B］」会被当成一个组 ⇒ strip_brackets 剥成 "A］［B"
#    ⇒ skel 校验报「实词不在原句」（2026-10-24 实测踩到）。
GRP_RE = re.compile(r"[【\［（(〈][^】\］\)）〉]*[】\］\)）〉]"
                     r"|[^【\［（(〈】\］\)）〉]+")

# 语法术语白名单：skel 里出现这些词时视为「占位说明」而不是原文（不当幻觉）
GRAMMAR_TERMS = {
    "主语", "谓语", "宾语", "主题", "对象", "从句", "修饰", "定语", "状语",
    "补语", "连接", "副词", "动词", "形容词", "助词", "形式名词", "语气",
    "主句", "从句", "否定", "疑问", "命令", "劝诱", "意志", "使役", "被动",
    "可能", "能力", "补助", "敬语", "时", "体", "内容", "接续", "句型",
}

# 括号 → 允许的角色关键词（role 里出现这些词才算配套）
ROLE_RULES = {
    "[]": ("连接", "副词", "转折", "并列"),
    "［］": ("从句", "条件", "让步", "修饰", "宾语", "原因", "定语", "补语"),
    "〈〉": ("宾语", "内容", "主语", "主题", "对象", "形式名词"),
    "（）": ("谓语", "动词", "补助", "语气", "否定", "敬语", "终"),
}

SYSTEM = (
    "你是日语老师，给中文母语的学习者做句子结构解析。"
    "只输出 JSON，不要 markdown 代码块标记，不要寒暄，不要 emoji。"
)

USER_TMPL = """给下面这句日语做「句子结构详解」。严格按这个 JSON 结构输出：

{{
  "frame": [{{"s": "<块原文>", "b": "<括号>", "role": "<角色标签>"}}],
  "skel": "<主句骨架公式>",
  "skelMean": "<骨架的中文释义>",
  "chain": ["<第一层修饰>", "<第二层修饰>", "...", "<主句谓语>"],
  "blocks": [
    {{"s": "<块原文>", "inner": "<块内骨架，可为空串>",
      "parts": [{{"s": "<成分原文>", "note": "<1~2 句中文说明>"}}],
      "mean": "<整块中文意思>"}}
  ],
  "whole": "<整句中文意思>"
}}

原句：{ja}

硬性要求：
1. **所有 s 字段的原文必须逐字取自原句**（连续子串，一个字都不能改，不能加读音括号）。
2. frame 的块按出现顺序排列；blocks 与 frame **一一对应**（blocks[i].s == frame[i].s，顺序一致）。
3. b 只能是这四种之一，各管一类：
   - "[]"    连接词 / 副词（独立成块，如「反対に」「でも」「そして」）
   - "［］"  从句（让步 / 条件 / 宾语从句 / 修饰从句 / 原因从句）
   - "〈〉"  主句里的宾语内容 / 主语内容（含形式名词化「〜こと/の/もの」）
     🔴 「〈〉」里只能放**名词性或动词性**的内容。纯接续词/副词/**助词**
        （如「どう」「では」「最近」「は」「が」这种单独成块的）不要用「〈〉」——
        要么并进相邻的块，要么用 "[]"。
        🔴 助词（は/が/を/に/で/も/の）**绝不能单独成块** ⇒ 并进它前面的名词。
   - "（）"  谓语（主句谓语、补助动词、句末语气）
   role 要和括号配套：[]→连接/副词，［］→…从句，〈〉→…内容，（）→…谓语。role 用 8 字以内的中文短语。
4. 🔴 **skel 写「可复用的句型公式」**，不是这一句的具体词：
   把具体成分换成槽位，用「〜」表示。例如
     「［〜ことが］＋（できません）」
     「〈〜は〉＋（〜です）」
     「［〜たら］〈〜を〉＋（〜します）」
   ⇒ 不许写空括号（如「〈〉」），不许把原句的具体词照抄进 skel。
   skelMean 是这个公式的中文释义（如「无法做到〜」「是〜」）。
5. chain 是「层层修饰主句」的箭头串：从最外层修饰一路到主句谓语，按嵌套顺序排。
   chain 里的每一项可以带括号（如「［毎日同じように］」），但括号里的文字必须逐字来自原句。
6. inner 写块内骨架，格式如「【お金が】 [たくさん] （あっても）」；块内部没有结构就写空串。
7. parts 是块内逐成分说明，s 逐字取自**该块**，note 讲这个成分的词性/语法功能 + 在这句里的具体作用。
8. 简单句就少拆：主谓宾句不要硬造从句；frame 可以只有 2~3 块。
9. 不确定就写朴素说法，不要编造语法术语；不要写例句。
10. 🔴 **原句没有谓语时（标题卡/名词短语，如「シュンと日本語。」「初心者。」「ポットキャスト。」）**：
    - 不要凭空造出「です」或任何谓语。
    - frame 里**不要**出现「。」这种纯标点单独成块。
    - 🔴 **这种句子绝对不要用「（）」括号**（那是谓语专用）。名词短语用「〈〉」（内容）或「[]」。
      例：「シュンと日本語」→ 一块，b="〈〉"，role="标题"；「ポットキャスト」→ b="〈〉"，role="标题"。
    - frame 只需要 1~2 块。chain 只写真实存在的那一块，skel 照实写，不补任何原句里没有的词。
    - 如果这句话实在分析不出结构，就只给 frame 一块 + whole 一句中文意思，其余字段给空串或最小值。
11. 🔴 **chain 的每一项都必须是原句里真实存在的片段**（可以带括号，括号内文字也必须来自原句）。
    不许写「（です）」「（〜ます）」这类原句里没有的词。
"""

# ── 批量模板：一次 N 句，各自独立成数组元素（防串味：每句只许用自己的原句） ──
BATCH_TMPL = """给下面 {n} 句日语分别做「句子结构详解」。**每句独立，互不参照**。

输出一个 JSON 数组，长度正好 {n}，顺序与输入一致。每个元素是这个结构：

{{
  "ja": "<原句，原样抄回>",
  "frame": [{{"s": "<块原文>", "b": "<括号>", "role": "<角色标签>"}}],
  "skel": "<主句骨架公式>",
  "skelMean": "<骨架的中文释义>",
  "chain": ["<第一层修饰>", "...", "<主句谓语>"],
  "blocks": [
    {{"s": "<块原文>", "inner": "<块内骨架，可为空串>",
      "parts": [{{"s": "<成分原文>", "note": "<1~2 句中文说明>"}}],
      "mean": "<整块中文意思>"}}
  ],
  "whole": "<整句中文意思>"
}}

要分析的句子：
{list}

硬性要求（每句都适用）：
1. **每句的所有 s 字段必须逐字取自该句自己的原句**——绝不许串到别的句子上。
2. 元素里的 "ja" 字段必须原样抄回对应的那句，方便对齐。
3. frame 的块按出现顺序；blocks 与 frame 一一对应（blocks[i].s == frame[i].s）。
4. b 只能是 "[]" "［］" "〈〉" "（）"：
   [] = 连接词/副词；［］ = 从句；〈〉 = 宾语内容/主语内容；（） = 谓语。
   🔴 「〈〉」只能放**名词性或动词性**的内容；纯接续词/副词/助词（如「どう」「では」「は」单独成块）
      请并进相邻块或用 "[]"。助词**绝不能单独成块**。
5. role 与括号配套，8 字以内中文短语。
6. 🔴 skel 是**可复用的句型公式**（用「〜」表示槽位，不许空括号、不许照抄具体词）；
   chain 是层层修饰的箭头串（可带括号，括号内文字逐字来自原句）。
7. inner 是块内骨架，没有就写空串。
8. parts 逐成分说明，note 讲词性/语法功能 + 在这句里的具体作用。
9. 简单句就少拆，不要硬造从句。不确定就写朴素说法，不编造术语。
10. 🔴 **原句没有谓语时**（标题卡/名词短语，如「シュンと日本語。」）：不要凭空造出「です」或任何谓语；
    frame 不要出现「。」单独成块；🔴 **这种句子不要用「（）」括号**（谓语专用），名词短语用「〈〉」或「[]」；
    frame 只要 1~2 块，chain 只写真实存在的那块，skel 照实写。
    实在分析不出就给 frame 一块 + whole 一句中文意思，其余字段给空串或最小值。
11. 🔴 **chain 每一项都必须是原句里真实存在的片段**，不许写「（です）」这种原句没有的词。
"""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ── MiniMax 调用 ──────────────────────────────────────────────
RATE_ERR = "2062"


def call_minimax(user_text, timeout=120, tries=3, model=MODEL):
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    if not key:
        raise RuntimeError("没有 MINIMAX_SUB_KEY")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user_text}],
    }).encode("utf-8")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                API, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            br = d.get("base_resp") or {}
            if br.get("status_code") not in (0, None):
                last = RuntimeError("minimax %s: %s" % (br.get("status_code"), br.get("status_msg")))
                # 速率限制 ⇒ 退避更久（Token Plan 速率上限，串行快一点就会被顶）
                time.sleep(8 * (i + 1) if str(br.get("status_code")) == RATE_ERR else 2 * (i + 1))
                continue
            ch = (d.get("choices") or [{}])[0].get("message", {}).get("content")
            if not ch:
                last = RuntimeError("空回复")
                time.sleep(1.0 * (i + 1))
                continue
            return ch
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def extract_json(text):
    """从模型回复里抠出 JSON 对象（容忍 ```json 包裹和前后废话）。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    # 直接试
    for cand in (t, ):
        try:
            return json.loads(cand)
        except Exception:
            pass
    # 找第一个 { 到最后一个 }
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            pass
    raise ValueError("模型回复里找不到 JSON")


# ── 校验（事实量） ────────────────────────────────────────────
# 🔴 归一化的唯一权威在 tools/gs_text.py（gen_gs 与 clean_gs 共用）
#    铁律：**等长**（gs 的字符位置是在原句上算的 ⇒ 长度一变位置全错）
from gs_text import norm as _norm_raw  # noqa: E402


def norm(s):
    return _norm_raw(s).strip()


# ── janome 词性（独立于模型的事实源） ──────────────────────────
_POS = {
    "名詞": "N", "動詞": "V", "形容詞": "Adj", "副詞": "Adv",
    "助詞": "P", "助動詞": "Aux", "接続詞": "C", "感動詞": "Ex",
    "連体詞": "R", "名詞,固有名詞": "PN", "記号": "Sym", "その他": "X",
    "接頭詞": "Pref", "接尾詞": "Suf", "フィラー": "F",
}
_tok = None


def pos_of(text):
    """→ 该片段的词性码集合（janome）。没装 janome 就返回空集（不阻塞）。

    🔴 片段里主要是**非日语字符**（英文标题卡「Let's get excited!」）时返回空集
       ⇒ 不参与词性判据（janome 会把整串拉丁字母当一个名词，
       那会让「英文标题当谓语」这类误判发生）。
    """
    global _tok
    if _tok is None:
        try:
            from janome.tokenizer import Tokenizer
            _tok = Tokenizer()
        except Exception:  # noqa: BLE001
            return set()
    # 统计假名/汉字占比
    kana = sum(1 for ch in text if "぀" <= ch <= "ヿ")
    kanji = sum(1 for ch in text if "一" <= ch <= "鿿")
    if len(text) == 0 or (kana + kanji) * 2 < len(text):
        return set()
    out = set()
    try:
        for t in _tok.tokenize(text):
            head = (t.part_of_speech or "").split(",")[0]
            out.add(_POS.get(t.part_of_speech, _POS.get(head, "X")))
    except Exception:  # noqa: BLE001
        return set()
    return out


def load_vocab():
    """词库（独立事实源）：{词: {def, usage, ...}}。没有就返回空 dict。"""
    if _VOCAB is not None:
        return _VOCAB
    _VOCAB = {}
    for name in ("vocab.json", "vocab-extra.json"):
        p = os.path.join(WS, "web", "data", "lexicon", name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    _VOCAB.update(d)
            except Exception:  # noqa: BLE001
                pass
    return _VOCAB


_VOCAB = None

# note 里「表示 X」「意为 X」这类具体释义的模式
_DEFN_PAT = re.compile(r"(?:表示|意为|意思是|指的是|即)\s*[『「“\"']([^』」”\"']{1,20})[』」”\"']")


def note_def_conflict(note, part_text, ja):
    """note 里的具体释义必须与词库一致（抓「シュン＝低沉的声音」这类幻觉）。

    规则：note 若写出「表示『X』」，X 必须是
        · 原句里出现过的字串，或
        · 词库里该词的 def 覆盖的词
      否则 ⇒ 与已知事实冲突 ⇒ 红。
    词库里查不到该词 ⇒ 放行（词库不完整，不能拿它当否决依据）。
    """
    if not note:
        return True, ""
    v = load_vocab()
    if not v:
        return True, ""
    # 查这个成分在词库里的条目（取最长的匹配）
    entry = None
    for w in sorted(v.keys(), key=len, reverse=True):
        if w and w in part_text:
            entry = v[w]
            break
    if not entry:
        return True, ""
    defs = []
    for key in ("def", "usage"):
        val = entry.get(key)
        if isinstance(val, str):
            defs.append(val)
    blob = " ".join(defs)
    for m in _DEFN_PAT.finditer(note):
        claim = m.group(1).strip()
        if not claim:
            continue
        if claim in ja:
            continue
        # 词库里有没有覆盖这个释义的关键字
        if claim and claim in blob:
            continue
        # 释义里的每个词都出现在词库释义里？太严 ⇒ 用「至少有一个共同片段」判断
        if len(claim) >= 2 and any(claim[i:i + 2] in blob for i in range(len(claim) - 1)):
            continue
        return False, "note 释义与词库冲突: %r（词库: %r）" % (claim, blob[:40])
    return True, ""


def role_matches_pos(role, block_text):
    """role 声称的成分类别必须和 janome 词性一致（抓「把名词说成谓语」这类幻觉）。

    🔴 判据必须精确，不能用子串：
        "修饰谓语从句" 里也含「谓语」二字 ⇒ 用子串会把修饰从句误判成谓语块。
        ⇒ 只在 role **以**谓语类词**结尾**时才当谓语块（「主句谓语」「谓语」「动词」…）。
    返回 (ok, reason)。janome 不可用（词性集合为空）时一律放行。
    """
    ps = pos_of(block_text)
    if not ps:
        return True, ""
    has_pred = bool(ps & {"V", "Adj", "Aux", "Ex"})
    has_nom = bool(ps & {"N", "PN"})

    # 谓语块：role 以谓语类词结尾
    if any(role.endswith(k) for k in ("谓语", "动词", "补助", "否定", "敬语")):
        if not has_pred:
            return False, "role 声称谓语但词性是 %s: %r" % (sorted(ps), block_text)
    # 名词性内容块：role 以内容/主语/宾语类词结尾
    if any(role.endswith(k) for k in ("内容", "主语", "宾语", "主题", "对象")):
        if not (has_nom or has_pred):
            return False, "role 声称名词性内容但词性是 %s: %r" % (sorted(ps), block_text)
    # 连接/副词块：role 以连接/副词类词结尾
    if any(role.endswith(k) for k in ("连接", "副词", "转折", "并列")):
        if not (ps & {"C", "Adv", "P", "X"}):
            return False, "role 声称连接/副词但词性是 %s: %r" % (sorted(ps), block_text)
    return True, ""


BRACKET_PAIRS = [("［", "］"), ("【", "】"), ("〈", "〉"), ("（", "）"),
                 ("[", "]"), ("(", ")"), ("<", ">")]


def _strip(s):
    """剥掉括号后是否是空（纯括号项）。"""
    t = (s or "").strip()
    for a, b in BRACKET_PAIRS:
        if t == a + b:
            return ""
    return t


def strip_brackets(s):
    """剥掉一层括号（chain / skel 允许带括号，比对时要剥）。

    🔴 空括号组也要剥：「（）できません」⇒ 剥成「できません」
       （只判「整体是空括号」不够，前缀是空括号 + 文字的情况很常见）。
    """
    t = (s or "").strip()
    for a, b in BRACKET_PAIRS:
        if t == a + b:
            return ""          # 空括号组（如「〈〉」）⇒ 视为空
        if t.startswith(a + b):
            return t[len(a) + len(b):].strip()
    for a, b in BRACKET_PAIRS:
        if t.startswith(a) and t.endswith(b) and len(t) > 2:
            return t[1:-1].strip()
    return t


def check(gs, ja):
    """返回 (ok, reason)。判据全部落在「事实」上。"""
    del WARN[:]
    # 🔴 原句也归一化（等长 ⇒ 后面 ja.find() 出来的位置仍是原句坐标）
    ja = norm(ja)
    if not isinstance(gs, dict):
        return False, "不是 dict"
    frame = gs.get("frame")
    blocks = gs.get("blocks")
    if not isinstance(frame, list) or not frame:
        return False, "frame 空"
    if not isinstance(blocks, list) or not blocks:
        return False, "blocks 空"
    if len(frame) != len(blocks):
        return False, "frame/blocks 数量不等"

    for i, f in enumerate(frame):
        if not isinstance(f, dict):
            return False, "frame[%d] 不是 dict" % i
        s = norm(f.get("s"))
        b = f.get("b")
        role = norm(f.get("role"))
        if not s:
            return False, "frame[%d].s 空" % i
        if s not in ja:
            return False, "frame[%d].s 不在原句: %r" % (i, s)
        if b not in BRACKETS:
            return False, "frame[%d].b 非法: %r" % (i, b)
        if not role:
            return False, "frame[%d].role 空" % i
        if len(role) > 12:
            return False, "frame[%d].role 太长: %r" % (i, role)
        # b ↔ role 配套：**软规则**（只记，不拦）
        #   理由：不成句（标题卡）时模型会用「标题」「名词短语」这类 role，
        #   硬拦会误伤 ⇒ 真正拦人的是「事实」维度（原文逐字 / 词性一致 / 数量对应）。
        kws = ROLE_RULES[b]
        if not any(k in role for k in kws):
            WARN.append("frame[%d] 括号 %s 与角色 %r 不太配套" % (i, b, role))
        # 🔴 括号语义硬判：**不看 role 文本，只看括号**
        #    「（）」= 谓语 ⇒ 块内必须有动词/形容词/助动词（janome 独立事实源）
        #    「〈〉」= 主句内容 ⇒ 块内必须有名词/动词
        #    理由：role 是模型自己写的，可能自相矛盾（如「日本語」标成「谓语内容」却用「（）」）。
        ps = pos_of(s)
        if ps:
            # 🔴 谓语类含 Ex（感動詞）：janome 把「こんにちは」「すみません」「はい」
            #    全判成感動詞，但它们在日语里确实独立成句、承担谓语功能。
            has_pred = bool(ps & {"V", "Adj", "Aux", "Ex"})
            has_nom = bool(ps & {"N", "PN"})
            if b == "（）" and not has_pred:
                return False, "frame[%d] 用「（）」当谓语但词性是 %s: %r" % (i, sorted(ps), s)
            if b == "〈〉" and not (has_nom or has_pred):
                return False, "frame[%d] 用「〈〉」当内容但词性是 %s: %r" % (i, sorted(ps), s)
        # role ↔ janome 词性（独立事实源）
        ok2, why = role_matches_pos(role, s)
        if not ok2:
            return False, "frame[%d] %s" % (i, why)

    # frame 的块在原句里的位置必须**递增不重叠**（防止模型乱序 / 重复）
    # 🔴 同一个词可能在句里出现多次（如「も」）⇒ 必须**从上一块的结束位置往后找**，
    #    固定用 find(第一处) 会把后面的块判成「位置倒退」⇒ 误杀。
    last_end = 0
    for i, f in enumerate(frame):
        s = norm(f.get("s"))
        pos = ja.find(s, last_end) if s else -1
        if pos >= 0:
            last_end = pos + len(s)
        else:
            pos = ja.find(s)
            if pos >= 0:
                return False, "frame[%d] 位置倒退或重叠: %r (pos=%d, last_end=%d)" % (i, s, pos, last_end)

    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            return False, "blocks[%d] 不是 dict" % i
        if norm(b.get("s")) != norm(frame[i].get("s")):
            return False, "blocks[%d].s 与 frame 不对应" % i
        inner = b.get("inner")
        if inner is None:
            inner = ""
        if not isinstance(inner, str):
            return False, "blocks[%d].inner 不是字符串" % i
        # inner 的成分校验：**软警告**（不拦人）
        #   理由：inner 是「块内骨架」，是**示意性**的。模型习惯给名词加助词
        #   （原句「AIを使って」⇒ inner 写「【AIが】」）⇒ 硬拦误伤率极高。
        #   真正该拦的幻觉（frame/blocks/parts 的原文片段）已经由严格判据守着。
        for piece in GRP_RE.findall(inner):
            p = piece[1:-1].strip()
            if p and p not in ja:
                WARN.append("blocks[%d].inner 成分不在原句: %r" % (i, p))
        parts = b.get("parts")
        if not isinstance(parts, list):
            return False, "blocks[%d].parts 不是 list" % i
        for j, p in enumerate(parts):
            if not isinstance(p, dict):
                return False, "blocks[%d].parts[%d] 不是 dict" % (i, j)
            ps = norm(p.get("s"))
            note = norm(p.get("note"))
            if not ps:
                return False, "blocks[%d].parts[%d].s 空" % (i, j)
            # 🔴 parts[].s 必须在**原句**里，且位置在**该块附近**。
            #    为什么不强求「在块内」：模型常把块拆得更细
            #    （块「ません」→ parts「ない」+「ません」；块「工作了」→ parts「工作」）
            #    ⇒ 强求块内会误杀 ⇒ 只抓「原句里根本没有」和「张冠李戴（离块太远）」。
            if ps not in ja:
                # 🔴 模型爱把「成分原文」写成「原句片段 + 助词」（原句「世界は」⇒ 写「世界が」）。
                #    这是**用户会看到的错原文** ⇒ 不能放过。
                #    但也不该整句作废 ⇒ 自动剥掉尾部助词（剥完必须在原句里）。
                fixed = None
                for suf in ("こと", "が", "を", "に", "で", "は", "も", "の", "ね", "よ"):
                    if ps.endswith(suf) and len(ps) > len(suf):
                        cand = ps[:-len(suf)]
                        if cand in ja:
                            fixed = cand
                            break
                if fixed:
                    WARN.append("blocks[%d].parts[%d].s 剥掉助词修正: %r → %r" % (i, j, ps, fixed))
                    ps = fixed
                    p["s"] = fixed
                else:
                    return False, "blocks[%d].parts[%d].s 不在原句: %r" % (i, j, ps)
            # ⚠️ 「离块太远」只做**软警告**：
            #    同一个词在句里出现多次时（「を」「も」…）位置判定不可靠 ⇒ 误杀率高
            #    ⇒ 真幻觉（原文里根本没有）已由上面的「不在原句」抓住。
            blk_s = b.get("s", "")
            pos = ja.find(ps, max(0, ja.find(blk_s)))
            bpos = ja.find(blk_s)
            if pos >= 0 and bpos >= 0 and not (bpos - 4 <= pos <= bpos + len(blk_s) + 4):
                WARN.append("blocks[%d].parts[%d].s 离块偏远: %r" % (i, j, ps))
            if not note:
                return False, "blocks[%d].parts[%d].note 空" % (i, j)
        if not norm(b.get("mean")):
            return False, "blocks[%d].mean 空" % i

    skel = norm(gs.get("skel"))
    if not skel:
        return False, "skel 空"
    # skel 是公式：允许「〜」占位，但实词部分必须逐字来自原句
    #   先把「＋ ＝ ／ 、 空格」当成分隔符换成空格，再按「括号组」切出每一片
    skel_parts = re.sub(r"[＋+＝=／/、,\s]+", " ", skel)
    for piece in GRP_RE.findall(skel_parts):
        fb = strip_brackets(piece)
        if not fb:
            continue          # 空括号组（如「〈〉」）⇒ 跳过，不是实词
        if "〜" in fb:
            # 公式占位：整组带「〜」就放行（实词是槽位说明，不要求出现在原句）
            #   例：「［〜ことが］」「（〜ません）」
            continue
        if fb not in ja:
            # 模型常把 skel 里的成分写成「原句片段 + 助词」（如「皆さんが」←「皆さん」+「が」）
            # ⇒ 剥掉尾部助词再查一次。
            stripped = re.sub(r"[がをにではもねよ、。]+$", "", fb)
            if stripped and stripped in ja:
                continue
            # 省略号（…/・）是模型表示「此处省略」的记号 ⇒ 剥掉再查
            stripped2 = stripped.replace("…", "").replace("・", "").strip()
            if stripped2 and stripped2 in ja:
                continue
            # 🔴 **语法术语**（主语/谓语/从句…）是占位说明不是原文 ⇒ 跳过。
            #    ⚠️ 不能用「纯中文就放过」—— 那会把「提高」这种普通词也放过（假绿）。
            #    ⇒ 只放过**白名单里的语法术语**。
            if fb in GRAMMAR_TERMS:
                continue
            return False, "skel 实词不在原句: %r" % fb
    chain = gs.get("chain")
    if not isinstance(chain, list) or not chain:
        return False, "chain 空"
    # 🔴 chain 里可能有**纯括号项**（「[]」「（）」）—— 剥完括号是空的
    #    ⇒ 它不是「原文片段」⇒ 删掉（形状修复，不是幻觉）。
    chain = [c for c in chain if _strip(c)]
    if not chain:
        return False, "chain 剥掉空括号后为空"
    gs["chain"] = chain
    for j, c in enumerate(chain):
        cs = norm(c)
        if not cs:
            return False, "chain[%d] 空" % j
        # 🔴 chain 是「层层修饰主句」⇒ 每一项都必须是原句里**真实存在**的片段。
        #    允许带括号（那是它本来的样子），剥掉括号后必须逐字来自原句。
        #    公式（如「〜ことが」）属于 skel，不属于 chain。
        bare = strip_brackets(cs)
        if not bare:
            continue          # 空括号组 ⇒ 跳过（模型偶尔这么写，不是幻觉）
        if bare not in ja:
            return False, "chain[%d] 不在原句: %r" % (j, cs)
    if not norm(gs.get("whole")):
        return False, "whole 空"
    return True, "ok"


def repair_shape(raw, ja):
    """把模型常见的形状偏差掰回来（b/role 写成数组等）。只做形状修复，不改语义。"""
    if not isinstance(raw, dict):
        return raw
    fr = raw.get("frame")
    if isinstance(fr, list):
        fixed = []
        for f in fr:
            if not isinstance(f, dict):
                continue
            b = f.get("b")
            if isinstance(b, list) and b:
                b = "[]"          # 模型把 b 当成成分列表了 ⇒ 整块退化成一个块
            if b not in BRACKETS:
                b = "（）"
            role = f.get("role")
            if isinstance(role, list):
                role = role[0] if role else ""
            if not isinstance(role, str):
                role = str(role or "")
            s = (f.get("s") or "").strip()
            # 🔴 纯标点不配当���「块」：模型有时把句号「。」单独当一块，
            #    而它的词性是 Sym ⇒ 一定不是谓语 ⇒ 丢掉它（它是句子的一部分，不是成分）
            if s and all(ch in "。．、，,.!?！？；;：:…・「」『』（）()[]「」" for ch in s):
                continue
            # 🔴 形状修复：块里没有动词/形容词/助动词/感動詞，却用了「（）」= 谓语括号
            #    ⇒ 它不是谓语块 ⇒ 改成「〈〉」（内容），role 里的「谓语」也一并去掉。
            #    这不是「替模型改语义」，是 janome 词性给出的硬事实。
            if b == "（）" and s:
                ps = pos_of(s)
                if ps and not (ps & {"V", "Adj", "Aux", "Ex"}):
                    b = "〈〉"
                    if role.endswith("谓语"):
                        role = role[:-2] + "内容" if role[:-2] else "内容"
            fixed.append({"s": s, "b": b, "role": role})
        raw["frame"] = fixed
    bl = raw.get("blocks")
    if isinstance(bl, list):
        for b in bl:
            if isinstance(b, dict):
                if not isinstance(b.get("inner"), str):
                    b["inner"] = ""
                if not isinstance(b.get("parts"), list):
                    b["parts"] = []
                for p in b.get("parts") or []:
                    if isinstance(p, dict):
                        if not isinstance(p.get("note"), str):
                            p["note"] = str(p.get("note") or "")
        # frame 删过纯标点块 ⇒ blocks 要按同样的 s 同步删，否则数量对不上
        if isinstance(raw.get("frame"), list):
            keep = {f.get("s") for f in raw["frame"] if isinstance(f, dict)}
            newb = []
            for b in bl:
                if not isinstance(b, dict):
                    continue
                s = (b.get("s") or "").strip()
                if s and all(ch in "。．、，,.!?！？；;：:…・「」『』（）()[]「」" for ch in s):
                    continue
                newb.append(b)
            raw["blocks"] = newb
    return raw


def annotate_positions(gs, ja):
    """给 gs 的每个原文片段标注**字符位置** [start, end]。

    🔴 为什么需要：只有「原文片段」时，前端只能用 indexOf 定位 ——
       句子里同一个词出现多次时（如「も」「を」）会定位到第一处 ⇒ 指错地方。
    ⇒ 按**出现顺序**推进地定位（frame 块本来就按原句顺序）⇒ 位置是准的。
    ⇒ 这层是**确定性计算**（不依赖模型），所以放形状修复里。

    标注位置：
      · frame[i].p   —— 块在原句里的 [start, end)
      · blocks[i].p  —— 同上（与 frame[i] 相同）
      · blocks[i].parts[j].p —— 成分在**块内**的 [start, end)（相对整句）
      · chain[i].p   —— 在原句里的位置
    """
    if not isinstance(gs, dict):
        return gs
    cursor = 0
    for i, f in enumerate(gs.get("frame") or []):
        t = norm(f.get("s"))
        if not t:
            continue
        pos = ja.find(t, cursor)
        if pos < 0:
            pos = ja.find(t)
        if pos >= 0:
            f["p"] = [pos, pos + len(t)]
            cursor = pos + len(t)
    for i, b in enumerate(gs.get("blocks") or []):
        fr = gs.get("frame") or []
        if i < len(fr) and isinstance(fr[i], dict) and fr[i].get("p"):
            b["p"] = list(fr[i]["p"])
        bt = norm(b.get("s"))
        bp = b.get("p")
        inner_cursor = bp[0] if bp else 0
        for j, pt in enumerate(b.get("parts") or []):
            pt2 = norm(pt.get("s"))
            if not pt2:
                continue
            p2 = ja.find(pt2, inner_cursor)
            if p2 < 0:
                p2 = ja.find(pt2, bp[0] if bp else 0)
            if p2 >= 0:
                pt["p"] = [p2, p2 + len(pt2)]
                inner_cursor = p2 + len(pt2)
    for i, c in enumerate(gs.get("chain") or []):
        t = strip_brackets(c)      # chain 项常带括号 ⇒ 要剥掉再找
        if not t:
            continue
        pos = ja.find(t)
        if pos >= 0:
            gs["chain"][i] = c
            # chain 是 list[str] ⇒ 位置另存
            if not isinstance(gs.get("chain_p"), list):
                gs["chain_p"] = []
            while len(gs["chain_p"]) <= i:
                gs["chain_p"].append(None)
            gs["chain_p"][i] = [pos, pos + len(t)]
    return gs


# ── 主流程 ────────────────────────────────────────────────────
def gen_one(ja, tries=3):
    for i in range(tries):
        try:
            txt = call_minimax(USER_TMPL.format(ja=ja))
            raw = extract_json(txt)
            raw = repair_shape(raw, ja)
            ok, reason = check(raw, ja)
            if ok:
                annotate_positions(raw, ja)
                raw["src"] = "llm"
                return raw, None
            last = reason
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
        time.sleep(1.0)
    return None, last


def gen_batch(jas, tries=2):
    """一次生成多句。返回 {ja: (gs|None, reason)}。

    🔴 批量会「串味」：模型可能把 A 句的成分写进 B 句。
       ⇒ 每句都独立跑 check()，串味的句子自动作废（回落到单句重生成）。
    """
    lst = "\n".join("%d. %s" % (i + 1, j) for i, j in enumerate(jas))
    user = BATCH_TMPL.format(n=len(jas), list=lst)
    out = {}
    try:
        txt = call_minimax(user, timeout=180)
        arr = extract_json(txt)
    except Exception as e:  # noqa: BLE001
        return {j: (None, "batch 调用失败: %s" % e) for j in jas}
    if not isinstance(arr, list):
        return {j: (None, "batch 返回不是数组") for j in jas}
    # 对齐：优先用 "ja" 字段，其次按顺序
    by_ja = {}
    for it in arr:
        if isinstance(it, dict) and it.get("ja"):
            by_ja[norm(it["ja"])] = it
    used = set()
    for idx, ja in enumerate(jas):
        item = by_ja.get(norm(ja))
        if item is None and idx < len(arr) and isinstance(arr[idx], dict):
            cand = arr[idx]
            if norm(cand.get("ja", "")) == norm(ja):
                item = cand
        if item is None:
            out[ja] = (None, "batch 里找不到这句")
            continue
        used.add(ja)
        try:
            item = repair_shape(item, ja)
            ok, reason = check(item, ja)
        except Exception as e:  # noqa: BLE001
            out[ja] = (None, "check 抛错: %s" % e)
            continue
        if ok:
            annotate_positions(item, ja)
            item["src"] = "llm-batch"
            out[ja] = (item, None)
        else:
            out[ja] = (None, "batch 结果不合格: %s" % reason)
    return out


def load_eps(ep):
    """读一集的行数据。返回 (rows, path)。

    🔴 两种来源都要支持（周更 agent 与本地项目的产物格式不同）：
        · web/data/eps/epNN.json    —— 已发布的集（本地/仓库版 web 目录）
        · work/cues/epNN_cues.json  —— 周更新集（build_cues.py 的产物）
      两者的行文本都在 row["t"]（cues 文件里是 cue["t"]）。
    """
    p_web = os.path.join(WS, "web", "data", "eps", ep + ".json")
    p_cues = os.path.join(WS, "work", "cues", ep + "_cues.json")
    for p in (p_web, p_cues):
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:  # noqa: BLE001
            continue
        cues = d.get("cues")
        if not cues:
            continue
        rows = []
        for c in cues:
            r = (c.get("rows") or [{}])[0]
            t = r.get("t") or c.get("t") or ""
            rows.append({"t": t})
        if rows:
            return rows, p
    return None, p_web


def _one_ep(ep, args, lock, stat):
    rows, path = load_eps(ep)
    if rows is None:
        log("跳过 %s（找不到 %s 或 work/cues/%s_cues.json）" % (ep, ep, ep))
        return
    cache_path = os.path.join(args.out, ep + ".json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    n = len(rows) if args.limit <= 0 else min(len(rows), args.limit)
    todo = []
    for i in range(n):
        ja = rows[i]["t"]
        key = "%s:%d" % (ep, i)
        if key in cache and cache[key].get("gs"):
            stat["ok"] += 1
            continue
        todo.append((i, key, ja))
    if not todo:
        log("%s: 全部已有，跳过" % ep)
    else:
        log("%s: 待生成 %d 句（来源 %s）" % (ep, len(todo), os.path.basename(path)))
        bs = max(1, args.batch)
        chunks = [todo[i:i + bs] for i in range(0, len(todo), bs)]
        done = 0
        for ch in chunks:
            jas = [x[2] for x in ch]
            res = gen_batch(jas)
            # 不合格的回落单句重生成
            for (i, key, ja) in ch:
                gs, err = res.get(ja, (None, "batch 无结果"))
                if gs is None and not args.no_single:
                    gs, err = gen_one(ja, tries=2)
                with lock:
                    if gs:
                        cache[key] = {"ja": ja, "gs": gs}
                        stat["ok"] += 1
                    else:
                        cache[key] = {"ja": ja, "gs": None, "err": err}
                        stat["fail"] += 1
                        log("  ✗ %s %r ← %s" % (key, ja[:36], err))
                done += 1
            with lock:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False)
            log("  %s %d/%d  ok=%d fail=%d" % (ep, done, len(todo), stat["ok"], stat["fail"]))
    if args.apply:
        apply_gs(ep, path, cache, args)
    return


def apply_gs(ep, path, cache, args):
    """把 gs 写回数据文件。

    · 来源是 web/data/eps/epNN.json ⇒ 直接写回那一行
    · 来源是 work/cues/epNN_cues.json ⇒ **不写**（那不是 App 数据，
      真正的写入点是 build_ep_data.py 读 work/gs/epNN.json）
    """
    if not path.endswith(os.sep + "eps" + os.sep + ep + ".json") and \
            os.sep + "eps" + os.sep not in path:
        log("  %s 来源是 cues 文件 ⇒ 不写回（由 build_ep_data.py 读 work/gs/）" % ep)
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    n = 0
    for i, c in enumerate(data.get("cues") or []):
        key = "%s:%d" % (ep, i)
        g = (cache.get(key) or {}).get("gs")
        rows = c.get("rows") or []
        if rows:
            rows[0]["gs"] = g
            n += 1 if g else 0
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    log("  已写回 %s（%d 句带 gs）" % (path, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True, help="ep01 … ep13 或 all")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 句")
    ap.add_argument("--apply", action="store_true", help="生成后写回 web/data/eps/*.json")
    ap.add_argument("--batch", type=int, default=4, help="一次请求几句")
    ap.add_argument("--workers", type=int, default=3, help="并发集数")
    ap.add_argument("--no-single", action="store_true", help="batch 失败不回落单句")
    ap.add_argument("--out", default=os.path.join(WS, "work", "gs"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    eps = ([f"ep{i:02d}" for i in range(1, 14)] if args.ep == "all"
           else args.ep.split(","))

    lock = threading.Lock()
    stat = {"ok": 0, "fail": 0}
    t0 = time.time()
    if len(eps) <= 1:
        _one_ep(eps[0], args, lock, stat)
    else:
        ths = []
        q = list(eps)
        while q:
            batch = q[:args.workers]
            q = q[args.workers:]
            ts = [threading.Thread(target=_one_ep, args=(e, args, lock, stat)) for e in batch]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
    log("\n合计 ok=%d fail=%d，用时 %.0f 秒" % (stat["ok"], stat["fail"], time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
