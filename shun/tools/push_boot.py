#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_boot.py —— 推 Shun 的**永久引导页**（shun.json + shun.html）到 GitHub Pages

🔴 为什么必须脚本化：
    mcode 每次部署都换随机域名 ⇒ 对话里给的链接**下次发版就死**
    ⇒ 永久入口只有一个：https://tlv-creator.github.io/nhk-easy-news/shun.html
    ⇒ 里面的 site / audio / apk 每次发版都要更新。

🔴 推的方式（照共享仓库 scripts/update_guide_page.py 的做法）：
    · REST API（沙箱里没有 gh CLI，只有 GH_TOKEN）
    · **先读 sha，PUT 时带上 sha** ⇒ 别人刚推过就 409 ⇒ 重读 + 合并，**不覆盖对方**
    · 推完**回读复核**（Pages 有缓存延迟 ⇒ 复核走 Contents API 源端）

用法：
    python3 tools/push_boot.py --site https://xxx.space.mcode.cn \
        --audio https://xxx.space.mcode.cn/media/ --apk https://xxx.space.mcode.cn/shun.apk \
        --ver 20 --vername 1.2.2 --note "新增 Ep278" --old-site https://bo8u69hdw58t4.space.mcode.cn
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
API = "https://api.github.com/repos/%s/%s/contents/" % (OWNER, REPO)
PAGES = "https://tlv-creator.github.io/nhk-easy-news/"


def api(method, url, token, payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "mavis-shun-boot")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=45) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:200]}


def get_file(path, token):
    st, d = api("GET", API + path, token)
    if st != 200:
        raise SystemExit("❌ 读不到 %s：HTTP %s %s" % (path, st, str(d)[:150]))
    raw = d.get("content", "")
    try:
        text = base64.b64decode(raw).decode("utf-8")
    except Exception:
        text = raw
    return text, d["sha"]


