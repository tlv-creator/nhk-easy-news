#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_snapshot_api.py —— 把项目快照推到共享仓库（**走 GitHub Data API，不走 git push**）

🔴 为什么不用 git push（2026-11-27 实测）：
    沙箱里 git 的 GnuTLS 握手老挂：
      `gnutls_handshake() failed: The TLS connection was non-properly terminated`
      （`git clone` 有时能过、`git push` 基本过不去；curl 则一直正常）
    ⇒ 手册 §38.4 已经写过「git clone 到 github.com TLS 握手失败 ⇒ 用 codeload tar.gz」
    ⇒ 这里补另一半：**推也用 API**（和 scripts/sync_pages.py / update_nhk_guide.py 同一套 GH 客户端）

用法：
    python3 scripts/push_snapshot_api.py --repo-dir /workspace/nhk-easy-news \
        --old <远端已有 commit> --new <本地 commit>
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from sync_pages import GH  # noqa: E402

TOKEN = "/workspace/.secrets/gh_token"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", required=True)
    ap.add_argument("--repo", default="tlv-creator/nhk-easy-news")
    ap.add_argument("--old", required=True, help="远端当前 commit")
    ap.add_argument("--new", required=True, help="本地新 commit")
    ap.add_argument("--message", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tok = open(TOKEN, encoding="utf-8").read().strip()
    gh = GH(tok, a.repo)

    # 🔴 用 -z（NUL 分隔）⇒ 拿到**原始 UTF-8 路径**
    #    （不用 -z 时 git 会把中文名转义成 "nhk/\346\211\213..." ⇒ 文件被跳过）
    out = subprocess.run(["git", "-C", a.repo_dir, "diff", "--name-only", "-z", a.old, a.new],
                         capture_output=True).stdout
    files = [f.decode("utf-8") for f in out.split(b"\0") if f]
    print("要推 %d 个文件" % len(files))
    if a.dry_run:
        for f in files:
            print("   ", f)
        return 0

    entries = []
    for rel in files:
        p = os.path.join(a.repo_dir, rel)
        if not os.path.isfile(p):
            print("   ⚠️ 跳过（不是文件）：%s" % rel)
            continue
        raw = open(p, "rb").read()
        sha = gh.put_blob(raw)
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
        print("   blob %s = %s" % (rel, sha[:8]))

    head = gh.head()
    tree = gh.call("POST", "/git/trees",
                   {"base_tree": gh.base_tree(head),
                    "tree": entries})
    msg = a.message or ("snapshot: %s" % a.new[:8])
    commit = gh.call("POST", "/git/commits",
                     {"message": msg, "tree": tree["sha"], "parents": [head]})
    gh.call("PATCH", "/git/refs/heads/main", {"sha": commit["sha"], "force": False})
    print("✅ 已推送：%s（%d 个文件）" % (commit["sha"][:12], len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
