#!/usr/bin/env python3
"""
NHK ONE 「免账号 · 海外」授权（accountlessAuthZ / a-alaz）

来源：news.web.nhk 前端 chunk
  getTokenBFFEndpoint = e => `${window.location.origin}/tix/${e}`
  loginAccountlessAbroad(): idp=a-alaz, profileType=abroad, entity=none,
                            area=130, pref=13, jisx0402=13101

拿到 z_at（JWT），scope 一般含 "get:news get:tv"，有效期 ~12h。
不需要 NHK 账号，也不需要受信合约（海外 accountless 通道）。

只依赖标准库（沙箱 PyPI 不可达）。
"""
import base64
import os
import gzip
import io
import json
import time
import urllib.parse
import urllib.request
import http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ORIGIN = "https://news.web.nhk"
REDIRECT = "https://news.web.nhk/news/easy/"


def _b64url_decode(seg: str) -> dict:
    seg += "=" * (-len(seg) % 4)
    return json.loads(base64.urlsafe_b64decode(seg.encode()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跳转，用来逐跳看清授权链路。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(jar):
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPRedirectHandler(),
    )



# ---------------- curl 后备（沙箱里 urllib 连某些站点会 TLS EOF，curl 正常）----------------
import subprocess as _sp, tempfile as _tf


def _curl(url, headers=None, cookie=None, out=None, timeout=40, follow=True):
    """用 curl 发请求，返回 (status, body_bytes)。失败抛 RuntimeError。"""
    cmd = ["curl", "-s", "-L" if follow else "", "--max-time", str(timeout),
           "-A", UA, "-w", "\n__HTTP__%{http_code}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if cookie:
        cmd += ["-H", f"Cookie: {cookie}"]
    if out:
        cmd += ["-o", out]
    cmd.append(url)
    r = _sp.run([c for c in cmd if c], capture_output=True, timeout=timeout + 15)
    raw = r.stdout
    m = __import__("re").search(rb"__HTTP__(\d+)\s*$", raw)
    code = int(m.group(1)) if m else 0
    body = raw[:m.start()] if m else raw
    if code == 0:
        raise RuntimeError("curl 失败: %s" % r.stderr.decode(errors="replace")[:200])
    return code, body


def _curl_authorize(origin, redirect, q):
    """curl 版授权：-L 跟随重定向，-c/-b 存 cookie"""
    jar = _tf.NamedTemporaryFile(delete=False, suffix=".cookie").name
    url = f"{origin}/tix/build_authorize?{q}"
    cmd = ["curl", "-s", "-L", "--max-time", "90", "--retry", "2", "--retry-delay", "2",
           "-c", jar, "-b", jar,
           "-A", UA,
           "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "-H", "Accept-Language: ja,en;q=0.8",
           "-H", f"Referer: {origin}/news/easy/",
           "-o", os.devnull, "-w", "%{http_code}", url]
    r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
    token = expires = None
    try:
        for line in open(jar, encoding="utf-8", errors="replace"):
            if line.startswith("#") or not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) >= 7:
                if f[5] == "z_at":
                    token = f[6]
                elif f[5] == "exp_z_at":
                    try: expires = int(f[6])
                    except Exception: pass
    except Exception:
        pass
    return token, expires


def get_token(origin: str = ORIGIN, redirect: str = REDIRECT, verbose: bool = False) -> dict:
    """走一遍 accountless abroad 授权，返回 {token, expires, profile_id, scope, cookies}"""
    jar = http.cookiejar.CookieJar()
    op = _opener(jar)
    q = urllib.parse.urlencode({
        "idp": "a-alaz",
        "profileType": "abroad",
        "redirect_uri": redirect,
        "entity": "none",
        "area": "130",
        "pref": "13",
        "jisx0402": "13101",
    })
    url = f"{origin}/tix/build_authorize?{q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
        "Referer": f"{origin}/news/easy/",
    })
    token = expires = None
    try:
        with op.open(req, timeout=40) as r:
            r.read()
            if verbose:
                print("  final:", r.status, r.url)
        for c in jar:
            if c.name == "z_at":
                token = c.value
            elif c.name == "exp_z_at":
                expires = int(c.value)
    except Exception as e:
        # urllib 在沙箱里常 TLS EOF → 换 curl
        if verbose:
            print("  urllib 失败(%s)，改用 curl" % str(e)[:60])
        token, expires = _curl_authorize(origin, redirect, q)

    if not token:
        # 🔴🔴 2026-10-25 加**第二套实现**兜底：nhk_auth.py（共享仓库版）
        #   背景：主会话实测「授权链 400」的**真因是没带 cookie jar**（不是 idp 参数集丢了），
        #   而我这边的 urllib+CookieJar / curl -c -b 两条路都带 jar ⇒ 仍可能因沙箱 TLS 抖动而失败。
        #   ⇒ 两套**独立实现**（urllib / curl / 共享脚本）⇒ 任一成功即可。
        #   ⇒ 共享脚本在 /workspace/nhk/nhk_auth.py（从引导页仓库取回）。
        if verbose:
            print("  主实现没拿到 z_at ⇒ 换 nhk_auth.py 兜底")
        try:
            import nhk_auth
            token = nhk_auth.get_zat()
            if token:
                expires = None
                for k, v in (jar or {}).items():
                    if k == "z_at":
                        token = v
        except Exception as e:
            if verbose:
                print("  nhk_auth.py 兜底也失败：%s" % str(e)[:80])

    if not token:
        raise RuntimeError("未取得 z_at（origin=%s）" % origin)

    claims = token_claims(token)
    return {
        "token": token,
        "expires": expires,
        "profile_id": claims.get("profileId"),
        "scope": claims.get("scope"),
        "claims": claims,
        "cookies": {c.name: c.value for c in jar},
        "jar": jar,
    }


def token_claims(token: str) -> dict:
    try:
        return _b64url_decode(token.split(".")[1])
    except Exception:
        return {}


def cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def api_get(path: str, token: str, cookies: dict = None, base: str = "https://api.web.nhk/r8",
            timeout: int = 30) -> tuple:
    """带 z_at 去打 NHK 的 r8 API。返回 (status, body_bytes)"""
    url = path if path.startswith("http") else f"{base}/{path.lstrip('/')}"
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "Origin": "https://news.web.nhk",
        "Referer": "https://news.web.nhk/",
    }
    if cookies:
        h["Cookie"] = cookie_header(cookies)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


if __name__ == "__main__":
    t = get_token(verbose=True)
    c = t["claims"]
    print("z_at 拿到 ✅")
    print("  scope      :", c.get("scope"))
    print("  profileType:", c.get("profileType"))
    print("  client_id  :", c.get("client_id"))
    print("  profileId  :", t["profile_id"])
    print("  exp        :", time.strftime("%Y-%m-%d %H:%M", time.localtime(c.get("exp", 0))))
    # 试一个新闻文章
    st, body = api_get("t/newsarticle/na/nd-20261021de62637.json", t["token"], t["cookies"])
    print(f"\n  t/newsarticle/na/... -> HTTP {st}  {len(body)}B")
    print("  ", body[:300])
