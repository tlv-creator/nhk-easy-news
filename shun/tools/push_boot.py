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
        if args.old_site:
            for pref in (args.old_site, args.old_site.replace("https://", "")):
                s = s.replace(pref, args.site.replace("https://", "")
                              if pref.startswith("http") else args.site)
        s = re.sub(r'href="https://[^"/]+\.space\.mcode\.cn"',
                   'href="%s"' % args.site, s)
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
    bad = [k for k in ("site", "audio", "apk") if args.site not in str(jd.get(k, ""))]
    if bad:
        raise SystemExit("❌ 引导页里没换成新站：%s" % bad)
    h, _ = get_file("shun.html", token)
    hs = h
    if args.old_site and args.old_site in hs:
        raise SystemExit("❌ shun.html 里还残留旧站 %s" % args.old_site)
    print("🟢 引导页已更新并复核通过")


if __name__ == "__main__":
    main()