def put_file(path, content, token, sha=None):
    payload = {
        "message": "chore(shun): 更新引导页指向新内容站",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    st, d = api("PUT", API + path, token, payload)
    return st, d


def push_with_retry(path, build, token, tries=4):
    """PUT 409 = 别人刚推过 ⇒ 重读 + 重新生成 + 再推（**不覆盖对方的改动**）"""
    for attempt in range(tries):
        content, sha = get_file(path, token)
        new = build(content)
        st, d = put_file(path, new, token, sha)
        if st in (200, 201):
            return True, d
        if st == 409:
            print("  ↻ 409（有人刚推过）⇒ 重读再推（第 %d 次）" % (attempt + 1),
                  file=sys.stderr)
            time.sleep(2)
            continue
        raise SystemExit("❌ 推 %s 失败：HTTP %s %s" % (path, st, str(d)[:200]))
    raise SystemExit("❌ 推 %s 连续冲突 %d 次，放弃" % (path, tries))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--apk", required=True)
    ap.add_argument("--ver", type=int, required=True)
    ap.add_argument("--vername", required=True)
    ap.add_argument("--note", required=True)
    ap.add_argument("--old-site", default=None,
                    help="旧站域名（要把它从引导页里换掉）")
    ap.add_argument("--updated", default=None)
    args = ap.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise SystemExit("❌ 没有 GH_TOKEN")
    updated = args.updated or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def build_json(_old):
        return json.dumps({
            "ver": args.ver,
            "verName": args.vername,
            "site": args.site,
            "audio": args.audio,
            "apk": args.apk,
            "updated": updated,
            "note": args.note,
        }, ensure_ascii=False, indent=1) + "\n"

    def build_html(old):
        s = old
        # 换所有旧域名（精确 token 替换，不碰别的域）
        # 🔴🔴 2026-11-25 修的真 bug：原来的三元判断**写反了**
        #      旧代码：`pref 带协议 ⇒ 换成不带协议的新地址` / `pref 不带协议 ⇒ 换成带协议的`
        #      ⇒ 结果：href="https://旧站" 被换成 href="新站"（**丢了 https://** ⇒ 变成相对链接 ⇒ 点不开）
        #      且 args.site 末尾的 "/" 和原文的 "/shun.apk" 叠成 "//shun.apk"。
        #    ⇒ 现在：带协议↔带协议、裸域名↔裸域名；新地址统一去掉末尾 "/" 再拼。
        new_full = args.site.rstrip("/")
        if args.old_site:
            old_full = args.old_site.rstrip("/")
            old_bare = old_full.replace("https://", "").replace("http://", "")
            new_bare = new_full.replace("https://", "").replace("http://", "")
            # ① 带协议的完整地址（先换，避免裸域名那步把它再动一次）
            s = s.replace(old_full, new_full)
            # ② 裸域名（展示文本用）
            s = s.replace(old_bare, new_bare)
        s = re.sub(r'href="https://[^"/]+\.space\.mcode\.cn"',
                   'href="%s"' % new_full, s)
        # 🔴 规范化所有指向 mcode 站的 href：**必须带 https://**、**不能有双斜杠**
        #    （shun.html 是用户的永久入口 ⇒ 链接坏掉就是发版事故）
        def _fix_href(m):
            u = m.group(1)
            if "space.mcode.cn" not in u:
                return m.group(0)
            if u.startswith("//"):
                u = "https:" + u
            elif not u.startswith("http"):
                u = "https://" + u
            u = re.sub(r"(space\.mcode\.cn)/+", r"\1/", u)
            return 'href="%s"' % u
        s = re.sub(r'href="([^"]*)"', _fix_href, s)
        s = re.sub(r'(版本\s*<code>)\s*\d+\s*/\s*[\d.]+(\s*</code>)',
                   r'\g<1>%d / %s\g<2>' % (args.ver, args.vername), s)
        s = re.sub(r'(更新于\s*<code>)[^<]*(</code>)',
                   r'\g<1>%s\g<2>' % updated.replace("T", " ").replace("Z", "")[:16],
                   s)
        # 说明行（在「版本 …」那一行下面）
        s = re.sub(r'(版本\s*<code>.*?</code>.*?<br>\s*)',
                   lambda m: m.group(1) + args.note + "<br>\n", s, count=1, flags=re.S)
        return s

    print("== 推 shun.json ==")
    ok, d = push_with_retry("shun.json", build_json, token)
    print("  ✅ %s" % d.get("content", {}).get("html_url", ""))

    print("== 推 shun.html ==")
    ok, d = push_with_retry("shun.html", build_html, token)
    print("  ✅ %s" % d.get("content", {}).get("html_url", ""))

    # 复核：**回源**（Contents API），不是渲染端（Pages 有缓存延迟）
    print("== 回源复核 ==")
    j, _ = get_file("shun.json", token)
    jd = json.loads(j)
    for k in ("ver", "site", "audio", "apk"):
        print("  %-6s %s" % (k, jd.get(k)))
    bad = []
    # 🔴 2026-11-25：原来判据是「site 域名必须出现在 site/audio/apk 三个字段里」
    #    ⇒ **只适配「内容站和音频站同一个域名」的布局**
    #    ⇒ 而手册第 7 节明写「先部署音频站 → 再部署内容站」⇒ 两个域名不同
    #    ⇒ 老判据对**正确的两站布局**必然报「引导页里没换成新站」（假红）。
    #    ⇒ 改成：每个字段各自比对自己的期望值。
    for k, want in (("site", args.site), ("audio", args.audio), ("apk", args.apk)):
        got = str(jd.get(k, "")).rstrip("/")
        if got != want.rstrip("/"):
            bad.append("%s=%s（期望 %s）" % (k, got, want))
    if bad:
        raise SystemExit("❌ 引导页里没换成新站：%s" % bad)
    h, _ = get_file("shun.html", token)
    hs = h
    if args.old_site and args.old_site in hs:
        raise SystemExit("❌ shun.html 里还残留旧站 %s" % args.old_site)
    # 🔴🔴 链接完整性闸门（2026-11-25 加）：shun.html 是**用户的永久入口**
    #    ⇒ 链接坏掉 = 用户点不开 = 发版事故
    #    ⇒ 判据（事实量）：href 里的 mcode 域名必须**带 https://**；域名后不能有 "//"；不能有旧域名残留。
    hrefs = re.findall(r'href="([^"]*)"', hs)
    link_bad = []
    for u in hrefs:
        if "space.mcode.cn" not in u:
            continue
        if not u.startswith("https://"):
            link_bad.append("缺协议：%s" % u)
        if re.search(r"\.space\.mcode\.cn//", u):
            link_bad.append("双斜杠：%s" % u)
    for u in hrefs:
        if args.old_site and args.old_site.replace("https://", "") in u:
            link_bad.append("旧站残留：%s" % u)
    if link_bad:
        raise SystemExit("❌ shun.html 链接有问题：%s" % link_bad)
    print("  链接闸门：%d 个 mcode 链接全部合规 ✅" %
          len([u for u in hrefs if "space.mcode.cn" in u]))
    print("🟢 引导页已更新并复核通过")


if __name__ == "__main__":
    main()
