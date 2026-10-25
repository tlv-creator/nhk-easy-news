#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 站点内容一致性闸门（2026-10-24 新增）

为什么要有：
    站是「手工拼装」的（从旧站拉数据 + 本地构建产物 + 下载的音视频），
    拼错了**不会报错** ⇒ 用户看到的是「点不动的播放按钮」「空白的期数」。
    ⇒ 判据必须落在**事实量**上：文件在不在、期数对不对、指针指不指得动。

检查项（每项都落在事实量上）：
    ① data/episodes.js 的期数 ≥ 1，且 id 不重复
    ② 每期的 subs / audio / audioFast 文件**都存在**
    ③ durFast > 1 ⇔ 原速音文件存在（前端靠 durFast 决定给不给播放按钮
       ⇒ 两者不一致 ⇒ 要么按钮点了没声，要么有声却没按钮）
    ④ data/cdn.js 里每条路径都存在，且覆盖了全部期
    ⑤ subs/*.js 里能读到该期，且至少有一版句子
    ⑥ 没有孤儿文件（subs/ 和 media/ 里存在但 episodes.js 没引用的）
    ⑦ subs 的句子 ja 不含 ruby 括号（数据模型：ja 必须是纯文本）
    ⑧ 每句都有中文译文（空中文 = 没译完就上线了）

用法： python3 scripts/check_site_consistency.py [--app site/app] [--min-episodes N]
退出码 0 = 全过；1 = 有问题（打印每一条）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ep_sort  # noqa: E402

# 🔴 已知例外（人工逐条确认过，不是规则漏网）：
#   报纸原文里**内容本身**带括号（不是我们加的注音）⇒ 不算「ja 含 ruby」。
#   手册 §14 记的是同一件事（「那 3 处括号是内容本身，不是读音 → 不用管」）。
#   ⇒ 用**小清单**放行，不放宽规则（放宽规则 = 以后新引入的 ruby 也查不出来）。
KNOWN_BRACKETS = {
    "今回は、ちょうど昼の時間帯に大変多くの人に迷惑をおかけし、その点は真摯（しんし）に受け止める」と述べました。",
    "また、アメリカのトランプ大統領と北朝鮮のキム・ジョンウン（金正恩）総書記との間で米朝首脳会談が行われるか注目されていることに触れ、「アメリカと北朝鮮だけで話し合いが決まってしまうのではなく、日本も核問題などの話し合いにおいて存在感を示し、北朝鮮の目が日本に向かざるをえない状況を作ることが、話し合いの糸口になる」と話していました。",
    "国内の「信用情報機関」は、JICC（日本信用情報機構）、CIC、KSC（全国銀行個人信用情報センター）があります。",
}


def load_episodes(app):
    p = os.path.join(app, "data", "episodes.js")
    s = open(p, encoding="utf-8").read()
    m = re.search(r"=\s*(\[.*?\]);", s, re.S)
    if not m:
        sys.exit("❌ 读不出 data/episodes.js 里的数组")
    return json.loads(m.group(1))


def load_cdn(app):
    p = os.path.join(app, "data", "cdn.js")
    s = open(p, encoding="utf-8").read()
    m = re.search(r"=\s*(\{.*\});", s, re.S)
    if not m:
        sys.exit("❌ 读不出 data/cdn.js 里的对象")
    return json.loads(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "site", "app"))
    ap.add_argument("--min-episodes", type=int, default=1)
    a = ap.parse_args()
    app = os.path.normpath(a.app)

    errs = []
    eps = load_episodes(app)
    print("== 站点内容一致性 ==")
    print("   期数：%d" % len(eps))
    if len(eps) < a.min_episodes:
        errs.append("期数 %d < 要求的 %d" % (len(eps), a.min_episodes))
    ids = [e["id"] for e in eps]
    if len(set(ids)) != len(ids):
        dup = [i for i in set(ids) if ids.count(i) > 1]
        errs.append("episodes.js 里有重复期号：%s" % dup)

    # 🔴🔴 排序闸门（2026-10-24 加）：首页是按 **数组顺序**渲染的
    #   ⇒ 数组顺序错了 ⇒ 用户打开 App 第一条是旧的一期 ⇒ **看起来就是「停更」**
    #   ⇒ 而且不报错（没有重复 id，只是顺序反了）⇒ 极难发现
    #   ⇒ 判据：按 (date, time) 必须**严格倒序** + no 必须连续 1..N
    keys = [ep_sort.field_key(e) for e in eps]
    for i in range(len(keys) - 1):
        if keys[i] < keys[i + 1]:
            errs.append("episodes.js 顺序错了：第 %d 条(%s %s) 排在第 %d 条(%s %s) 后面"
                        " ⇒ 首页第一条不是最新的一期 ⇒ 用户会以为「停更了」"
                        % (i + 1, eps[i].get("date"), eps[i].get("time"),
                           i + 2, eps[i + 1].get("date"), eps[i + 1].get("time")))
            break
    nos = [e.get("no") for e in eps]
    if nos != list(range(1, len(eps) + 1)):
        errs.append("episodes.js 的 no 不是连续的 1..%d（首页按 no 排序 ⇒ 会错位）" % len(eps))
    if eps:
        print("   最新一期：%s %s（首页第一条）" % (eps[0].get("date"), eps[0].get("time")))

    used_subs, used_media = set(), set()
    nfast = 0
    for e in eps:
        eid = e["id"]
        for key in ("subs", "audio"):
            rel = e.get(key)
            if not rel:
                errs.append("%s 缺 %s" % (eid, key))
                continue
            p = os.path.join(app, rel)
            if not os.path.exists(p):
                errs.append("%s 的 %s 指向不存在的文件：%s" % (eid, key, rel))
            used_media.add(rel)
        # 原速音：durFast > 1 ⇒ 必须真的有原速音文件（前端靠 durFast 决定给不给播放按钮
        #          ⇒ 不一致 ⇒ 要么按钮点了没声，要么有声却没按钮）
        #   ⚠️ episodes.js 里的 audioFast 字段**前端并不用**（app.js 从 cdn.js 取地址），
        #      所以它可以缺；真正的事实量是「文件在不在 + cdn.js 有没有列」。
        rel = e.get("audioFast")
        has = bool(rel) and os.path.exists(os.path.join(app, rel))
        want = (e.get("durFast", 0) or 0) > 1
        if rel and not has:
            errs.append("%s 的 audioFast 指向不存在的文件：%s" % (eid, rel))
        if want and not has:
            # 没写 audioFast 字段也没关系，只要 cdn.js 里有、文件在就行 —— 下面单独查
            cdn_entry = (load_cdn(app).get(eid) or {}).get("fast") or []
            if not cdn_entry or not os.path.exists(os.path.join(app, cdn_entry[0])):
                errs.append("%s 的 durFast=%.1f 但原速音文件不存在（%s）⇒ 播放按钮点了没声"
                            % (eid, e.get("durFast", 0), rel))
        if has and not want:
            errs.append("%s 有原速音文件但 durFast=%.1f ⇒ 有声却没播放按钮"
                        % (eid, e.get("durFast", 0) or 0))
        if has:
            nfast += 1
            used_media.add(rel)
        # subs
        sp = os.path.join(app, e["subs"])
        if os.path.exists(sp):
            used_subs.add(e["subs"])
            raw = open(sp, encoding="utf-8").read()
            if ('window.__SUBS__["%s"]' % eid) not in raw:
                errs.append("%s 的 subs 文件里没有自己的期号" % eid)
            mm = re.search(r'window\.__SUBS__\["%s"\]\s*=\s*(\{.*?\});?\s*$' % eid, raw, re.S)
            if not mm:
                errs.append("%s 的 subs 文件解析不出来" % eid)
            else:
                try:
                    data = json.loads(mm.group(1))
                except Exception as ex:
                    errs.append("%s 的 subs JSON 坏了：%s" % (eid, ex))
                    data = {}
                vers = data.get("easy") or data.get("web")
                if not vers:
                    errs.append("%s 的 subs 里一句都没有" % eid)
                for ver, cues in data.items():
                    if not isinstance(cues, list):
                        continue
                    for i, c in enumerate(cues):
                        ja = c.get("ja", "")
                        if re.search(r"[（(][ぁ-んァ-ヶ一-鿿]{1,12}[）)]", ja) and ja not in KNOWN_BRACKETS:
                            errs.append("%s.%s[%d] 的 ja 里有 ruby 括号（ja 必须是纯文本）：%s"
                                        % (eid, ver, i, ja[:50]))
                            break
                        if not (c.get("zh") or "").strip():
                            errs.append("%s.%s[%d] 没有中文译文" % (eid, ver, i))
                            break
    print("   有原速音的期：%d" % nfast)

    # cdn.js
    cdn = load_cdn(app)
    for eid, v in cdn.items():
        for kind in ("slow", "fast"):
            for rel in v.get(kind, []):
                if not os.path.exists(os.path.join(app, rel)):
                    errs.append("cdn.js 的 %s/%s 指向不存在的文件：%s" % (eid, kind, rel))
                else:
                    # cdn.js 引用的文件也是「被引用的」⇒ 不算孤儿
                    used_media.add(rel)
    missing = [i for i in ids if i not in cdn]
    if missing:
        errs.append("cdn.js 少了 %d 期：%s" % (len(missing), missing[:6]))
    # durFast > 1 的期，cdn.js 必须列出原速音（前端就是从 cdn.js 取地址的）
    for e in eps:
        if (e.get("durFast", 0) or 0) > 1 and not (cdn.get(e["id"]) or {}).get("fast"):
            errs.append("%s 的 durFast=%.1f 但 cdn.js 没列原速音 ⇒ 点了没声"
                        % (e["id"], e.get("durFast", 0)))

    # 孤儿文件
    for d, used in (("subs", used_subs), ("media/slow", set()), ("media/fast", set())):
        p = os.path.join(app, d)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            rel = "%s/%s" % (d, f)
            if d == "subs":
                if rel not in used:
                    errs.append("孤儿文件：%s（episodes.js 没引用）" % rel)
            else:
                if rel not in used_media:
                    errs.append("孤儿文件：%s（episodes.js 没引用）" % rel)

    if errs:
        print("\n❌ 有 %d 处问题：" % len(errs))
        for x in errs[:60]:
            print("   -", x)
        if len(errs) > 60:
            print("   ... 还有 %d 条" % (len(errs) - 60))
        sys.exit(1)
    print("✅ 全部一致（期数 / 文件存在 / durFast⇔原速音 / cdn / 无孤儿 / ja 纯文本 / 译文齐全）")


if __name__ == "__main__":
    main()
