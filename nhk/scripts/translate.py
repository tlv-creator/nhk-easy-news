#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate.py —— 用 MiniMax 把日文逐句翻成中文（分片，别一次塞完）

🔴 为什么要**分片**：一次把 100+ 句塞给模型 ⇒ 它会漏句、串行、字数缩水。
   ⇒ 每片 ~15 句，带序号进、带序号出，**对不上号的直接判失败**（不糊过去）。

风格（照已发布数据）：
    · 口语化中文，一行对一行
    · 数字两侧留空格（「第 277 期」「1番」「800 人」）
    · 专有名词/英文保留原文

用法：
    python3 scripts/translate.py --cues work/asr/<epid>_easy_cues.json \
        --out work/zh/<epid>_easy.json [--only-missing]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

API = "https://api.minimax.chat/v1/text/chatcompletion_v2"
MODEL = "MiniMax-Text-01"

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
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8000, "temperature": 0.2}
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    if not key:
        raise RuntimeError("没有 MINIMAX_SUB_KEY")
    for _ in range(retries):
        try:
            p = subprocess.run(
                ["curl", "-s", "-X", "POST", API,
                 "-H", "Content-Type: application/json",
                 "-H", "Authorization: Bearer " + key,
                 "-d", json.dumps(body, ensure_ascii=False)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
            d = json.loads(p.stdout.decode())
        except Exception:
            time.sleep(2)
            continue
        ch = d.get("choices")
        if ch:
            return ch[0]["message"]["content"]
        print("  ⚠️ API: %s" % str(d.get("base_resp", d))[:120], file=sys.stderr)
        time.sleep(2)
    raise RuntimeError("MiniMax 调用失败")


def parse_items(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        s = re.sub(r",\s*([\]}])", r"\1", m.group(0))
        try:
            return json.loads(s)
        except Exception:
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cues", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=15)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--reuse", default=None, help="旧翻译文件：按**日文原文**复用")
    args = ap.parse_args()

    cues = json.load(open(args.cues, encoding="utf-8"))["cues"]
    out_path = args.out
    done = {}
    if args.only_missing and os.path.exists(out_path):
        done = {str(k): v for k, v in json.load(open(out_path, encoding="utf-8")).items()}

    if args.reuse and os.path.exists(args.reuse):
        old = json.load(open(args.reuse, encoding="utf-8"))
        oldc = args.reuse.replace("/zh/", "/asr/").replace(".json", "_cues.json")
        if not os.path.exists(oldc):
            oldc = args.reuse.replace("/zh/", "/cues/")
        if os.path.exists(oldc):
            oc = json.load(open(oldc, encoding="utf-8"))["cues"]
            ja2zh = {oc[int(k)]["ja"]: v for k, v in old.items() if int(k) < len(oc)}
            for i, c in enumerate(cues):
                if str(i) not in done and c.get("ja") in ja2zh:
                    done[str(i)] = ja2zh[c["ja"]]
                    print("  ♻️ 复用 %d" % i, file=sys.stderr)

    todo = [i for i in range(len(cues)) if str(i) not in done]
    print("待翻译 %d 句（已有 %d 句）" % (len(todo), len(done)), file=sys.stderr)

    for s in range(0, len(todo), args.shard):
        part = todo[s:s + args.shard]
        items = "\n".join('{"i": %d, "ja": %s}' % (i, json.dumps(cues[i]["ja"], ensure_ascii=False))
                          for i in part)
        resp = call_minimax(PROMPT.format(items=items))
        arr = parse_items(resp)
        if not arr:
            print("  ❌ 第 %d 片解析失败：%r" % (s // args.shard, (resp or "")[:200]),
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
        print("  片 %2d: 拿到 %d/%d 句%s" % (s // args.shard, len(part) - len(missing),
                                             len(part), ("  缺 %s" % missing) if missing else ""),
              file=sys.stderr)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=1)

    have = sum(1 for i in range(len(cues)) if str(i) in done)
    print("✅ %d/%d 句已翻译 → %s" % (have, len(cues), out_path), file=sys.stderr)
    if have < len(cues):
        sys.exit(1)


if __name__ == "__main__":
    main()
