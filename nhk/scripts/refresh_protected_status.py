#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 把「不可删站清单」里**已经 404 的本项目站点**标成 DELETED（滚动维护）

为什么：
    清单里的 status 是「滚动维护」的（靠人记得跑 update_protected_sites.py）
    ⇒ 实际上经常**过期**：站早就删了，清单还写着 SUPERSEDED / current_site
    ⇒ 后果 ①：闸门①误以为它「还有保护」⇒ 不敢删（其实它早没了）
           ②：`role=current_site` 挂在死站上 ⇒ **现役站反而不受保护**（今天就是这样：
              ac8e29hqy5sv5 挂着 current_site，其实 404 了；
              真正的现役 em8hjp6jskxyb 反而没在清单里 ⇒ 清理时可能被误删！）

规矩（照旧）：
    · **只动 project=nhk 的条目**（别的项目一个字节都不改）
    · 判定「已删」用 **curl 判 404**（不信 drive files search 的缓存索引）
    · 探不到（网络问题）⇒ 不动（宁可不动，不可误标）
    · 当前站（引导页指向的）⇒ status=ACTIVE + role=current_site（**必须受保护**）

用法：
    python3 scripts/refresh_protected_status.py            # 只改本地
    python3 scripts/refresh_protected_status.py --push     # 改完推线上
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, "boot", "protected-sites.json")
LIVE = "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json"
API = "https://api.github.com"
PATH = "/repos/tlv-creator/nhk-easy-news/contents/protected-sites.json"
PROJ = "nhk"


