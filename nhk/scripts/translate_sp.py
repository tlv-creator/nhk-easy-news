#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 `sp` 轨（电视播报稿）的中文翻译 —— 调 MiniMax API 批量做

为什么用 API 而不是派 subagent：
    本环境没有 task/subagent 工具（只有 bash / web / website_deploy），
    而手册要求「翻译必须派 subagent，自己翻会耗尽上下文」。
    ⇒ 用**同一个模型**（MiniMax-M3）以 API 形式批量做 = 等价的委派，
      不占 agent 上下文。key 只在沙箱的环境变量里，**不进前端/静态站/APK**。

🔴 纪律：
  · 译文必须**逐句**对上一句（日文原文原样回传，便于校验对齐）
  · 数量对不上 / 原文对不上 ⇒ **重试**；重试仍失败 ⇒ **该期不发布**（不写半成品）
  · 成分块中文（yb）数量对不上 ⇒ 置空（前端会退化成「只有日文块」）

产出： work/sp/<id>.json = {"items":[{"ja":…, "zh":…, "yb":[…]}]}
用法： python3 scripts/translate_sp.py [--ids a,b] [--batch 10]
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
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import structure as ST  # noqa: E402

API = "https://api.minimaxi.com/v1/chat/completions"
MODEL = "MiniMax-M3"

SYS = (
    "你是日中对照精读 App 的翻译员。用户给你的是**电视新闻的口语播报稿**（口语、"
    "短句、有时带语气词），请逐句翻译成简体中文。\n"
    "要求：\n"
    "1) 严格逐句对应，**不要合并、不要拆分、不要漏句、不要改写原文**；\n"
    "2) 日文原文原样回传（用于校验对齐），一个字都不要改；\n"
    "3) 译文用**简体中文**，口语就译口语，不要译成书面语；\n"
    "4) 数字、专有名词（地名/人名/机构名）照译；\n"
    "5) 如果给了成分块，还要给出每个块的中文，**顺序与块一一对应**。\n"
    "只输出 JSON 数组，不要任何解释文字。"
)


def blocks_of(plain):
    try:
        y = ST.analyze(plain)
    except Exception:
        y = []
    out = []
    for b in y:
        out.append(plain[b[0]:b[0] + b[1]])
    return out


