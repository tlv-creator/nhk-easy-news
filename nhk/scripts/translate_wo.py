#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_wo.py —— 用 MiniMax 把工作单（work/wo/*.json）翻成中文

🔴 为什么自己写（2026-11-21）：
    · 老脚本 `scripts/translate.py` 打的是 **api.minimax.chat** + 模型 `MiniMax-Text-01`
      ⇒ 该模型 2026-11 已下线（实测返回 status_code=2069 "no longer available"）
    ⇒ 现在打 **api.minimaxi.com/v1/chat/completions** + 模型 `MiniMax-M3`
      （与 `scripts/gen_blk.py` 同一套 ⇒ 配方不写两遍）

🔴 一��调用干两件事（easy 轨）：
    「整句中文」+「成分块中文」一起要 ⇒ 模型同时给 `zh` 和 `yb`
    ⇒ 块数对齐由**本地校验**兜底（对不上就重试 ⇒ 不写盘）

🔴 分片：一次最多 12 句（多了会漏句/串行）

用法：
    python3 scripts/translate_wo.py --ids a,b,c
    python3 scripts/translate_wo.py --all-new [--only-missing]
"""
import argparse
import re
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

API = "https://api.minimaxi.com/v1/chat/completions"
MODEL = "MiniMax-M3"
WO = os.path.join(ROOT, "work", "wo")
ZH = os.path.join(ROOT, "work", "zh")
SYN = os.path.join(ROOT, "work", "syn_out")
CHUNK = 12

SYS_EASY = """你是日语精读 App 的译者。下面给你若干句日文台词（NHK「やさしいことばニュース」易懂版）。
每句后面附了它**已经切好的成分块**（带编号）。

硬要求：
1. **一行对一行**，按原顺序输出，**不要合并、不要拆分、不要漏句**。
2. 严格输出 JSON 数组，每个元素形如：
   {"i": 序号, "zh": "整句中文", "yb": ["块1中文","块2中文"]}
   · `i` 必须与输入的 `i` 完全一致
   · 🔴 `yb` 的个数必须**正好等于该句标注的「成分块」个数**，且顺序与编号一一对应
     （句后已写明「共 N 个」⇒ 你的 yb 必须正好 N 个元素，一个都不能少、不能多）
3. 风格：**简体中文**、口语化，像老师讲给 N5 学习者听；**不要书面腔**。
4. 数字两侧留空格（如「第 277 期」「1 番」「800 人」「33℃」）。
5. 英文/专有名词**保留原文**（NHK、KDDI、台风30号、YouTube…）。
6. 语气词（えー、ねー）翻译成自然的中文语气即可。
7. **成分块的中文要短**（几个字到十来个字），贴着块的字面意思，**不要写成整句**。
8. **只输出 JSON 数组**，不要任何解释文字。"""

SYS_WEB = """你是日语精读 App 的译者。下面给你若干句日文原文（NHK 新闻的**普通版/报纸原文**）。
请逐句翻译成中文。

硬要求：
1. **一行对一行**，按原顺序输出，**不要合并、不要拆分、不要漏句**。
2. 严格输出 JSON 数组，每个元素形如：{"i": 序号, "zh": "中文"}
   · `i` 必须与输入的 `i` 完全一致
