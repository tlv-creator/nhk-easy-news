#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 发布闸门：**缓存版本三件套必须同号**（2026-10-25）

为什么这是硬闸门（不是「建议」）：
    app.js 里 `var APP_VER = 'vNN'` 决定**运行时抓 subs/*.json 的 URL**
    （app.js 的 subUrl()：`path + '?v=' + APP_VER`）
    sw.js 里 `const CACHE = 'nhk-easynews-vNN'` 决定 **Service Worker 的预缓存桶**
    ⇒ 如果这两个号不同步（或者发布时忘了升）：
       · 字幕 URL 不变 ⇒ Service Worker 的 stale-while-revalidate **先返回旧字幕**
         ⇒ 用户手机上「我更新了但还是旧的」（真因就在这儿，不是玄学）
       · 桶名不变 ⇒ 旧 SW 不重装 ⇒ 预缓存里的 data/episodes.js 还是旧的期数
    ⇒ 所以：**每次发布，APP_VER 与 CACHE 必须一起升到同一个号**。

判据（全部落在事实量上，缺一即 FAIL —— 不许「取不到就当过了」）：
    ① app.js 里能读出 APP_VER            ⇒ 读不到 = FAIL
    ② sw.js 里能读出 CACHE               ⇒ 读不到 = FAIL
    ③ APP_VER == CACHE                    ⇒ 不同 = FAIL
    ④ （给了期望值）APP_VER == 期望值      ⇒ 不同 = FAIL
    ⑤ version.json 的 ver == APP_VER 的号
       ⇒ 站点内容版本与缓存身份必须是同一个计数器
       （**ver 与 apkVer 是两回事**：ver 是内容版本，apkVer 是 APK 的 versionCode，
         拿 apkVer 去比 APP_VER 会恒假 ⇒ 见手册「版本提示的判据」）

用法：
    python3 scripts/check_cache_ver.py <app.js> <sw.js> [期望APP_VER]
    python3 scripts/check_cache_ver.py --dir <站点目录> [期望APP_VER]
        （目录里要有 app.js / sw.js / version.json）
    python3 scripts/check_cache_ver.py --url https://<域名>.space.mcode.cn [期望APP_VER]
        （**线上**核查：直接抓线上 app.js / sw.js / version.json）
"""
import argparse
import json
import os
import re
import sys
import urllib.request


def fail(msg):
    print("❌ " + msg)
    return 1


def read(p):
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def check(app, sw, ver_json, want, where):
    m = re.search(r"var\s+APP_VER\s*=\s*'([^']+)'", app)
    n = re.search(r"const\s+CACHE\s*=\s*'nhk-easynews-([^']+)'", sw)
    av = m.group(1) if m else None
    cv = n.group(1) if n else None
    print("  [%s] APP_VER = %s    CACHE = nhk-easynews-v%s"
          % (where, av, cv.lstrip("v") if cv else "?"))
    if not av:
        return fail("app.js 里读不出 APP_VER（var APP_VER = 'vNN'）")
    if not cv:
        return fail("sw.js 里读不出 CACHE（const CACHE = 'nhk-easynews-vNN'）")
    if av.lstrip("v") != cv.lstrip("v"):
        return fail("APP_VER(%s) 与 CACHE(v%s) 不同步 ⇒ 改了也发不到用户" % (av, cv))
    if want and av != want:
        return fail("APP_VER 应为 %s，实际 %s" % (want, av))
    if ver_json is not None:
        try:
            ver = json.loads(ver_json).get("ver")
        except Exception as e:
            return fail("version.json 解析失败：%s" % e)
        if ver is None:
            return fail("version.json 里没有 ver")
        print("  [%s] version.json ver = %s" % (where, ver))
        if str(ver) != av.lstrip("v"):
            return fail("version.json 的 ver(%s) 与 APP_VER(%s) 不同号 ⇒ "
                        "引导页的版本号与站点缓存身份会打架" % (ver, av))
    print("  ✅ [%s] 三件套同号（%s）" % (where, av))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="app.js sw.js [期望APP_VER]（或 --dir / --url 模式）")
    ap.add_argument("--dir", default=None, help="站点目录（含 app.js / sw.js / version.json）")
    ap.add_argument("--url", default=None, help="线上站点地址（https://域名.space.mcode.cn）")
    a = ap.parse_args()

    want = None
    if a.url:
        base = a.url.rstrip("/")
        if not base.startswith("http"):
            base = "https://" + base
        if a.paths:
            want = a.paths[0]
        try:
            app = fetch(base + "/app.js")
            sw = fetch(base + "/sw.js")
            vj = fetch(base + "/version.json")
        except Exception as e:
            return fail("线上抓取失败：%s" % e)
        return check(app, sw, vj, want, "线上")

    if a.dir:
        d = a.dir
        if a.paths:
            want = a.paths[0]
        app = read(os.path.join(d, "app.js"))
        sw = read(os.path.join(d, "sw.js"))
        vj = read(os.path.join(d, "version.json"))
        if app is None:
            return fail("读不到 %s" % os.path.join(d, "app.js"))
        if sw is None:
            return fail("读不到 %s" % os.path.join(d, "sw.js"))
        if vj is None:
            return fail("读不到 %s" % os.path.join(d, "version.json"))
        return check(app, sw, vj, want, d)

    if len(a.paths) < 2:
        print(__doc__)
        return 2
    app_p, sw_p = a.paths[0], a.paths[1]
    if len(a.paths) >= 3:
        want = a.paths[2]
    app = read(app_p)
    sw = read(sw_p)
    if app is None:
        return fail("读不到 %s" % app_p)
    if sw is None:
        return fail("读不到 %s" % sw_p)
    return check(app, sw, None, want, os.path.basename(os.path.dirname(app_p)) or "本地")


if __name__ == "__main__":
    sys.exit(main())