def call_api(payload, timeout=120):
    key = os.environ.get("MINIMAX_SUB_KEY")
    if not key:
        sys.exit("❌ 没有 MINIMAX_SUB_KEY")
    body = {"model": MODEL, "messages": payload, "max_tokens": 4000,
            "thinking": {"type": "disabled"}}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"] or ""


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def translate_batch(items, with_blocks=True, tries=5):
    lines = []
    for i, it in enumerate(items, 1):
        blk = it.get("blocks") or []
        lines.append("%d) %s" % (i, it["ja"]))
        if with_blocks and blk:
            lines.append("   成分块：" + " ｜ ".join("%d.%s" % (j + 1, b) for j, b in enumerate(blk)))
    user = "请翻译下面 %d 句：\n%s\n\n输出格式：[{\"ja\":\"原文\",\"zh\":\"译文\",\"yb\":[\"块1中文\",...]}]" % (
        len(items), "\n".join(lines))
    for attempt in range(tries):
        try:
            txt = call_api([{"role": "system", "content": SYS},
                            {"role": "user", "content": user}])
        except urllib.error.HTTPError as e:
            # 🔴 429 = 速率限制 ⇒ 退避重试（不是失败）
            if e.code == 429:
                wait = 12 * (attempt + 1)
                print("    ! 429 速率限制 ⇒ 等 %d 秒重试" % wait, flush=True)
                time.sleep(wait)
                continue
            print("    ! API 失败：%s" % e, flush=True)
            time.sleep(3)
            continue
        except Exception as e:
            print("    ! API 失败：%s" % e, flush=True)
            time.sleep(3)
            continue
        arr = parse_json(txt)
        if not arr or len(arr) != len(items):
            print("    ! 第 %d 次：返回条数不对（%s），重试" % (attempt + 1, len(arr) if arr else "无"),
                  flush=True)
            continue
        ok = True
        for a, b in zip(arr, items):
            if str(a.get("ja", "")).strip() != b["ja"].strip():
                ok = False
                break
        if not ok:
            print("    ! 第 %d 次：日文原文对不上，重试" % (attempt + 1), flush=True)
            continue
        out = []
        for a, b in zip(arr, items):
            yb = a.get("yb")
            blk = b.get("blocks") or []
            if not isinstance(yb, list) or (blk and len(yb) != len(blk)):
                yb = []
            out.append({"ja": b["ja"], "zh": str(a.get("zh", "")).strip(), "yb": yb})
        return out
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sp", default=os.path.join(ROOT, "data", "sp"))
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "sp"))
    ap.add_argument("--ids", default="")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--no-blocks", action="store_true")
    ap.add_argument("--only-blocks", action="store_true",
                    help="只补成分块中文（yb），**保留已有的整句译文**（额度恢复后补用）")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    want = [x.strip() for x in a.ids.split(",") if x.strip()]
    files = sorted(f for f in os.listdir(a.sp) if f.endswith(".json"))
    if want:
        files = [f for f in files if f[:-5] in want]
    print("== 待译 %d 期 ==" % len(files), flush=True)
    for f in files:
        eid = f[:-5]
        outp = os.path.join(a.out, f)
        if os.path.exists(outp):
            d = json.load(open(outp, encoding="utf-8"))
            if d.get("items") and not a.only_blocks:
                print("  %s 已有译文 ⇒ 跳过" % eid, flush=True)
                continue
        sp = json.load(open(os.path.join(a.sp, f), encoding="utf-8"))
        sents = sp.get("sents") or []
        if not sents:
            print("  ! %s 没有句子 ⇒ 跳过" % eid, flush=True)
            continue
        items = []
        keep_zh = {}
        if a.only_blocks and os.path.exists(outp):
            for it in (json.load(open(outp, encoding="utf-8")).get("items") or []):
                keep_zh[it["ja"]] = it.get("zh", "")
        prev_yb = {}
        if a.only_blocks and os.path.exists(outp):
            for it in (json.load(open(outp, encoding="utf-8")).get("items") or []):
                prev_yb[it["ja"]] = it.get("yb") or []
        for s in sents:
            it = {"ja": s["ja"]}
            if not a.no_blocks:
                it["blocks"] = blocks_of(s["ja"])
            if a.only_blocks:
                it["zh"] = keep_zh.get(s["ja"], "")     # 保留人工译文
                # 🔴 已经**对齐**的（块数 == 本地块数）就不用重算了 ⇒ 省额度
                yb0 = prev_yb.get(s["ja"]) or []
                if yb0 and len(yb0) == len(it.get("blocks") or []):
                    it["_done"] = True
            items.append(it)
        items = [x for x in items if not x.pop("_done", False)]
        out_items = []
        t0 = time.time()
        if a.only_blocks:
            prev = {}
            if os.path.exists(outp):
                for it in (json.load(open(outp, encoding="utf-8")).get("items") or []):
                    prev[it["ja"]] = it.get("zh", "")
        for i in range(0, len(items), a.batch):
            part = items[i:i + a.batch]
            r = translate_batch(part, not a.no_blocks)
            if r is None:
                print("  ❌ %s 第 %d 批翻译失败 ⇒ 本期不发布（不写半成品）"
                      % (eid, i // a.batch), flush=True)
                break
            out_items += r
            print("    %s %d/%d 句" % (eid, len(out_items), len(items)), flush=True)
            time.sleep(2)          # 🔴 别把 API 打爆（429）
        else:
            if len(out_items) != len(items):
                print("  ❌ %s 句数对不上（%d vs %d）⇒ 本期不发布"
                      % (eid, len(out_items), len(items)), flush=True)
                continue
            if a.only_blocks:
                # 🔴 把「已对齐的旧块中文」和「新算出来的」按**原句顺序**合并回去
                #   （--only-blocks 时 items 是「还没算的那些句」⇒ 句数本来就少于 sents）
                newmap = {it["ja"]: it for it in out_items}
                olditems = (json.load(open(outp, encoding="utf-8")).get("items")
                            or []) if os.path.exists(outp) else []
                merged = []
                for sent in sents:
                    ja0 = sent["ja"]
                    if ja0 in newmap:
                        it2 = dict(newmap[ja0])
                        it2["zh"] = prev.get(ja0, it2.get("zh", ""))
                        merged.append(it2)
                    else:
                        old0 = next((x for x in olditems if x.get("ja") == ja0), None)
                        if old0:
                            merged.append(old0)
                out_items = merged
            json.dump({"id": eid, "items": out_items},
                      open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("  ✅ %s 译完 %d 句（%.0fs）" % (eid, len(out_items), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
