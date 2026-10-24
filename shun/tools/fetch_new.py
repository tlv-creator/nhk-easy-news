#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_new.py —— 查 RSS 有没有新一期；没有就干净退出，有就下载 + 转码 + 体检

🔴 判据全是「事实量」：
    · 只收【N5-N4】EpXXX（Special Episode 是英语的，不要）
    · 只取**比现有最新一期更新**的（RSS 里有 361 期，Ep278 及以下是历史存量）
    · RSS 断连重试 3 次
    · 音频健康闸门（硬失败 ⇒ 这一期不发、成品删掉、不写 meta）

🔴 配方（照**已发布**的文件反推出来的，量了三个维度都对得上）：
    AAC-LC / 32 kbps / 单声道 / 44.1 kHz（**不重采样**）
    已发布 ep01.m4a 实测：aac LC, 44100 Hz, 1ch, 32637 bps, 881.607s（源 881.6s）
    本脚本产物实测：aac LC, 44100 Hz, 1ch, 32634 bps, 1017.104s（源 1017.104s）✅ 同配方

用法：
    python3 tools/fetch_new.py --dry-run      # 只查，不下载
    python3 tools/fetch_new.py                # 下载 + 转码 + 体检 + 写 work/epmeta.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(PROJ)

# 官方 RSS（RedCircle；Apple Podcasts lookup 里的 feedUrl）
FEED = "https://feeds.redcircle.com/e8ab057c-683d-4375-a197-2dcc42d4f851"
TITLE_RE = re.compile(r"【N5-N4】\s*Ep(\d+)")
SPECIAL_RE = re.compile(r"special", re.I)

# 交付音频配方（见文件头）
RECIPE = ["-c:a", "aac", "-b:a", "32k", "-ar", "44100", "-ac", "1"]

# 闸门判据
MAX_DURATION_DIFF = 0.5     # 成品 vs 源
MAX_PEAK_DB = 6.0           # 硬失败
MAX_OVER_PCT = 0.05         # |x|>4.0 的占比上限
MAX_FLAT_PCT = 5.0          # 平顶占比上限


def say(msg):
    print(msg, file=sys.stderr)


