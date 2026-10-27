#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /workspace/bsj 的关键产物用 GitHub contents API 推到共享仓库的 bsj/ 目录。"""
import base64, json, os, sys, time, urllib.request, urllib.error

REPO = "tlv-creator/nhk-easy-news"
BRANCH = "main"
ROOT = "/workspace/bsj"
TOKEN = os.environ["GH_TOKEN"]
API = f"https://api.github.com/repos/{REPO}/contents"

SKIP_DIRS = {"__pycache__", "vocab", "_work", "nuxt", ".git"}
SKIP_FILES = {"kanjidic2.xml", "kanjidic2.xml.gz", "apple_ssd.json", "ep737.mp3",
              "rendered.html", "watch.html", "detail.html", "shot.png", "shot_jiten.png",
              "detail.png", "doh_connect.sh", "probe_jiten.py", "probe_sources.py",
              "match_and_probe.py"}

def put(path, data: bytes):
    from urllib.parse import quote
    url = f"{API}/{quote(path)}?branch={BRANCH}"
    payload = {
        "message": f"bsj: {path}",
        "content": base64.b64encode(data).decode(),
        "branch": BRANCH,
    }
    # 已存在的文件必须带 sha
    try:
        from urllib.parse import quote
        g = urllib.request.Request(f"{API}/{quote(path)}?branch={BRANCH}", headers={
            "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
            "User-Agent": "mavis-bsj"})
        with urllib.request.urlopen(g, timeout=60) as r:
            ex = json.loads(r.read().decode())
        if isinstance(ex, dict) and ex.get("sha"):
            payload["sha"] = ex["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "mavis-bsj",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())

def collect():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = [] if rel == "." else rel.split(os.sep)
        if any(p in SKIP_DIRS for p in parts):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES or fn.endswith(".pyc") or fn.endswith(".log"):
                continue
            full = os.path.join(dirpath, fn)
            rp = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if not (rp.endswith(".py") or rp.endswith(".json") or rp.endswith(".md")
                    or rp.endswith(".html") or rp.endswith(".zip") or rp == "dicrc"):
                continue
            if rp.startswith("data/subs") or rp.startswith("data/asr") or rp.startswith("data/subs2"):
                pass
            if os.path.getsize(full) > 8 * 1024 * 1024:
                print("SKIP(big)", rp); continue
            files.append((rp, full))
    return files

if __name__ == "__main__":
    files = collect()
    files.sort()
    print(f"待上传 {len(files)} 个文件")
    ok = fail = 0
    for rp, full in files:
        for attempt in range(3):
            try:
                with open(full, "rb") as f:
                    put(f"bsj/{rp}", f.read())
                print(f"  ✓ {rp}  ({os.path.getsize(full)}B)")
                ok += 1
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:200]
                if e.code == 422 and "already exists" in body:
                    print(f"  = {rp} (已存在, 跳过)"); ok += 1; break
                if attempt == 2:
                    print(f"  ✗ {rp}: {e.code} {body}"); fail += 1
                else:
                    time.sleep(2)
            except Exception as e:
                if attempt == 2:
                    print(f"  ✗ {rp}: {e}"); fail += 1
                else:
                    time.sleep(2)
    print(f"\n完成: 成功 {ok} / 失败 {fail}")
    sys.exit(1 if fail else 0)
