#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_site.py —— 把抓回来的 + 翻译好的内容合并成站点产物

产出（写进 site/app/）：
    data/episodes.js   期数表
    subs/<epid>.js     三轨字幕：easy（慢速音+易懂版）/ web（报纸原文）/ sp（电视播报稿）
    data/reading.js    汉字→假名（查表注音）
    media/slow|fast/   音频

🔴 字段契约（照线上已发布产物反推，前端靠**字符位置**在原句里定位）：
    t  = 逐字切分 [[起点, 长度, 片段]]      （Sudachi 形态切分）
    c  = 内容块   [[起点, 长度, 片段]]      （生词）
    y  = 整句结构 [[起点, 长度, 角色id]]    （角色id 见 data/syntax.js 的 r）
    yb = 与 y 一一对应的中文
    g  = 句型标签 [[起点, 长度, 句型id]]    （句型id 见 data/patterns.js）
🔴 **每个片段必须逐字来自原句**（不能改写、不能脑补）⇒ 位置才点得中。
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site", "app")
WORK = os.path.join(ROOT, "work")

sys.path.insert(0, os.path.join(ROOT, "scripts"))


# ────────────────────────── 形态切分 ──────────────────────────
def tokenize(text):
    """Sudachi 形态切分 ⇒ [[起点, 长度, 片段], …]"""
    try:
        from sudachipy import dictionary, tokenizer
        dic = dictionary.Dictionary()
        tok = tokenizer.Tokenizer(dic)
        out, pos = [], 0
        for m in tok.tokenize(text):
            surface = m.surface()
            if not surface:
                continue
            i = text.find(surface, pos)
            if i < 0:
                i = pos
            out.append([i, len(surface), surface])
            pos = i + len(surface)
        return out
    except Exception as e:
        print("  ⚠️ Sudachi 不可用（%s）⇒ 退化成按字符切" % e, file=sys.stderr)
        return [[i, 1, c] for i, c in enumerate(text)]


def content_chunks(text):
    """生词块：名词/动词/形容词/副词/固有名词"""
    try:
        from sudachipy import dictionary, tokenizer
        dic = dictionary.Dictionary()
        tok = tokenizer.Tokenizer(dic)
        keep = ("名詞", "動詞", "形容詞", "副詞")
        out, pos = [], 0
        for m in tok.tokenize(text):
            s = m.surface()
            if not s:
                continue
            i = text.find(s, pos)
            if i < 0:
                i = pos
            pos = i + len(s)
            posinfo = m.part_of_speech()
            if posinfo and posinfo[0] in keep:
                out.append([i, len(s), s])
        return out
    except Exception:
        return [[i, len(c), c] for i, c in enumerate(text)]


# ────────────────────────── 整句结构（角色） ──────────────────────────
ROLES = {"主语": "0", "谓语": "1", "宾语": "2", "状语": "3",
         "修饰": "4", "从句": "5", "语气": "6", "其他": "7"}


def sentence_roles(text, toks):
    """🔴 **规则版**整句结构标注（启发式，非人工精标）：
       主题/主语(が・は 前面的名词) / 宾语(を 前面) / 谓语(句末动词) /
       修饰(连体修饰) / 语气(句末助词) / 其他。
       ⇒ 想要更准 ⇒ 派 subagent 按 work/SYN_TASK.md 产出 yb/结构（见 手册 §3.2）。"""
    out = []
    n = len(toks)
    for i, (st, ln, s) in enumerate(toks):
        role = "7"
        if s in ("が", "は", "も", "の", "に", "で", "へ", "と", "から", "まで"):
            # 找前面那个名词 → 主语 / 宾语 / 修饰
            prev = None
            for j in range(i - 1, -1, -1):
                if toks[j][2] not in ("、", "。", "「", "」", " "):
                    prev = toks[j]
                    break
            if prev is not None:
                if s in ("が", "は", "も"):
                    role = "0"
                elif s == "を":
                    role = "2"
                elif s in ("の",):
                    role = "4"
                else:
                    role = "3"
        elif s.endswith("ます") or s in ("です", "だ", "ます", "ました", "ない", "なかった", "でしょう", "でしょうね"):
            role = "1"
        elif s in ("ね", "よ", "か", "な", "けど", "が", "ので", "から", "ば"):
            role = "6"
        out.append([st, ln, ROLES[role]])
    # 句末动词 → 谓语（覆盖上面的启发式）
    for i in range(len(out) - 1, -1, -1):
        st, ln, s = toks[i]
        if s.endswith(("ます", "ました", "だ", "です", "ない", "illion")) or s in ("です", "ました"):
            out[i][2] = ROLES["谓语"]
            break
    return out


