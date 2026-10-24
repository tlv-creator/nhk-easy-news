#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gs_gate.py —— 「句子结构详解」生成器的闸门自检（正反两向）。

🔴 纪律：闸门必须**正反两向自检**：
    · 正向：一份真实合格的数据必须**绿**
    · 反向：每个已知坏形态必须**红**，且红的原因是**那一维**
🔴 反向验证的前提是「反例真的造出来了」⇒ 每个反例都断言「它确实触发了预期判据」。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(WS, "tools"))

import gen_gs  # noqa: E402

PASS = []
FAIL = []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("  ← " + detail) if (detail and not cond) else ""))


JA = "でも毎日ExcitingExciting".replace("ExcitingExciting", "同じようにワクワクするのは難しいですよね")
JA = "でも毎日同じようにワクワクするのは難しいですよね"

FRAME = [
    {"s": "でも", "b": "[]", "role": "转折连接"},
    {"s": "毎日同じように", "b": "［］", "role": "修饰谓语从句"},
    {"s": "ワクワクするのは", "b": "〈〉", "role": "主句内容"},
    {"s": "難しいですよね", "b": "（）", "role": "主句谓语"},
]
BLOCKS = [
    {"s": "でも", "inner": "", "parts": [{"s": "でも", "note": "转折连接词"}], "mean": "但是"},
    {"s": "毎日同じように", "inner": "", "parts": [{"s": "毎日", "note": "时间名词"}], "mean": "每天都一样地"},
    {"s": "ワクワクするのは", "inner": "【ワクワクする】",
     "parts": [{"s": "ワクワクする", "note": "动词"}], "mean": "兴奋的事"},
    {"s": "難しいですよね", "inner": "【難しい】",
     "parts": [{"s": "難しい", "note": "形容词"}], "mean": "很难"},
]


def mk(frame=None, blocks=None, chain=None, skel="［毎日同じように］（難しい）",
       skel_mean="每天都一样地…是难的", whole="但是每天都一样地兴奋是很难的"):
    return {
        "frame": frame if frame is not None else FRAME,
        "blocks": blocks if blocks is not None else BLOCKS,
        "chain": chain if chain is not None else ["［毎日同じように］", "〈ワクワクするのは〉"],
        "skel": skel, "skelMean": skel_mean, "whole": whole,
    }


def red(gs, ja=JA):
    ok_, reason = gen_gs.check(gs, ja)
    return (not ok_), reason


print("=== 正向：合格数据必须绿 ===")
good, reason = gen_gs.check(mk(), JA)
ok("正例整体通过", good, reason)

good2, reason2 = gen_gs.check(mk(skel="［〜ことが］＋（難しい）"), JA)
ok("skel 允许「〜」占位公式", good2, reason2)

good3, reason3 = gen_gs.check(mk(chain=["毎日同じように", "ワクワクするのは", "難しいですよね"]), JA)
ok("chain 允许不带括号的字面片段", good3, reason3)

print("\n=== 反向：每个坏形态必须红，且红在那��维 ===")

r, why = red(mk(frame=[{"s": "日本語", "b": "（）", "role": "谓语"}],
                 blocks=[{"s": "日本語", "inner": "", "parts": [{"s": "日本語", "note": "x"}], "mean": "y"}],
                 chain=["日本語"], skel="（日本語）"), "シュンと日本語。")
ok("名词被当成谓语 ⇒ 红", r, why)
ok("  且原因是词性维度", "词性" in why, why)

r, why = red(mk(chain=["提高"]))
ok("chain 出现原句没有的片段 ⇒ 红", r, why)
ok("  且原因是 chain 不在原句", "chain" in why, why)

r, why = red(mk(skel="［提高］（難しい）"))
ok("skel 出现原句没有的实词 ⇒ 红", r, why)
ok("  且原因是 skel 实词不在原句", "skel" in why, why)

bad_parts = json_copy = [dict(b) for b in BLOCKS]
bad_parts[0] = dict(bad_parts[0], parts=[{"s": "別的东西", "note": "x"}])
r, why = red(mk(blocks=bad_parts))
ok("parts 出现原句没有的片段 ⇒ 红", r, why)
ok("  且原因是 parts 不在原句", "不在原句" in why, why)

# parts 的成分可以比块更细（块「ません」→ parts「ない」+「ません」）⇒ 不在块内也要放行
NEG_JA = "私も毎日はワクワクできません"
neg = {
    "frame": [{"s": "私も毎日はワクワクできません", "b": "（）", "role": "主句谓语"}],
    "blocks": [{"s": "私も毎日はワクワクできません", "inner": "",
                "parts": [{"s": "でき", "note": "可能形"},
                          {"s": "ません", "note": "礼貌体否定"}],
                "mean": "不能"}],
    "chain": ["私も毎日はワクワクできません"], "skel": "（〜できません）",
    "skelMean": "无法做到", "whole": "我也没法每天都兴奋起来",
}
ok("parts 拆得比块细（不在块内）⇒ 放行", gen_gs.check(neg, NEG_JA)[0],
   gen_gs.check(neg, NEG_JA)[1])

