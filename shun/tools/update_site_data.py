#!/usr/bin/env python3
"""只更新 50 集站点的**数据**，渲染层 HTML 一个字节都不动。

🔴 为什么不用 build.py 重建（2026-10-25 实测）：
   build.py 的 TEMPLATE/CSS/JS 比线上 site-web-50/index.html **旧**
   （差一个换行 ⇒ 说明后来修的第④段/溢出修复等没进 build.py）
   ⇒ 重建会**回退渲染层的修复** ⇒ 绝对不能这么做

✅ 本脚本只做：
   ① site-web-50/data/<tag>.json  ← 新数据的 sents（其余字段照抄现有文件）
   ② site-web-50/data/index.json  ← 句数 n 重算
   ③ output/Japanese-with-Shun-Text-50.html ← **只替换 window.SHUN= 的数据段**
      （HTML 其余部分逐字保留 ⇒ 渲染层不动）

自检：
   · 数据段替换后，HTML 其余部分逐字不变
   · 单文件版与站点版的句子内容一致
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site-web-50")
DATA = os.path.join(HERE, "data-merged-50.new.json")
SINGLE = os.path.join(HERE, "output", "Japanese-with-Shun-Text-50.html")


def main():
    new = json.load(open(DATA, encoding="utf-8"))
    eps = new["eps"]

    # ---------- ① 站点版 per-episode 数据 ----------
    datadir = os.path.join(SITE, "data")
    idx_path = os.path.join(datadir, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8"))
    by_tag = {e["tag"]: e for e in eps}

    changed = 0
    for e in idx["eps"]:
        src = by_tag.get(e["tag"])
        if src is None:
            print("  ⚠️ 索引里的 %s 在新数据里没有 ⇒ 保留原文件" % e["tag"])
            continue
        p = os.path.join(datadir, e["tag"] + ".json")
        one = json.load(open(p, encoding="utf-8"))
        old_sents = one.get("sents")
        one["sents"] = src["sents"]
        # 只改 sents + n（其余字段照抄）
        with open(p, "w", encoding="utf-8") as f:
            json.dump(one, f, ensure_ascii=False, separators=(",", ":"))
        if old_sents != src["sents"]:
            changed += 1
        e["n"] = len(src["sents"])
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, separators=(",", ":"))
    print("  站点版数据更新 %d 集" % changed)

    # ---------- ② 单文件版：只替换数据段 ----------
    html = open(SINGLE, encoding="utf-8").read()
    # 🔴 用 JSONDecoder.raw_decode 精确定位 JSON 边界
    #    （正则 \{.*?\} 遇到嵌套 {} 会提前截断 ⇒ 实测抓错了位置）
    key = "window.SHUN="
    i = html.index(key) + len(key)
    payload_old, end = json.JSONDecoder().raw_decode(html[i:])
    head = html[:i]
    tail = html[i + end:]
    print("  原数据段 %d 字节" % end)
    payload_new = json.dumps({"eps": eps}, ensure_ascii=False, separators=(",", ":"))
    new_html = head + payload_new + tail
    # 自检：除数据段外逐字不变
    if new_html[:i] != html[:i] or new_html[i + len(payload_new):] != html[i + end:]:
        print("🔴 数据段之外的部分变了 ⇒ 放弃")
        sys.exit(1)
    with open(SINGLE, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("  单文件版数据段已更新（HTML 其余部分逐字未变）")

    # ---------- ③ 交叉校验：两版句子内容一致 ----------
    site_first = json.load(open(os.path.join(datadir, idx["eps"][0]["tag"] + ".json"),
                                encoding="utf-8"))
    single = re.search(r"window\.SHUN=(\{.*?\});", new_html, re.S)
    sj = json.JSONDecoder().raw_decode(new_html[new_html.index(key) + len(key):])[0]
    a = site_first["sents"]
    b = sj["eps"][0]["sents"]
    if a != b:
        print("🔴 站点版与单文件版首集内容不一致")
        sys.exit(1)
    print("  ✅ 站点版与单文件版首集逐字一致")
    print("  单文件版大小: %.2f MB" % (os.path.getsize(SINGLE) / 1048576))


if __name__ == "__main__":
    main()
