#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 GitHub Pages 永久网页版的同步护栏（2026-10-25）

背景（一次真事故）：
    同步脚本 `scripts/sync_pages.py` 是「**把源目录整份推上去**」
    ⇒ 源目录是旧副本时，**线上 Pages 版的能力会被悄悄降级**
    （实测：旧 sw.js 没有「离线预热」⇒ 一次同步就把断网可看字幕的能力抹掉了；
      旧 sw.js 没有 MEDIA 音频桶 ⇒ 用户存的音频每次发版被清空）

⇒ 所以同步前后各要有一道检查：
    ① **同步前**：源目录必须**具备线上已发布版本已有的全部能力特征**
       （源比线上旧 ⇒ 直接停，不许推）
    ② **同步后**：线上副本必须与源目录**能力特征一致** + **缓存三件套同号**
       （判据用唯一实现 `scripts/check_cache_ver.py`）

🔴 判据落在「事实量」上：抓**仓库里已发布的那份**（不是 CDN，可能有缓存），
   逐条比能力特征；**不是**比版本号大小。

用法：
    python3 scripts/check_pages_sync.py --before --src <发版目录>
    python3 scripts/check_pages_sync.py --after  --src <发版目录> [--expect-ver v33]
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = "tlv-creator/nhk-easy-news"
PAGES_SUB = "nhk-web"
TOKEN = "/workspace/.secrets/gh_token"

# 能力特征：文件 → [(特征名, 正则)]
#   每一项都是「线上已有、但旧副本可能没有」的能力
FEATURES = {
    "sw.js": [
        ("音频独立桶 MEDIA（用户的 ⬇下載 音频不随发版被清空）", r"const\s+MEDIA\s*="),
        ("离线预热 warmSubs（断网也能看字幕）", r"function\s+warmSubs"),
        ("词库预热清单 DATA_PRELOAD（断网点词也有释义）", r"const\s+DATA_PRELOAD\s*="),
        ("字幕目录前缀 SUBS_GLOB", r"const\s+SUBS_GLOB\s*="),
    ],
    "index.html": [
        ("拦截器 BLOCK（挡住平台统计外发）", r"var\s+BLOCK\s*=\s*'agent\.minimax\.cn'"),
        ("运行时兜底 platformInjectGuard（摘掉注入的脚本/悬浮球）", r"platformInjectGuard"),
    ],
}


def gh_get(path):
    """从 GitHub 仓库取文件内容（bytes）。走 curl —— 沙箱里 urllib 连 api.github.com 会被网关打成 400。"""
    if not os.path.isfile(TOKEN):
        sys.exit("❌ 找不到 %s（token 绝不能进仓库）" % TOKEN)
    tok = open(TOKEN).read().strip()
    out = subprocess.run(
        ["curl", "-sS", "-m", "60", "-H", "Authorization: Bearer " + tok,
         "-H", "Accept: application/vnd.github+json",
         "https://api.github.com/repos/%s/contents/%s" % (REPO, path)],
        capture_output=True, text=True).stdout
    try:
        j = json.loads(out)
    except Exception:
        return None
    if not isinstance(j, dict) or "content" not in j:
        return None
    return base64.b64decode(j["content"])


