#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 生成**语法讲解新格式**（用户 2026-10-24 亲定：只有这一种结构）

    一、大框架   （四类括号标出整句层次，每层一行标注语法角色）
    二、逐块拆解 （每块：成分分解 + 2~4 条说明 + 块的中文意思）
    整句意思    （完整中文翻译）

四类括号（全局一致）：
    [ ]   连接词 / 独立成分（呼语、承接词、转折词）
    ［ ］  从句（条件、让步、修饰、宾语从句…）
    〈 〉  句体成分（主语、宾语、补语等句内成分）
    （ ）  谓语（动词/形容词 + 助动词，含疑问与终助词）

🔴 硬约束（不合规就**拒绝写入**，宁可没有也不要错的）：
    ① frame 里的每个片段**逐字来自原句**（去掉括号和空白后，拼接起来必须是原句的子序列）
    ② blocks 的块数 == 本地成分块数（structure.analyze 算出来的），且每块的 range 对得上
    ③ 每块的 text == 原句的 [start, start+len)
    ④ notes 2~4 条；说明要落到**形态链条**
    ⑤ 整句 zh 必须是**完整翻译**（不是摘要）

用法：
    python3 scripts/gen_blk.py --ep 20261023de64170 --track easy [--max-sent 5]
    python3 scripts/gen_blk.py --all          # 增量（已生成过的跳过）
产出： work/blk/<ep>.<track>.json = {"<句号>": {frame, frameLabels, skeleton, blocks[], zh}}
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import structure as ST  # noqa: E402

API = "https://api.minimaxi.com/v1/chat/completions"
MODEL = "MiniMax-M3"
BRACKETS = "[]［］〈〉（）"

SYS = """你是日语精读 App 的语法讲解作者。用户给你一句日文，和它**已经切好的成分块**。
你要输出这个句子的「语法讲解」，格式**只有**下面这一种：

一、大框架：用**四类括号**标出整句的层次结构，每一层下面标它的语法角色。
  [ ]   连接词 / 独立成分（呼语、承接词、转折词）
  ［ ］  从句（条件、让步、修饰、宾语从句…）
  〈 〉  句体成分（主语、宾语、补语等句内成分）
  （ ）  谓语（动词/形容词 + 助动词，含疑问与终助词）
  frameLabels：与 frame 的层**一一对应**的语法角色名（如「呼语」「主题」「谓语（疑问＋确认）」）。

二、逐块拆解：按我给你的成分块**逐块**处理，**不要自己重新切分**，块数必须完全一致。
  每块：
    parts  块内成分分解：[[片段, 词性/语法角色], …]（片段必须逐字来自该块）
    notes  2~4 条说明：讲清「这个成分是什么 + 起什么作用」；动词/形容词要**落到形态链条**（如 ある → あって → あっても）
    zh     该块的中文意思（能独立看懂）
整句意思：整句的**完整**中文翻译（不是摘要）。

🔴 硬约束：
  1. frame 里的每个片段**逐字来自原句**，不得改写、不得增删字、不得换同义说法；
  2. frame 的片段顺序 = 原句顺序；
  3. skeleton：只有**两层以上修饰**的句子才写；单层句**不要凑**（就给空字符串）；
  4. 整句中文用**简体中文**；
  5. 只输出 JSON，不要任何解释文字。

输出格式（**layers 是逐层给的，层数就是 frame 的层数，不许再另给 frameLabels**）：
{"layers":[{"text":"皆さん","type":"[ ]","label":"呼语"},
           {"text":"同僚との関係は","type":"［ ］","label":"主题"},
           {"text":"どうですかね","type":"（ ）","label":"谓语（疑问＋确认）"}],
 "skeleton":"主句骨架：［〜は］＋（どうですかね）＝\"〜怎么样呢\"。",
 "blocks":[{"parts":[["皆さん","呼语"]],"notes":["…","…"],"zh":"大家"}],
 "zh":"大家，和同事的关系怎么样呢？"}

🔴 layers 的硬约束：
  · type 只能是这四个之一："[ ]" / "［ ］" / "〈 〉" / "（ ）"（**括号要成对**）
  · text 必须**逐字来自原句**，顺序与原句一致，**不要漏字、不要改字**
  · label 是这一层的语法角色（如「呼语」「主题」「谓语（疑问＋确认）」「条件从句」）
  · 层次按**由外到内**的顺序给：先给整句的外层结构，再给里面的成分
  · 标点、语气词可以单独成层，label 写「语气」「标点」之类
  · 🔴 **frame 的每一层必须由「若干个完整的成分块」拼成**：
      可以把相邻的几个块合并成一层（例：块1+块2 → 一层），
      但**不能把一个块从中间切开分到两层**（否则框架和拆解自相矛盾）
  · 🔴 layer 的 text 要**逐字等于**它所覆盖的那些块的原文连写（标点块可以不带）"""