# parts 的成分离块太远 ⇒ 软警告（不拦人：重复词会让位置判定不可靠）
far = [dict(b) for b in BLOCKS]
far[0] = dict(far[0], parts=[{"s": "は", "note": "x"}])   # 「は」在原句里，但离第一块很远
ok("parts 离块太远 ⇒ 软警告（不拦人）", gen_gs.check(mk(blocks=far), JA)[0],
   gen_gs.check(mk(blocks=far), JA)[1])

# frame 块位置倒退 ⇒ 红
r, why = red(mk(frame=[{"s": "難しいですよね", "b": "（）", "role": "主句谓语"},
                        {"s": "でも", "b": "[]", "role": "转折连接"}],
                 blocks=[{"s": "難しいですよね", "inner": "", "parts": [{"s": "難しい", "note": "n"}], "mean": "m"},
                         {"s": "でも", "inner": "", "parts": [{"s": "でも", "note": "n"}], "mean": "m"}],
                 chain=["でも"]))
ok("frame 块位置倒退 ⇒ 红", r, why)
ok("  且原因是位置倒退", "位置倒退" in why, why)

r, why = red(mk(frame=FRAME[:2], blocks=BLOCKS))
ok("frame/blocks 数量不等 ⇒ 红", r, why)
ok("  且原因是数量不等", "数量不等" in why, why)

r, why = red(mk(frame=[{"s": "でも", "b": "【】", "role": "转折连接"}],
                 blocks=[{"s": "でも", "inner": "", "parts": [{"s": "でも", "note": "x"}], "mean": "m"}],
                 chain=["でも"]))
ok("非法括号 ⇒ 红", r, why)
ok("  且原因是括号非法", "b 非法" in why, why)

mismatch = mk(frame=[{"s": "毎日同じように", "b": "［］", "role": "标题"}],
               blocks=[{"s": "毎日同じように", "inner": "", "parts": [{"s": "毎日", "note": "x"}], "mean": "m"}],
               chain=["毎日同じように"])
good_m, why_m = gen_gs.check(mismatch, JA)
ok("括号与角色不配套 ⇒ 软警告（不拦人）", good_m, why_m)
ok("  软规则确实记了警告", any("不太配套" in w for w in gen_gs.WARN), str(gen_gs.WARN))

r, why = red(mk(frame=[{"s": "毎日同じように", "b": "［］", "role": "谓语"}],
                 blocks=[{"s": "毎日同じように", "inner": "", "parts": [{"s": "毎日", "note": "x"}], "mean": "m"}],
                 chain=["毎日同じように"]))
ok("role 说是谓语但词性里没有动词 ⇒ 红（硬闸门）", r, why)
ok("  且原因是词性维度", "词性" in why, why)

r, why = red(mk(frame=[{"s": "不存在于原句", "b": "[]", "role": "连接"}],
                 blocks=[{"s": "不存在于原句", "inner": "", "parts": [{"s": "不存在于原句", "note": "x"}], "mean": "m"}],
                 chain=["毎日同じように"]))
ok("frame 出现原句没有的片段 ⇒ 红", r, why)
ok("  且原因是 frame 不在原句", "不在原句" in why, why)

print("\n=== 非日语片段不参与词性判据 ===")
ok("英文标题卡 ⇒ 词性集合为空", gen_gs.pos_of("Let's get excited!") == set(),
   str(gen_gs.pos_of("Let's get excited!")))
ok("纯日语 ⇒ 词性非空", len(gen_gs.pos_of("こんにちは")) > 0)

print("\n=== skel 里的语法术语 vs 普通中文 ===")
ok("skel 里的「主语」这类术语 ⇒ 放行",
   gen_gs.check(mk(chain=["［毎日同じように］"], skel="［主语］（難しい）"), JA)[0],
   gen_gs.check(mk(chain=["［毎日同じように］"], skel="［主语］（難しい）"), JA)[1])
r, why = red(mk(chain=["［毎日同じように］"], skel="［提高］（難しい）"))
ok("skel 里的「提高」这种普通中文 ⇒ 红（不能全放行）", r, why)

print("\n=== 括号组切分（全角右括号 ］ 必须在排除集里）===")
ok("「［A］［B］」切成两组",
   gen_gs.GRP_RE.findall("［1991年から］［今しか］") == ["［1991年から］", "［今しか］"],
   str(gen_gs.GRP_RE.findall("［1991年から］［今しか］")))
ok("「＋」是分隔符不是内容",
   gen_gs.GRP_RE.findall("［〜ことが］＋（できません）") == ["［〜ことが］", "＋", "（できません）"],
   str(gen_gs.GRP_RE.findall("［〜ことが］＋（できません）")))
r, why = red(mk(chain=["［毎日同じように］"], skel="［1991年から］［今しか］"))
ok("skel 里两个相邻括号组各自校验（不跨界）", r, why)