# ────────────────────────── 句型标签 ──────────────────────────
def pattern_tags(text, toks, patterns):
    """🔴 **保守版**：只在能确定时打句型标签（避免贴错标签误导学习）。
       句型id 见 data/patterns.js。"""
    out = []
    joined = "".join(t[2] for t in toks)
    detectors = [
        ("～ました（丁寧・過去）", "ました"),
        ("～ます（丁寧・現在）", "ます"),
        ("～だ（断定）", "だ"),
    ]
    for name, key in detectors:
        pid = None
        for k, v in patterns.items():
            if v.get("n") == name:
                pid = k
                break
        if not pid:
            continue
        for st, ln, s in toks:
            if s == key or (key == "ます" and s.endswith("ます")) or (key == "ました" and s.endswith("ました")):
                out.append([st, ln, pid])
                break
    return out


# ────────────────────────── 主流程 ──────────────────────────
def load_patterns():
    p = os.path.join(SITE, "data", "patterns.js")
    if not os.path.exists(p):
        return {}
    s = open(p, encoding="utf-8").read()
    m = re.search(r"window\.__PATTERNS__=(\{.*\})", s, re.S)
    return json.loads(m.group(1)) if m else {}


def read_json(p, default=None):
    if not p or not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def build_episode(eid, patterns):
    """把一期拼成 subs 结构；翻译不齐就返回 None（**只收翻译完整的期**）"""
    ep = read_json(os.path.join(DATA, "episodes", eid, "episode.json"))
    if not ep:
        return None
    out = {"easy": [], "web": [], "sp": []}
    for track in ("easy", "sp"):
        cues = read_json(os.path.join(WORK, "asr", "%s_%s_cues.json" % (eid, track)))
        zh = read_json(os.path.join(WORK, "zh", "%s_%s.json" % (eid, track)))
        if not cues:
            continue
        syn = read_json(os.path.join(WORK, "syn_out", "%s_%s.json" % (eid, track)), {}) or {}
        arr = []
        for i, c in enumerate(cues.get("cues", [])):
            z = (zh or {}).get(str(i))
            if z is None:
                print("  ❌ %s/%s 第 %d 句没翻译 ⇒ 整期不收" % (eid, track, i))
                return None
            ja = c["ja"]
            toks = tokenize(ja)
            y = sentence_roles(ja, toks)
            yb = syn.get(str(i)) or syn.get(str(i), {}).get("yb") if isinstance(syn.get(str(i)), dict) else syn.get(str(i))
            if not isinstance(yb, list):
                yb = [""] * len(y)
            yb = (yb + [""] * len(y))[:len(y)]
            arr.append({
                "s": int(c.get("s", 0)), "e": int(c.get("e", 0)),
                "ja": ja, "zh": z,
                "t": toks, "c": content_chunks(ja),
                "g": pattern_tags(ja, toks, patterns), "y": y, "yb": yb,
            })
        out[track] = arr
    # web 轨：报纸原文（阅读用）—— 时间轴按原速音时长摊分
    web = ep.get("web_text") or []
    if web and out["sp"]:
        total = out["sp"][-1]["e"] or 1
        chars = sum(len(s) for s in web) or 1
        acc, t0 = 0, 0
        arr = []
        for s in web:
            n = len(s)
            s_ms = int(total * acc / chars)
            e_ms = int(total * (acc + n) / chars)
            toks = tokenize(s)
            y = sentence_roles(s, toks)
            arr.append({"s": s_ms, "e": e_ms, "ja": s, "zh": "",
                        "t": toks, "c": content_chunks(s),
                        "g": pattern_tags(s, toks, patterns),
                        "y": y, "yb": [""] * len(y)})
            acc += n
        out["web"] = arr
    return {"id": eid, "subs": out, "ep": ep}


