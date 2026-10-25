#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
push_many.py —— 批量推送到 tlv-creator/nhk-easy-news（**一次提交 = 一次 Pages build**）

🔴🔴 为什么不用 push_files.py：
    它走 Contents API **逐文件 PUT** ⇒ 推 30 个文件 = 连续触发 30 次 build
    ⇒ 新 build 取消上一个未完成的 ⇒ **站点永远 built 不起来**（这个坑我踩过）
    ⇒ 必须走 Git Data API：blob → tree → commit → update ref，**一次提交**

🔴 纪律（继承共享仓库所有教训）：
  ① 基线永远取**线上最新 HEAD**（不拿本地副本）⇒ 别人刚推的改动不会被覆盖
  ② **只推真正变了的文件**（逐个比 blob sha）⇒ 少推 = 少冲突 = 快
  ③ 推完**回读逐字对账**（不是只看 API 返回 ok）
  ④ 单文件 < 100MB（GitHub 硬限制）

用法：
    python3 push_many.py --root nhk/scripts            # 推整个目录（排除 __pycache__ 等）
    python3 push_many.py nhk/scripts/a.py nhk/scripts/b.py   # 推指定文件（保持相对仓库根的路径）
    python3 push_many.py --dry-run --root nhk/scripts
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
BRANCH = "main"
API = "https://api.github.com"
TOK_PATH = "/workspace/.secrets/gh_token"

EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", "media", "models", ".cache",
                # 🔴 Android SDK 解包产物 + 官方 jar（82MB / 2600+ 文件）
                #    ⇒ 绝不能进 GitHub：单文件 100MB 硬限制 + 仓库体积 + 毫无意义
                "jars", "tmp-29", "android-10"}
EXCLUDE_SUFFIX = (".pyc", ".orig", ".bak", ".zip", ".apk", ".m4a", ".mp3", ".gz", ".tar", ".jar")
EXCLUDE_NAMEPART = ("tmp-",)


def tok():
    with open(TOK_PATH) as f:
        return f.read().strip()