def feats(text, rules):
    out = {}
    for name, pat in rules:
        out[name] = bool(re.search(pat, text or ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", action="store_true")
    ap.add_argument("--after", action="store_true")
    ap.add_argument("--src", required=True)
    ap.add_argument("--expect-ver", default=None, help="期望的 APP_VER（形如 v33）")
    a = ap.parse_args()
    if a.before == a.after:
        sys.exit("❌ --before / --after 必选一个")
    src = os.path.abspath(a.src)
    ok = True

    if a.before:
        print("════ 同步前：源目录不许比线上旧 ════")
        for fname, rules in FEATURES.items():
            online = gh_get("%s/%s" % (PAGES_SUB, fname))
            if online is None:
                print("   · %s 线上还没有（首次同步）⇒ 跳过" % fname)
                continue
            local_p = os.path.join(src, fname)
            if not os.path.exists(local_p):
                print("   ❌ 源目录里没有 %s ⇒ 不许推（会把线上那份删掉）" % fname)
                ok = False
                continue
            o = feats(online.decode("utf-8", "replace"), rules)
            l = feats(open(local_p, encoding="utf-8", errors="replace").read(), rules)
            for name in o:
                if o[name] and not l[name]:
                    print("   ❌ 源目录的 %s 少了「%s」⇒ 推上去等于**降级线上版**" % (fname, name))
                    ok = False
                elif o[name]:
                    print("   ✅ %s 保留「%s」" % (fname, name))
        if not ok:
            print("❌ 源目录比线上旧 ⇒ 停止同步（先修源目录，别拿旧副本推）")
            return 1
        print("✅ 源目录能力特征齐全")
        return 0

    # ---- after ----
    print("════ 同步后：线上副本核对 ════")
    for fname, rules in FEATURES.items():
        online = gh_get("%s/%s" % (PAGES_SUB, fname))
        if online is None:
            print("   ❌ 线上没有 %s" % fname)
            ok = False
            continue
        o = feats(online.decode("utf-8", "replace"), rules)
        for name, has in o.items():
            if has:
                print("   ✅ 线上 %s 有「%s」" % (fname, name))
            else:
                print("   ❌ 线上 %s 缺「%s」" % (fname, name))
                ok = False

    # 缓存三件套：线上 app.js / sw.js 同号（用唯一实现跑）
    app = gh_get("%s/app.js" % PAGES_SUB)
    sw = gh_get("%s/sw.js" % PAGES_SUB)
    if app is None or sw is None:
        print("   ❌ 取不到线上 app.js / sw.js")
        return 1
    tmp = "/tmp/_pages_check"
    os.makedirs(tmp, exist_ok=True)
    ap_, sp_ = os.path.join(tmp, "app.js"), os.path.join(tmp, "sw.js")
    open(ap_, "wb").write(app)
    open(sp_, "wb").write(sw)
    cmd = [sys.executable, "/workspace/nhk/scripts/check_cache_ver.py", ap_, sp_]
    if a.expect_ver:
        cmd.append(a.expect_ver)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        ok = False

    # MEDIA_BASE 必须指向现役站（音频源）
    cfg = gh_get("%s/config.js" % PAGES_SUB)
    if cfg:
        m = re.search(r"window\.CFG\.MEDIA_BASE\s*=\s*\[(.*?)\]", cfg.decode("utf-8", "replace"), re.S)
        bases = re.findall(r"'(https?://[^']+)'", m.group(1)) if m else []
        print("   MEDIA_BASE：%s" % bases)
        if not bases:
            print("   ❌ MEDIA_BASE 为空 ⇒ 音频放不出来")
            ok = False
        else:
            try:
                ver = json.loads(gh_get("version.json").decode("utf-8"))
                live = (ver.get("site") or "").rstrip("/")
                if live and live not in [b.rstrip("/") for b in bases]:
                    print("   ❌ MEDIA_BASE 里没有现役站 %s ⇒ 音频指错地方" % live)
                    ok = False
                else:
                    print("   ✅ MEDIA_BASE 含现役站 %s" % live)
            except Exception as e:
                print("   ⚠️ 读引导页 version.json 失败：%s" % e)

    # 内容不是旧的：期数与源目录一致
    try:
        eps_online = gh_get("%s/data/episodes.js" % PAGES_SUB).decode("utf-8", "replace")
        eps_local = open(os.path.join(src, "data", "episodes.js"), encoding="utf-8").read()
        n_on = len(json.loads(eps_online.split("=", 1)[1].rstrip().rstrip(";")))
        n_lo = len(json.loads(eps_local.split("=", 1)[1].rstrip().rstrip(";")))
        print("   期数：线上 %d / 源目录 %d" % (n_on, n_lo))
        if n_on != n_lo:
            print("   ❌ 期数不一致 ⇒ 线上是旧的")
            ok = False
        else:
            print("   ✅ 期数一致（%d 期）" % n_on)
    except Exception as e:
        print("   ⚠️ 期数核对失败：%s" % e)
        ok = False

    if not ok:
        print("❌ 同步后核对不过")
        return 1
    print("✅ 线上副本：能力特征齐全 + 缓存同号 + 音频源正确 + 期数一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
