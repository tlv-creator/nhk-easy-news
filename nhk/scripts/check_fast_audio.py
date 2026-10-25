#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
媒体库闸门：扫一个目录里的音频，**抓「张冠李戴」的假货**（值无关 ⇒ 各项目通用）

🔴🔴 为什么需要这道闸门（2026-10-23 NHK 实战）：
    从「普通版网页」里找 m3u8 时，页面上嵌的**不只这一条新闻的视频**
    —— NHK 的新闻页底部会挂「最新一期」的播放器 ⇒
    抓到的那条流**属于别的新闻**，但文件名却是这一期的 ⇒
    **27 期里有 17 期抓到了同一个 60 秒的别人的音轨**，
    而「抓取成功」在日志里长得和真货一模一样。

🔴 判据（落在**事实量**上，不看文件名/标题）：
    ① **跨期 md5 重复** ⇒ 同一段音频配了多期 ⇒ 至少有一期是抓错的
       （真货必然逐期不同：不同新闻 ⇒ 不同音轨 ⇒ 字节不同）
    ② 时长 < 15 秒 ⇒ 不是一条新闻的完整音轨
    ③ 时长 > 20 分钟 ⇒ 不像新闻（可能是抓到了合集/直播）
    ⚠️ 这三条都**不需要 ASR、不需要对照文本** ⇒ 能在 build 前几秒内跑完。

🔴 为什么不用「和字幕对拍」当第一道闸：
    ASR 对拍最强，但慢（每期分钟级）而且要模型 ⇒ 只能当**第二道**。
    md5 去重是**秒级**的 ⇒ 放第一道，脏数据根本进不了库。

用法：
    python3 check_fast_audio.py --dir site/app/media/fast
    python3 check_fast_audio.py --dir data/audio_fast --min-seconds 15
    python3 check_fast_audio.py --dir site/app/media/fast --json

退出码：0 = 全过；1 = 有问题（每个问题都带 reason）
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def duration_of(path):
    """用 ffprobe 读时长；读不到返回 None（**读不到不算过，也不算坏** ⇒ 单独报告）。"""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        s = (p.stdout or "").strip().splitlines()
        if not s:
            return None
        return float(s[0])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--min-seconds", type=float, default=15.0)
    ap.add_argument("--max-seconds", type=float, default=20 * 60)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(a.dir):
        # 🔴 「目录不存在」必须 FAIL —— 「没东西可检」不等于「没问题」
        print("❌ [媒体库闸门] reason=dir_missing  目录不存在：%s" % a.dir)
        sys.exit(1)

    files = sorted(f for f in os.listdir(a.dir) if f.lower().endswith((".m4a", ".mp4", ".mp3", ".aac")))
    if not files:
        # 空目录：不能当成「全过」（那是恒绿）。但也不能当成「有坏货」——
        # 报成「没有可检的音频」，退出码 1（调用方自己决定要不要放行空目录）。
        print("❌ [媒体库闸门] reason=no_files  目录里没有音频：%s" % a.dir)
        sys.exit(1)

    by_md5 = {}
    rows = []
    problems = []
    for f in files:
        p = os.path.join(a.dir, f)
        m = md5_of(p)
        d = duration_of(p)
        by_md5.setdefault(m, []).append(f)
        rows.append({"file": f, "md5": m, "seconds": d, "bytes": os.path.getsize(p)})

    # ① 跨期 md5 重复
    for m, fs in by_md5.items():
        if len(fs) > 1:
            problems.append({
                "reason": "duplicate_across_episodes",
                "detail": "同一段音频（md5 %s）配了 %d 期：%s" % (m[:12], len(fs), ", ".join(fs)),
                "files": fs,
            })
    # ②③ 时长越界
    for r in rows:
        if r["seconds"] is None:
            problems.append({"reason": "duration_unreadable",
                             "detail": "读不出时长：%s" % r["file"], "files": [r["file"]]})
            continue
        if r["seconds"] < a.min_seconds:
            problems.append({"reason": "too_short",
                             "detail": "只有 %.1f 秒（< %.0f）：%s —— 不是一条完整新闻音轨"
                                       % (r["seconds"], a.min_seconds, r["file"]),
                             "files": [r["file"]]})
        elif r["seconds"] > a.max_seconds:
            problems.append({"reason": "too_long",
                             "detail": "%.1f 秒（> %.0f）：%s —— 可能抓到了合集/直播"
                                       % (r["seconds"], a.max_seconds, r["file"]),
                             "files": [r["file"]]})

    if a.json:
        print(json.dumps({"dir": a.dir, "count": len(rows), "rows": rows,
                          "problems": problems}, ensure_ascii=False, indent=1))
    else:
        print("   扫了 %d 个音频（%s）" % (len(rows), a.dir))
        for p in problems:
            print("❌ [媒体库闸门] reason=%s" % p["reason"])
            print("   %s" % p["detail"])

    if problems:
        if not a.json:
            print("❌ 媒体库闸门不过：%d 个问题" % len(problems))
        sys.exit(1)
    if not a.json:
        print("✅ 媒体库闸门通过：%d 个音频，md5 各不相同，时长正常" % len(rows))
    sys.exit(0)


if __name__ == "__main__":
    main()
