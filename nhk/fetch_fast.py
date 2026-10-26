#!/usr/bin/env python3
"""
NHK やさしいことばニュース —— **原速版**音声抓取

普通新闻的电视画面流（media.vd.st.nhk/news/nd/…）里带同一条新闻的原速播报音轨。
三道坎都踩过了：
  1) 分片 URL 必须带 ?hdnts=<媒体令牌>，否则 403
  2) ffmpeg 走 crypto+https 会丢 Referer → 用本地代理补头（hls_proxy.py）
  3) 音轨参数探测不到 → 必须加 -analyzeduration 30000000 -probesize 50000000
  4) 密钥 URI 里的 acl=/* 含斜杠，ffmpeg 解析会断 → 代理自己取密钥下发

用法： python3 fetch_fast.py [期数] [输出目录]
产出： audio_fast/<id>.m4a
"""
import gzip
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import hls_proxy

OUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/nhk/data"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 120
UA = auth.UA
PORT = 8099
_proxy = None


def get(url, headers=None, timeout=45):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
            return r.status, b
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def media_token(z_at):
    st, b = get("https://mediatoken.web.nhk/v1/token",
                {"Authorization": "Bearer " + z_at, "Accept": "application/json",
                 "Origin": "https://news.web.nhk", "Referer": "https://news.web.nhk/"})
    if st != 200:
        raise RuntimeError("mediatoken %s" % st)
    return json.loads(b)["token"]


def prepare(master_url, mtok):
    """解析 master→变体→分片列表 + 密钥；返回 (segments, key)"""
    tok = urllib.parse.quote(mtok, safe="")
    if "hdnts=" not in master_url:
        master_url += ("&" if "?" in master_url else "?") + "hdnts=" + tok
    st, b = get(master_url, {"Referer": "https://news.web.nhk/"})
    if st != 200:
        return None, None
    master = b.decode("utf-8", "replace")
    m = re.search(r'^(hdntl=\S+)$', master, re.M)
    if not m:
        return None, None
    base = master_url.split("?")[0].rsplit("/", 1)[0] + "/"
    var_rel = m.group(1)
    st, b = get(base + var_rel, {"Referer": "https://news.web.nhk/"})
    if st != 200:
        return None, None
    var = b.decode("utf-8", "replace")
    vdir = base + var_rel.split("/")[0] + "/"
    key = None
    mu = re.search(r'URI="([^"]+)"', var)
    if mu:
        kurl = mu.group(1)
        if "hdnts=" not in kurl:
            kurl += ("&" if "?" in kurl else "?") + "hdnts=" + tok
        st, kb = get(kurl, {"Referer": "https://news.web.nhk/"})
        if st == 200 and len(kb) == 16:
            key = kb
    segs = []
    for line in var.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        u = s if s.startswith("http") else vdir + s
        if "hdnts=" not in u:
            u += ("&" if "?" in u else "?") + "hdnts=" + tok
        segs.append(u)
    return segs, key


def extract(name, master_url, mtok, out_path):
    segs, key = prepare(master_url, mtok)
    if not segs:
        return "prepare-fail"
    hls_proxy.REG.set(name, segs, key)
    full = "/tmp/_f_%s.mp4" % name
    p1 = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-allowed_extensions", "ALL",
         "-analyzeduration", "30000000", "-probesize", "50000000",
         "-i", "http://127.0.0.1:%d/%s/pl.m3u8" % (PORT, name),
         "-c", "copy", "-f", "mp4", full],
        capture_output=True, text=True, timeout=420)
    if p1.returncode != 0 or not os.path.exists(full):
        return "dl-fail:" + (p1.stderr or "")[:70]
    p2 = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", full,
                         "-vn", "-c:a", "copy", "-f", "mp4", out_path],
                        capture_output=True, text=True, timeout=180)
    ok = os.path.exists(out_path) and os.path.getsize(out_path) > 15000
    if not ok and os.path.exists(out_path):
        os.remove(out_path)
    if os.path.exists(full):
        os.remove(full)
    return "ok" if ok else "extract-fail:" + (p2.stderr or "")[:70]


def main():
    global _proxy
    os.makedirs(os.path.join(OUT, "audio_fast"), exist_ok=True)
    t = auth.get_token()
    mtok = media_token(t["token"])
    _proxy = hls_proxy.start(PORT)
    print("z_at + media token ok, proxy on %d" % PORT, flush=True)

    st, b = get("https://news.web.nhk/news/easy/news-list.json",
                {"Referer": "https://news.web.nhk/news/easy/", "Accept": "application/json",
                 "Cookie": auth.cookie_header(t["cookies"])})
    if st != 200:
        raise RuntimeError("news-list %s" % st)
    days = json.loads(b)[0]
    flat = []
    for day in sorted(days.keys(), reverse=True):
        for it in days[day]:
            if it.get("news_web_movie_uri"):
                flat.append(it)
    print("候选 %d 期" % len(flat), flush=True)

    n = 0
    for it in flat[:LIMIT]:
        n += 1
        nid = it["news_id"]
        out = os.path.join(OUT, "audio_fast", nid + ".m4a")
        if os.path.exists(out) and os.path.getsize(out) > 15000:
            continue
        mr = re.search(r"mrurl=([^&]+)", it["news_web_movie_uri"])
        if not mr:
            continue
        r = extract(nid, mr.group(1), mtok, out)
        sz = os.path.getsize(out) if os.path.exists(out) else 0
        print("  [%3d/%d] %s  %-9s %5dKB  %s" % (
            n, LIMIT, nid, r, sz // 1024, (it.get("title") or "")[:26]), flush=True)
        if n % 12 == 0:
            mtok = media_token(t["token"])
            print("     (media token refreshed)", flush=True)

    print("== 原速音声完成 ==", flush=True)


if __name__ == "__main__":
    main()
