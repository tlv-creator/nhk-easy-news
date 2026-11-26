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


def _curl_hop(url, jar, extra=None, timeout=40):
    """走一跳（不自动跳转），返回 (status, location, headers_text)"""
    cmd = ["curl", "-sS", "-m", str(timeout), "--retry", "2", "--retry-delay", "2",
           "-o", os.devnull, "-D", "-", "-c", jar, "-b", jar, "-A", UA]
    for k, v in (extra or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        r = _sp.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    except Exception:
        return 0, "", ""
    out = r.stdout or ""
    m = re.search(r"HTTP/\S+\s+(\d+)", out)
    code = int(m.group(1)) if m else 0
    lm = re.search(r"(?im)^location:\s*(.+?)\r?$", out)
    loc = lm.group(1).strip() if lm else ""
    return code, loc, out


def _curl_authorize(origin, redirect, q):
    """curl 版授权：**逐跳手动走**（2026-11-26 实测：`-L` 一把梭会 403 / 0，逐跳才稳）

    🔴 为什么改成逐跳（今天真踩到）：
        · 原来用 `curl -L` 自动跟完所有跳转 ⇒ IdP 那跳偶发 403 ⇒ 拿不到 z_at
        · 手动逐跳（并带上 cookie jar）⇒ 2026-11-26 实测一次就通
        · 每一跳都重试 2 次 ⇒ 抗瞬时抖动
    """
    import re as _re
    jar = _tf.NamedTemporaryFile(delete=False, suffix=".cookie").name
    url = f"{origin}/tix/build_authorize?{q}"
    hdr = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "ja,en;q=0.8",
           "Referer": f"{origin}/news/easy/"}
    token = None
    for _attempt in range(3):
        c1, l1, h1 = _curl_hop(url, jar, hdr)
        if not l1:
            time.sleep(2)
            continue
        c2, l2, h2 = _curl_hop(l1, jar, hdr)
        for m in _re.finditer(r"(?im)^set-cookie:\s*z_at=([^;]+)", h2 or ""):
            token = m.group(1)
        if token:
            break
        if l2:
            c3, l3, h3 = _curl_hop(l2, jar, {"Referer": f"{origin}/"})
            for m in _re.finditer(r"(?im)^set-cookie:\s*z_at=([^;]+)", h3 or ""):
                token = m.group(1)
            if token:
                break
            if 300 <= c3 < 400 and l3:
                c4, l4, h4 = _curl_hop(l3, jar, {"Referer": f"{origin}/"})
                for m in _re.finditer(r"(?im)^set-cookie:\s*z_at=([^;]+)", h4 or ""):
                    token = m.group(1)
                if token:
                    break
        time.sleep(3)
    expires = None
    cookies = {}
    try:
        for line in open(jar, encoding="utf-8", errors="replace"):
            if line.startswith("#") or not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) >= 7:
                cookies[f[5]] = f[6]
                if f[5] == "z_at":
                    token = f[6]
                elif f[5] == "exp_z_at":
                    try: expires = int(f[6])
                    except Exception: pass
    except Exception:
        pass
    # 🔴🔴 2026-11-26 修：**curl 兜底这条路必须把 cookie 一起带出来**
    #   坑：urllib 那条路在沙箱里常 TLS EOF ⇒ 落到 curl 兜底 ⇒ 但 cookie jar 在**临时文件**里
    #      ⇒ get_token() 返回的 cookies 还是空 ⇒ 下游 `cookie_header({})` = ""
    #      ⇒ news-list.json **不带 z_at** ⇒ 401 ⇒ 抓不到索引 ⇒ 没有音频/没有 easy.html
    #      ⇒ 而 update.py 的 sh() 只打印失败不中断 ⇒ **整轮静默跳过**（今天真踩到）
    #   ⇒ 修：curl 兜底时把 jar 里的 cookie 全部带出来
    if token:
        cookies["z_at"] = token
    return token, expires, cookies


