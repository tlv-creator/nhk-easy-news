#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auth.py —— NHK 授权链（抓音声用）

🔴 背景：`media.vd.st.nhk` 在没有 `hdnts` 时一律 403（Akamai）⇒ 必须先换 token。
   链子是两段：
       auth.get_token()        → t["token"]     （news.web.nhk/tix/build_authorize 一族）
       media_token(t["token"])  → hdnts          （换 HLS 授权串）

🔴 2026-10-24 的现状（重建时实测，**别当成已验证可用**）：
   · `https://news.web.nhk/tix/build_authorize` 是**活着的**（不带参数返回
     `{"error":"invalid redirect_uri"}`，带 redirect_uri 返回 `{"error":"invalid idp"}`）
     ⇒ 它需要一组**完整参数**（idp / redirect_uri / 其它），参数集要从 NHK ONE 客户端里扒。
   · `https://media.vd.st.nhk/...` 域名可达，但**没有 hdnts 一律 403**。
   ⇒ 下面的 get_token() 是**按已还原的配方**写的：走 build_authorize，
     参数集缺失时会**明确报错**（不是静默返回空 token），
     免得下游拿着空 token 去抓 43 期全 403 还以为是「这期没音声」。

配方（从共享仓库 scripts/refetch_audio.py 反推，可信度高）：
    m3u8 = "https://media.vd.st.nhk/news/easy_audio/%s/index.m3u8?hdnts=%s" % (base, hdnts)
"""
import json
import os
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

# 授权链的落点（从共享脚本反推）
TIX = "https://news.web.nhk/tix/build_authorize"
MEDIA_HOST = "https://media.vd.st.nhk"


def _get(url, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_token():
    """拿第一段 token。**失败就抛异常**（别返回空 dict 糊过去）。"""
    # 🔴 缺的是 idp 等参数；没有它 build_authorize 会回 {"error":"invalid idp"}。
    params = {
        "redirect_uri": "https://news.web.nhk/news/easy/",
        "idp": os.environ.get("NHK_IDP", ""),
    }
    if not params["idp"]:
        raise RuntimeError(
            "NHK 授权链缺 idp 参数：build_authorize 会返回 {\"error\":\"invalid idp\"}。\n"
            "  需要从 NHK ONE 客户端的登录/授权跳转里补出 idp/redirect_uri 等参数，\n"
            "  配方见 手册.md §5。")
    url = TIX + "?" + urllib.parse.urlencode(params)
    raw = _get(url).decode("utf-8", "replace")
    try:
        d = json.loads(raw)
    except Exception:
        raise RuntimeError("build_authorize 返回的不是 JSON：%r" % raw[:200])
    if d.get("error"):
        raise RuntimeError("build_authorize 失败：%s" % d.get("error"))
    return d


def media_token(token):
    """第一段 token → HLS 授权串 hdnts。"""
    if not token:
        raise RuntimeError("media_token(): 传入的 token 为空")
    # 媒体侧换票：把 token 交给 media.vd.st.nhk 换成 hdnts。
    # 🔴 这一步的**确切接口形状**在沙箱重置时丢了；下面是按 HLS 通用做法写的，
    #    拿到 403 时请对照手册 §5 补参数。
    url = "%s/api/v1/media_token" % MEDIA_HOST
    raw = _get(url, headers={"Authorization": "Bearer " + token}).decode("utf-8", "replace")
    try:
        d = json.loads(raw)
    except Exception:
        raise RuntimeError("media_token 返回的不是 JSON：%r" % raw[:200])
    hdnts = d.get("hdnts") or d.get("token")
    if not hdnts:
        raise RuntimeError("media_token 返回里没有 hdnts：%r" % raw[:200])
    return hdnts


def easy_audio_m3u8(voice_uri, hdnts):
    """慢速音的 HLS 地址（recipe 来自共享仓库 scripts/refetch_audio.py）。"""
    base = voice_uri[:-4] if voice_uri.endswith(".m4a") else voice_uri
    return "%s/news/easy_audio/%s/index.m3u8?hdnts=%s" % (MEDIA_HOST, base, hdnts)


if __name__ == "__main__":
    import sys
    t = get_token()
    print(json.dumps({k: (v[:12] + "…" if isinstance(v, str) and len(v) > 12 else v)
                      for k, v in t.items()}, ensure_ascii=False, indent=1))
    if len(sys.argv) > 1:
        print(media_token(t["token"]))