def api(payload, timeout=180, maxtok=4000):
    key = os.environ.get("MINIMAX_SUB_KEY")
    if not key:
        sys.exit("❌ 没有 MINIMAX_SUB_KEY")
    body = {"model": MODEL, "messages": payload, "max_tokens": maxtok,
            "thinking": {"type": "disabled"}}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"] or ""


def parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def strip_brackets(s):
    out = []
    for ch in s:
        if ch in BRACKETS or ch.isspace():
            continue
        out.append(ch)
    return "".join(out)


def validate(obj, ja, blocks):
    """严格校验：不合规返回错误原因（不写盘）"""
    if not isinstance(obj, dict):
        return "不是对象"
    layers = obj.get("layers")
    if not isinstance(layers, list) or not layers:
        return "layers 缺失"
    TYPES = ("[ ]", "［ ］", "〈 〉", "（ ）")
    flat = ""
    for i, L in enumerate(layers):
        if not isinstance(L, dict):
            return "层 %d 不是对象" % i
        t = L.get("text") or ""
        ty = (L.get("type") or "").replace(" ", "")
        if not t:
            return "层 %d 没有 text" % i
        if t not in ja:
            return "层 %d 的片段「%s」不在原句里" % (i, t)
        if ty not in ("[]", "［］", "〈〉", "（）"):
            return "层 %d 的 type「%s」不是四类括号之一" % (i, L.get("type"))
        if not (L.get("label") or "").strip():
            return "层 %d 没有 label" % i
        flat += t
    # ① 所有层拼起来必须是原句的**子序列**（可以不连续：标点层可能被跳过）
    it = iter(ja)
    if not all(ch in it for ch in flat):
        return "各层拼起来不是原句的子序列"
    # ② 骨架：单层句不要凑
    sk = obj.get("skeleton") or ""
    if sk and len(layers) < 2:
        return "只有一层却写了 skeleton（单层句不要凑）"
    # ③ blocks 的块数必须与本地一致
    bl = obj.get("blocks")
    if not isinstance(bl, list) or len(bl) != len(blocks):
        return "块数 %s != 本地 %d" % (len(bl) if isinstance(bl, list) else "无", len(blocks))
    for i, (b, blk) in enumerate(zip(bl, blocks)):
        st, ln, role = blk[0], blk[1], blk[2]
        if not isinstance(b, dict):
            return "块 %d 不是对象" % i
        parts = b.get("parts")
        if not isinstance(parts, list) or not parts:
            return "块 %d 没有 parts" % i
        for p in parts:
            if not isinstance(p, list) or len(p) != 2 or not isinstance(p[0], str):
                return "块 %d 的 parts 格式错" % i
            if p[0] and p[0] not in ja:
                return "块 %d 的成分「%s」不在原句里" % (i, p[0])
        notes = b.get("notes")
        if not isinstance(notes, list) or not (2 <= len(notes) <= 4):
            return "块 %d 的 notes 条数 %s（要 2~4 条）" % (i, len(notes) if isinstance(notes, list) else "无")
        if not (b.get("zh") or "").strip():
            return "块 %d 没有中文" % i
    if not (obj.get("zh") or "").strip():
        return "没有整句中文"
    return None


