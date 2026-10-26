#!/usr/bin/env python3
"""
NHK やさしいことばニュース —— 完整抓取链路验证

  1) z_at      : accountless abroad 授权 JWT（不需要账号）
  2) mediaToken: mediatoken.web.nhk/v1/token（Bearer z_at）
  3) m3u8      : <media_url>?hdnts=<mediaToken>  → 200
"""
import gzip, json, os, re, subprocess, sys, urllib.request, urllib.error
sys.path.insert(0, "/workspace/nhk")
import auth


def media_token(z_at):
    req = urllib.request.Request("https://mediatoken.web.nhk/v1/token", headers={
        "User-Agent": auth.UA, "Accept": "application/json",
        "Authorization": "Bearer " + z_at,
        "Origin": "https://news.web.nhk", "Referer": "https://news.web.nhk/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            b = gzip.decompress(b)
    return json.loads(b)["token"]


def try_url(url, mtok, label):
    full = url + ("&" if "?" in url else "?") + "hdnts=" + urllib.request.quote(mtok, safe="")
    req = urllib.request.Request(full, headers={"User-Agent": auth.UA, "Referer": "https://news.web.nhk/"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            b = r.read(4000)
            print("  [%s] %s %dB  %s" % (label, r.status, len(b), url[:95]))
            if b.startswith(b"#EXTM3U"):
                return full, b
    except urllib.error.HTTPError as e:
        print("  [%s] HTTP %s  %s" % (label, e.code, url[:95]))
    except Exception as e:
        print("  [%s] ERR %s  %s" % (label, str(e)[:50], url[:95]))
    return None, None


def main():
    print("== 1) 取 z_at ==")
    t = auth.get_token()
    print("   scope:", t["scope"], " profile:", t["profile_id"])

    print("\n== 2) 取 media token ==")
    mtok = media_token(t["token"])
    print("   ", mtok[:80], "...")

    newslist = json.load(open("/tmp/news-list.json"))
    dates = newslist[0]
    item = dates["2026-10-20"][0]
    print("\n== 3) 样本条目 ==")
    for k in ["news_id", "title", "news_easy_voice_uri", "news_web_movie_uri", "news_web_url"]:
        print("   %-22s %s" % (k, str(item.get(k))[:110]))
    print("   全部字段:", sorted(item.keys()))

    print("\n== 4) 找 easy voice 的媒体地址 ==")
    vid = item["news_easy_voice_uri"]
    base = vid[:-4] if vid.endswith(".m4a") else vid
    cands = [
        "https://media.vd.st.nhk/news/easy/%s/index.m3u8" % base,
        "https://media.vd.st.nhk/news/easy/%s.m3u8" % vid,
        "https://media.vd.st.nhk/news/voice/%s/index.m3u8" % base,
        "https://media.vd.st.nhk/news/easy/%s/master.m3u8" % base,
        "https://media.vd.st.nhk/easy/%s/index.m3u8" % base,
        "https://media.vd.st.nhk/news/nd/%s/index.m3u8" % base,
    ]
    hit = None
    for c in cands:
        u, b = try_url(c, mtok, "voice")
        if u:
            hit = (u, b)
            break

    print("\n== 5) 普通新闻视频 m3u8（已知 URL） ==")
    mr = re.search(r"mrurl=([^&]+)", item["news_web_movie_uri"])
    vurl = mr.group(1) if mr else None
    vhit = None
    if vurl:
        vhit = try_url(vurl, mtok, "video")

    print("\n== 6) 真下载 12 秒 ==")
    for label, h in [("voice", hit), ("video", vhit)]:
        if not h:
            continue
        out = "/tmp/proof_%s.m4a" % label
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-headers",
               "Referer: https://news.web.nhk/\r\nUser-Agent: Mozilla/5.0\r\n",
               "-i", h[0], "-t", "12", "-c", "copy", out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        ok = os.path.exists(out) and os.path.getsize(out) > 10000
        print("  %s: ffmpeg rc=%s size=%s  %s" % (
            label, r.returncode,
            os.path.getsize(out) if os.path.exists(out) else 0,
            "✅" if ok else "❌ " + (r.stderr or "")[:160]))
        if ok:
            p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration,size,format_name:stream=codec_name,sample_rate,channels",
                                "-of", "default=nw=1", out], capture_output=True, text=True)
            print("     ", p.stdout.replace("\n", " ")[:220])


if __name__ == "__main__":
    main()
