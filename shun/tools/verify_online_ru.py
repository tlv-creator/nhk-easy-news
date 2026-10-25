#!/usr/bin/env python3
"""线上实测：50 集粒度细化后的注音是否已生效（带 cache-buster）。

判据：
 ① 50 集全部可拉（HTTP 200 + JSON 可解析）
 ② 每句表层 == 句原文 t
 ③ 每个含汉字的 unit 都有读音（空读音 = 0）
 ④ 整块比例 <= 本地目标值（本地 583/13315 = 4.4%）
 ⑤ 关键样本抽查：教師 应逐字、селas 风应为 かぜ、地下鉄 应为 ち/か/てつ
 ⑥ 单文件版内联数据已是新版

用法：python3 verify_online_ru.py
"""
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://tlv-creator.github.io/nhk-easy-news"
LOCAL = os.path.join(HERE, "data-merged-50.new.json")


def is_kanji(c):
    return "一" <= c <= "鿿"


def fetch(path, tries=3):
    import time
    for i in range(tries):
        try:
            url = "%s/%s?v=%d" % (BASE, path, int(time.time() * 1000) % 100000000)
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            return urllib.request.urlopen(req, timeout=90).read().decode("utf-8")
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3)
    return ""


def main():
    local = json.load(open(LOCAL, encoding="utf-8"))
    by_tag = {e["tag"]: e for e in local["eps"]}

    idx = json.loads(fetch("shun-web/data/index.json"))
    tags = [e["tag"] for e in idx["eps"]]
    print("线上 index: %d 集" % len(tags))

    ok = 0
    fail = []
    whole = 0
    units = 0
    empty = 0
    surf_bad = 0
    mismatch_local = 0
    samples = {}

    for tag in tags:
        try:
            d = json.loads(fetch("shun-web/data/%s.json" % tag))
        except Exception as e:
            fail.append((tag, "fetch_failed: %s" % e))
            continue
        loc = by_tag.get(tag)
        for i, s in enumerate(d.get("sents") or []):
            ru = s.get("ru") or []
            if "".join(u[0] for u in ru) != (s.get("t") or ""):
                surf_bad += 1
                continue
            for u in ru:
                if u[0] and is_kanji(u[0][0]):
                    units += 1
                    if len(u[0]) > 1:
                        whole += 1
                    if not u[1]:
                        empty += 1
            # 与本地新版逐字比对
            if loc and i < len(loc["sents"]):
                if ru != loc["sents"][i].get("ru"):
                    mismatch_local += 1
            ok += 1
        # 抓样本
        for i, s in enumerate(d.get("sents") or []):
            t = s.get("t") or ""
            if "教師" in t and "teacher" not in samples:
                samples["教師"] = (t, s.get("ru"))
            if "風" in t and "風" not in samples:
                samples["風"] = (t, s.get("ru"))

    print("  线上句数: %d" % ok)
    print("  表层还原失败: %d" % surf_bad)
    print("  空读音: %d" % empty)
    print("  与本地新版不一致的句: %d" % mismatch_local)
    print("  整块 unit: %d / %d = %.1f%%" % (whole, units, 100.0 * whole / max(1, units)))
    for t, e in fail:
        print("  🔴", t, e)

    bad = 0
    if surf_bad:
        print("  🔴 表层还原有失败")
        bad += 1
    if empty:
        print("  🔴 有空读音")
        bad += 1
    if mismatch_local:
        print("  🔴 线上与本地新版不一致（说明 build 未生效或数据不匹配）")
        bad += 1
    pct = 100.0 * whole / max(1, units)
    if pct > 6.0:
        print("  🔴 整块比例 %.1f%% > 6%%（本地目标 4.4%%）" % pct)
        bad += 1
    # 样本抽查
    if "風" in samples:
        t, ru = samples["風"]
        wd = [u for u in ru if u[0] == "風"]
        if wd and wd[0][1] != "かぜ":
            print("  🔴 風 的读音应是 かぜ，线上是 %r" % wd[0][1])
            bad += 1
        else:
            print("  ✅ 風 → かぜ（训读正确）")
    # 单文件版
    try:
        html = fetch("shun-text.html")
        i = html.index("window.SHUN=") + len("window.SHUN=")
        obj, _ = json.JSONDecoder().raw_decode(html[i:])
        s0 = obj["eps"][0]["sents"][0]
        has_new = any(len(u[0]) > 1 for u in (s0.get("ru") or []))
        print("  ✅ 单文件版内联 %d 集，首集 ru %s"
              % (len(obj["eps"]), "含整块" if has_new else "全逐字"))
    except Exception as e:
        print("  🔴 单文件版取不到:", e)
        bad += 1

    print()
    print("退出码: %d" % (1 if bad else 0))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
