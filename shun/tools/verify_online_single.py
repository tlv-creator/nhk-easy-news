#!/usr/bin/env python3
"""线上单文件版的完整性校验（Range 分块下载 ⇒ 绕开大文件反复截断）。

🔴 为什么不用一次性下载（实测）：
   5.9 MB 的 shun-text.html 在沙箱里 curl/urllib 都会**中途截断**
   （实测只拿到 511KB / 1.6MB / 3.4MB 就断）
   ⇒ 改用 **Range 分块 + 断点续传**，每块 256KB，失败重试

用法：python3 verify_online_single.py
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://tlv-creator.github.io/nhk-easy-news/shun-text.html"
LOCAL = os.path.join(HERE, "output", "Japanese-with-Shun-Text-50.html")
OUT = "/tmp/single_full.html"
CHUNK = 256 * 1024


def head_len():
    req = urllib.request.Request(URL, headers={"Cache-Control": "no-cache"}, method="HEAD")
    r = urllib.request.urlopen(req, timeout=90)
    return int(r.headers.get("Content-Length") or 0)


def get_range(start, end):
    # 🔴 **不要加随机 query**：实测带 query 的 Range 请求在 GitHub Pages CDN 上
    #    会返回**不一致的片段**（下载 100% 后解码报错，边界正好在 2MB）
    #    ⇒ 用稳定 URL（不带 query），靠 Cache-Control 头绕缓存
    h = {"Range": "bytes=%d-%d" % (start, end), "Cache-Control": "no-cache"}
    req = urllib.request.Request(URL, headers=h)
    last = None
    for i in range(6):
        try:
            d = urllib.request.urlopen(req, timeout=90).read()
            if len(d) == end - start + 1:
                return d
            last = "长度不对: %d（期望 %d）" % (len(d), end - start + 1)
        except Exception as e:
            last = "%s" % e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("块 %d-%d 取不到: %s" % (start, end, last))


def blob_sha_via_api():
    """🔴 更硬的判据：读 GitHub API 的线上 blob sha，与本地文件算的 blob sha 比对。

    为什么优先用它（2026-10-25 实测）：
      从 Pages CDN 下载 5.9 MB 的文件会**反复截断**（实测 511KB/1.6MB/3.4MB/5.2MB 都断过）
      ⇒ Range 分块也不可靠（CDN 偶尔返回错位片段 ⇒ 解码在 2MB/3.8MB 处报错）
      ⇒ **blob sha 是内容寻址的权威值**，不受传输问题影响
    """
    import hashlib
    tok = os.environ.get("GH_TOKEN")
    api = "https://api.github.com/repos/tlv-creator/nhk-easy-news"
    h = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
         "User-Agent": "mavis-push"}
    req = urllib.request.Request(api + "/contents/shun-text.html", headers=h)
    o = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    data = open(LOCAL, "rb").read()
    local = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    print("线上 blob sha: %s（%d 字节）" % (o["sha"], o["size"]))
    print("本地 blob sha: %s（%d 字节）" % (local, len(data)))
    return o["sha"] == local


def main():
    if blob_sha_via_api():
        print("✅ 单文件版线上与本地逐字一致（blob sha 相同）")
        return
    print("⚠️ blob sha 不一致 ⇒ 退回下载比对")
    total = head_len()
    print("线上 Content-Length: %d 字节 (%.2f MB)" % (total, total / 1048576))
    if not total:
        print("🔴 拿不到 Content-Length")
        sys.exit(1)
    with open(OUT, "wb") as f:
        pos = 0
        while pos < total:
            end = min(pos + CHUNK - 1, total - 1)
            data = get_range(pos, end)
            if len(data) != end - pos + 1:
                print("🔴 第 %d 块长度不对: 期望 %d 实得 %d" % (pos, end - pos + 1, len(data)))
                sys.exit(1)
            f.write(data)
            pos = end + 1
            if pos % (CHUNK * 8) == 0 or pos >= total:
                print("  已下载 %.1f%% (%.1f MB)" % (100.0 * pos / total, pos / 1048576), flush=True)
    sz = os.path.getsize(OUT)
    print("下载完成: %d 字节" % sz)
    if sz != total:
        print("🔴 大小不符")
        sys.exit(1)

    html = open(OUT, encoding="utf-8").read()
    i = html.index("window.SHUN=") + len("window.SHUN=")
    obj, end = json.JSONDecoder().raw_decode(html[i:])
    print("内联集数: %d" % len(obj["eps"]))

    # 与**本地新版数据**逐句 ru 比对（不是读 HTML 当 JSON）
    local = json.load(open(os.path.join(HERE, "data-merged-50.new.json"), encoding="utf-8"))
    print("本地新版集数: %d" % len(local["eps"]))
    if len(obj["eps"]) != len(local["eps"]):
        print("🔴 集数不一致")
        sys.exit(1)

    # 逐句 ru 比对
    loc = {e["tag"]: e for e in local["eps"]}
    same = diff = 0
    bad = []
    for e in obj["eps"]:
        l = loc.get(e["tag"])
        if not l:
            bad.append(e["tag"])
            continue
        for i, s in enumerate(e["sents"]):
            if i >= len(l["sents"]):
                diff += 1
                continue
            if s.get("ru") == l["sents"][i].get("ru"):
                same += 1
            else:
                diff += 1
    print("ru 与本地逐字一致: %d ｜不一致: %d" % (same, diff))
    if bad:
        print("🔴 缺集:", bad)
    if diff:
        print("🔴 单文件版与本地不一致")
        sys.exit(1)
    print("✅ 单文件版线上已是新版，且与本地逐字一致")


if __name__ == "__main__":
    main()
