#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_audio_sites_alive.py —— 闸门：受保护清单里的**音频站**必须真能取到每一集。

🔴 为什么需要（2026-10-24 主线课程站反馈）：
   `check_apk_domains.py` 只查「这个域名在不在受保护清单里」
   ⇒ **查不出「这个域名已经死了」**
   ⇒ 而「域名在清单里 + 站已经 404」= **引导页指向死站 ⇒ 全部音频哑掉**
   ⇒ 而且**现场看不出来**（播放器条照样亮着，只是 currentTime 不推进）

🔴 判据（落在「真的能取到字节」上，不是 HEAD）：
   · **必须 GET + Range**（HEAD 在 mcode 站上偶发 404 ⇒ 假红）
   · 每一集都要试（漏一集 = 那一集哑）
   · Range 206 或 200 都算通过（取到字节即可）

用法：
    python3 scripts/check_audio_sites_alive.py
    python3 scripts/check_audio_sites_alive.py --site https://xxx.space.mcode.cn/media/
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
PROTECTED = "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json"
EPS = ["ep%02d" % i for i in range(1, 14)]


def get_protected():
    try:
        with urllib.request.urlopen(PROTECTED, timeout=40) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    out = {}
    for x in d.get("protected_sites") or []:
        if isinstance(x, dict) and x.get("domain"):
            out[x["domain"]] = x
    return out, ""


def probe(base, tag):
    """GET + Range 取 1 字节 ⇒ 能取到就算活。返回 (ok, detail)"""
    url = "%s%s.m4a" % (base, tag)
    req = urllib.request.Request(url, headers={"Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            n = len(r.read(2048))
            if n > 0:
                return True, "%d B (HTTP %s)" % (n, r.status)
            return False, "0 字节 (HTTP %s)" % r.status
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="", help="只查这一个 media 目录")
    ap.add_argument("--eps", default="", help="逗号分隔，默认 ep01..ep13")
    args = ap.parse_args()
    eps = args.eps.split(",") if args.eps else EPS
    bases = []
    if args.site:
        bases = [args.site.rstrip("/") + "/"]
    else:
        prot, err = get_protected()
        if prot is None:
            print("❌ 拉不到受保护清单（%s）⇒ **不放行**" % err)
            return 2
        for dom, x in prot.items():
            if x.get("role") == "audio-source" and x.get("status") != "DELETED":
                bases.append("https://%s.space.mcode.cn/media/" % dom)
    if not bases:
        print("❌ 清单里没有 audio-source 站点 ⇒ 不放行")
        return 2
    # 🔴 显式豁免：清单条目里可以写 `missing_eps: ["ep13"]`
    #    （老音频站是**兜底**，本来就少一集 ⇒ 已知且可接受）
    #    ⇒ 豁免必须**在清单里登记**，不能靠「我以为它少」⇒ 状态漂移就红
    known = {}
    if not args.site:
        prot, _ = get_protected()
        for dom, x in (prot or {}).items():
            known["https://%s.space.mcode.cn/media/" % dom] = set(
                x.get("missing_eps") or [])
    bad = []
    for base in bases:
        print("=== %s" % base)
        exp_missing = known.get(base, set())
        dead = []
        for tag in eps:
            ok, detail = probe(base, tag)
            if ok:
                print("  ✅ %s  %s" % (tag, detail))
            else:
                if tag in exp_missing:
                    print("  ⚠ %s  %s（**清单已登记为已知缺失**）" % (tag, detail))
                else:
                    print("  ❌ %s  %s" % (tag, detail))
                dead.append(tag)
        unexpected = [t for t in dead if t not in exp_missing]
        registered = [t for t in dead if t in exp_missing]
        if unexpected:
            bad.append((base, unexpected))
            print("  ✗ 未登记的缺失：%s" % ",".join(unexpected))
        elif registered:
            print("  ✓ %d 集全在（另有 %d 集按清单登记为已知缺失）" %
                  (len(eps) - len(registered), len(registered)))
        else:
            print("  ✓ %d 集全在" % len(eps))
    if bad:
        print("\n✗ %d 个音频站有取不到的集" % len(bad))
        return 1
    print("\n✓ 所有音频站的每一集都能取到")
    return 0


if __name__ == "__main__":
    sys.exit(main())
