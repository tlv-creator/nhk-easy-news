#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 滚动更新「不可删站清单」（protected-sites.json）

为什么要有这个脚本：
    保护清单里写着「现役内容站」—— 但**现役站每次发版都换域名**，
    清单不会自己跟着变 ⇒ 过期的「现役」标记会**保护不该保护的旧站**
    （旧站一直占着配额 ⇒ 50 个配额几天就满 ⇒ 部署失败）。
    ⇒ 所以每次发版都要**滚动**更新它。

🔴 纪律（别把别的项目的条目弄坏）：
    · 顶层是 **dict**，条目在 **protected_sites 数组**里
    · **只动本项目（project=nhk）的条目**，别的项目的一个字节都不改
    · 数量对不上 ⇒ 拒绝写入（防止把别人的 48 条覆盖成 3 条）

用法：
    python3 scripts/update_protected_sites.py --new <新域名> --old <旧域名> [--push]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json"
LOCAL = os.path.join(ROOT, "boot", "protected-sites.json")
API = "https://api.github.com"
PATH = "/repos/tlv-creator/nhk-easy-news/contents/protected-sites.json"


def get_token():
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    p = "/workspace/.secrets/gh_token"
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def api(method, body=None, token=None):
    req = urllib.request.Request("https://api.github.com" + PATH,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read().decode()
        return json.loads(b) if b.strip() else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True, help="本次部署的新站域名")
    ap.add_argument("--old", default="", help="上一版内容站域名（会被标 SUPERSEDED）")
    ap.add_argument("--note", default="")
    ap.add_argument("--supersede", default="",
                    help="逗号分隔：这些域名一并标 SUPERSEDED（可删）")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    # 1) 取线上那份（线上才是权威；本地只是兜底副本）
    data = None
    try:
        req = urllib.request.Request(LIVE, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        print("✅ 取到线上清单")
    except Exception as e:
        print("⚠️ 线上取不到（%s）⇒ 用本地副本" % e)
        data = json.load(open(LOCAL, encoding="utf-8"))

    if not isinstance(data, dict) or "protected_sites" not in data:
        sys.exit("❌ 清单结构不对（顶层必须是 dict，条目在 protected_sites 数组）⇒ 拒绝写入")
    before = len(data["protected_sites"])
    ps = data["protected_sites"]

    # 2) 旧现役站 → SUPERSEDED（不再是现役 ⇒ 不再占着「保护」名额）
    #    ⚠️ 旧站**可能压根没在清单里**（清单是滚动维护的，不是每次都记全）
    #    ⇒ 没找到就**补一条 SUPERSEDED 记录**（记着它被谁取代 ⇒ 别再被当成活引用）
    if a.old:
        hit = False
        for s in ps:
            if s.get("domain") == a.old and s.get("project") == "nhk":
                s.pop("role", None)
                s["status"] = "SUPERSEDED"
                s["note"] = ("2026-10-24 已被 %s 取代（引导页已切走）⇒ "
                             "**不再是现役站**，可删（保留记录是为了别再被误当成活引用）" % a.new)
                hit = True
        if not hit:
            ps.append({
                "domain": a.old, "project": "nhk", "status": "SUPERSEDED",
                "reason": "NHK 上一版内容站",
                "note": ("2026-10-24 已被 %s 取代（引导页已切走）⇒ **不再是现役站**，可删"
                         "（保留记录是为了别再被误当成活引用）" % a.new),
                "frozen": False,
            })
        print("   旧现役站 %s → SUPERSEDED%s" % (a.old, "" if hit else "（补记）"))

    # 2b) 其它还挂着 role=current_site 的**本项目**旧站 ⇒ 一并降级
    #     （清单不会自己跟着发版变 ⇒ 过期标记会保护不该保护的旧站 ⇒ 白占配额）
    for s in ps:
        if (s.get("project") == "nhk" and s.get("role") == "current_site"
                and s.get("domain") != a.new and not s.get("status")):
            s.pop("role", None)
            s["status"] = "SUPERSEDED"
            s["note"] = ("2026-10-24 已被 %s 取代 ⇒ **不再是现役站**，可删"
                         "（保留记录是为了别再被误当成活引用）" % a.new)
            print("   过期的「现役」标记 %s → SUPERSEDED" % s.get("domain"))

    # 2c) 显式点名要降级的旧站（例如清单里还挂着「现役内容站」字样但早已不是）
    #     🔴 只降级**本项目**的条目（domain 在本项目登记表里），别的项目不碰
    reg = os.path.join(ROOT, "sites_nhk.json")
    reg_doms = set()
    try:
        for s in json.load(open(reg, encoding="utf-8")).get("sites", []):
            if (s.get("project") or "") == "nhk":
                reg_doms.add(s.get("domain"))
    except Exception:
        pass
    for dom in [x.strip() for x in a.supersede.split(",") if x.strip()]:
        if dom == a.new or dom in ("vkwwuh9wgkzo0",):
            continue
        if reg_doms and dom not in reg_doms:
            print("   ⚠️ %s 不在本项目登记表里 ⇒ 不动（别碰别的项目的站）" % dom)
            continue
        hit = False
        for s in ps:
            if s.get("domain") == dom:
                s.pop("role", None)
                s["status"] = "SUPERSEDED"
                s["note"] = ("2026-10-24 已被 %s 取代 ⇒ **不再是现役站**，可删"
                             "（保留记录是为了别再被误当成活引用）" % a.new)
                hit = True
        if not hit:
            ps.append({"domain": dom, "project": "nhk", "status": "SUPERSEDED",
                       "reason": "NHK 旧内容站",
                       "note": ("2026-10-24 已被 %s 取代 ⇒ 可删" % a.new),
                       "frozen": False})
        print("   点名降级 %s → SUPERSEDED" % dom)

    # 3) 新站登记（现役 ⇒ 保护）
    found = False
    for s in ps:
        if s.get("domain") == a.new and s.get("project") == "nhk":
            s.pop("status", None)
            s["role"] = "current_site"
            s["reason"] = "NHK 内容站（引导页当前指向）"
            s["written_in"] = "引导页 version.json 的 site 字段"
            s["note"] = ("🔴 **现役内容站** —— 域名每次发版都换 ⇒ "
                         "以引导页 version.json 的 site 为准；更新内容 = 新建站 + 改引导页 + 才删旧的")
            found = True
    if not found:
        ps.append({
            "domain": a.new, "project": "nhk", "role": "current_site",
            "reason": "NHK 内容站（引导页当前指向）",
            "written_in": "引导页 version.json 的 site 字段",
            "note": ("🔴 **现役内容站** —— 域名每次发版都换 ⇒ "
                     "以引导页 version.json 的 site 为准；更新内容 = 新建站 + 改引导页 + 才删旧的"),
            "frozen": False,
        })
    print("   现役站 %s 已登记" % a.new)

    # 4) 别把别的项目的条目弄坏
    after = len(ps)
    if after < before:
        sys.exit("❌ 条目数从 %d 变成 %d ⇒ 疑似丢了别人的条目 ⇒ 拒绝写入" % (before, after))
    data["updated"] = date.today().isoformat()
    if a.note:
        data["_last_roll"] = a.note

    txt = json.dumps(data, ensure_ascii=False, indent=2)
    open(LOCAL, "w", encoding="utf-8").write(txt)
    print("✅ 本地副本已写：%s（条目 %d → %d）" % (LOCAL, before, after))

    if a.push:
        token = get_token()
        if not token:
            sys.exit("❌ 没有 token ⇒ 没法推线上")
        cur = api("GET", token=token)
        body = {"message": "chore: 滚动更新不可删站清单（NHK 现役站 → %s）" % a.new,
                "content": __import__("base64").b64encode(txt.encode()).decode(),
                "branch": "main"}
        if cur.get("sha"):
            body["sha"] = cur["sha"]
        api("PUT", body=body, token=token)
        print("✅ 已推上线（%s）" % LIVE)


if __name__ == "__main__":
    main()
