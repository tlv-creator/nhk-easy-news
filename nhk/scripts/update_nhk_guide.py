#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_nhk_guide.py —— 推 NHK 引导页（index.html + version.json）到 GitHub Pages

🔴 引导页是**地址永久不变**的那一页：https://tlv-creator.github.io/nhk-easy-news/
   App 每次启动读它的 version.json 拿当前内容站 ⇒ 域名换了不用用户操心。

🔴 **用 Git Data API（blob→tree→commit→update ref）一次提交两个文件**：
   逐文件用 Contents API PUT 会**连续触发两次 Pages build** ⇒ 新 build 取消上一个
   ⇒ 引导页永远 built 不起来。（这是踩过的坑，别改回逐文件 PUT。）

🔴 仓库是**公开**的 ⇒ 这里只放 version.json + index.html，
   **绝对不能放凭据/学号/密码**。version.json 里只有站点地址和版本号。

🔴 version.json 的三个字段语义不同，别混：
   ver    = 站点内容版本（内容涨得比 APK 快）
   verName= 给用户看的版本名
   apkVer = 站上 APK 的真实 versionCode（App 判断「有没有新 APK」**只能用它**）
   ⇒ 用 ver 去比 APK 的 APP_CODE ⇒ 判据恒真 ⇒ 用户被「發現新版本」死循环提示。

用法：
    python3 scripts/update_nhk_guide.py --site <新站根> --ver 24 --ver-name 1.9 \
        --apk-ver 20 --note "说明" --date 2026-10-24 [--push]
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
API = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)
HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, "guide_tpl")


def api(method, url, token, payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "nhk-guide-updater")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=45) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def read_b64(path):
    return base64.b64encode(open(path, "rb").read()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="新内容站根（https://xxx.space.mcode.cn）")
    ap.add_argument("--ver", required=True, help="站点内容版本（整数）")
    ap.add_argument("--ver-name", default="", help="给用户看的版本名，如 1.9")
    ap.add_argument("--apk-ver", default="", help="站上 APK 的真实 versionCode")
    ap.add_argument("--note", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if a.push and not token:
        print("❌ 没有 GH_TOKEN")
        return 2

    site = a.site.rstrip("/")
    vj = {
        "ver": int(a.ver),
        "verName": a.ver_name or str(a.ver),
        "apkVer": int(a.apk_ver) if a.apk_ver else None,
        "site": site,
        "apk": "%s/NHK.apk" % site,
        "note": a.note,
        "updated": a.date or time.strftime("%Y-%m-%d"),
    }
    if vj["apkVer"] is None:
        vj.pop("apkVer")
    tpl = os.path.join(TPL_DIR, "index.html")
    if not os.path.exists(tpl):
        print("❌ 缺引导页模板：%s" % tpl)
        return 2
    html = open(tpl, encoding="utf-8").read()

    print("将要推送的 version.json：")
    print(json.dumps(vj, ensure_ascii=False, indent=1))
    if not a.push:
        print("（dry-run，没加 --push）")
        return 0

    # 1) 取当前 ref
    st, ref = api("GET", "%s/git/ref/heads/main" % API, token)
    if st != 200:
        print("❌ 取 ref 失败：%s" % ref)
        return 1
    head = ref["object"]["sha"]
    st, c = api("GET", "%s/git/commits/%s" % (API, head), token)
    base_tree = c["tree"]["sha"]

    # 2) 两个 blob
    blobs = {}
    for path, content in (("version.json", json.dumps(vj, ensure_ascii=False, indent=1) + "\n"),
                          ("index.html", html)):
        b = base64.b64encode(content.encode("utf-8")).decode()
        st, r = api("POST", "%s/git/blobs" % API, token, {"content": b, "encoding": "base64"})
        if st not in (200, 201):
            print("❌ 建 blob 失败 %s：%s" % (path, r))
            return 1
        blobs[path] = r["sha"]
        print("  blob %s = %s" % (path, r["sha"][:8]))

    # 3) 一个 tree（🔴 两个文件一个 tree ⇒ 一次 commit ⇒ 只触发一次 Pages build）
    st, tree = api("POST", "%s/git/trees" % API, token, {
        "base_tree": base_tree,
        "tree": [{"path": p, "mode": "100644", "type": "blob", "sha": s}
                 for p, s in blobs.items()]})
    if st not in (200, 201):
        print("❌ 建 tree 失败：%s" % tree)
        return 1

    # 4) 一个 commit
    st, cm = api("POST", "%s/git/commits" % API, token, {
        "message": "update: site=%s ver=%s apkVer=%s" % (site, a.ver, a.apk_ver),
        "tree": tree["sha"], "parents": [head]})
    if st not in (200, 201):
        print("❌ 建 commit 失败：%s" % cm)
        return 1

    # 5) 推 ref
    st, r = api("PATCH", "%s/git/refs/heads/main" % API, token,
                {"sha": cm["sha"], "force": False})
    if st not in (200, 201):
        print("❌ 推 ref 失败：%s" % r)
        return 1
    print("✅ 已推送引导页：commit %s" % cm["sha"][:8])
    return 0


if __name__ == "__main__":
    sys.exit(main())
