#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_inc.py —— **增量**构建：只加新的一期，已发布的期一个字都不动

🔴🔴 为什么不能直接用 `build_data.py`（2026-11-16 实测）：
    `build_data.py` 是**全量重建** —— 它从 `data/episodes/` 重新拼 `episodes.js`、
    `reading.js`、`lexicon.js`、`vocab.js`、`patterns.js`。
    ⇒ 沙箱重置后本地 `data/episodes/` 只剩新抓的那几期
    ⇒ 跑一次 build_data.py ⇒ **线上已发布的 67 期会被整段抹掉**（而且不报错）。
    ⇒ 所以本脚本是**外科手术式**的：
        · `subs/<id>.js`     —— 只写新期（原子写）
        · `data/episodes.js` —— **合并**（保留旧条目；`no` 不重编，见下）
        · `data/reading.js` / `data/lexicon.js` —— **只增不删**
        · `data/cdn.js`      —— 合并
        · `media/slow/`      —— 拷新期的慢速音（随后由 retranscode.py 重编成 96k）

🔴 **`no` 一律不重编**（手册 §32.3）：
    `ep.no` 是生词本关联用的编号（用户已存的生词靠它指回期数）
    ⇒ 新期从「已发布的最大 no + 1」接着排 ⇒ 旧期的 no 一个都不动。
    ⇒ 数组按 (date, time) 倒序排（最新在最上面）—— 但**只排数组、不动 no**。

🔴 cue 的结构**复用 `build_data.build_cues()`**（唯一权威）：
    t = ruby 块切分（easy 轨用 NHK 自己的注音块）／janome（web 轨）
    c = 生词标记（按全站词频过滤）
    g = 句型标记
    y = `structure.analyze()` 的成分块+角色
    yb = 成分块中文（长度必须与 y 一致，否则这一句的 yb 置空）

用法：
    python3 scripts/build_inc.py --ids a,b,c
    python3 scripts/build_inc.py --all-new
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
# 🔴 build_data 在 **import 时**就读 sys.argv[1]（`int(sys.argv[1])`）⇒ import 前先把 argv 藏起来
#    （但**用完要还原** ⇒ 否则本脚本自己的 argparse 拿不到 --ids/--all-new）
_ARGV = list(sys.argv)
sys.argv = [sys.argv[0]]
import build_data as BD            # noqa: E402
sys.argv = _ARGV                   # ← 还原：本脚本的 argparse 还要用
import parse_text as P             # noqa: E402
from build_sp import corpus_freq   # noqa: E402
import ep_sort as EP_SORT       # noqa: E402

DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")
SITE = os.path.join(ROOT, "site", "app")


