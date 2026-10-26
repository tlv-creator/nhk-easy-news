#!/usr/bin/env python3
"""
NHK やさしいことばニュース —— 批量抓取管线

产出（每期一个目录）：
  audio/<id>.m4a          ゆっくり版音声
  episode.json            索引元数据（标题/时间/两个版本的 URL）
  easy.html               易懂版正文（带 ruby）
  web.json                普通版書き起こし（articleBody）

用法： python3 fetch_batch.py [期数] [输出目录]
"""
import gzip
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth

OUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/nhk/data"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 100

UA = auth.UA
LIST_URL = "https://news.web.nhk/news/easy/news-list.json"
API = "https://api.web.nhk/r8"


def get(url, headers=None, timeout=40, raw=False):
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
        raise RuntimeError("mediatoken 失败 %s" % st)
    return json.loads(b)["token"]


def fetch_index(cookies=None):
    h = {"Referer": "https://news.web.nhk/news/easy/", "Accept": "application/json"}
    if cookies:
        h["Cookie"] = auth.cookie_header(cookies)
    st, b = get(LIST_URL, h)
    if st != 200:
        raise RuntimeError("news-list.json %s" % st)
    return json.loads(b)


def flatten(idx):
    """按时间倒序取条目列表"""
    out = []
    days = idx[0]
    for day in sorted(days.keys(), reverse=True):
        for it in days[day]:
            out.append(it)
    return out


EASY_BODY_RE = re.compile(
    r'<div[^>]*class="[^"]*article-body[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S)


def fetch_easy_html(nid, cookies=None):
    url = "https://news.web.nhk/news/easy/%s/%s.html" % (nid, nid)
    h = {"Referer": "https://news.web.nhk/news/easy/"}
    if cookies:
        h["Cookie"] = auth.cookie_header(cookies)
    st, b = get(url, h)
    if st != 200:
        return None
    return b.decode("utf-8", "replace")


def fetch_web_article(web_url, z_at, cookies):
    """普通版書き起こし：把 web_url 换成 API 路径"""
    m = re.search(r"/newsweb/na/(nd-[a-z0-9]+)", web_url or "")
    if not m:
        return None
    st, b = auth.api_get(f"t/newsarticle/na/{m.group(1)}.json", z_at, cookies)
    if st != 200:
        return None
    return json.loads(b)


def download_audio(m3u8_with_token, out_path):
    if os.path.exists(out_path) and os.path.getsize(out_path) > 30000:
        return "skip"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-headers", "Referer: https://news.web.nhk/\r\nUser-Agent: Mozilla/5.0\r\n",
           "-i", m3u8_with_token, "-c", "copy", "-bsf:a", "aac_adtstoasc",
           "-f", "mp4", out_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "timeout"
    if p.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 20000:
        return "ok"
    if os.path.exists(out_path):
        os.remove(out_path)
    return "fail:" + (p.stderr or "")[:80]


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "audio"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "episodes"), exist_ok=True)

    print("== 授权 ==", flush=True)
    t = auth.get_token()
    print("   z_at ok, scope=%s" % t["scope"], flush=True)
    mtok = media_token(t["token"])
    print("   media token ok", flush=True)

    idx = fetch_index(t["cookies"])
    items = flatten(idx)
    items = [it for it in items if it.get("news_easy_voice_uri")]
    print("== 索引 %d 期，取最近 %d 期 ==" % (len(items), LIMIT), flush=True)

    manifest = []
    for n, it in enumerate(items[:LIMIT], 1):
        nid = it["news_id"]
        d = os.path.join(OUT, "episodes", nid)
        os.makedirs(d, exist_ok=True)
        ep = {"news_id": nid, "title": it.get("title"),
              "title_with_ruby": it.get("title_with_ruby"),
              "time": it.get("news_prearranged_time"),
              "voice_uri": it.get("news_easy_voice_uri"),
              "web_url": it.get("news_web_url"),
              "movie_uri": it.get("news_web_movie_uri"),
              "easy_url": "https://news.web.nhk/news/easy/%s/%s.html" % (nid, nid)}
        with open(os.path.join(d, "episode.json"), "w", encoding="utf-8") as f:
            json.dump(ep, f, ensure_ascii=False, indent=1)

        # 1) 音声
        base = it["news_easy_voice_uri"][:-4]
        m3u8 = ("https://media.vd.st.nhk/news/easy_audio/%s/index.m3u8?hdnts=%s"
                % (base, urllib.request.quote(mtok, safe="")))
        audio = os.path.join(OUT, "audio", nid + ".m4a")
        if os.path.exists(audio) and os.path.getsize(audio) > 30000:
            r = "skip"
        else:
            r = download_audio(m3u8, audio)
        # 2) 易懂版 HTML
        h = fetch_easy_html(nid, t["cookies"])
        if h:
            with open(os.path.join(d, "easy.html"), "w", encoding="utf-8") as f:
                f.write(h)
        # 3) 普通版書き起こし
        w = fetch_web_article(it.get("news_web_url"), t["token"], t["cookies"])
        if w:
            with open(os.path.join(d, "web.json"), "w", encoding="utf-8") as f:
                json.dump(w, f, ensure_ascii=False, indent=1)
        manifest.append(ep)
        print("  [%3d/%d] %s  audio=%s  easy=%s  web=%s  %s" % (
            n, LIMIT, nid, r,
            "ok" if h else "-", "ok" if w else "-",
            (it.get("title") or "")[:36]), flush=True)

        if n % 20 == 0:
            with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=1)
        if n % 15 == 0:
            mtok = media_token(t["token"])
            print("     (media token refreshed)", flush=True)

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("== 完成 %d 期 ==" % len(manifest), flush=True)


if __name__ == "__main__":
    main()