3. 风格：**简体中文**。原文是书面语，译文要**稍微书面一点**，但仍然用简体。
4. 数字两侧留空格（如「第 277 期」「800 人」「33℃」）。
5. 英文/专有名词**保留原文**（NHK、KDDI、台风30号…）。
6. **只输出 JSON 数组**，不要任何解释文字。"""


def api(messages, timeout=240, maxtok=8000, retries=3):
    key = os.environ.get("MINIMAX_SUB_KEY")
    if not key:
        sys.exit("❌ 没有 MINIMAX_SUB_KEY")
    body = {"model": MODEL, "messages": messages, "max_tokens": maxtok,
            "thinking": {"type": "disabled"}}
    last = ""
    for a in range(retries):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            txt = d["choices"][0]["message"]["content"] or ""
            return txt
        except Exception as ex:
            last = "%s: %s" % (type(ex).__name__, ex)
            print("     (API 重试 %d/%d：%s)" % (a + 1, retries, last), flush=True)
            time.sleep(4 + a * 4)
    raise RuntimeError("API 失败：" + last)


def parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    t = t.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def save(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def chunk(lst, n=CHUNK):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def do_easy(items, out_zh, out_syn, zh_path, syn_path):
    """easy 轨：整句中文 + 成分块中文"""
    ok = True
    for part in chunk(items):
        parts = []
        for it in part:
            bl = "".join("\n       %d) %s" % (j + 1, b.get("text") or "")
                          for j, b in enumerate(it.get("blocks") or []))
            parts.append("  [%d] %s\n     成分块（共 %d 个）：%s"
                         % (it["i"], it["ja"], len(it.get("blocks") or []), bl))
        user = ("待翻译的台词：\n" + "\n".join(parts) +
                "\n\n输出要求：\n"
                "· 严格 JSON 数组，每个元素 {\"i\":序号,\"zh\":\"整句中文\",\"yb\":[...]}\n"
                "· 每句的 yb 必须**正好等于**该句「成分块」标注的个数，顺序与编号一一对应\n"
                "· 只输出 JSON 数组")
        got = None
        for a in range(3):
            arr = parse_json(api([{"role": "system", "content": SYS_EASY},
                                   {"role": "user", "content": user}]))
            if isinstance(arr, list) and len(arr) == len(part):
                good = True
                for k, it in enumerate(part):
                    e = arr[k]
                    if not isinstance(e, dict) or int(e.get("i", -1)) != int(it["i"]):
                        good = False
                        break
                    if not str(e.get("zh") or "").strip():
                        good = False
                        break
                    yb = e.get("yb")
                    if not isinstance(yb, list) or len(yb) != len(it.get("blocks") or []):
                        good = False
                        break
                if good:
                    got = arr
                    break
            print("     (分片重试 %d/3：下标/块数对不上)" % (a + 1), flush=True)
        if got is None:
            # 🔴 兜底：**逐句重试**（分片里混着多句时模型容易对错行）
            got = []
            for it in part:
                one = easy_one(it)
                got.append(one)
                if one is None:
                    ok = False
        for it, e in zip(part, got):
            if e is None:
                out_syn.setdefault(str(it["i"]), [])
                continue
            out_zh[str(it["i"])] = e["zh"]
            out_syn[str(it["i"])] = [str(x) for x in e["yb"]]
        save(zh_path, out_zh)
        save(syn_path, out_syn)
    return ok


def easy_one(it):
    """单句重试：块数必须正好对上，对不上就退化为空（宁可没有，也不要挂错块）"""
    bl = "".join("\n       %d) %s" % (j + 1, b.get("text") or "")
                  for j, b in enumerate(it.get("blocks") or []))
    user = ("原句：%s\n成分块（共 %d 个，下面已编号）：%s\n\n"
            "请输出 JSON 数组，一个元素：{\"i\":%d,\"zh\":\"整句中文\",\"yb\":[...]}\n"
            "· yb 必须**正好 %d 个**元素，与上面编号一一对应，顺序不许变\n"
            "· 每个 yb 元素是该块的中文意思（几个字到十来个字）\n"
            "· 只输出 JSON 数组"
            % (it["ja"], len(it.get("blocks") or []), bl, it["i"],
               len(it.get("blocks") or [])))
    for a in range(3):
        arr = parse_json(api([{"role": "system", "content": SYS_EASY},
                               {"role": "user", "content": user}]))
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            e = arr[0]
            yb = e.get("yb")
            if (str(e.get("zh") or "").strip() and isinstance(yb, list)
                    and len(yb) == len(it.get("blocks") or [])
                    and int(e.get("i", -1)) == int(it["i"])):
                return e
        print("     (单句重试 %d/3：%s)" % (a + 1, it["i"]), flush=True)
    print("  ⚠️ 第 %s 句成分块对不上 ⇒ 该句不挂成分块中文" % it["i"], flush=True)
    # 🔴🔴 但**整句中文必须还要**（缺整句中文 ⇒ build_inc 认为「整期没译完」⇒ 整期被丢掉）
    #    ⇒ 兜底：只翻整句，不要成分块
    zh = zh_only_one(it)
    if zh:
        return {"i": it["i"], "zh": zh, "yb": []}
    return None


def zh_only_one(it):
    """兜底：只要整句中文（成分块中文放弃）"""
    user = ("原句：%s\n\n只输出 JSON 数组，一个元素：{\"i\":%d,\"zh\":\"整句中文\"}\n"
            "· 简体中文、口语化；数字两侧留空格；专有名词保留原文\n"
            "· 只输出 JSON 数组" % (it["ja"], it["i"]))
    for a in range(3):
        arr = parse_json(api([{"role": "system", "content": SYS_WEB},
                               {"role": "user", "content": user}]))
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            z = str(arr[0].get("zh") or "").strip()
            if z and int(arr[0].get("i", -1)) == int(it["i"]):
                return z
        print("     (整句兜底重试 %d/3：%s)" % (a + 1, it["i"]), flush=True)
    print("  ❌ 第 %s 句整句中文也没拿到 ⇒ 该期不能上线" % it["i"], flush=True)
    return None


def do_web(items, out_zh, zh_path):
    ok = True
    for part in chunk(items):
        lines = ["[%d] %s" % (it["i"], it["ja"]) for it in part]
        user = "待翻译的原文：\n" + "\n".join(lines)
        got = None
        for a in range(3):
            arr = parse_json(api([{"role": "system", "content": SYS_WEB},
                                   {"role": "user", "content": user}]))
            if isinstance(arr, list) and len(arr) == len(part):
                good = all(isinstance(e, dict) and int(e.get("i", -1)) == int(it["i"])
                           and str(e.get("zh") or "").strip()
                           for e, it in zip(arr, part))
                if good:
                    got = arr
                    break
            print("     (分片重试 %d/3：下标对不上)" % (a + 1), flush=True)
        if got is None:
            print("  ❌ web 分片 [%d..%d] 翻译失败 ⇒ 整期不收" %
                  (part[0]["i"], part[-1]["i"]), flush=True)
            ok = False
            continue
        for it, e in zip(part, got):
            out_zh[str(it["i"])] = e["zh"]
        save(zh_path, out_zh)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--all-new", action="store_true")
    ap.add_argument("--only-missing", action="store_true")
    a = ap.parse_args()
    if a.ids:
        ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    else:
        ids = sorted(f[:-5] for f in os.listdir(WO)
                     if f.endswith(".json") and not f.startswith("_"))
    if a.all_new:
        pub = set(re.findall(r'"id"\s*:\s*"([^"]+)"',
                              open(os.path.join(ROOT, "site", "app", "data", "episodes.js"),
                                   encoding="utf-8").read()))
        ids = [i for i in ids if i not in pub]
    nb = 0
    for eid in ids:
        wo = load(os.path.join(WO, eid + ".json"))
        if not wo:
            print("⚠️ 没有工作单", eid)
            continue
        tracks = wo.get("tracks") or {}
        easy = tracks.get("easy") or []
        web = tracks.get("web") or []
        zp = os.path.join(ZH, "%s_easy.json" % eid)
        sp_ = os.path.join(SYN, "%s_easy.json" % eid)
        zzh = load(zp, {}) or {}
        zsy = load(sp_, {}) or {}
        if a.only_missing:
            easy = [it for it in easy if str(it["i"]) not in zzh or str(it["i"]) not in zsy]
        ok = True
        if easy:
            ok = do_easy(easy, zzh, zsy, zp, sp_) and ok
        wp = os.path.join(ZH, "%s_web.json" % eid)
        wzh = load(wp, {}) or {}
        if web:
            if a.only_missing:
                web = [it for it in web if str(it["i"]) not in wzh]
            ok = do_web(web, wzh, wp) and ok
        print("  %s：easy %d 句 / web %d 句 %s" %
              (eid, len(easy), len(web), "✅" if ok else "❌"), flush=True)
        if not ok:
            nb += 1
    print("== 翻译结束：%d 期有问题 ==" % nb)
    return 1 if nb else 0


if __name__ == "__main__":
    import re
    sys.exit(main())