def write_subs(eid, data):
    p = os.path.join(SITE, "subs", "%s.js" % eid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    body = json.dumps(data["subs"], ensure_ascii=False, separators=(",", ":"))
    with open(p, "w", encoding="utf-8") as f:
        f.write('window.__SUBS__=window.__SUBS__||{};window.__SUBS__["%s"]=%s;\n' % (eid, body))


def update_episodes(built):
    p = os.path.join(SITE, "data", "episodes.js")
    s = open(p, encoding="utf-8").read() if os.path.exists(p) else "window.__EPISODES__=[];"
    m = re.search(r"window\.__EPISODES__=(\[.*\])", s, re.S)
    eps = json.loads(m.group(1)) if m else []
    by_id = {e["id"]: e for e in eps}
    for eid, b in built.items():
        ep = b["ep"]
        easy = b["subs"].get("easy") or []
        sp = b["subs"].get("sp") or []
        dur = (easy[-1]["e"] / 1000.0) if easy else 0
        durf = (sp[-1]["e"] / 1000.0) if sp else 0
        ver = ["easy"]
        if b["subs"].get("web"):
            ver.append("web")
        item = {
            "id": eid, "no": 0,
            "title": ep.get("title", ""),
            "easyTitle": (ep.get("easy_text") or [""])[0][:60],
            "date": "%s-%s-%s" % (eid[0:4], eid[4:6], eid[6:8]),
            "week": "", "time": "20:10",
            "duration": round(dur, 2), "durFast": round(durf, 2),
            "subs": "subs/%s.js" % eid,
            "audio": "media/slow/%s.m4a" % eid,
            "audioFast": ("media/fast/%s.m4a" % eid) if durf else "",
            "ver": ver,
        }
        by_id[eid] = item
    eps = sorted(by_id.values(), key=lambda e: e["id"])
    for i, e in enumerate(eps, 1):
        e["no"] = i
    # 🔴🔴 列表顺序：**日期倒序**（最新的一期在最上面），同一天按播出时间倒序。
    #   为什么要排：原产物里前 36 期是「新→旧」、后追加的 8 期是「旧→新」
    #   ⇒ 首页列表从 10-21 开始、10-23 在最底下，而且 10-21 出现两次 ⇒ 用户以为 App 停更了。
    #   🔴 **但 `no`（期号）绝不能重排**：生词本按 `ep.no` 关联（app.js 的 addWord(..., ep.no, ...)）
    #   ⇒ 重排会让用户已存的生词指到别的期。**只排数组，不动 no。**
    eps.sort(key=lambda e: (e.get("date", ""), e.get("time", "")), reverse=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("window.__EPISODES__=" +
                json.dumps(eps, ensure_ascii=False, indent=1) + ";\n")
    return len(eps)


def copy_media(eid, fast=False):
    for kind in ("slow", "fast"):
        src = os.path.join(DATA, "audio", "%s.%s" % (eid, "m4a" if kind == "slow" else "fast.m4a"))
        if not os.path.exists(src):
            continue
        dst_dir = os.path.join(SITE, "media", kind)
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(dst_dir, "%s.m4a" % eid))


def update_cdn(ids):
    p = os.path.join(SITE, "data", "cdn.js")
    cur = {}
    if os.path.exists(p):
        s = open(p, encoding="utf-8").read()
        m = re.search(r"window\.__CDN__=(\{.*\})", s, re.S)
        cur = json.loads(m.group(1)) if m else {}
    for eid in ids:
        e = {"slow": ["media/slow/%s.m4a" % eid]}
        if os.path.exists(os.path.join(SITE, "media", "fast", "%s.m4a" % eid)):
            e["fast"] = ["media/fast/%s.m4a" % eid]
        cur[eid] = e
    with open(p, "w", encoding="utf-8") as f:
        f.write("/* 音声地址表（相对本站根；慢速=易懂版 / 原速=普通版） */\n")
        f.write("window.__CDN__=" + json.dumps(cur, ensure_ascii=False, separators=(",", ":")) + ";\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", help="要构建的期 id（默认：全部已抓到的）")
    args = ap.parse_args()
    patterns = load_patterns()
    ids = args.ids
    if not ids:
        d = os.path.join(DATA, "episodes")
        ids = sorted(os.listdir(d)) if os.path.isdir(d) else []
    built, skipped = {}, []
    for eid in ids:
        b = build_episode(eid, patterns)
        if not b:
            skipped.append(eid)
            continue
        write_subs(eid, b)
        copy_media(eid)
        built[eid] = b
    if not built:
        print("❌ 没有任何一期是完整的（翻译/对齐缺一不可）")
        if skipped:
            print("   跳过的：%s" % ", ".join(skipped))
        return 1
    n = update_episodes(built)
    update_cdn(list(built))
    print("✅ 构建 %d 期（站点共 %d 期）；跳过 %d 期" % (len(built), n, len(skipped)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
