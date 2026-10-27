#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓 YouTube 官方日文字幕（youtubetranslate.net 代理）—— 密集并发采样模式。

单位：currentTime / start_time / end_time 都是【毫秒】。
做法：每 STEP 毫秒采一个点（STEP < 最短字幕段长 ⇒ 不漏段），并发请求后去重排序。
"""
import json, os, re, sys, time, urllib.request, argparse
from concurrent.futures import ThreadPoolExecutor

API = "https://youtubetranslate.net/api/subtitle"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
STEP = 2000          # 采样步长(ms) —— 字幕段长 3~6s，2s 采样不漏

def post(video_id, current_time_ms, tries=3, timeout=30):
    body = json.dumps({"videoId": video_id, "currentTime": int(current_time_ms)}).encode("utf-8")
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=body, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": UA,
                         "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            su = d.get("subtitle") or {}
            orig = (su.get("original") or "").strip()
            st, et = su.get("start_time"), su.get("end_time")
            if orig and st is not None and et is not None:
                return orig, int(st), int(et)
            return None, None, None
        except Exception:
            time.sleep(0.8 + 1.2 * i)
    return None, None, None

def clean(s):
    s = s.replace("", "").replace("﻿", "")
    s = re.sub(r"^\s*\?", "", s)
    return re.sub(r"\s+", " ", s).strip()

def probe(video_id, t):
    o, st, et = post(video_id, t)
    return (st, et, clean(o)) if o else None

def fetch_all(video_id, duration_ms, workers=10, step=STEP, verbose=True):
    points = list(range(0, int(duration_ms) + step, step))
    segs = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(lambda t: probe(video_id, t), points):
            done += 1
            if r:
                segs[(r[0], r[2])] = [r[0], r[1], r[2]]
            if verbose and done % 200 == 0:
                print(f"    {done}/{len(points)} 采样点, {len(segs)} 段", flush=True)
    out = sorted(segs.values(), key=lambda x: x[0])
    return out

def get_duration(video_id):
    """用 metadata 端点拿时长；拿不到就返回 None"""
    try:
        u = f"https://youtubetranslate.net/api/video-metadata?v={video_id}"
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8"))
        for k in ("duration", "lengthSeconds", "durationSeconds"):
            if isinstance(d, dict) and d.get(k):
                return int(d[k]) * 1000
        s = json.dumps(d)
        m = re.search(r'"lengthSeconds"\s*:\s*"?(\d+)', s)
        if m:
            return int(m.group(1)) * 1000
    except Exception:
        pass
    return None

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=int, default=0, help="秒；0=自动探测")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--step", type=int, default=STEP)
    a = ap.parse_args()
    dur_ms = a.duration * 1000 if a.duration else get_duration(a.vid)
    if not dur_ms:
        # 兜底：从已有段推断 + 逐段试探（最多 40 分钟）
        dur_ms = 40 * 60 * 1000
        print("  (时长未知，按 40 分钟上限扫)")
    t0 = time.time()
    segs = fetch_all(a.vid, dur_ms, a.workers, a.step)
    print(f"{len(segs)} 段, {time.time()-t0:.0f}s")
    for s in segs[:6]:
        print(f"  [{s[0]}-{s[1]}] {s[2][:70]}")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(segs, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("→", a.out)
