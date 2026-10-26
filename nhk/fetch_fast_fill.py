#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补抓原速（普通版）音声：只针对指定的期数（默认取 build 出的最新 30 期）。
用法： python3 fetch_fast_fill.py [期数]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import fetch_fast
import hls_proxy

DATA = "/workspace/nhk/data"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30
PORT = fetch_fast.PORT

# 目标：build_data 选中的最新 N 期
eps = []
try:
    s = open("/workspace/nhk/site/app/data/episodes.js", encoding="utf-8").read()
    eps = [e["id"] for e in json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))]
except Exception as e:
    print("读 episodes 失败:", e)
eps = eps[:LIMIT]
want = set(eps)
print("目标 %d 期" % len(want), flush=True)

t = auth.get_token()
mtok = fetch_fast.media_token(t["token"])
hls_proxy.start(PORT)
print("token ok, proxy on %d" % PORT, flush=True)

st, b = fetch_fast.get(
    "https://news.web.nhk/news/easy/news-list.json",
    {"Referer": "https://news.web.nhk/news/easy/", "Accept": "application/json",
     "Cookie": auth.cookie_header(t["cookies"])})
days = json.loads(b)[0]
flat = []
for day in sorted(days.keys(), reverse=True):
    for it in days[day]:
        if it.get("news_web_movie_uri"):
            flat.append(it)
print("候选 %d 期" % len(flat), flush=True)

n = 0
for it in flat:
    nid = it["news_id"]
    if nid not in want:
        continue
    n += 1
    out = os.path.join(DATA, "audio_fast", nid + ".m4a")
    if os.path.exists(out) and os.path.getsize(out) > 15000:
        print("  [%2d/%d] %s  skip" % (n, len(want), nid), flush=True)
        continue
    mr = re.search(r"mrurl=([^&]+)", it["news_web_movie_uri"])
    if not mr:
        continue
    r = fetch_fast.extract(nid, mr.group(1), mtok, out)
    sz = os.path.getsize(out) if os.path.exists(out) else 0
    # 🔴 2026-10-22：原速音声是从**电视视频流抽的音轨**，NHK 电视新闻的混音电平
    #    本来就比广播朗读低一截（实测平均低 2.4~12 dB）→ 用户反馈「普通语速的声音不响」。
    #    ⇒ 抓完立刻做**响度归一化**，对齐同期的慢速音声电平。
    if r == "ok" and os.path.exists(out):
        try:
            sys.path.insert(0, "/workspace/nhk/scripts")
            import loudnorm_fix
            slow = os.path.join(DATA, "audio", nid + ".m4a")
            bm, _ = loudnorm_fix.probe_volume(out)
            sm, _ = loudnorm_fix.probe_volume(slow) if os.path.exists(slow) else (None, None)
            if bm is not None and sm is not None:
                ok, bm2, am, _, _, gain = loudnorm_fix.process_one(out, sm)
                print("      响度归一化: %.1f → %.1f dB (%+.1f dB)"
                      % (bm2 if bm2 else 0, am if am else 0, gain if gain else 0), flush=True)
        except Exception as e:
            print("      (响度归一化跳过: %s)" % str(e)[:120], flush=True)
    print("  [%2d/%d] %s  %-9s %5dKB  %s" % (
        n, len(want), nid, r, sz // 1024, (it.get("title") or "")[:24]), flush=True)
    if n % 8 == 0:
        mtok = fetch_fast.media_token(t["token"])
        print("     (media token refreshed)", flush=True)

print("== 补抓完成 ==", flush=True)
