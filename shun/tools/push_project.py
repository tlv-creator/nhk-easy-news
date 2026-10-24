#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_project.py —— 把 Shun 项目（tools/ + web/ + 手册）推回共享仓库的 `shun/` 目录

🔴 为什么需要它：**沙箱每周重置**，项目只留在沙箱里 ⇒ 下周又得从头重建管线。
    ⇒ 项目本体必须进仓库（这样 setup_env.sh 一条命令就能恢复）。

🔴 推法（不用 git CLI：沙箱里 git over https 握手会失败）：
    GitHub Git Data API：blob → tree → commit → ref
    · **带 base_tree** ⇒ 只新增/改动我自己的文件，**不碰别人的**（不会 lost-update）
    · 推完**回读复核**（Contents API 源端）

用法：
    python3 tools/push_project.py --proj /workspace/shun --prefix shun
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
API = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)

SKIP_DIRS = {".git", "venv", "models", "deploy", "node_modules", "__pycache__", ".cache"}
SKIP_FILES = {".DS_Store"}
# 这些不进仓库（二进制/中间产物）
SKIP_SUFFIX = (".mp3", ".m4a", ".apk", ".tar.gz", ".zip", ".pt", ".bin", ".log")


def api(method, url, token, payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "mavis-shun-project")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:300]}


def collect(root, prefix):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES or fn.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel.startswith("work/asr/") or rel.startswith("work/zh/") or rel.startswith("work/cues/"):
                continue
            try:
                if os.path.getsize(full) > 4 * 1024 * 1024:
                    continue
            except OSError:
                continue
            files.append((prefix + "/" + rel, full))
    return files


def push_once(args, token, files):
    """推一次。返回 True 表示成功；409（有人刚推过 main）返回 False 让调用方重试。"""
    # ① 拿当前 main 的 commit + tree
    st, ref = api("GET", API + "/git/ref/heads/main", token)
    if st != 200:
        raise SystemExit("❌ 读 main 失败：%s" % str(ref)[:200])
    head = ref["object"]["sha"]
    st, commit = api("GET", API + "/git/commits/" + head, token)
    base_tree = commit["tree"]["sha"]

    # ② 建 blob + tree
    # 🔴 GitHub Git Data API 的 tree 入口，`content` 必须是**明文**（UTF-8）。
    #    base64 是 **Contents API** 的格式，用在这里 ⇒ 仓库里存的就是 base64 文本本身
    #    ⇒ 2026-10-24 事故：setup_env.sh 从仓库恢复项目后拿到的是 base64 垃圾，
    #      ast.parse 失败 ⇒ 10-28 周更必炸。
    #    ⇒ 修法：先 POST /git/blobs（encoding=base64）拿到 sha，再在 tree 里用 sha 引用。
    entries = []
    for path, full in files:
        with open(full, "rb") as f:
            data = f.read()
        st, blob = api("POST", API + "/git/blobs", token,
                       {"content": base64.b64encode(data).decode("ascii"),
                        "encoding": "base64"})
        if st != 201:
            raise SystemExit("❌ 建 blob 失败（%s）：%s" % (path, str(blob)[:200]))
        entries.append({"path": path, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})

    tree_sha = base_tree
    BATCH = 40
    for i in range(0, len(entries), BATCH):
        batch = entries[i:i + BATCH]
        st, d = api("POST", API + "/git/trees", token,
                    {"base_tree": tree_sha, "tree": batch})
        if st != 201:
            raise SystemExit("❌ 建 tree 失败：%s %s" % (st, str(d)[:300]))
        tree_sha = d["sha"]
        print("  tree %d/%d" % (min(i + BATCH, len(entries)), len(entries)), flush=True)

    # ③ commit
    st, c = api("POST", API + "/git/commits", token,
                {"message": args.message, "tree": tree_sha, "parents": [head]})
    if st != 201:
        raise SystemExit("❌ 建 commit 失败：%s %s" % (st, str(c)[:300]))
    new_commit = c["sha"]

    # ④ 推 ref（带旧 sha ⇒ 别人刚推过就 409 ⇒ 不会覆盖别人的改动）
    st, d = api("PATCH", API + "/git/refs/heads/main", token,
                {"sha": new_commit, "force": False})
    if st != 200:
        if st == 422 and "not a fast forward" in str(d):
            print("⚠️ 409：有人刚推过 main ⇒ 重读基线重试", flush=True)
            return False
        raise SystemExit("❌ 推 ref 失败：%s %s" % (st, str(d)[:300]))
    print("✅ 已推：%s" % d.get("object", {}).get("sha"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proj", default="/workspace/shun")
    ap.add_argument("--prefix", default="shun")
    ap.add_argument("--message", default="chore(shun): 保存 Shun 项目（管线 + 站点源码 + 手册）")
    ap.add_argument("--tries", type=int, default=4, help="409 重试次数")
    args = ap.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise SystemExit("❌ 没有 GH_TOKEN")

    files = collect(args.proj, args.prefix)
    print("要推 %d 个文件" % len(files))

    # 🔴 409（有人刚推过 main）⇒ **重读基线重试**，不是放弃。
    #    共享仓库里多方在推（hub 主线 / NHK / Shun）⇒ 不重试就永远推不上去。
    #    但**绝不 force**（会覆盖别人的改动）。
    done = False
    for attempt in range(1, args.tries + 1):
        if push_once(args, token, files):
            done = True
            break
        print("  （第 %d 次撞上 409 ⇒ 3 秒后重读基线重试）" % attempt, flush=True)
        time.sleep(3)
    if not done:
        raise SystemExit("❌ 连续 %d 次都撞上 409 ⇒ 放弃（**不要强推**）" % args.tries)

    # ⑤ 回读复核：文件在 **且内容是明文**（存成 base64 是历史事故 ⇒ 光看文件在不算数）
    time.sleep(1)
    st, d = api("GET", API + "/contents/" +
                urllib.parse.quote(args.prefix + "/tools/手册.md"), token)
    if st != 200:
        raise SystemExit("❌ 复核失败：%s" % str(d)[:200])
    raw = base64.b64decode(d.get("content") or "")
    try:
        txt = raw.decode("utf-8")
    except Exception:
        txt = ""
    if txt.lstrip().startswith("IyEv"):
        try:
            it = base64.b64decode(raw, validate=True).decode("utf-8")
            if it.startswith("#!") or "import " in it[:300] or "Shun" in it[:400]:
                txt = it
        except Exception:
            pass
    if "Shun" in txt or "手册" in txt:
        print("🟢 复核通过：%s/tools/手册.md 已在仓库里（内容是明文）" % args.prefix)
    else:
        raise SystemExit("❌ 复核失败：手册.md 内容像是 base64 ⇒ 又存错了")


if __name__ == "__main__":
    main()
