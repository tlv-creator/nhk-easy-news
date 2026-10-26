#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NHK 原速音声抓取 v2（两阶段并发版）

为什么要有 v2：
  v1（fetch_fast.py）用索引里的 `news_web_movie_uri` 找视频，
  但那个字段只对「单独出过视频」的期有值 → 36 期里只有 9 期。
  实际上**普通版网页里内嵌了这条新闻自己的电视片段流**
  （media.vd.st.nhk/news/nd/<web_id>_E*_s/index.m3u8），
  哪怕索引字段是空的也能找到 → 覆盖面大得多。

⚠️ 普通版的 id 跟 easy 版不是同一个！
   episode.json 里 web_url = .../nd-20261021de62637
   → 页面里嵌的 m3u8 也是 20261021de62637_E*_s/，不是 easy 的 62763。

两阶段（提速 ~3 倍）：
  A. 并行 4 线程：抓普通版页面 + 解析 m3u8 + 拉分片清单和密钥（纯 HTTP）
  B. 并行 2 线程：ffmpeg 下载 + 抽音轨（-c copy，不转码不吃 CPU）
  hls_proxy 是 ThreadingTCPServer + 带锁的 registry → 并发安全。

用法： python3 fetch_fast2.py [最多处理几期] [输出目录]
产出： data/audio_fast/<easy_id>.m4a
"""
import concurrent.futures as cf
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
import fetch_fast

OUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/nhk/data"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 120
UA = auth.UA
PORT = fetch_fast.PORT


def free_port(prefer=8099):
    """拿一个真正空闲的端口。代理是**进程内** registry，
    跨进程复用旧代理 = registry 里没我们的分片 → 全 502。"""
    import socket
    for cand in [prefer] + list(range(8200, 8299)):
        so = socket.socket()
        try:
            so.bind(("127.0.0.1", cand))
            so.close()
            return cand
        except OSError:
            try:
                so.close()
            except Exception:
                pass
    raise RuntimeError("找不到空闲端口")


def free_port(prefer=8099):
    """拿一个真正空闲的端口。代理是**进程内** registry，
    跨进程复用旧代理 = registry 里没我们的分片 → 全 502。"""
    import socket
    for cand in [prefer] + list(range(8200, 8299)):
        so = socket.socket()
        try:
            so.bind(("127.0.0.1", cand))
            so.close()
            return cand
        except OSError:
            try:
                so.close()
            except Exception:
                pass
    raise RuntimeError("找不到空闲端口")
WORKERS = int(os.environ.get("FAST_WORKERS", "2"))

M3U8_RE = re.compile(
    r'https://media\.vd\.st\.nhk/news/nd/([0-9a-zA-Z]+)_E[0-9]+_\d+_s/index\.m3u8')


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


def page_m3u8(html, news_id):
    """从普通版页面里挑这条新闻自己的视频流。
    页面里是 JSON-LD(contentUrl) 和播放器 iframe(mrurl=) 两种形态。

    🔴🔴 2026-10-23 加严：**只接受 id 精确匹配的那条流**。
       原来这里找不到精确匹配时会**退回「页面上第一条 m3u8」** ⇒
       而 NHK 新闻页底部挂着**最新一期**的播放器 ⇒
       ⇒ 27 期里有 **17 期抓到了同一个 60 秒的「别人的音轨」**，
          而日志里显示「ok」，看起来跟真货一模一样。
       ⇒ 现在没有精确匹配就**明确失败**（返回 None），宁可不抓也不抓错。
       ⇒ 配套两道：① 媒体库闸门 scripts/check_fast_audio.py（跨期 md5 重复）
                     ② ASR 对拍（fast 音的文字 vs 该期普通版文本）

    返回 (完整 m3u8 url, 说明)"""
    h = html.replace("\\/", "/").replace("\\", "")
    for m in M3U8_RE.finditer(h):
        if m.group(1) == news_id:
            return m.group(0), "精确匹配"
    # 🔴 没有精确匹配 ⇒ **不兜底**。见上面的事故说明。
    return None, ""


def phase_a(ep, cookies):
    """抓页面 + 解析 m3u8 + 拉分片/密钥。返回 (ep, segs, key, title)"""
    epdir = os.path.join(OUT, "episodes", ep)
    try:
        epj = json.load(open(os.path.join(epdir, "episode.json"), encoding="utf-8"))
    except Exception:
        return (ep, None, None, "")
    web = epj.get("web_url") or ""
    title = (epj.get("title") or "")[:22]
    if not web:
        return (ep, None, None, title)
    st, b = get(web, {"Cookie": cookies, "Referer": "https://news.web.nhk/news/easy/"})
    if st != 200:
        return (ep, None, None, title)
    # 🔴 2026-10-23 修：原来要求 URL **末尾**正好是 id（`/nd-XXX$`），
    #    但 web_url 常带 `#anchor-002:~:text=...` 后缀 ⇒ 匹配失败
    #    ⇒ wid 退化成 easy_id ⇒ **精确匹配永远落空** ⇒ 全掉进「其它片段」兜底。
    mw = re.search(r'nd-([0-9a-zA-Z]+)', web)
    wid = mw.group(1) if mw else ep
    url, why = page_m3u8(b.decode("utf-8", "replace"), wid)
    if not url:
        return (ep, None, None, title)
    mtok = MTOK[0]
    segs, key = fetch_fast.prepare(url, mtok)
    if not segs:
        return (ep, None, None, title)
    return (ep, segs, key, title)


def phase_b(job):
    """ffmpeg 下载 + 抽音轨"""
    ep, segs, key, title = job
    out = os.path.join(OUT, "audio_fast", ep + ".m4a")
    if os.path.exists(out) and os.path.getsize(out) > 15000:
        return (ep, "skip", 0, title)
    hls_proxy.REG.set(ep, segs, key)
    full = "/tmp/_f2_%s.mp4" % ep
    try:
        p1 = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-allowed_extensions", "ALL",
             "-analyzeduration", "30000000", "-probesize", "50000000",
             "-i", "http://127.0.0.1:%d/%s/pl.m3u8" % (PORT, ep),
             "-c", "copy", "-f", "mp4", full],
            capture_output=True, text=True, timeout=600)
        if p1.returncode != 0 or not os.path.exists(full):
            return (ep, "dl-fail:" + (p1.stderr or "")[:50], 0, title)
        p2 = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", full,
                             "-vn", "-c:a", "copy", "-f", "mp4", out],
                            capture_output=True, text=True, timeout=180)
        ok = os.path.exists(out) and os.path.getsize(out) > 15000
        if not ok and os.path.exists(out):
            os.remove(out)
        sz = os.path.getsize(out) if os.path.exists(out) else 0
        return (ep, "ok" if ok else "extract-fail", sz, title)
    except Exception as e:
        return (ep, "err:" + str(e)[:50], 0, title)
    finally:
        for p in (full,):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


MTOK = [None]


def main():
    os.makedirs(os.path.join(OUT, "audio_fast"), exist_ok=True)
    t = auth.get_token()
    MTOK[0] = fetch_fast.media_token(t["token"])
    global PORT
    PORT = free_port()
    hls_proxy.start(PORT)
    print("  代理端口 %d" % PORT, flush=True)
    cookies = auth.cookie_header(t["cookies"])
    print("z_at + media token ok, proxy on %d" % PORT, flush=True)

    epdir = os.path.join(OUT, "episodes")
    scanf = "/workspace/nhk/work/fast2_scan.json"
    if os.path.exists(scanf):
        exact = json.load(open(scanf, encoding="utf-8")).get("exact", [])
    else:
        exact = sorted([e for e in os.listdir(epdir)
                        if os.path.exists(os.path.join(epdir, e, "episode.json"))],
                       reverse=True)
    eps = [e for e in exact if os.path.exists(os.path.join(epdir, e, "episode.json"))]
    todo = [e for e in eps
            if not (os.path.exists(os.path.join(OUT, "audio_fast", e + ".m4a"))
                    and os.path.getsize(os.path.join(OUT, "audio_fast", e + ".m4a")) > 15000)]
    print("候选 %d 期，待抓 %d 期" % (len(eps), len(todo)), flush=True)

    # ── 阶段 A：并行抓页面 + 拉分片清单 ──
    jobs = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(phase_a, e, cookies): e for e in todo}
        for f in cf.as_completed(futs):
            ep, segs, key, title = f.result()
            if segs:
                jobs.append((ep, segs, key, title))
            else:
                print("  %s  页面无 m3u8 / 拉取失败" % ep, flush=True)
    print("阶段A 完成：%d 期拿到分片" % len(jobs), flush=True)

    # ── 阶段 B：并行 ffmpeg 抽音轨 ──
    ok_n = 0
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for ep, r, sz, title in ex.map(phase_b, jobs):
            if r == "ok":
                ok_n += 1
            print("  %s  %-14s %6dKB  %s" % (ep, r, sz // 1024, title), flush=True)

    print("== 原速音声 v2 完成：成功 %d / %d ==" % (ok_n, len(jobs)), flush=True)

    # 🔴🔴 媒体库闸门（2026-10-23 新加）—— 抓完立刻验「有没有抓错」
    #    判据：跨期 md5 重复（同一段音频配多期）＝ 张冠李戴的指纹
    #    ⇒ 抓错时**必须在这里就红**，而不是等发布到线上才发现
    gate = "/workspace/nhk/scripts/check_fast_audio.py"
    if os.path.exists(gate):
        print("== 媒体库闸门 ==", flush=True)
        subprocess.run([sys.executable, gate, "--dir", os.path.join(OUT, "audio_fast")])


if __name__ == "__main__":
    main()
