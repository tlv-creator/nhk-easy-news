#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复引导页仓库（误删后用）。

做四件事：
  1. 建仓库 tlv-creator/nhk-easy-news（public）
  2. 写 version.json + index.html
  3. 开 GitHub Pages（首次用 POST，PUT 是"更新已存在"会 404）
  4. 验证
"""
import os, sys, json, base64, time, urllib.request, urllib.error

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
BRANCH = "main"
API = "https://api.github.com"
BOOT_DIR = "/workspace/nhk/boot"
SITE = "https://p4h4kqklfb66t.space.mcode.cn/"
VER, VERNAME = 6, "1.6"


def token():
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    p = "/workspace/.secrets/gh_token"
    if os.path.exists(p):
        return open(p).read().strip()
    sys.exit("no token")


def api(method, path, body=None, raw=False):
    t = token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + t)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "nhk-restore")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read().decode()
            return b if raw else (json.loads(b) if b.strip() else {})
    except urllib.error.HTTPError as e:
        return {"__err": e.code, "__msg": e.read().decode()[:300]}


def b64(s):
    return base64.b64encode(s.encode()).decode()


def put_file(path, content, msg):
    sha = None
    g = api("GET", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path))
    if isinstance(g, dict) and g.get("sha"):
        sha = g["sha"]
    body = {"message": msg, "content": b64(content), "branch": BRANCH}
    if sha:
        body["sha"] = sha
    r = api("PUT", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path), body)
    if isinstance(r, dict) and r.get("__err"):
        print("  ✗ put %s → %s %s" % (path, r["__err"], r.get("__msg", "")[:160]))
        return False
    print("  ✓ %s" % path)
    return True


print("① 建仓库")
r = api("POST", "/user/repos", {
    "name": REPO, "private": False, "auto_init": True,
    "description": "NHK やさしいことばニュース · 精读 App 引导页（version.json + index.html）",
})
if isinstance(r, dict) and r.get("__err"):
    if r["__err"] == 422:
        print("  · 已存在（422），跳过")
    else:
        print("  ✗ %s %s" % (r["__err"], r.get("__msg", "")[:200])); sys.exit(1)
else:
    print("  ✓ 已创建：%s（public=%s）" % (r.get("full_name"), not r.get("private")))
    time.sleep(2)

print("② 写文件")
cfg = {
    "ver": VER, "verName": VERNAME, "site": SITE,
    "apk": SITE.rstrip("/") + "/NHK.apk",
    "note": "32期 · 每日21:00自动更新",
    "updated": time.strftime("%Y-%m-%d"),
}
put_file("version.json", json.dumps(cfg, ensure_ascii=False, indent=2), "update version.json")
idx = open(os.path.join(BOOT_DIR, "index.html"), encoding="utf-8").read()
put_file("index.html", idx, "restore index.html")

print("③ 开 Pages")
p = api("GET", "/repos/%s/%s/pages" % (OWNER, REPO))
if isinstance(p, dict) and p.get("__err"):
    r = api("POST", "/repos/%s/%s/pages" % (OWNER, REPO),
            {"source": {"branch": BRANCH, "path": "/"}})
    if isinstance(r, dict) and r.get("__err"):
        print("  ✗ POST pages → %s %s" % (r["__err"], r.get("__msg", "")[:200]))
    else:
        print("  ✓ POST 建 Pages 成功，status=%s" % r.get("status"))
else:
    r = api("PUT", "/repos/%s/%s/pages" % (OWNER, REPO),
            {"source": {"branch": BRANCH, "path": "/"}})
    print("  ✓ PUT 更新 Pages，status=%s" % (r.get("status") if isinstance(r, dict) else "?"))

print("④ 等待构建 + 验证")
for i in range(24):
    time.sleep(10)
    s = api("GET", "/repos/%s/%s/pages" % (OWNER, REPO))
    st = s.get("status") if isinstance(s, dict) else None
    try:
        import urllib.request as U
        with U.urlopen(U.Request("https://tlv-creator.github.io/nhk-easy-news/version.json",
                                 headers={"Cache-Control": "no-cache"}), timeout=15) as f:
            live = json.loads(f.read().decode())
        print("  ✓ 线上 version.json:", json.dumps(live, ensure_ascii=False))
        if live.get("site") == SITE:
            print("  ✅ 引导页恢复正常，site 指向正确")
            break
    except Exception as e:
        pass
    print("  · pages status=%s，等待中 (%d/24)" % (st, i + 1))
else:
    print("  ⚠ 等待超时，稍后再查")