print("\n=== parts 成分原文的助词自动修正 ===")
# 原句是「世界は」，模型写成「世界が」⇒ 剥掉「が」后必须在原句里
WORLD_JA = "この35年で世界は本当に変わりましたよね。"
world = {
    "frame": [{"s": "世界は本当に変わりました", "b": "〈〉", "role": "主句内容"}],
    "blocks": [{"s": "世界は本当に変わりました", "inner": "",
                "parts": [{"s": "世界が", "note": "主语"}], "mean": "世界变了"}],
    "chain": ["世界は本当に変わりました"], "skel": "〈世界は…〉",
    "skelMean": "世界", "whole": "世界真的变了",
}
ok("parts 的「世界が」（原句是「世界は」）⇒ 自动修正后通过",
   gen_gs.check(world, WORLD_JA)[0], gen_gs.check(world, WORLD_JA)[1])
fixed_w = gen_gs.repair_shape(json.loads(json.dumps(world)), WORLD_JA)
ok("  修正后 parts[0].s == 「世界」",
   fixed_w["blocks"][0]["parts"][0]["s"] == "世界",
   fixed_w["blocks"][0]["parts"][0]["s"])

print("\n=== 形状修复：模型常见的偏差要能掰回来 ===")

# 感動詞（Ex）单独成句时是谓语 ⇒ 必须放行（janome 把「こんにちは」判成 Ex）
greet = {"frame": [{"s": "こんにちは", "b": "（）", "role": "谓语"}],
         "blocks": [{"s": "こんにちは", "inner": "", "parts": [{"s": "こんにちは", "note": "问候语"}], "mean": "你好"}],
         "chain": ["こんにちは"], "skel": "（こんにちは）", "skelMean": "你好", "whole": "你好。"}
ok("感動詞单独成谓语块 ⇒ 放行", gen_gs.check(greet, "みなさん、こんにちは。")[0],
   gen_gs.check(greet, "みなさん、こんにちは。")[1])

# 括号语义硬判：名词块用「（）」当谓语 ⇒ 必须红（不管 role 写什么）
noun_pred = {"frame": [{"s": "日本語", "b": "（）", "role": "谓语内容"}],
             "blocks": [{"s": "日本語", "inner": "", "parts": [{"s": "日本語", "note": "n"}], "mean": "m"}],
             "chain": ["日本語"], "skel": "（日本語）", "skelMean": "m", "whole": "w"}
ok("名词块用「（）」⇒ 红（括号语义硬判）", not gen_gs.check(noun_pred, "シュンと日本語。")[0],
   gen_gs.check(noun_pred, "シュンと日本語。")[1])

shaped = {
    "frame": [{"s": "シュン", "b": ["シュン", "と"], "role": ["名词", "助词"]}],
    "blocks": [{"s": "シュン", "inner": None, "parts": [{"s": "シュン", "note": "n"}], "mean": "m"}],
    "chain": ["シュン"], "skel": "〈シュン〉", "skelMean": "m", "whole": "w",
}
fixed = gen_gs.repair_shape(shaped, "シュンと日本語。")
ok("b 是数组 ⇒ 掰成合法括号", fixed["frame"][0]["b"] in gen_gs.BRACKETS, str(fixed["frame"][0]["b"]))
ok("role 是数组 ⇒ 掰成字符串", isinstance(fixed["frame"][0]["role"], str), str(fixed["frame"][0]["role"]))
ok("inner 是 None ⇒ 掰成空串", fixed["blocks"][0]["inner"] == "", repr(fixed["blocks"][0]["inner"]))

# 名词块被标成谓语 ⇒ 自动改成「〈〉」（janome 词性硬事实）
auto = gen_gs.repair_shape({
    "frame": [{"s": "ポットキャスト", "b": "（）", "role": "谓语"}],
    "blocks": [{"s": "ポットキャスト", "inner": "", "parts": [{"s": "ポットキャスト", "note": "n"}], "mean": "m"}],
    "chain": ["ポットキャスト"], "skel": "〈ポットキャスト〉", "skelMean": "m", "whole": "w",
}, "ポットキャスト。")
ok("名词块「（）」⇒ 自动改成「〈〉」", auto["frame"][0]["b"] == "〈〉", auto["frame"][0]["b"])
ok("  改完就通过校验", gen_gs.check(auto, "ポットキャスト。")[0],
   gen_gs.check(auto, "ポットキャスト。")[1])

print("\n=== 括号剥离 ===")
ok("strip（です）⇒ です", gen_gs.strip_brackets("（です）") == "です")
ok("strip［条件］⇒ 条件", gen_gs.strip_brackets("［条件］") == "条件")
ok("strip 无括号 ⇒ 原样", gen_gs.strip_brackets("接続") == "接続")

print("\n=== inner 是示意性的 ⇒ 只软警告 ===")
inner_bad = [dict(b) for b in BLOCKS]
inner_bad[1] = dict(inner_bad[1], inner="【AIが】 [四角]")
ok("inner 里原句没有的成分 ⇒ 软警告（不拦人）",
   gen_gs.check(mk(blocks=inner_bad), JA)[0],
   gen_gs.check(mk(blocks=inner_bad), JA)[1])
ok("  软规则记了警告", any("inner" in w for w in gen_gs.WARN), str(gen_gs.WARN))

print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
