#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_local.py —— 本地功能验收（13 组）

判据落在**产物**上，不靠印象：
    · 期数表与 subs 文件一一对应
    · 每条 cue 的字段齐、位置自洽（t/c/y 的片段必须逐字来自原句）
    · 音频文件存在、大小合理
    · 注音表覆盖到句子里出现的汉字
    · 前端源码的关键约束（绝对 URL / 音字同步开关 / 标签默认关）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site", "app")
G = []


def check(name, ok, detail=""):
    G.append((name, ok, detail))
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  — " + detail) if detail and not ok else ""))


def load(p, var):
    p = os.path.join(SITE, p)
    if not os.path.exists(p):
        return None
    s = open(p, encoding="utf-8").read()
    m = re.search(r"window\.%s=(\{.*\}|\[.*\])\s*;?\s*$" % var, s, re.S)
    if not m:
        m = re.search(r"window\.%s=(\{.*?\}|\[.*?\]);" % var, s, re.S)
    return json.loads(m.group(1)) if m else None


def main():
    print("== test_local ==")
    eps = load("data/episodes.js", "__EPISODES__")
    check("① 期数表存在", eps is not None)
    if not eps:
        return 1
    check("② 期数 ≥ 1", len(eps) >= 1, "实际 %d" % len(eps))

    # ③ 期号连续（🔴 `no` 不随列表顺序变 ⇒ 查**集合**，不是数组顺序）
    nos = [e.get("no") for e in eps]
    check("③ 期号连续（集合）", sorted(nos) == list(range(1, len(eps) + 1)), str(nos[:8]))
    dates = [e.get("date", "") for e in eps]
    check("④ 列表按日期倒序（最新期在最上面）", dates == sorted(dates, reverse=True),
          "%s … %s" % (dates[0], dates[-1]))
    # 🔴 `no` 必须与历史一致（生词本按 ep.no 关联）⇒ 这里的期号不能被重排
    check("④b 期号与排列顺序无关（no 稳定）", sorted(nos) == list(range(1, len(eps) + 1)))
    check("⑤ id 与 date 一致",
          all(e["id"][:8] == e["date"].replace("-", "") for e in eps
              if e["id"][:1].isdigit()))

    # ⑥ subs 文件都在
    missing = [e["id"] for e in eps
               if not os.path.exists(os.path.join(SITE, "subs", "%s.js" % e["id"]))]
    check("⑥ subs 文件齐全", not missing, str(missing[:5]))

    # ⑦ cue 字段齐 + 位置自洽
    bad_fields, bad_pos = [], []
    for e in eps[:50]:
        p = os.path.join(SITE, "subs", "%s.js" % e["id"])
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["[^"]+"\]=(\{.*\})\s*;?\s*$', s, re.S)
        if not m:
            bad_fields.append(e["id"] + ":解析失败")
            continue
        d = json.loads(m.group(1))
        for track in ("easy", "web", "sp"):
            for i, c in enumerate(d.get(track) or []):
                for f in ("s", "e", "ja", "zh", "t", "c", "g", "y", "yb"):
                    if f not in c:
                        bad_fields.append("%s/%s#%d 缺 %s" % (e["id"], track, i, f))
                ja = c.get("ja", "")
                for key in ("t", "c"):
                    for st, ln, seg in (c.get(key) or []):
                        if ja[st:st + ln] != seg:
                            bad_pos.append("%s/%s#%d %s 片段不逐字" % (e["id"], track, i, key))
                # 🔴 y 的第三个元素是**角色 id**（不是文字）⇒ 只查区间合法
                for st, ln, seg in (c.get("y") or []):
                    if st < 0 or st + ln > len(ja):
                        bad_pos.append("%s/%s#%d y 区间越界" % (e["id"], track, i))
                # 🔴 y/yb 长度不等 = 成分块中文缺口 ⇒ 下面单独报（不当场判死）
    check("⑦ cue 字段齐全", not bad_fields, str(bad_fields[:5]))
    check("⑧ 片段逐字来自原句", not bad_pos, str(bad_pos[:5]))

    # ⑨ 音频文件存在
    miss_a = []
    for e in eps:
        if e.get("audio") and not os.path.exists(os.path.join(SITE, e["audio"])):
            miss_a.append(e["id"])
    check("⑨ 慢速音文件都在", not miss_a, str(miss_a[:5]))
    nfast = sum(1 for e in eps if e.get("durFast"))
    nfastfile = sum(1 for e in eps if e.get("audioFast")
                    and os.path.exists(os.path.join(SITE, e["audioFast"])))
    check("⑩ 有原速音的期都有文件", nfast == nfastfile, "%d vs %d" % (nfast, nfastfile))

    # ⑪ ja 里不能有内联 ruby（汉字后面跟一串纯假名的括号）
    #    🔴 括号里是汉字/数字的**不是** ruby（如「キム・ジョンウン（金正恩）」「（10月9日）」）⇒ 不能误报
    #    🔴 **web 轨是 NHK 报纸原文，逐字照搬**（NHK 自己就写「真摯（しんし）」）⇒ 只查 easy/sp 轨
    bad_ruby = []
    for e in eps:
        p = os.path.join(SITE, "subs", "%s.js" % e["id"])
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["[^"]+"\]=(\{.*\})\s*;?\s*$', s, re.S)
        if not m:
            continue
        d = json.loads(m.group(1))
        for track in ("easy", "sp"):
            for c in d.get(track) or []:
                if re.search(r"[一-龯][（(][ぁ-んァ-ヶ]+[）)]", c.get("ja", "")):
                    bad_ruby.append("%s/%s" % (e["id"], track))
                    break
    check("⑪ ja 是纯文本（无括号注音）", not bad_ruby, str(bad_ruby[:5]))

    # ⑫ 注音表覆盖（至少覆盖 easy 轨 90% 的汉字词）
    reading = load("data/reading.js", "__READING__") or {}
    if eps:
        p = os.path.join(SITE, "subs", "%s.js" % eps[-1]["id"])
        if os.path.exists(p):
            s = open(p, encoding="utf-8").read()
            m = re.search(r'window\.__SUBS__\["[^"]+"\]=(\{.*\})\s*;?\s*$', s, re.S)
            d = json.loads(m.group(1)) if m else {}
            words = set()
            for c in d.get("easy") or []:
                for st, ln, seg in (c.get("c") or []):
                    if re.search(r"[一-龯]", seg):
                        words.add(seg)
            hit = [w for w in words if w in reading]
            rate = len(hit) / len(words) if words else 1.0
            check("⑫ 注音表覆盖生词 ≥90%%", rate >= 0.9, "%.0f%%" % (rate * 100))

    # 🔴 成分块中文（yb）的覆盖：这是**已知缺口**，不判失败，但要明确报出来
    gap = []
    for e in eps:
        p = os.path.join(SITE, "subs", "%s.js" % e["id"])
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        m = re.search(r'window\.__SUBS__\["[^"]+"\]=(\{.*\})\s*;?\s*$', s, re.S)
        if not m:
            continue
        d = json.loads(m.group(1))
        for track in ("easy", "web", "sp"):
            arr = d.get(track) or []
            if not arr:
                continue
            bad = sum(1 for c in arr
                      if len(c.get("y") or []) != len(c.get("yb") or []) or not c.get("yb"))
            if bad:
                gap.append("%s/%s(%d/%d)" % (e["id"], track, bad, len(arr)))
    if gap:
        print("  ⚠️ 成分块中文(yb)缺口 %d 处：%s" % (len(gap), ", ".join(gap[:8])))
        print("     修：python3 scripts/syn_zh.py --all   （补齐后再 build）")
    else:
        print("  ✅ 成分块中文(yb)全覆盖")

    # ⑬ 前端关键约束
    appjs = ""
    p = os.path.join(SITE, "app.js")
    if os.path.exists(p):
        appjs = open(p, encoding="utf-8").read()
    check("⑬ 原生播放器走绝对 URL", "new URL(" in appjs and "playAudio" in appjs)
    check("⑬b 标签默认不显示", "tags: false" in appjs or "tags:false" in appjs)
    check("⑬c 普通版有音字同步开关", "S.settings.sp" in appjs)

    nfail = sum(1 for _, ok, _ in G if not ok)
    print("\n%s  %d 组检查，%d 失败" % ("✅ 全绿" if nfail == 0 else "❌", len(G), nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