def load_json(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def published_ids():
    p = os.path.join(SITE, "data", "episodes.js")
    if not os.path.exists(p):
        return set()
    s = open(p, encoding="utf-8").read()
    return set(re.findall(r'"id"\s*:\s*"([^"]+)"', s))


def all_new():
    d = os.path.join(DATA, "episodes")
    pub = published_ids()
    return sorted([e for e in os.listdir(d)
                   if os.path.isdir(os.path.join(d, e)) and e not in pub], reverse=True)


def dur_of(path):
    if not os.path.exists(path):
        return 0.0
    p = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return round(float(p.stdout.strip()), 2)
    except Exception:
        return 0.0


def zh_map(path):
    """{ja: 中文} —— 从 work/zh/<id>_<track>.json（下标→中文）按句子顺序对齐"""
    d = load_json(path, {}) or {}
    return d


def build_one(eid, freq):
    """把一期拼成 subs 结构；翻译不齐 ⇒ 返回 None（**只收翻译完整的期**）"""
    d = os.path.join(DATA, "episodes", eid)
    epj = load_json(os.path.join(d, "episode.json"), {}) or {}
    out = {}

    # ── easy 轨 ──
    easy_p = os.path.join(d, "easy.html")
    if not os.path.exists(easy_p):
        print("  ! %s 没有 easy.html ⇒ 跳过" % eid)
        return None
    easy_sents = P.parse_easy(easy_p)
    if not easy_sents:
        print("  ! %s easy 解析 0 句 ⇒ 跳过" % eid)
        return None
    cues_p = os.path.join(WORK, "asr", "%s_easy_cues.json" % eid)
    cues_j = load_json(cues_p)
    if not cues_j or not cues_j.get("cues"):
        print("  ! %s 没有 ASR 时间轴 ⇒ 跳过" % eid)
        return None
    asr_cues = cues_j["cues"]
    if len(asr_cues) != len(easy_sents):
        print("  ! %s easy 句数对不上（ASR %d vs 原文 %d）⇒ 跳过"
              % (eid, len(asr_cues), len(easy_sents)))
        return None
    times = [[int(c.get("s", 0)), int(c.get("e", 0))] for c in asr_cues]
    zh = zh_map(os.path.join(WORK, "zh", "%s_easy.json" % eid))
    zhmap = {}
    for s, c in zip(easy_sents, asr_cues):
        v = zh.get(str(c["i"]) if "i" in c else str(len(zhmap)))
        if v is None:
            v = zh.get(str(len(zhmap)))
        if v is None:
            print("  ❌ %s/easy 第 %d 句没翻译 ⇒ 整期不收" % (eid, len(zhmap)))
            return None
        zhmap[s["plain"]] = v
    # 成分块中文
    syn = load_json(os.path.join(WORK, "syn_out", "%s_easy.json" % eid), {}) or {}
    bzhmap = {}
    for i, s in enumerate(easy_sents):
        b = syn.get(str(i))
        if isinstance(b, list) and b:
            bzhmap[s["plain"]] = b
    out["easy"] = BD.build_cues(easy_sents, times, zhmap, freq, bzhmap)

    # ── web 轨（报纸原文）──
    web_p = os.path.join(d, "web.json")
    if os.path.exists(web_p):
        web_sents = P.parse_web(web_p)
        if web_sents:
            wzh = zh_map(os.path.join(WORK, "zh", "%s_web.json" % eid))
            wmap = {}
            ok = True
            for i, s in enumerate(web_sents):
                v = wzh.get(str(i))
                if v is None:
                    print("  ❌ %s/web 第 %d 句没翻译 ⇒ web 轨不收" % (eid, i))
                    ok = False
                    break
                wmap[s["plain"]] = v
            if ok:
                # 🔴 没有原速音 ⇒ 时间轴走「每句 3 秒」的阅读节奏（与线上 easy-only 期一致）
                cues = BD.build_cues(web_sents, [], wmap, freq, {})
                # 🔴 web 轨**没有成分块中文**（yb）⇒ 把 y/yb 置空
                #    （否则弹窗的「③ 整句结构」会渲染出「只有日文、没有中文」的半空表）
                for c in cues:
                    c["y"] = []
                    c["yb"] = []
                out["web"] = cues
    return {"id": eid, "subs": out, "ep": epj}


def write_subs(eid, data):
    p = os.path.join(SITE, "subs", "%s.js" % eid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    atomic_write(p, 'window.__SUBS__=window.__SUBS__||{};window.__SUBS__["%s"]=%s;\n'
               % (eid, body))


def merge_episodes(built):
    p = os.path.join(SITE, "data", "episodes.js")
    s = open(p, encoding="utf-8").read() if os.path.exists(p) else "window.__EPISODES__=[];"
    m = re.search(r"window\.__EPISODES__\s*=\s*(\[.*\])", s, re.S)
    eps = json.loads(m.group(1)) if m else []
    by_id = {e["id"]: e for e in eps}
        max_no = max([int(e.get("no") or 0) for e in eps] or [0])
        # 新期按**播出时间**升序接号（旧的 no 一个都不动）
        # 🔴 用 ep_sort.raw_key（**唯一权威**）：NHK 的 time 格式不统一
        #    （"2026-11-12 20:08:00" / "20261112T200800+0900"）
        #    ⇒ 直接按字符串排序会错（新期被甩到旧期前面 ⇒ no 与日期倒挂）
        news = sorted(built.values(), key=lambda b: (EP_SORT.raw_key(b["ep"].get("time")), b["id"]))
        for b in news:
            eid = b["id"]
            ep = b["ep"]
            easy = b["subs"].get("easy") or []
            dur = round((easy[-1]["e"] / 1000.0) if easy else 0, 2)
            easy_sents = P.parse_easy(os.path.join(DATA, "episodes", eid, "easy.html"))
            easy_t = ""
            if easy_sents:
                easy_t = re.sub(r"[（(][ぁ-んァ-ヶ一-鿿]+[）)]", "", easy_sents[0]["plain"])
                easy_t = easy_t.split("。")[0][:40]
            tm = str(ep.get("time") or "")
            date, clock = "", ""
            m = re.search(r"(\d{4})[-/年.]?(\d{2})[-/月.]?(\d{2})", tm)
            if m:
                date = "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
            m2 = re.search(r"(\d{2})[:時.]?(\d{2})", tm[m.end():] if m else "")
            if m2:
                clock = "%s:%s" % (m2.group(1), m2.group(2))
            if not date:
                date = "%s-%s-%s" % (eid[0:4], eid[4:6], eid[6:8])
            # 🔴 重跑 build 时**不要重新发号**（否则同一批期会一路涨号，no 与日期倒挂）
            #    ⇒ 已经在这张表里的期：**沿用它原来的 no**
            if eid in by_id:
                no = int(by_id[eid].get("no") or 0)
            else:
                max_no += 1
                no = max_no
        row = {
            "id": eid, "no": no,
            "title": (ep.get("title") or "")[:70], "easyTitle": easy_t,
            "date": date, "week": ep.get("week") or "", "time": clock,
            "duration": dur, "durFast": 0.0,
            "subs": "subs/%s.js" % eid,
            "audio": "media/slow/%s.m4a" % eid,
            "ver": sorted(k for k, v in b["subs"].items() if v),
        }
        by_id[eid] = row
    eps = list(by_id.values())
    # 🔴 只排数组，不动 no（手册 §32.3）
    eps.sort(key=lambda e: (e.get("date", ""), e.get("time", "")), reverse=True)
    atomic_write(p, "window.__EPISODES__=" +
                 json.dumps(eps, ensure_ascii=False, indent=1) + ";\n")
    return len(eps)


def merge_reading(built):
    """reading.js / lexicon.js：**只增不删**（已发布的注音表一个字都不许动）"""
    rp = os.path.join(SITE, "data", "reading.js")
    lp = os.path.join(SITE, "data", "lexicon.js")
    s = open(rp, encoding="utf-8").read() if os.path.exists(rp) else "window.__READING__={};"
    m = re.search(r"window\.__READING__\s*=\s*(\{.*\})", s, re.S)
    reading = json.loads(m.group(1)) if m else {}
    s2 = open(lp, encoding="utf-8").read() if os.path.exists(lp) else "window.__LEXICON__={};"
    m2 = re.search(r"window\.__LEXICON__\s*=\s*(\{.*\})", s2, re.S)
    lexicon = json.loads(m2.group(1)) if m2 else {}
    add_r = add_l = 0
    for eid, b in built.items():
        for track in ("easy", "web"):
            fn = ("easy.html" if track == "easy" else "web.json")
            p = os.path.join(DATA, "episodes", eid, fn)
            if not os.path.exists(p):
                continue
            sents = (P.parse_easy(p) if track == "easy" else P.parse_web(p))
            for sn in sents:
                for w in sn["words"]:
                    surf = w["w"]
                    if not surf:
                        continue
                    if w.get("r") and re.search(r"[一-鿿ァ-ヶ]", surf) and surf not in reading:
                        reading[surf] = w["r"]
                        add_r += 1
                    if surf not in lexicon:
                        lexicon[surf] = {"p": w.get("p", ""), "r": w.get("r", ""),
                                         "bf": w.get("bf", surf)}
                        add_l += 1
    atomic_write(rp, "window.__READING__=" + json.dumps(reading, ensure_ascii=False) + ";\n")
    atomic_write(lp, "window.__LEXICON__=" + json.dumps(lexicon, ensure_ascii=False) + ";\n")
    return add_r, add_l


def merge_cdn(built):
    p = os.path.join(SITE, "data", "cdn.js")
    s = open(p, encoding="utf-8").read() if os.path.exists(p) else "window.__CDN__={};"
    m = re.search(r"window\.__CDN__\s*=\s*(\{.*\})", s, re.S)
    cur = json.loads(m.group(1)) if m else {}
    for eid in built:
        e = {"slow": ["media/slow/%s.m4a" % eid]}
        if os.path.exists(os.path.join(SITE, "media", "fast", "%s.m4a" % eid)):
            e["fast"] = ["media/fast/%s.m4a" % eid]
        cur[eid] = e
    atomic_write(p, "/* 音声地址表（相对本站根；慢速=易懂版 / 原速=普通版） */\n"
                 "window.__CDN__=" + json.dumps(cur, ensure_ascii=False, separators=(",", ":")) + ";\n")


def copy_media(eid):
    src = os.path.join(DATA, "audio", "%s.m4a" % eid)
    if not os.path.exists(src):
        return False
    dst_dir = os.path.join(SITE, "media", "slow")
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copyfile(src, os.path.join(dst_dir, "%s.m4a" % eid))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--all-new", action="store_true")
    ap.add_argument("--no-media", action="store_true")
    a = ap.parse_args()
    ids = ([x.strip() for x in a.ids.split(",") if x.strip()] if a.ids
           else (all_new() if a.all_new else []))
    if not ids:
        print("需要 --ids 或 --all-new")
        return 2
    print("== 增量构建 %d 期 ==" % len(ids))
    freq = corpus_freq(SITE)
    print("   词频：%d 期" % freq.get("__total__", 0))
    built = {}
    for eid in ids:
        b = build_one(eid, freq)
        if not b:
            continue
        write_subs(eid, b["subs"])
        if not a.no_media:
            copy_media(eid)
        built[eid] = b
        ne = len(b["subs"].get("easy") or [])
        nw = len(b["subs"].get("web") or [])
        print("  ✅ %s  easy=%d web=%d" % (eid, ne, nw))
    if not built:
        print("❌ 一期都没建出来")
        return 1
    n = merge_episodes(built)
    add_r, add_l = merge_reading(built)
    merge_cdn(built)
    print("✅ 站点共 %d 期（新增 %d）· 注音表 +%d · 词库 +%d"
          % (n, len(built), add_r, add_l))
    return 0


if __name__ == "__main__":
    sys.exit(main())
