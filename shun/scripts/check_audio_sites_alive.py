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
    # 🔴 两个键都认（线上权威键是 `sites`；`protected_sites` 线上从来不存在）
    _lst = d.get("sites") if isinstance(d.get("sites"), list) else d.get("protected_sites")
    for x in (_lst or []):
        if isinstance(x, dict) and x.get("domain"):
            out[x["domain"]] = x
    return out, ""


# 🔴 2026-10-27（跨会话修复）：清单里条目数组的键名有两个
#    · `sites`            —— **线上权威键**（清单现在就是这个结构）
#    · `protected_sites`  —— 老代码在用，但**线上从来就没有这个键**
#    ⇒ 只认旧键的实现一律读到**空列表**：
#      清理脚本的「冻结保护集」为空 ⇒ 可能误删现役站；
#      闸门则变成「校验代码在、校验对象是空的」⇒ **恒绿**。
#    ⇒ 两个键都认（收进词汇表，不是把线上的改回去）；
#      写端**必须写进线上实际在用的那个键**，否则又写进没人读的数组。
def _sites_key(obj):
    for _k in ("sites", "protected_sites"):
        if isinstance(obj.get(_k), list):
            return _k
    return "sites"          # 两个都没有 ⇒ 建 `sites`（与线上结构一致）


def _sites_of(obj):
    """取条目数组（并保证写回时用的是同一个键）。"""
    _k = _sites_key(obj)
    _v = obj.get(_k)
    if not isinstance(_v, list):
        _v = []
        obj[_k] = _v
    return _v


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


def _ep_set(v):
    """missing_eps 可能是**数组**也可能是**JSON 字符串** ⇒ 两种都认。

    🔴 踩过的坑：清单里写的是字符串 '["ep13"]'
       ⇒ 若直接 set(v) ⇒ 变成 {'[','"','e','p','1','3',']'} ⇒ 豁免**静默失效**
       ⇒ 闸门于是报假红（而真相是「豁免已登记」）
    ⇒ 所以：**同一个值两种形态都要认**，且解析失败必须显式报错，不能静默当空。
    """
    if v is None or v == "":
        return set()
    if isinstance(v, (list, tuple, set)):
        return set(str(i) for i in v)
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            try:
                arr = json.loads(t)
                return set(str(i) for i in arr)
            except Exception as e:  # noqa: BLE001
                raise SystemExit("❌ 清单里 missing_eps 解析失败（%s）：%r" % (e, v))
        return {t}
    raise SystemExit("❌ 清单里 missing_eps 类型不认识：%r" % (v,))

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
            known["https://%s.space.mcode.cn/media/" % dom] = _ep_set(x.get("missing_eps"))
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