def blocks_of(plain):
    y = ST.analyze(plain)
    out = []
    for st, ln, role in y:
        out.append([st, ln, role, plain[st:st + ln]])
    return out


def gen_sentence(ja, blocks, tries=3):
    bl = "\n".join("   %d. [%s] 角色=%s" % (i + 1, b[3], b[2]) for i, b in enumerate(blocks))
    user = "原句：%s\n成分块（已切好，按顺序）：\n%s\n\n请按系统里的格式输出这个句子的语法讲解（JSON）。" % (ja, bl)
    for attempt in range(tries):
        try:
            txt = api([{"role": "system", "content": SYS}, {"role": "user", "content": user}])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                w = 10 * (attempt + 1)
                print("      429 ⇒ 等 %ds" % w, flush=True)
                time.sleep(w)
                continue
            print("      API 失败 %s" % e, flush=True)
            time.sleep(3)
            continue
        except Exception as e:
            print("      API 异常 %s" % str(e)[:60], flush=True)
            time.sleep(3)
            continue
        obj = parse_json(txt)
        if not obj:
            print("      第 %d 次：JSON 解析失败" % (attempt + 1), flush=True)
            continue
        err = validate(obj, ja, blocks)
        if err:
            print("      第 %d 次：校验不过（%s）⇒ 重试" % (attempt + 1, err), flush=True)
            continue
        return obj
    return None


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def cues_of(app, eid, track):
    raw = open(os.path.join(app, "subs", eid + ".js"), encoding="utf-8").read()
    m = re.search(r'window\.__SUBS__\["%s"\]\s*=\s*(\{.*\});?\s*$' % eid, raw, re.S)
    if not m:
        return None
    d = json.loads(m.group(1))
    return d.get(track) or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", default="")
    ap.add_argument("--track", default="easy")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-sent", type=int, default=0, help="每期最多生成几句（0=不限）")
    ap.add_argument("--app", default=os.path.join(ROOT, "site", "app"))
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "blk"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    eps = []
    if a.all:
        raw = open(os.path.join(a.app, "data", "episodes.js"), encoding="utf-8").read()
        eps = [x["id"] for x in json.loads(re.search(r"=\s*(\[.*?\]);", raw, re.S).group(1))]
    elif a.ep:
        eps = [x.strip() for x in a.ep.split(",") if x.strip()]
    else:
        ap.error("需要 --ep 或 --all")

    total_ok = total_skip = 0
    for eid in eps:
        outp = os.path.join(a.out, "%s.%s.json" % (eid, a.track))
        cur = load(outp)
        cues = cues_of(a.app, eid, a.track)
        if not cues:
            print("  ! %s 没有 %s 轨 ⇒ 跳过" % (eid, a.track))
            continue
        todo = [(i, c) for i, c in enumerate(cues)
                if not (cur.get(str(i)) or {}).get("frame")]
        if a.max_sent:
            todo = todo[:a.max_sent]
        if not todo:
            print("  %s %s：已全有 ⇒ 跳过" % (eid, a.track))
            continue
        print("== %s %s：待生成 %d 句" % (eid, a.track, len(todo)))
        t0 = time.time()
        for i, c in todo:
            ja = c.get("ja") or ""
            blocks = blocks_of(ja)
            if not blocks:
                continue
            obj = gen_sentence(ja, blocks)
            if not obj:
                print("   ! 第 %d 句生成失败（不合规 ⇒ 不写）" % i)
                continue
            # 前端要显示的 frame 字符串（由 layers 拼出来 ⇒ 层数与标注不可能对不上）
            obj["frame"] = " ".join(
                (L.get("type") or "[]")[0] + (L.get("text") or "") + (L.get("type") or "[]")[-1]
                for L in (obj.get("layers") or []))
            obj["range"] = [0, len(ja)]
            obj["text"] = ja
            cur[str(i)] = obj
            total_ok += 1
            json.dump(cur, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("   ✅ 完成（%.0fs）" % (time.time() - t0))
    print("== 生成 %d 句 ⇒ %s ==" % (total_ok, a.out))


if __name__ == "__main__":
    main()