def get_token(origin: str = ORIGIN, redirect: str = REDIRECT, verbose: bool = False) -> dict:
    """走一遍 accountless abroad 授权，返回 {token, expires, profile_id, scope, cookies}

    🔴🔴 2026-11-26 加**令牌缓存**（work/z_at.json）：
        · 授权链每跑一次都要打 3 跳（news.web.nhk → a.authz.ac1.nhk → idpresponse）
        · 连着跑几次 ⇒ IdP 那跳开始 403 ⇒ 整轮抓不到索引 ⇒ **静默无更新**
        · z_at 有效期 ~12h ⇒ 缓存起来，够用就别再授权
    """
    jar = http.cookiejar.CookieJar()
    # ① 先看缓存
    ctok, cexp, ccookies = _cache_load()
    if ctok:
        if verbose:
            print("  用缓存的 z_at（还剩 %.1f 小时）" % ((cexp - time.time()) / 3600))
        claims = token_claims(ctok)
        ccookies = dict(ccookies or {})
        ccookies["z_at"] = ctok
        return {"token": ctok, "expires": cexp, "profile_id": claims.get("profileId"),
                "scope": claims.get("scope"), "claims": claims, "cookies": ccookies, "jar": jar}
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
    cookies = {}
    try:
        with op.open(req, timeout=40) as r:
            r.read()
            if verbose:
                print("  final:", r.status, r.url)
        for c in jar:
            cookies[c.name] = c.value
            if c.name == "z_at":
                token = c.value
            elif c.name == "exp_z_at":
                expires = int(c.value)
    except Exception as e:
        # urllib 在沙箱里常 TLS EOF → 换 curl
        if verbose:
            print("  urllib 失败(%s)，改用 curl" % str(e)[:60])
        token, expires, cookies = _curl_authorize(origin, redirect, q)

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
                cookies["z_at"] = token
        except Exception as e:
            if verbose:
                print("  nhk_auth.py 兜底也失败：%s" % str(e)[:80])

    if not token:
        raise RuntimeError("未取得 z_at（origin=%s）" % origin)

    # 🔴🔴 2026-11-26 修：**cookies 里必须一定有 z_at**
    #   少了它 ⇒ 下游任何带鉴权的请求都变成「匿名」⇒ news-list 401 ⇒ 整轮静默无更新
    if token and not cookies.get("z_at"):
        cookies["z_at"] = token

    claims = token_claims(token)
    # 🔴 缓存（下次直接用，别再打授权链）
    _cache_save(token, claims.get("exp", 0), cookies)

    return {
        "token": token,
        "expires": expires,
        "profile_id": claims.get("profileId"),
        "scope": claims.get("scope"),
        "claims": claims,
        "cookies": cookies,
        "jar": jar,
    }


def token_claims(token: str) -> dict:
    try:
        return _b64url_decode(token.split(".")[1])
    except Exception:
        return {}


# ── z_at 缓存（2026-11-26）：授权链连打几次会被 IdP 403 ⇒ 缓存起来复用 ──
TOKEN_CACHE = os.environ.get("NHK_ZAT_CACHE", "/workspace/nhk/work/z_at.json")


def _cache_load():
    """返回 (token, exp, cookies)；没有/过期 ⇒ (None, None, {})"""
    try:
        d = json.load(open(TOKEN_CACHE, encoding="utf-8"))
        tok = d.get("token")
        exp = int(d.get("exp") or 0)
        if tok and exp - 300 > time.time():
            return tok, exp, dict(d.get("cookies") or {})
    except Exception:
        pass
    return None, None, {}


def _cache_save(tok, exp, cookies):
    try:
        os.makedirs(os.path.dirname(TOKEN_CACHE), exist_ok=True)
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            json.dump({"token": tok, "exp": exp, "cookies": cookies}, f,
                      ensure_ascii=False)
    except Exception:
        pass


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