def code(domain):
    try:
        req = urllib.request.Request("https://%s.space.mcode.cn/" % domain,
                                     headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def token():
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    p = "/workspace/.secrets/gh_token"
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def api(method, body=None, tok=None):
    req = urllib.request.Request("https://api.github.com" + PATH,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read().decode()
        return json.loads(b) if b.strip() else {}


def current_site():
    """引导页当前指向的站（唯一权威）。

    取不到 ⇒ **不许猜**：不知道现役站是谁，就没法保护它 ⇒ 宁可失败。
    """
    for _ in range(2):
        try:
            url = "https://tlv-creator.github.io/nhk-easy-news/version.json"
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read().decode())
            m = re.search(r"([0-9a-z]+)\.space\.mcode\.cn", j.get("site", ""))
            if m:
                return m.group(1)
        except Exception:
            pass
    # 线上取不到 ⇒ 用本地底稿（push_boot.py 会同步写）
    try:
        j = json.load(open(os.path.join(ROOT, "boot", "version.json"), encoding="utf-8"))
        m = re.search(r"([0-9a-z]+)\.space\.mcode\.cn", j.get("site", ""))
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    data = None
    try:
        req = urllib.request.Request(LIVE, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        print("✅ 取到线上清单（%d 条）" % len(data.get("protected_sites", [])))
    except Exception as e:
        print("⚠️ 线上取不到（%s）⇒ 用本地副本" % e)
        data = json.load(open(LOCAL, encoding="utf-8"))
    if not isinstance(data, dict) or "protected_sites" not in data:
        sys.exit("❌ 清单结构不对 ⇒ 拒绝写入")
    ps = data["protected_sites"]
    before = len(ps)
    before_doms = {s.get("domain") for s in ps}
    cur = current_site()
    print("   引导页当前站：%s" % cur)
    if not cur:
        sys.exit("❌ 取不到引导页当前站 ⇒ 不知道该保护谁 ⇒ 拒绝写入（不猜）")

    changed = 0
    have_cur = False
    # 🔴 先做去重：**同一个 domain 只留一条**（主会话加的条目常常没写 project）
    #    ⇒ 不去重就会出现两条同域名的记录 ⇒ 闸门判断会打架
    seen = {}
    dedup = []
    for s in ps:
        d = s.get("domain")
        if not d or "." in d:
            dedup.append(s)
            continue
        if d in seen:
            seen[d].update({k: v for k, v in s.items() if k not in seen[d]})
            print("   ⚠️ %s 有重复条目 ⇒ 已合并（保留 %d → %d 条）" % (d, len(ps), len(dedup) + 1))
            changed += 1
            continue
        seen[d] = s
        dedup.append(s)
    ps[:] = dedup

    for s in ps:
        d = s.get("domain")
        if not d or "." in d:
            continue
        if d == cur:
            have_cur = True
            s["project"] = PROJ
            s["status"] = "ACTIVE"
            s["role"] = "current_site"
            s.setdefault("reason", "NHK 内容站（引导页当前指向）")
            s["written_in"] = "引导页 version.json 的 site 字段"
            s["note"] = ("🔴 **现役内容站** —— 域名每次发版都换 ⇒ "
                         "以引导页 version.json 的 site 为准；更新内容 = 新建站 + 改引导页 + 才删旧的")
            s["frozen"] = False
            print("   ✅ %s → ACTIVE / current_site" % d)
            changed += 1
            continue
        if s.get("project") != PROJ:
            continue
        if s.get("status") == "DELETED":
            if s.get("role") == "current_site":
                # 死站不该挂着「现役」标记 ⇒ 否则**真正的现役站反而没保护**
                s.pop("role", None)
                print("   ✅ %s 已死却挂着 current_site ⇒ 清掉" % d)
                changed += 1
            continue
        if s.get("frozen"):
            # 🔴 冻结站（兜底站 / 音频源站）**永不删、永不 redeploy**
            #    ⇒ 它的 status 一律不参与「能不能删」的判断 ⇒ 这里不碰它
            if s.get("status"):
                s.pop("status", None)
                print("   ✅ %s 是冻结站 ⇒ 清掉 status（冻结本身就是保护）" % d)
                changed += 1
            continue
        c = code(d)
        if c == 404:
            s["status"] = "DELETED"
            s.pop("role", None)
            s["note"] = "已删（curl 判 404，%s 核过）⇒ 不可删也不必再保护" % date.today().isoformat()
            print("   ✅ %s → DELETED（404 实锤）" % d)
            changed += 1
        elif c == 200:
            if s.get("status") != "SUPERSEDED":
                s["status"] = "SUPERSEDED"
                s.pop("role", None)
                print("   · %s 活着但不是现役 → SUPERSEDED（可删）" % d)
                changed += 1
        else:
            print("   ⚠️ %s 探不到（%s）⇒ 不动" % (d, c))

    added = 0
    if cur and not have_cur:
        # 🔴 现役站**必须**在清单里（不然清理时它没有保护 ⇒ 可能被误删！）
        ps.append({
            "domain": cur, "project": PROJ, "status": "ACTIVE",
            "role": "current_site",
            "reason": "NHK 内容站（引导页当前指向）",
            "written_in": "引导页 version.json 的 site 字段",
            "note": ("🔴 **现役内容站** —— 域名每次发版都换 ⇒ "
                     "以引导页 version.json 的 site 为准；更新内容 = 新建站 + 改引导页 + 才删旧的"),
            "frozen": False,
        })
        added = 1
        changed += 1
        print("   ✅ %s 不在清单里 ⇒ 补进去（ACTIVE / current_site）" % cur)

    # 域名的**集合**不许变小（不许丢别人的站），只许多出「现役站」这一条
    after_doms = {s.get("domain") for s in ps}
    lost = before_doms - after_doms
    if lost:
        sys.exit("❌ 少了这些域名：%s ⇒ 拒绝写入" % lost)
    extra = after_doms - before_doms
    if extra - {cur}:
        sys.exit("❌ 多出了这些域名：%s ⇒ 拒绝写入" % (extra - {cur}))
    data["updated"] = date.today().isoformat()
    txt = json.dumps(data, ensure_ascii=False, indent=2)
    open(LOCAL, "w", encoding="utf-8").write(txt)
    print("✅ 本地副本已写：%s（改了 %d 条）" % (LOCAL, changed))

    if a.push:
        tok = token()
        if not tok:
            sys.exit("❌ 没有 token ⇒ 没法推线上")
        cur_file = api("GET", tok=tok)
        body = {"message": "chore: 刷新不可删站清单状态（404 → DELETED，现役 → ACTIVE）",
                "content": __import__("base64").b64encode(txt.encode()).decode(),
                "branch": "main"}
        if cur_file.get("sha"):
            body["sha"] = cur_file["sha"]
        api("PUT", body=body, tok=tok)
        print("✅ 已推上线（%s）" % LIVE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