def api(method, path, body=None, raw=False):
    """🔴 用 curl 不用 urllib —— 沙箱里 urllib 连 api.github.com 会被网关打成 400
    🔴 body 走临时文件 + curl -d @file（大 body 塞 argv 会 Argument list too long）"""
    url = API + path
    cmd = ["curl", "-s", "-m", "180", "-X", method, url,
           "-H", "Authorization: Bearer " + tok(),
           "-H", "Accept: application/vnd.github+json"]
    tmp = None
    try:
        if body is not None:
            tmp = "/workspace/.push_tmp/body_%d.json" % int(time.time() * 1000)
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(body if isinstance(body, bytes)
                        else json.dumps(body).encode("utf-8"))
            cmd += ["-H", "Content-Type: application/json", "--data-binary", "@" + tmp]
        p = subprocess.run(cmd, capture_output=True)
        if raw:
            return p.stdout
        try:
            return json.loads(p.stdout.decode("utf-8", "replace"))
        except Exception:
            return {"_raw": p.stdout[:400].decode("utf-8", "replace"),
                    "_rc": p.returncode, "_stderr": p.stderr[:200].decode("utf-8", "replace")}
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def collect(paths, root=None):
    """列出要推的 (仓库相对路径, 本地绝对路径)

    目录模式：dest = root + 目录内相对路径（仓库根为基准）
    文件模式：dest = 给的路径本身；若给了 root，则 dest = root/文件名
    """
    out = []
    for p in paths:
        if os.path.isdir(p):
            prefix = root if root else p.rstrip("/")
            for dp, dns, fns in os.walk(p):
                dns[:] = [d for d in dns
                          if d not in EXCLUDE_DIRS and not any(x in d for x in EXCLUDE_NAMEPART)]
                for fn in fns:
                    if fn.endswith(EXCLUDE_SUFFIX):
                        continue
                    ap = os.path.join(dp, fn)
                    rel = os.path.relpath(ap, p)
                    out.append((os.path.join(prefix, rel) if rel != "." else prefix, ap))
        else:
            dest = os.path.join(root, os.path.basename(p)) if root else p
            out.append((dest, p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--root", help="目录模式下的仓库前缀（如 nhk/scripts）")
    ap.add_argument("--message", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.paths and not a.root:
        print("❌ 至少给一个路径")
        return 1

    # 1) 线上基线：一次拿整棵树（含所有 blob sha）
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    if "object" not in ref:
        print("❌ 拿不到 HEAD：", json.dumps(ref)[:300])
        return 1
    head_commit = ref["object"]["sha"]
    cm = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{head_commit}")
    base_tree = cm["tree"]["sha"]
    tree_full = api("GET", f"/repos/{OWNER}/{REPO}/git/trees/{base_tree}?recursive=1")
    remote = {}
    if "tree" in tree_full:
        for e in tree_full["tree"]:
            if e.get("type") == "blob":
                remote[e["path"]] = e["sha"]
    print("基线 HEAD = %s  tree = %s  （线上 blob %d 个）"
          % (head_commit[:10], base_tree[:10], len(remote)))

    # 2) 收集 + 比对（只推真的变了的）
    pairs = []
    if a.root:
        pairs += collect([a.root], root=a.root)
    for p in a.paths:
        pairs += collect([p], root=a.root if a.root else None)
    # 去重
    seen = {}
    for dest, src in pairs:
        seen[dest] = src
    pairs = sorted(seen.items())

    changed, same = [], 0
    for dest, src in pairs:
        if not os.path.isfile(src):
            print("  ⚠️ 缺文件，跳过：%s" % src)
            continue
        data = open(src, "rb").read()
        if len(data) > 100 * 1024 * 1024:
            print("  ❌ %s = %.1fMB > 100MB 硬限制" % (dest, len(data) / 1048576))
            continue
        bs = blob_sha(data)
        if remote.get(dest) == bs:
            same += 1
            continue
        changed.append((dest, src, data, bs))

    print("候选 %d 个：未变 %d，**要推 %d**" % (len(pairs), same, len(changed)))
    for dest, src, data, bs in changed:
        print("   ~ %-52s %8d B" % (dest, len(data)))

    if not changed:
        print("✅ 无差异，不提交")
        return 0
    if a.dry_run:
        print("（dry-run，未提交）")
        return 0

    # 3) 建 blob
    entries = []
    for dest, src, data, bs in changed:
        r = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if "sha" not in r:
            print("❌ 建 blob 失败：%s -> %s" % (dest, json.dumps(r)[:200]))
            return 1
        if r["sha"] != bs:
            print("❌ blob sha 对不上（本地算错）：%s" % dest)
            return 1
        entries.append({"path": dest, "mode": "100644", "type": "blob", "sha": r["sha"]})

    # 4) 基于**最新 base_tree** 建新 tree（别人的改动天然保留）
    r = api("POST", f"/repos/{OWNER}/{REPO}/git/trees",
            {"base_tree": base_tree, "tree": entries})
    if "sha" not in r:
        print("❌ 建 tree 失败：", json.dumps(r)[:300])
        return 1
    new_tree = r["sha"]

    # 5) commit
    msg = a.message or ("chore: 补齐 NHK 日更收尾闸门脚本 + 刷新 site/app 到 v32（%d 个文件）" % len(entries))
    r = api("POST", f"/repos/{OWNER}/{REPO}/git/commits",
            {"message": msg, "tree": new_tree, "parents": [head_commit]})
    if "sha" not in r:
        print("❌ 建 commit 失败：", json.dumps(r)[:300])
        return 1
    new_commit = r["sha"]

    # 6) update ref（期间有人推了就重取 HEAD 重建）
    for attempt in range(1, 4):
        ref2 = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
        cur = ref2.get("object", {}).get("sha")
        if cur != head_commit:
            print("⚠️ 期间有人推了（HEAD %s → %s）⇒ 重取基线重建" % (head_commit[:8], str(cur)[:8]))
            # 以新 HEAD 重建：只重算 tree/commit，条目不变（基于新 base_tree）
            cm2 = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{cur}")
            base_tree = cm2["tree"]["sha"]
            head_commit = cur
            r = api("POST", f"/repos/{OWNER}/{REPO}/git/trees",
                    {"base_tree": base_tree, "tree": entries})
            new_tree = r["sha"]
            r = api("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                    {"message": msg, "tree": new_tree, "parents": [head_commit]})
            new_commit = r["sha"]
            continue
        r = api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", {"sha": new_commit})
        if "object" in r:
            print("✅ 已提交 %s（%d 个文件，一次 commit = 一次 build）" % (new_commit[:10], len(entries)))
            break
        print("  重试 %d：%s" % (attempt, json.dumps(r)[:160]))
        time.sleep(2)
    else:
        print("❌ 推 ref 失败")
        return 1

    # 7) 回读逐字对账
    bad = 0
    for dest, src, data, bs in changed:
        rr = api("GET", f"/repos/{OWNER}/{REPO}/contents/{dest}?ref={BRANCH}")
        if "content" not in rr:
            print("  ❌ 回读失败：%s" % dest)
            bad += 1
            continue
        got = base64.b64decode(rr["content"])
        if got != data:
            print("  ❌ 内容不一致：%s" % dest)
            bad += 1
    print("回读对账：%d/%d 一致%s" % (len(changed) - bad, len(changed), "" if not bad else "  ❌"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
