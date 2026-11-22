#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seed_daily_state.py —— 沙箱重置后**重建日更基线**（2026-11-22）

🔴 为什么需要：
    work/daily_state.json 是**状态文件**（不进仓库：.gitignore 只忽略 work/*.jsonl，
    但这个文件从来没被 commit 过）⇒ 沙箱重置后丢失
    ⇒ daily_publish.py 读到空状态 ⇒ 「上次发音频站：从来没有」+「内容没有基线」
    ⇒ **判据全歪**：会把「今天有 2 期新音频」误判成「首次」，也会把上次发布当成本次发布。

✅ 正解（本脚本）：
    **用现役站（引导页 version.json 指向的那个）的真实内容**当基线：
      · content_sha   = 当前 site/app 的内容指纹（= 现役站那一版）
      · audio_sha     = 当前 site/app/media 的 md5 集合（= 现役站那一版的音频）
      · last_audio_domain     = 现役站域名
      · last_audio_deploy_at  = 现役站**实际发布**的时间（从共享仓库的提交时间反查，
                                不猜「现在」——猜成现在会让 days_since=0）
    ⇒ 之后 plan() 的 diff = **恰好是今天新增的东西** ⇒ 判据回到正轨。

用法：
    python3 scripts/seed_daily_state.py [--deploy-time <unix ts>] [--force]
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import daily_publish as DP  # noqa: E402

ROOT = "/workspace/nhk"
REPO = "tlv-creator/nhk-easy-news"


def _curl(url, extra=None):
    """🔴 沙箱里 urllib 打 github.io 有时 SSL 握手超时 ⇒ 用 curl（稳定）+ 重试"""
    cmd = ["curl", "-s", "-m", "30", "-H", "Cache-Control: no-cache"]
    if extra:
        cmd += extra
    cmd.append(url)
    for _ in range(3):
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.decode("utf-8", "replace")
    return ""


def live_boot():
    txt = _curl("https://tlv-creator.github.io/nhk-easy-news/version.json")
    if not txt:
        raise SystemExit("❌ 读不到引导页 version.json")
    return json.loads(txt)


def last_publish_ts(path_in_repo="nhk/site/app/version.json"):
    """现役站**实际发布**的时间 = 共享仓库里最后一次改这份产物的提交时间"""
    tok = ""
    tf = "/workspace/.secrets/gh_token"
    if os.path.exists(tf):
        tok = open(tf, encoding="utf-8").read().strip()
    url = ("https://api.github.com/repos/%s/commits?path=%s&per_page=1" % (REPO, path_in_repo))
    txt = _curl(url, extra=["-H", "Authorization: token %s" % tok,
                            "-H", "User-Agent: nhk-updater"])
    try:
        arr = json.loads(txt)
        if arr:
            return arr[0]["commit"]["committer"]["date"]
    except Exception as ex:
        print("⚠️ 解析提交时间失败：%s" % ex)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy-time", type=float, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if os.path.exists(DP.STATE) and not a.force:
        print("ℹ️ %s 已存在（不覆盖；真要重建加 --force）" % DP.STATE)
        return 0

    boot = live_boot()
    site = (boot.get("site") or "").rstrip("/")
    domain = site.replace("https://", "").replace(".space.mcode.cn", "")
    fp, items = DP.content_fingerprint()
    aud = DP.audio_fingerprint()
    print("   现役站：%s（ver %s）" % (site, boot.get("ver")))
    print("   内容指纹 %s（%d 个文件）" % (fp[:12], len(items)))
    print("   音频指纹 %d 个文件" % len(aud))

    ts = a.deploy_time
    if ts is None:
        iso = last_publish_ts()
        if iso:
            import calendar
            import time as _t
            ts = calendar.timegm(_t.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
            print("   上次发布提交时间：%s" % iso)
        else:
            ts = _t.time() - 86400
            print("   ⚠️ 反查不到提交时间 ⇒ 兜底用「昨天」")

    st = {
        "content_sha": fp,
        "pages_synced_sha": fp,
        "audio_sha": aud,
        "last_audio_deploy_at": ts,
        "last_audio_domain": domain,
        "_seeded_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
        "_seed_note": "沙箱重置后从现役站重建基线（scripts/seed_daily_state.py）",
    }
    DP.save_state(st)
    print("✅ 基线已写入 %s" % DP.STATE)
    print("   last_audio_domain = %s" % domain)
    return 0


if __name__ == "__main__":
    import time as _t
    sys.exit(main())
