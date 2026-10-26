#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「引导页」推到 GitHub Pages（固定域名）→ App 每次启动读它 → 自动跳到最新内容站。

用法：
    export GH_TOKEN=ghp_xxx            # 或 GITHUB_TOKEN
    python3 push_boot.py --site <内容站URL> --apk <APK地址> --ver 5 --note "33期"
    python3 push_boot.py --check        # 只看状态，不推送

凭据来源：环境变量 GH_TOKEN / GITHUB_TOKEN
仓库：tlv-creator/nhk-easy-news（必须是 public —— GitHub Pages 免费版不支持 private）
⚠️ 这个仓库里只放 version.json + index.html，绝不能放任何凭据或学号。
"""
import os
import sys
import json
import base64
import re
import urllib.request
import urllib.error
import datetime

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
BRANCH = "main"
API = "https://api.github.com"
BOOT_DIR = "/workspace/nhk/boot"
# 🔴 引导页（永久锚点）：内容站域名每次部署都变，只有这个地址永远不变。
#    站内 version.json 的 site 字段指向它 → 打破「域名要部署后才知道」的死循环。
BOOT_PAGE = "https://tlv-creator.github.io/nhk-easy-news/"


def token():
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    return None


def api(method, path, body=None):
    t = token()
    if not t:
        sys.exit("❌ 没有 token：请 export GH_TOKEN=ghp_xxx")
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + t)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "nhk-easy-news-updater")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read().decode()
            # 🔴 204 No Content（.nojekyll 这类空文件）→ 不能 json.loads，否则崩
            return json.loads(b) if b.strip() else {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        sys.exit("❌ GitHub API %s %s → %s\n%s" % (method, path, e.code, detail))


def put_file(path, content, message):
    """新增或覆盖文件（覆盖要先拿 sha）"""
    sha = None
    try:
        cur = api("GET", "/repos/%s/%s/contents/%s?ref=%s" % (OWNER, REPO, path, BRANCH))
        sha = cur.get("sha")
    except SystemExit:
        pass
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = api("PUT", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path), body)
    return r.get("content", {}).get("html_url", "")


def ensure_pages():
    try:
        p = api("GET", "/repos/%s/%s/pages" % (OWNER, REPO))
        print("   Pages 已开启：%s  status=%s" % (p.get("html_url"), p.get("status")))
        return
    except SystemExit:
        pass
    try:
        api("PUT", "/repos/%s/%s/pages" % (OWNER, REPO),
            {"source": {"branch": BRANCH, "path": "/"}})
        print("   ✅ 已请求开启 Pages（GitHub 首次构建要 1–3 分钟）")
    except SystemExit as e:
        print("   ⚠️ 开 Pages 失败：%s（可以稍后在仓库 Settings→Pages 手动开）" % e)



def ensure_built(tries=20, wait=9):
    """等 Pages 真的 built。

    🔴 坑：每次 PUT 文件都会触发一个新 build，**把上一个还没跑完的 build 取消掉**
    （Actions 里 conclusion=cancelled，Pages 记成 errored）。
    所以这里【只轮询等待】，不要反复 PUT 触发重建 —— 那会造成连环取消。
    真的连续 errored 两次，才 PUT 一次重建，然后继续等。
    """
    import time
    retriggered = False
    for i in range(tries):
        p = api("GET", "/repos/%s/%s/pages" % (OWNER, REPO))
        st = p.get("status") if isinstance(p, dict) else None
        if st == "built":
            return True
        if st == "errored" and not retriggered:
            # 只触发一次，且给正在跑的 build 一点时间收尾
            time.sleep(wait)
            api("PUT", "/repos/%s/%s/pages" % (OWNER, REPO),
                {"source": {"branch": BRANCH, "path": "/"}})
            retriggered = True
        time.sleep(wait)
    return False


def main():
    args = sys.argv[1:]
    if "--check" in args:
        print("== 仓库状态 ==")
        r = api("GET", "/repos/%s/%s" % (OWNER, REPO))
        print("   %s  private=%s  default=%s" % (r.get("full_name"), r.get("private"), r.get("default_branch")))
        for f in ("version.json", "index.html"):
            try:
                c = api("GET", "/repos/%s/%s/contents/%s?ref=%s" % (OWNER, REPO, f, BRANCH))
                print("   ✓ %s  sha=%s" % (f, (c.get("sha") or "")[:10]))
            except SystemExit:
                print("   ✗ %s  不存在" % f)
        try:
            p = api("GET", "/repos/%s/%s/pages" % (OWNER, REPO))
            print("   Pages: %s  status=%s" % (p.get("html_url"), p.get("status")))
        except SystemExit:
            print("   Pages: 未开启")
        return

    site = None
    apk = None
    ver = None
    vername = None
    apkver = None
    note = ""
    for i, a in enumerate(args):
        if a == "--site" and i + 1 < len(args):
            site = args[i + 1]
        if a == "--apk" and i + 1 < len(args):
            apk = args[i + 1]
        if a == "--ver" and i + 1 < len(args):
            ver = int(args[i + 1])
        if a == "--note" and i + 1 < len(args):
            note = args[i + 1]
        if a == "--vername" and i + 1 < len(args):
            vername = args[i + 1]
        # 🔴 站上 APK 的 versionCode（2026-10-23 新增）
        #   App 判断「有没有新 APK」只能用这个字段（apkVer），不能用 ver（站点内容版本）
        #   ⇒ 详见 apk/app/src/com/nhk/easynews/Bootstrap.java 的 Info.apkVer 注释
        if a == "--apk-ver" and i + 1 < len(args):
            apkver = int(args[i + 1])

    if not site:
        # 没给就沿用站里的 version.json
        cfg = json.load(open(BOOT_DIR + "/version.json", encoding="utf-8"))
        site = cfg["site"]
        apk = apk or cfg.get("apk")
        ver = ver or cfg.get("ver")
        vername = vername or cfg.get("verName")
        apkver = apkver if apkver is not None else cfg.get("apkVer")
        note = note or cfg.get("note", "")

    # 🔴 没给 apkVer ⇒ 从**站上那份 APK** 里读真实的 versionCode（aapt2 dump badging）
    #   ⇒ 宁可用工具读事实，也不要手填（手填就会漂）
    if apkver is None:
        try:
            import subprocess
            guess = None
            for cand in ("/workspace/nhk/site/app/NHK.apk",
                         "/workspace/nhk/apk/out/signed.apk"):
                if os.path.exists(cand):
                    out = subprocess.run(["/opt/android/android-14/aapt2", "dump", "badging", cand],
                                         capture_output=True, text=True, timeout=60).stdout
                    m = re.search(r"versionCode='(\d+)'", out)
                    if m:
                        guess = int(m.group(1))
                        break
            apkver = guess or 0
        except Exception:
            apkver = 0

    # 🔴 站內 APK 檔名固定叫 NHK.apk；傳進來的 --apk 如果檔名不對，一律換成 NHK.apk
    #    （舊版 cron prompt 傳的是帶版本號的檔名，會 404）
    if apk and not apk.endswith("/NHK.apk"):
        apk = site.rstrip("/") + "/NHK.apk"
        print("   → 統一改用站內固定檔名:", apk)

    cfg = {
        "ver": ver or 1,
        "verName": vername or "1.4",
        "apkVer": apkver or 0,
        "site": site,
        "apk": apk or "",
        "note": note,
        "updated": datetime.date.today().isoformat(),
    }
    msg = "update: site=%s ver=%s apkVer=%s" % (site, cfg["ver"], cfg["apkVer"])
    print("== 推送引导页 ==")
    print("   site   =", site)
    print("   ver    =", cfg["ver"], "(站点内容版本)")
    print("   apkVer =", cfg["apkVer"], "(站上 APK 的 versionCode —— App 用它判断新版本)")
    u1 = put_file("version.json", json.dumps(cfg, ensure_ascii=False, indent=2), msg)

    # 🔴🔴 同步写**站内**的 version.json —— 而且**写两份**：
    #   ① site/app/version.json     —— 本地源码目录
    #   ② deploy_site/version.json  —— 🔴 **真正被部署的那一份**（之前只写 ①，
    #      部署完才发现 deploy_site 里还是上上版的旧值 → 站内 version.json 指向已删的站）
    #
    # 🔴🔴 站内那份的 site 字段 = **引导页（永久锚点）**，不是内容站自己！
    #   理由：内容站的域名是**部署完才知道的** → 如果站内 version.json 指向"自己"，
    #   就成了死循环（要改它就得先部署，部署才有域名）→ 每次发版都要多部署一轮。
    #   指向引导页就打破了循环：引导页 = 唯一稳定地址，它再指向最新内容站。
    #   （这个 site 字段目前没有程序读它 —— App 只取 ver/apk，网页版不读站内 version.json
    #     —— 但留个指向 404 的链接是隐患。）
    site_cfg = dict(cfg)
    site_cfg["site"] = BOOT_PAGE
    site_cfg["apk"] = "NHK.apk"          # 相对路径：跟站绑定，不会指向别的站
    for base in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "app"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy_site")):
        site_vj = os.path.join(base, "version.json")
        try:
            with open(site_vj, "w", encoding="utf-8") as f:
                f.write(json.dumps(site_cfg, ensure_ascii=False, indent=2))
            print("  ✓ 站内 version.json 已同步: %s" % site_vj)
        except Exception as e:
            print("  ⚠️ 站内 version.json 同步失败（不阻断）: %s" % e)

    # 同步写本地副本（之前漏了 → 本地 boot/version.json 长期是旧值，
    # 万一仓库被删/重建，本地就是唯一底稿，必须是最新的）
    try:
        with open(os.path.join(BOOT_DIR, "version.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("  ✓ 本地副本已更新: %s/version.json" % BOOT_DIR)
    except Exception as e:
        print("  ⚠ 本地副本写入失败: %s" % e)

    put_file(".nojekyll", "", "add .nojekyll")
    idx = open(BOOT_DIR + "/index.html", encoding="utf-8").read()
    u2 = put_file("index.html", idx, msg)
    print("   ✓ version.json →", u1)
    print("   ✓ index.html   →", u2)
    ensure_pages()
    if ensure_built():
        print("  ✓ Pages 已 built")
    else:
        print("  ⚠ Pages 未 built（可能是缓存/竞态），稍后再查")
    print("   引导站: https://%s.github.io/%s/" % (OWNER, REPO))


if __name__ == "__main__":
    main()
