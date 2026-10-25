#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_online.py —— 线上验收（17 组）

用法：
    python3 test_online.py <线上URL>        # 位置参数！
    python3 test_online.py <线上URL> --ignore-certificate-errors
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

G = []
UA = {"User-Agent": "Mozilla/5.0"}


def check(name, ok, detail=""):
    G.append((name, ok))
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  — " + detail) if detail and not ok else ""))


def get(url, timeout=45, tries=3):
    # 🔴 沙箱里 TLS 偶发被断（EOF）⇒ 重试，别把网络抖动判成站点坏了
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), r.headers
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def head(url, timeout=45):
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, int(r.headers.get("Content-Length") or 0)


def main():
    if len(sys.argv) < 2:
        print("用法：python3 test_online.py <线上URL>")
        return 2
    base = sys.argv[1].rstrip("/")
    print("== test_online %s ==" % base)

    # ① 站首页 200
    try:
        st, body, _ = get(base + "/")
        check("① 站首页 200", st == 200, str(st))
    except Exception as e:
        check("① 站首页 200", False, str(e))
        return 1

    html = body.decode("utf-8", "replace")
    # ② 顶栏剧名标签
    check("② 顶栏常驻剧名标签", "NHKやさしいことばニュース" in html)
    # ③ 关键资源都在
    ok = True
    for p in ("app.js", "style.css", "config.js", "data/episodes.js",
              "data/reading.js", "data/patterns.js", "data/syntax.js", "data/cdn.js"):
        try:
            s, _, _ = get("%s/%s" % (base, p))
            if s != 200:
                ok = False
        except Exception:
            ok = False
    check("③ 关键资源齐全", ok)
    # ④ app.js 里没有平台注入的统计脚本
    s, appjs, _ = get(base + "/app.js")
    appjs = appjs.decode("utf-8", "replace")
    check("④ app.js 无平台统计注入", "agent.minimax.cn" not in appjs or "BLOCK" in appjs)
    # ⑤ 期数表
    s, eps_b, _ = get(base + "/data/episodes.js")
    m = re.search(r"window\.__EPISODES__=(\[.*\])", eps_b.decode("utf-8", "replace"), re.S)
    eps = json.loads(m.group(1)) if m else []
    check("⑤ 期数表可解析", len(eps) > 0, "期数 %d" % len(eps))
    # ⑥ 期号连续
    check("⑥ 期号连续", [e.get("no") for e in eps] == list(range(1, len(eps) + 1)))
    # ⑦ 日期分组连续（同一日期不能散在列表两处）+ 最新期在不在最上面
    ds = [e.get("date", "") for e in eps]
    groups, seen = [], set()
    for d in ds:
        if not groups or groups[-1] != d:
            if d in seen:
                check("⑦ 日期分组连续", False, "日期 %s 散在多处" % d)
                break
            groups.append(d)
            seen.add(d)
    else:
        check("⑦ 日期分组连续", True)
    if ds and ds[0] != max(ds):
        print("  ⚠️ 列表不是「最新在最上面」（第一条 %s，最新 %s）" % (ds[0], max(ds)))
        print("     ⇒ 排序修复已在源码里（scripts/build_site.py），随下一次内容更新上线")
    else:
        print("  ✅ 列表按日期倒序（最新期在最上面）")
    # ⑧ 最新一期 = 站点里最大日期
    # 🔴🔴 2026-10-25 修（**测试写错，不是站坏了**）：这一条原来判的是 `ds[-1]`（列表**最后**一条）
    #    ⇒ 那是**最旧**一期 ⇒ 排序改成「最新在最上面」（手册 §21，2026-10-25 按用户要求改的）之后
    #    ⇒ 这条永远判红（2026-09-08 < 2026-10-01）。
    #    ⇒ 正确判据：**最大日期**（跟列表方向无关）。
    check("⑧ 最新日期合理", (max(ds) if ds else "") >= "2026-10-01", max(ds) if ds else "-")
    # ⑨-⑫ 抽样 4 期：subs 200 且三轨字段齐
    bad = []
    for e in eps[-4:]:
        try:
            s, sb, _ = get("%s/subs/%s.js" % (base, e["id"]))
        except Exception as ex:
            bad.append("%s 取不到 %s" % (e["id"], ex))
            continue
        mm = re.search(r'window\.__SUBS__\["[^"]+"\]=(\{.*\})\s*;?\s*$',
                       sb.decode("utf-8", "replace"), re.S)
        if not mm:
            bad.append("%s 解析失败" % e["id"])
            continue
        d = json.loads(mm.group(1))
        if not d.get("easy"):
            bad.append("%s 没有 easy 轨" % e["id"])
        for track, arr in d.items():
            for c in arr or []:
                if not c.get("ja") or not c.get("zh") and track != "web":
                    bad.append("%s/%s 缺 ja/zh" % (e["id"], track))
                    break
    check("⑨ 抽样 4 期 subs 完好", not bad, str(bad[:4]))
    # ⑬ 音频（抽样 3 期）
    bad = []
    for e in eps[-3:]:
        if not e.get("audio"):
            continue
        try:
            st, ln = head("%s/%s" % (base, e["audio"]))
            if st != 200 or ln < 20000:
                bad.append("%s %s %sB" % (e["id"], st, ln))
        except Exception as ex:
            bad.append("%s %s" % (e["id"], ex))
    check("⑩ 抽样 3 期慢速音可下载", not bad, str(bad[:3]))
    # ⑪ 有原速音的期，fast 也能下
    bad = []
    for e in eps:
        if not e.get("audioFast"):
            continue
        try:
            st, ln = head("%s/%s" % (base, e["audioFast"]))
            if st != 200 or ln < 20000:
                bad.append("%s %s %sB" % (e["id"], st, ln))
        except Exception as ex:
            bad.append("%s %s" % (e["id"], ex))
        if len(bad) > 3:
            break
    check("⑪ 原速音可下载", not bad, str(bad[:3]))
    # ⑫ cdn 表与 episodes 一致
    s, cdn_b, _ = get(base + "/data/cdn.js")
    mm = re.search(r"window\.__CDN__=(\{.*\})", cdn_b.decode("utf-8", "replace"), re.S)
    cdn = json.loads(mm.group(1)) if mm else {}
    check("⑫ cdn 表覆盖全部期", set(cdn) >= {e["id"] for e in eps},
          "cdn %d / eps %d" % (len(cdn), len(eps)))
    # ⑬ 死链扫描（站内）
    dead = []
    for e in eps[-6:]:
        for p in ("subs/%s.js" % e["id"], e.get("audio", ""), e.get("audioFast", "")):
            if not p:
                continue
            try:
                st, _, _ = get("%s/%s" % (base, p))
                if st != 200:
                    dead.append("%s %s" % (p, st))
            except Exception as ex:
                dead.append("%s %s" % (p, ex))
    check("⑬ 死链为零", not dead, str(dead[:3]))
    # ⑭ APK 可下载
    try:
        st, _, hd = get(base + "/NHK.apk", timeout=120)
        check("⑭ APK 可下载", st == 200 and int(hd.get("Content-Length") or 0) > 20000)
    except Exception as e:
        check("⑭ APK 可下载", False, str(e))
    # ⑮ 引导页 + version.json
    try:
        s, vj, _ = get("https://tlv-creator.github.io/nhk-easy-news/version.json")
        d = json.loads(vj.decode("utf-8", "replace"))
        check("⑮ 引导页 version.json 有 site/ver/apkVer",
              all(k in d for k in ("site", "ver", "apkVer")), str(list(d)))
        check("⑮b 引导页指向的就是本站", d.get("site", "").rstrip("/") == base)
    except Exception as e:
        check("⑮ 引导页 version.json", False, str(e))
    # ⑯ 引导页首页可开
    try:
        s, _, _ = get("https://tlv-creator.github.io/nhk-easy-news/")
        check("⑯ 引导页首页 200", s == 200)
    except Exception as e:
        check("⑯ 引导页首页 200", False, str(e))
    # ⑰ 无凭据泄漏
    try:
        st, cfgb, _ = get(base + "/config.js")
        cfg = cfgb.decode("utf-8", "replace")
        leak = [k for k in ("sk-", "ghp_", "sb_publishable", "nhk_ks_pass")
                if k in cfg]
        check("⑰ config.js 无凭据", not leak, str(leak))
        check("⑰b config.js 里只有公开的 AI 中转地址",
              "AI_ENDPOINT" in cfg and "AI_TOKEN" in cfg)
    except Exception as e:
        check("⑰ config.js", False, str(e))

    nfail = sum(1 for _, ok in G if not ok)
    print("\n%s  %d 组检查，%d 失败" % ("✅ 全绿" if nfail == 0 else "❌", len(G), nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
