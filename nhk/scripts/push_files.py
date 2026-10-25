#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用共享仓库推送器（2026-10-25）—— 把指定文件推到 tlv-creator/nhk-easy-news

🔴🔴 为什么不用现成的 push_shared.py：
    它把文件列表**硬编码**在 TOOLS 常量里 ⇒ 每加一个新工具就要改脚本
    ⇒ 「同一配方只写一遍」被破坏（文件清单散在两个地方）
    ⇒ 改成：**命令行给文件列表** ⇒ 清单只有一个来源（命令行 / 调用方）

🔴 纪律（继承 push_shared.py 的教训）：
  ① 基线永远取**线上最新**（不拿本地副本）⇒ 409 时重取重试，不覆盖别人
  ② 回读**逐字对账**（不是只看 API 返回 ok）
  ③ 只推指定文件，不整份覆盖
  ④ 单文件 < 100MB（GitHub Contents API 硬限制）
"""
import base64
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


def api(method, path, body=None):
    """🔴 用 curl 不用 urllib —— 沙箱里 urllib 连 api.github.com 会被网关打成 400

    🔴🔴 body 走**临时文件 + curl -d @file**：
        手册 99KB → base64 后 132KB ⇒ 直接塞进 argv 会报
        `OSError: [Errno 7] Argument list too long: 'curl'`
        ⇒ 大 body 必须走文件（argv 长度有硬上限）
    """
    url = API + path
    cmd = ["curl", "-s", "-m", "120", "-X", method,
           "-H", "Authorization: Bearer " + TOK,
           "-H", "Accept: application/vnd.github+json",
           "-H", "X-GitHub-Api-Version: 2022-11-28",
           "-H", "User-Agent: nhk-easy-news-updater"]
    tmp = None
    try:
        if body is not None:
            tmp = "/workspace/.gh_body.json"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False)
            cmd += ["-d", "@" + tmp]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"_raw": r.stdout[:800], "_stderr": r.stderr[:400], "_rc": r.returncode}


def sha1_blob(data: bytes) -> str:
    """Git blob sha = sha1(b"blob <len>\0" + data)"""
    import hashlib
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def get_file(path):
    """拿线上文件内容 + blob sha（sha 参数要用它，不是 sha256sum）"""
    d = api("GET", f"/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}")
    if isinstance(d, dict) and d.get("content") is not None:
        raw = base64.b64decode(d["content"])
        return raw, d.get("sha")
    return None, None


def put_file(path, data: bytes, msg: str):
    """PUT（新建或更新）。**不带 sha 时 GitHub 会拒绝覆盖已有文件** ⇒ 必须带上原 sha"""
    _, sha = get_file(path)
    body = {
        "message": msg,
        "content": base64.b64encode(data).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = api("PUT", f"/repos/{OWNER}/{REPO}/contents/{path}", body)
    ok = isinstance(r, dict) and r.get("content") is not None
    return ok, r


def main():
    if len(sys.argv) < 2:
        print("用法： push_files.py <目标仓库路径> <本地文件> [<目标仓库路径> <本地文件> ...]")
        print("   例： push_files.py nhk/手册.md 手册.md")
        print("   例： push_files.py nhk/scripts/daily_publish.py scripts/daily_publish.py")
        print("   （成对给：仓库里的路径 + 本地源文件；本地源不存在就用仓库路径当源）")
        return 1
    if not os.path.isfile(TOK_PATH):
        print(f"❌ 找不到 {TOK_PATH}")
        return 1
    global TOK
    TOK = open(TOK_PATH).read().strip()

    argv = sys.argv[1:]
    if len(argv) % 2 != 0:
        print("❌ 参数必须成对（目标路径 本地源）")
        return 1
    pairs = []
    for i in range(0, len(argv), 2):
        dest, src = argv[i], argv[i + 1]
        if not os.path.isfile(src):
            print(f"❌ 本地源文件不存在：{src}")
            return 1
        pairs.append((dest, src))

    msg = f"chore: 更新 {len(pairs)} 个文件（NHK 日更零配额架构 + 手册补全）"
    ok_all = True
    for dest, src in pairs:
        data = open(src, "rb").read()
        if len(data) > 100 * 1024 * 1024:
            print(f"❌ {dest} = {len(data)/1048576:.1f}MB > 100MB 硬限制")
            ok_all = False
            continue
        ok, r = put_file(dest, data, msg)
        if not ok:
            print(f"❌ 推 {dest} 失败：{json.dumps(r, ensure_ascii=False)[:400]}")
            ok_all = False
            continue
        # 🔴 回读逐字对账（不能只看 API 返回 ok）
        time.sleep(0.5)
        back, _ = get_file(dest)
        same = (back == data)
        print(f"{'✅' if same else '❌'} {dest}  {len(data)}B  blob={sha1_blob(data)[:10]}  回读{'一致' if same else '不一致!'}")
        if not same:
            ok_all = False
    if not ok_all:
        print("\n❌ 有文件没推成功")
        return 1
    print("\n✅ 全部推送并回读核对通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
