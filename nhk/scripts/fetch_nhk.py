#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_nhk.py —— 抓 NHK やさしいことばニュース 的新一期

🔴 源（2026-10-24 重建时实测）：
   ① 清单：https://news.web.nhk/news/easy/sitemap/sitemap.xml
      —— 636 条；静态页之后是文章，**最新在最前**。
      —— `<id>` = YYYYMMDD + ニュースID（如 20261023de64170）⇒ 前 8 位就是日期。
   ② 易懂版正文：https://news.web.nhk/news/easy/<id>/<id>.html   （**免登录可取**）
   ③ 普通版（报纸原文）：https://news.web.nhk/newsweb/na/nd-<id>   （见 --web-try 列表）
   ④ 慢速音声：HLS，需要授权链换 hdnts（见 auth.py + 手册 §5）

🔴 www3.nhk.or.jp 在沙箱里**被 SNI 过滤**（TLS 直接断）⇒ 不要再走 www3。

用法：
    python3 scripts/fetch_nhk.py --list                 # 只列新一期（站点里没有的 id）
    python3 scripts/fetch_nhk.py --all                  # 抓新一期（正文 + 元数据 + 音声 URL）
    python3 scripts/fetch_nhk.py --restore-site         # 从线上站把 site/app 整份取回
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site", "app")
UA = ("Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

EASY_SITEMAP = "https://news.web.nhk/news/easy/sitemap/sitemap.xml"
EASY_URL = "https://news.web.nhk/news/easy/%s/%s.html"
WEB_TRY = [
    "https://news.web.nhk/newsweb/na/nd-%s",
    "https://news.web.nhk/news/article/nd-%s",
    "https://news.web.nhk/newsweb/na/%s",
]


def http(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def strip_html(h):
    h = re.sub(r"<script.*?</script>", "", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", "", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "\n", h)
    h = html.unescape(h)
    return [l.strip() for l in h.split("\n") if l.strip()]


def easy_ids():
    """从 sitemap 抽文章 id（最新在最前）"""
    txt = http(EASY_SITEMAP)
    ids = re.findall(r"/news/easy/(\d{8}de\d+)/", txt)
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def known_ids():
    """站点里已有的 id（从 data/episodes.js 读）"""
    p = os.path.join(SITE, "data", "episodes.js")
    if not os.path.exists(p):
        return set()
    s = open(p, encoding="utf-8").read()
    return set(re.findall(r'"id"\s*:\s*"([^"]+)"', s))


def article_text(eid):
    """易懂版正文 + 标题 + 音声地址"""
    raw = http(EASY_URL % (eid, eid))
    lines = strip_html(raw)
    # 正文：从「〜のニュースです。」这类句子开始，到 consent 表单之前
    body, started = [], False
    for l in lines:
        if "ご利用にあたって" in l or "Copyright NHK" in l:
            break
        if l.startswith("メニュー") or l == "閉じる" or l.startswith("ログイン"):
            continue
        if not started:
            # 标题在 <title> 里，正文首句以「。」「！」结尾且含「です」等
            if len(l) >= 8 and l.endswith(("。", "！", "？")) and "ニュース" not in l[:6]:
                started = True
            else:
                continue
        body.append(l)
    title = ""
    m = re.search(r"<title>([^<]*)</title>", raw)
    if m:
        title = html.unescape(m.group(1)).split("|")[0].strip()
    # 音声地址：从页面里找 voice_uri（.m4a）
    voice = ""
    m = re.search(r"[\w./-]+\.m4a", raw)
    if m:
        voice = m.group(0)
    return {"id": eid, "title": title, "easy_text": body, "voice_uri": voice}


def web_text(eid):
    """普通版（报纸原文）—— 多个 URL 形状试一遍"""
    for tpl in WEB_TRY:
        url = tpl % eid
        try:
            raw = http(url, timeout=25)
        except Exception:
            continue
        if "404" in raw[:4000] and "お探しのページ" in raw:
            continue
        lines = strip_html(raw)
        body = []
        started = False
        for l in lines:
            if "Copyright NHK" in l or "ご利用にあたって" in l:
                break
            if l in ("メニュー", "閉じる") or l.startswith("ログイン"):
                continue
            if not started:
                if len(l) >= 20 and l.endswith(("。", "」", "）")):
                    started = True
                else:
                    continue
            body.append(l)
        if body:
            return {"url": url, "lines": body}
    return None


def cmd_list(args):
    known = known_ids()
    new = [i for i in easy_ids() if i not in known]
    print("站点已有 %d 期；NHK 清单里最新的新 id：" % len(known))
    for i in new[:20]:
        print("  ", i, i[:8])
    if not new:
        print("  （没有新的一期）")
    return 0


def cmd_all(args):
    known = known_ids()
    new = [i for i in easy_ids() if i not in known]
    if args.limit:
        new = new[:args.limit]
    if not new:
        print("✅ 没有新的一期（NHK 只工作日播出）")
        return 0
    for eid in new:
        print("抓 %s …" % eid)
        ep = article_text(eid)
        w = web_text(eid)
        ep["web_text"] = w["lines"] if w else []
        ep["web_url"] = w["url"] if w else ""
        d = os.path.join(DATA, "episodes", eid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "episode.json"), "w", encoding="utf-8") as f:
            json.dump(ep, f, ensure_ascii=False, indent=1)
        print("   标题：%s" % ep["title"])
        print("   易懂版 %d 句 / 普通版 %d 句 / voice_uri=%s"
              % (len(ep["easy_text"]), len(ep["web_text"]), ep["voice_uri"] or "（未找到）"))
    print("✅ 抓到 %d 期 → %s/episodes/" % (len(new), DATA))
    return 0


def cmd_restore_site(args):
    """从线上站把 site/app 整份取回（沙箱重置后用）"""
    import concurrent.futures as cf
    base = args.site.rstrip("/")
    os.makedirs(SITE, exist_ok=True)
    files = ["index.html", "app.js", "style.css", "config.js", "sw.js",
             "manifest.webmanifest", "icon-192.png", "icon-512.png",
             "data/episodes.js", "data/reading.js", "data/patterns.js",
             "data/chains.js", "data/syntax.js", "data/cdn.js"]
    eps = known_ids()
    files += ["subs/%s.js" % i for i in sorted(eps)]
    if not eps:
        # 站点还没有 episodes.js ⇒ 先取它，再补 subs
        for f in files:
            urllib.request.urlretrieve("%s/%s" % (base, f), os.path.join(SITE, f))
        eps = known_ids()
        files += ["subs/%s.js" % i for i in sorted(eps)]
    print("取回 %d 个文件 …" % len(files))
    def one(f):
        dst = os.path.join(SITE, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            urllib.request.urlretrieve("%s/%s" % (base, f), dst)
            return (f, True, os.path.getsize(dst))
        except Exception as e:
            return (f, False, str(e))

    bad = []
    with cf.ThreadPoolExecutor(8) as ex:
        for f, ok, info in ex.map(one, files):
            if not ok:
                bad.append((f, info))
    for f, info in bad:
        print("  ❌ %s：%s" % (f, info))
    # 音频：🔴 不进仓库（59MB）⇒ 按需从线上站整份拉
    if args.with_media:
        mfiles = []
        for e in eps:
            if e.get("audio"):
                mfiles.append(e["audio"])
            if e.get("audioFast"):
                mfiles.append(e["audioFast"])
        print("取回 %d 个音频 …" % len(mfiles))
        with cf.ThreadPoolExecutor(8) as ex:
            for f, ok, info in ex.map(one, mfiles):
                if not ok:
                    bad.append((f, info))
    print("✅ 完成（%d 失败）" % len(bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--restore-site", action="store_true")
    ap.add_argument("--site", default="", help="取回 site/app 用的线上站地址")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--with-media", action="store_true", help="连音频一起取回（默认不取）")
    args = ap.parse_args()
    if args.restore_site:
        if not args.site:
            print("需要 --site <线上站>")
            return 2
        return cmd_restore_site(args)
    if args.all:
        return cmd_all(args)
    return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
