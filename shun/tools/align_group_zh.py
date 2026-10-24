#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""align_group_zh.py —— 给「合并后的整行」重建正确的中文译文。

🔴 场景（2026-10-24 Shun）：切行把一句切成两行 ⇒ 译文按整句做、按行分配 ⇒
   译文集体漂移。合并碎行后，每一行的 t 完整了，**但它该有的中文要重新定**。

🔴 三种情况（本工具都要能处理）：
   ① 合并组的译文 = 候选 zh 的**拼接**（译文也被切成了两段）
      例：t[26]+t[27]「…色々」+「な人間関係があります。」
          zh[26]「…嗯，各种各样」+ zh[27]「的人际关系都有。」⇒ 拼起来才对
   ② 合并组的译文 = 候选 zh 里的**某一句**（译文没被切，只是分配错了）
   ③ 候选里**夹着别的句**（下一句的开头混在组尾）
      例：t[77..80] 的译文末尾混着下一句的「大家」

🔴🔴 **客观判据（关键）**：模型给的译文必须**只用候选 zh 里的字**
   （字符集合 ⊆ 候选字符集合）⇒ 说明它只是**重排/拼接**，没有重写。
   ⇒ 违反 ⇒ 标记「需人工看」，**不自动采用**。
   ⇒ 判据落在**字符集合这个事实量**上，不靠「感觉像」。

用法：
    python3 tools/align_group_zh.py --ep ep13 --groups work/groups_ep13.json
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-Text-01"
SYSTEM = "你是日中翻译校对员。只输出 JSON，不要解释文字。"

TMPL = """下面是日语精读 App 里**被切碎后合并**的一行日文，以及它周边几行的中文。

【合并后的日文】
{ja}

【候选中文（来自周边行，可能有错位，也可能夹着别的句）】
{cands}

请给出**这一行日文对应的那一段中文**（可以由上面几段拼接而成，也可以只是其中一段）。

输出（只要一个 JSON 对象）：
{{"zh": "<合并后这一行对应的完整中文>", "used": [<用到了第几段，0 起>], "note": "<≤10字说明>"}}

要求：
- 译文必须**只用上面候选里出现过的字**（可以调整顺序、可以在候选之间断开），**不许新写词**
- 如果候选里有多余的句子（属于别的行），**不要包含**
- 如果一段候选明显属于下一行，也不要包含
"""


def call(user, timeout=180, tries=4):
    key = os.environ.get("MINIMAX_SUB_KEY", "")
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user}]}).encode("utf-8")
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
                last = RuntimeError("minimax %s" % br.get("status_code"))
                time.sleep(8 * (i + 1))
                continue
            return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise last


def extract_obj(t):
    t = (t or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def charset(s):
    """只留中日文与字母数字（去掉标点/空白 ⇒ 标点差异不算改写）。"""
    return set(re.sub(r"[\s，、。．！？!?,.;；：:「」『』（）()\[\]【】〈〉・\-…~～\.]",
                      "", s or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True)
    ap.add_argument("--groups", required=True,
                    help="JSON: [[i, j], ...]（原始行号，j>=i，表示要合并的一组）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    p = os.path.join(WS, "web", "data", "eps", args.ep + ".json")
    d = json.load(open(p, encoding="utf-8"))
    rows = [(c.get("rows") or [{}])[0] for c in d.get("cues") or []]
    groups = json.load(open(args.groups, encoding="utf-8"))
    if isinstance(groups, dict):
        groups = groups.get("groups") or []
    # 🔴🔴 **合并写**（不是覆盖！）—— 覆盖会丢掉已算好的其他组
    #   （2026-10-24 踩到：只补 3 组却把 19 组的文件覆盖成 3 组）
    dst = args.out or os.path.join(WS, "work", "group_zh_%s.json" % args.ep)
    out = {}
    if os.path.exists(dst):
        try:
            out = json.load(open(dst, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            out = {}
    n_new = 0
    for gi, g in enumerate(groups):
        i, j = int(g[0]), int(g[1])
        ja = "".join((rows[k].get("t") or "") for k in range(i, j + 1))
        lo = max(0, i - 2)
        hi = min(len(rows), j + 3)
        cands = []
        for k in range(lo, hi):
            z = (rows[k].get("zh") or "").strip()
            if z:
                cands.append("  [%d] %s" % (k - lo, z))
        try:
            obj = extract_obj(call(TMPL.format(ja=ja, cands="\n".join(cands))))
        except Exception as e:  # noqa: BLE001
            print("组 %d 失败：%s" % (gi, e))
            continue
        zh = (obj.get("zh") or "").strip()
        pool = charset("".join((rows[k].get("zh") or "") for k in range(lo, hi)))
        extra = charset(zh) - pool
        ok = (not extra) and bool(zh)
        out["%d-%d" % (i, j)] = {
            "ja": ja, "zh": zh, "used": obj.get("used"),
            "note": obj.get("note"), "reorder_only": ok,
            "extra_chars": "".join(sorted(extra))[:40],
        }
        n_new += 1
        print("组 [%d..%d] %s  %s" % (i, j, "OK" if ok else "✗多了字",
                                       (obj.get("note") or "")))
        if not ok:
            print("    译文: %s" % zh)
            print("    多出的字: %r" % ("".join(sorted(extra))[:40]))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_ok = sum(1 for v in out.values() if v["reorder_only"])
    print("\n本次新增 %d 组；文件共 %d 组（%d 组只用了候选里的字）→ %s" %
          (n_new, len(out), n_ok, dst))


if __name__ == "__main__":
    main()