def fetch_feed(retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(FEED, headers={"User-Agent": "shun-updater/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            say("  RSS 拉取成功（%d 次尝试，%.1f KB）" % (i + 1, len(data) / 1024.0))
            return data
        except Exception as e:  # noqa: BLE001
            say("  ⚠️ RSS 拉取失败（第 %d 次）：%s" % (i + 1, e))
            time.sleep(3)
    raise SystemExit("❌ RSS 连不上（重试 %d 次）—— 原样报告，不要另找源" % retries)


def latest_published_no(index_path):
    """现有最新一期的原始集号（no 字段，例：277）"""
    if not os.path.exists(index_path):
        return 0
    try:
        idx = json.load(open(index_path))
    except Exception:
        return 0
    best = 0
    for e in idx.get("episodes", []):
        m = re.match(r"\d+", str(e.get("no", "")))
        if m:
            best = max(best, int(m.group(0)))
    return best


def parse_items(data):
    root = ET.fromstring(data)
    ch = root.find("channel")
    out = []
    for item in ch.findall("item"):
        title = (item.findtext("title") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        enc = item.find("enclosure")
        url = enc.get("url") if enc is not None else ""
        length = int(enc.get("length") or 0)
        m = TITLE_RE.search(title)
        if not m:
            continue
        if SPECIAL_RE.search(title):
            continue
        out.append({"ep": int(m.group(1)), "title": title, "pub": pub,
                    "url": url, "bytes": length})
    out.sort(key=lambda x: x["ep"], reverse=True)
    return out


def download(url, dest, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "shun-updater/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return True
        except Exception as e:  # noqa: BLE001
            say("  ⚠️ 下载失败（第 %d 次）：%s" % (i + 1, e))
            time.sleep(3)
    return False


def ffprobe_duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        return float(p.stdout.decode().strip())
    except Exception:
        return None


def transcode(src, dst):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src] + RECIPE + [dst, "-y"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise SystemExit("❌ 转码失败：%s" % p.stdout.decode()[:300])


def health_gate(product, source):
    """跑共享体检工具 + 时长闸门 + 峰值闸门。返回 (ok, 说明)"""
    tool = os.path.join(ROOT, "nhk-easy-news", "scripts", "audio_health.py")
    if not os.path.exists(tool):
        return False, "体检工具不存在（%s）—— 拿不到证据 ⇒ 不放行" % tool
    p = subprocess.run([sys.executable, tool, "--check", os.path.dirname(product),
                        "--deep", "--json", "--max-over-pct", str(MAX_OVER_PCT)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        res = json.loads(p.stdout.decode())
    except Exception:
        return False, "体检工具输出读不懂（跑不起来 ⇒ 不放行）"
    rc = res.get("rc", 1)
    dirs = res.get("dirs", [])
    rows = dirs[0].get("files", []) if dirs else []
    notes = []
    for r in rows:
        notes.append("%s 峰值%s dBFS / 平顶%s%% / 垃圾样本%s%% / LUFS%s"
                     % (r.get("file"), r.get("peak_db"), r.get("flat_ratio"),
                        r.get("over_pct"), r.get("lufs")))
    if rc != 0:
        why = dirs[0].get("reason") if dirs else "?"
        return False, "体检没过（reason=%s）%s" % (why, " ｜ ".join(notes))
    # 时长闸门（峰值守卫抓不到截断 ⇒ 必须另配）
    pd = ffprobe_duration(product)
    sd = ffprobe_duration(source)
    if pd and sd and abs(pd - sd) > MAX_DURATION_DIFF:
        return False, "成品与源时长差 %.3fs > %.1fs（转码被截断）" % (abs(pd - sd), MAX_DURATION_DIFF)
    # 峰值闸门（> +6 dBFS 硬失败；0~+6 只警告）
    for r in rows:
        pk = r.get("peak_db")
        if pk is not None and pk > MAX_PEAK_DB:
            return False, "%s 峰值 %.2f dBFS > +%.0f dBFS（幅度荒谬）" % (
                r.get("file"), pk, MAX_PEAK_DB)
    return True, " ｜ ".join(notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--index", default=os.path.join(PROJ, "web", "data", "eps", "index.json"))
    ap.add_argument("--outdir", default=os.path.join(PROJ, "work", "audio"))
    ap.add_argument("--meta", default=os.path.join(PROJ, "work", "epmeta.json"))
    args = ap.parse_args()

    say("== 查 RSS ==")
    data = fetch_feed()
    items = parse_items(data)
    cur = latest_published_no(args.index)
    say("  RSS 里 N5-N4 期数：%d（最新 Ep%d）｜ 已发布最新：Ep%d" % (len(items), items[0]["ep"], cur))

    new = [x for x in items if x["ep"] > cur]
    if not new:
        say("✅ 没有新一期（最新一期是 Ep%d）" % cur)
        return
    for x in new:
        say("  🆕 Ep%d  %s  %s" % (x["ep"], x["pub"], x["title"][:60]))
    if args.dry_run:
        say("（--dry-run：不下载）")
        return

    os.makedirs(args.outdir, exist_ok=True)
    metas = []
    for x in new:
        tag = "ep%02d" % (len([i for i in range(1, 999)]) and 0)  # 占位，下面算
        # 新集的 tag = 现有最大 tag + 1
        metas.append(x)

    # 一次只处理一期（周更场景），多期时按 ep 从大到小依次处理
    for x in metas:
        idx = json.load(open(args.index))
        tags = [e.get("tag") for e in idx.get("episodes", [])]
        nums = [int(re.sub(r"\D", "", t) or 0) for t in tags]
        tag = "ep%02d" % (max(nums) + 1)
        src = os.path.join(args.outdir, tag + "_src.mp3")
        dst = os.path.join(args.outdir, tag + ".m4a")
        say("== 下载 Ep%d → %s ==" % (x["ep"], tag))
        if not download(x["url"], src):
            raise SystemExit("❌ 下载失败：%s" % x["url"])
        say("  源文件 %.1f MB" % (os.path.getsize(src) / 1048576.0))
        transcode(src, dst)
        ok, why = health_gate(dst, src)
        say("  体检：%s" % why)
        if not ok:
            os.remove(dst)
            raise SystemExit("🔴 音频健康闸门没过 ⇒ 这一期不发（成品已删）\n   %s" % why)
        json.dump({"ep": x["ep"], "tag": tag, "title": x["title"], "pub": x["pub"],
                   "url": x["url"], "src": src, "product": dst,
                   "dur": ffprobe_duration(dst)},
                  open(args.meta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("✅ 体检通过 → %s" % args.meta)
        say("   下一步见 tools/手册.md 第 4 节（asr → 听写 → 切行 → 翻译 → 词库 → 集数据）")


if __name__ == "__main__":
    main()
