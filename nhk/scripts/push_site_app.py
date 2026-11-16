#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_site_app.py —— 把 site/app 整份推到共享仓库（**一次 commit = 一次 Pages build**）

🔴🔴 为什么不能用 `scripts/push_files.py` 推 site/app：
    `push_files.py` 是**逐文件** put（每个文件一个 commit）
    ⇒ 连着推 200 多个文件 ⇒ **连着触发 200 多次 Pages build**
    ⇒ 手册「坑 1」：**新 build 会取消上一个未完成的** ⇒ 站点永远 built 不起来
    ⇒ 2026-11-16 实测：逐文件推完 15 个文件 ⇒ Pages status = **errored**
    ⇒ 所以这里：**把所有文件凑成一个 tree，一次 commit**（同 sync_pages.py 的做法）

用法：
    python3 scripts/push_site_app.py [--dry-run]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sync_pages as SP  # noqa: E402

SITE = os.path.join(ROOT, "site", "app")
TOKEN = "/workspace/.secrets/gh_token"
COPY_FILES = ["index.html", "app.js", "style.css", "config.js", "sw.js",
              "manifest.webmanifest", "icon-192.png", "icon-512.png", "NHK.apk",
              "version.json"]
COPY_DIRS = ["data", "subs", "media"]


def collect():
    files = {}
    for f in COPY_FILES:
        p = os.path.join(SITE, f)
        if os.path.isfile(p):
            files["nhk/site/app/%s" % f] = p
    for d in COPY_DIRS:
        root = os.path.join(SITE, d)
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            for f in fn:
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, root).replace("\\", "/")
                files["nhk/site/app/%s/%s" % (d, rel)] = p
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    token = open(TOKEN).read().strip()
    gh = SP.GH(token, SP.REPO)
    files = collect()
    total = sum(os.path.getsize(p) for p in files.values())
    print("待推 %d 个文件（%.1f MB）" % (len(files), total / 1048576))
    if a.dry_run:
        for k in sorted(files)[:10]:
            print("   ", k)
        return 0
    entries = []
    for dest, local in sorted(files.items()):
        raw = open(local, "rb").read()
        rsha = gh.remote_blob(dest)
        lsha = SP.blob_sha(raw)
        if rsha == lsha:
            continue
        entries.append({"path": dest, "sha": gh.put_blob(raw)})
        print("   ~ %s (%.1f KB)" % (dest, len(raw) / 1024))
    if not entries:
        print("线上已是最新，无需提交")
        return 0
    sha = gh.push(entries, "NHK 源码站同步：%d 个文件（一次 commit = 一次 build）" % len(entries))
    print("✅ 已提交 %s（%d 个文件）⇒ 触发一次 Pages build" % (sha[:12], len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
