#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_zh.py —— 用 MiniMax 把日文逐行翻成中文（分片，别一次塞完）

🔴 为什么要**分片**：一次把 100+ 句塞给模型 ⇒ 它会漏句、串行、字数缩水。
   ⇒ 每片 ~15 句，带序号进、带序号出，**对不上号的直接判失败**（不糊过去）。

风格（照已发布数据）：
    · 口语化中文，一行对一行
    · 数字两侧留空格（「第 277 期」「1番」）
    · 专有名词/英文保留原文

用法：
    python3 tools/translate_zh.py --cues work/cues/ep13_cues.json \
        --out work/zh/ep13_zh.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

API = "https://api.minimax.chat/v1/text/chatcompletion_v2"
# 🔴 2026-11-25：上游把 "MiniMax-Text-01" 下线了（status_code 2069
#    "The model MiniMax-Text-01 is no longer available"）⇒ 换同族后继 MiniMax-M2.5
#    （/v1/models 实测在售：M3.1-Flash / M3 / M2.7 / M2.5；M2.1 与 M2 已下线）
MODEL = "MiniMax-M2.5"

PROMPT = """你是日语精读 App 的译者。下面给你若干句日文台词，请逐句翻译成中文。

要求：
1. **一行对一行**，按原顺序输出，**不要合并、不要拆分、不要漏句**。
2. 输出格式：严格 JSON 数组，每个元素是 {{"i": 序号, "zh": "中文"}}，序号与输入的 i 一致。
3. 风格：口语化、自然，像老师讲给 N5 学习者听；不要书面腔。
4. 数字两侧留空格，例如「第 277 期」「1番」「800 人」。
5. 英文/专有名词保留原文（YouTube、Genki 等）。
6. 语气词、感叹（えー、ねー）翻译成自然的中文语气即可。
7. 只输出 JSON 数组，不要任何解释文字。

待翻译的台词：
{items}"""


def call_minimax(prompt, retries=3):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8000,
        "temperature": 0.2,
    }
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    for attempt in range(retries):
        p = subprocess.run(
            ["curl", "-s", "-X", "POST", API,
             "-H", "Content-Type: application/json",
             "-H", "Authorization: Bearer " + key,
             "-d", json.dumps(body, ensure_ascii=False)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        try:
            d = json.loads(p.stdout.decode())
        except Exception:
            time.sleep(2)
            continue
        ch = d.get("choices")
        if ch:
            return ch[0]["message"]["content"]
        err = d.get("base_resp", d)
        print("  ⚠️ API: %s" % str(err)[:120], file=sys.stderr)
        time.sleep(2)
    raise RuntimeError("MiniMax 调用失败")


def parse_items(text):
    """从模型输出里抠出 JSON 数组

    🔴 2026-11-25：模型**有时不套数组**（直接吐 `{"i":60,"zh":"…"},{"i":61,…}`）
       ⇒ 老解析器只认 `[...]` ⇒ 整片 15 句**全丢**（且只打一行警告 ⇒ 很容易漏看）
       ⇒ 现在：数组优先；不是数组就**逐个抠对象**。
    """
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        raw = m.group(0)
        # 常见坏法：尾逗号 / 单引号
        for cand in (raw, re.sub(r",\s*([\]}])", r"\1", raw)):
            try:
                arr = json.loads(cand)
                if isinstance(arr, list):
                    return arr
            except Exception:
                continue
    # 🔴 没有数组 ⇒ 逐个抠 {"i":…, "zh":…}
    objs = []
    for mm in re.finditer(r"\{[^{}]*\}", text or ""):
        try:
            o = json.loads(mm.group(0))
        except Exception:
            continue
        if isinstance(o, dict) and "i" in o:
            objs.append(o)
    return objs or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cues", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=15)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--reuse", default=None,
                    help="旧翻译文件：按**日文原文**复用（行序变了也能复用）")
    args = ap.parse_args()

    cues = json.load(open(args.cues))["cues"]
    out_path = args.out
    done = {}
    if args.only_missing and os.path.exists(out_path):
        done = {str(k): v for k, v in json.load(open(out_path)).items()}

    if args.reuse and os.path.exists(args.reuse):
        old = json.load(open(args.reuse))
        oldcues = json.load(open(args.reuse.replace("/zh/", "/cues/")))["cues"] \
            if os.path.exists(args.reuse.replace("/zh/", "/cues/")) else None
        if oldcues:
            ja2zh = {oldcues[int(k)]["t"]: v for k, v in old.items()
                     if int(k) < len(oldcues)}
            for i, c in enumerate(cues):
                if str(i) not in done and c["t"] in ja2zh:
                    done[str(i)] = ja2zh[c["t"]]
                    print("  ♻️ 复用 %d" % i, file=sys.stderr)
    todo = [i for i in range(len(cues)) if str(i) not in done]
    print("待翻译 %d 句（已有 %d 句）" % (len(todo), len(done)), file=sys.stderr)

    for s in range(0, len(todo), args.shard):
        part = todo[s:s + args.shard]
        items = "\n".join('{"i": %d, "ja": %s}' % (i, json.dumps(cues[i]["t"], ensure_ascii=False))
                          for i in part)
        resp = call_minimax(PROMPT.format(items=items))
        arr = parse_items(resp)
        if not arr:
            print("  ❌ 第 %d 片解析失败，原样输出前 300 字：%r" % (s, resp[:300]),
                  file=sys.stderr)
            continue
        got = {}
        for x in arr:
            try:
                got[int(x["i"])] = str(x["zh"]).strip()
            except Exception:
                continue
        missing = [i for i in part if i not in got]
        for i in part:
            if i in got:
                done[str(i)] = got[i]
        print("  片 %2d: 拿到 %d/%d 句%s"
              % (s // args.shard, len(part) - len(missing), len(part),
                 ("  缺 %s" % missing) if missing else ""), file=sys.stderr)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=1)

    have = sum(1 for i in range(len(cues)) if str(i) in done)
    print("✅ %d/%d 句已翻译 → %s" % (have, len(cues), out_path), file=sys.stderr)
    if have < len(cues):
        sys.exit(1)


if __name__ == "__main__":
    main()
