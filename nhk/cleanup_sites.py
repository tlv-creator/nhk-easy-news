#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云盘站点清理助手 —— 防止 50 个站点配额被每日部署撑爆。

🔴 为什么必须有这个脚本
   mcode 平台每次部署都换新域名、不能原地更新 → **每天新增一个站点**。
   配额上限 50 → 不清理的话 **50 天后必然部署失败**（引导页也会跟着更新失败，
   用户手机上的 App 就停在旧站）。

📌 用法（给 cron / agent 用）
   python3 cleanup_sites.py list          # 看当前登记了哪些站
   python3 cleanup_sites.py plan           # 输出「该删哪些」+ 可直接执行的命令
   python3 cleanup_sites.py add <node_id> <domain> <ver> [--keep]
                                            # 部署完登记新站
   python3 cleanup_sites.py mark <domain> --keep | --nokeep
   python3 cleanup_sites.py prune           # 把已删的从登记表里去掉（每天跑）
   python3 cleanup_sites.py verify         # 验证待删的确实已 404

🔴 删除动作必须由 agent 用平台工具执行（脚本没权限直连 drive API）：
       mcp_invoke drive files delete <node_id>
   本脚本会把这个命令原样打印出来，别手抄错 node_id。

🔴 删除前必看两条硬规则（保护名单见 清理规则.md）：
   1. **引导页当前指向的站永远不删** —— 那是用户正在用的。
   2. **别的项目（哆啦/若草/魔卡…）的站不删** —— 只清同 project。

🔴 删除是**真副作用**，不是试探。
   `drive files search` 会返回已删节点的缓存索引（看着还在，其实没了）→ 别信它！
   真正的验证：`curl -o /dev/null -w "%{http_code}" https://<域名>.space.mcode.cn/`
   已删 → 404；还在 → 200。
"""
import json
import re
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/nhk")
REG = ROOT / "sites_nhk.json"   # 🔴 不要动 sites.json —— 那是全站快照（别的脚本生成的）
# 🔴🔴 全局「不可删除的站」清单（永久地址，所有项目共用）
#    别的项目的 APK 可能把它们的兜底站编译进 dex → 删了 = 那些用户的 App 白屏
#    ⇒ 清理站点前**必须**先拉这份清单（本地兜底一份，防网络不通）。
PROTECTED_URL = "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json"
PROTECTED_LOCAL = ROOT / "boot" / "protected-sites.json"

BOOT = "https://tlv-creator.github.io/nhk-easy-news/version.json"
PROJ = "nhk"          # 本项目代号；别的项目用别的代号登记，不受影响


# ---------------------------------------------------------------- 登记表
def load_reg():
    if not REG.exists():
        return {"sites": []}
    try:
        j = json.loads(REG.read_text(encoding="utf-8"))
    except Exception:
        return {"sites": []}
    # 兼容两种存法：{"sites":[...]} 或直接 [...]
    if isinstance(j, list):
        return {"sites": j}
    if isinstance(j, dict) and isinstance(j.get("sites"), list):
        return j
    return {"sites": []}


def save_reg(reg):
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def boot_site():
    """引导页当前指向的站 —— 绝对不能删。"""
    try:
        req = urllib.request.Request(BOOT, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode())
        return (j.get("site") or "").rstrip("/"), j.get("verName", "")
    except Exception as e:
        print(f"  ⚠️ 读引导页失败：{type(e).__name__}: {e}", file=sys.stderr)
        return "", ""


def apk_fallback_domain():
    """从 apk/app/src/com/nhk/easynews/Config.java 读 SITE_URL → 提取域名。
    🔴 这就是「引导页读不到时 App 落的那个地址」→ **永远不能删**。
    （读不到就返回空 → 退化成「只保护引导页当前站」）

    🔴🔴 2026-10-23 加了两个来源，因为**单一来源会失效**：
      ① Config.java —— 会被 build.sh 的 sed 改，也会被我手工还原成 PLACEHOLDER
         （v1.12 就是这么打出来的：dex 里写死 PLACEHOLDER，而源码早就不是那个值了）
      ② **已构建的 APK**（site/app/NHK.apk）—— 这才是用户手机上真正跑的东西，
         源码随便怎么改都影响不到它
    两个来源取并集，再叠加 HARD_KEEP 硬名单。
    """
    doms = set()

    # ① 源码里的 Config.java
    cfg = ROOT / "apk" / "app" / "src" / "com" / "nhk" / "easynews" / "Config.java"
    try:
        txt = cfg.read_text(encoding="utf-8")
        # ⚠️ 域名后面还跟一个 '/'（"https://xxx.space.mcode.cn/"）→
        #    贪婪的 [A-Za-z0-9.-]+ 会一路吃到引号前再回溯 → 永远匹配不上 → 返回空 → 兜底站失去保护！
        m = re.search(r'SITE_URL\s*=\s*"https?://([A-Za-z0-9.-]+?)/?"', txt)
        if m:
            d = m.group(1).split(".")[0]
            if d and d != "PLACEHOLDER":
                doms.add(d)
    except Exception:
        pass

    # ② 已构建的 APK 的 dex（ground truth）
    try:
        import zipfile
        apk = ROOT / "site" / "app" / "NHK.apk"
        dex = zipfile.ZipFile(apk).read("classes.dex")
        for mm in re.findall(rb'([a-z0-9]{4,})\.space\.mcode\.cn', dex):
            doms.add(mm.decode())
    except Exception:
        pass

    # ③ 硬名单：永删不得（哪怕前两个来源都失效）
    doms |= set(HARD_KEEP)

    for d in doms:
        if d:
            return d
    return ""


# 🔴 硬保护名单：**永删不得**。
#    理由：兜底站是「引导页读不到时 App 唯一能落地的地方」，
#    而它的来源是**会失效的**（源码被 sed 改 / APK 是旧版 / 登记表更新了）。
#    → 唯一可靠的做法是：**把它写死在代码里**，不依赖任何动态来源。
HARD_KEEP = {"9chr6us9mlatt"}


def protected_domains():
    """拉线上「不可删除的站」清单 → 返回受保护域名集合（拉不到就用本地副本）。

    🔴 为什么必须有：跨项目共享云盘 → 我清自己的旧站时可能删掉别的项目的兜底站
       （2026-10-23 真的踩了：NHK 的兜底站被别的 agent 当成「测试里的假域名」删掉
        → NHK App 引导页读不到就白屏）。
    """
    doms = set()
    raw = None
    try:
        req = urllib.request.Request(PROTECTED_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
    except Exception as e:
        print("  ⚠️ 拉不到线上保护清单（%s）→ 改用本地副本" % type(e).__name__, file=sys.stderr)
    if raw is None:
        try:
            raw = PROTECTED_LOCAL.read_text(encoding="utf-8")
        except Exception:
            raw = "{}"
    try:
        j = json.loads(raw)
        for s in j.get("protected_sites", []):
            d = (s.get("domain") or "").strip()
            # 🔴 只有「**没标状态**」的条目才是真正受保护的。
            #    DELETED   = 已经删了（留记录是为了别再被当成活引用）
            #    SUPERSEDED = 已被新站取代（**不再保护** ⇒ 正是要腾配额的对象）
            #    ⇒ 以前只排除 DELETED ⇒ SUPERSEDED 的旧站被清单永久保护着
            #      ⇒ 白占配额 ⇒ 50 个配额几天就满 ⇒ 部署失败。
            if d and not s.get("status"):
                doms.add(d)
    except Exception:
        pass
    return doms


def http_code(domain):
    url = f"https://{domain}.space.mcode.cn/"
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


# ---------------------------------------------------------------- 命令
def cmd_list(reg):
    site, ver = boot_site()
    print(f"  引导页当前站：{site}（ver {ver}）")
    print(f"  登记表 {REG}：{len(reg.get('sites', []))} 条\n")
    for s in reg.get("sites", []):
        dom = s.get("domain") or s.get("dom") or ""
        tag = "★当前" if site and dom and dom in site else ("【保护】" if s.get("keep") else "")
        print(f"   {dom:16} {s.get('project') or s.get('proj',''):6} v{s.get('ver',''):6} {tag}")


def cmd_plan(reg):
    """算：该删哪些。输出可直接执行的 mcp_invoke 命令。"""
    site, ver = boot_site()
    cur = site.replace("https://", "").replace(".space.mcode.cn", "").strip("/") if site else ""
    # 🔴 兜底站 = **APK 里 Config.SITE_URL 写死的那个**（引导页读不到时 App 就落到它）
    #    ⚠️ 不能用「登记表里的上一版」推 —— 那是「最近部署的上一版」，
    #    不一定是 APK 里写死的那个（我今天就踩过：按上一版推 → 兜底被删 → App 白屏）。
    prev = apk_fallback_domain()
    prot = protected_domains()          # 🔴 跨项目共享的受保护站
    if prot:
        print("  受保护站清单（线上）: %s" % ", ".join(sorted(prot)))
    doomed = []
    unknown_nid = []
    for s in reg.get("sites", []):
        dom = s.get("domain") or s.get("dom") or ""
        nid = str(s.get("node_id") or s.get("nid") or "").strip()
        prj = s.get("project") or s.get("proj") or ""
        if not dom:
            continue
        if prj and prj != PROJ:
            continue                      # 别的项目的站 → 不碰
        if cur and dom == cur:
            continue                      # 引导页当前站 → 不碰
        # 🔴 上一版站 = APK 里的兜底地址（Config.SITE_URL）→ **必须留**
        #   引导页读不到时（GitHub Pages 慢/被墙）App 就落到它 → 删了就白屏
        if prev and dom == prev:
            continue                      # 上一版（兜底）→ 不碰
        if dom in prot:
            continue                      # 🔴 线上共享清单里 → 一律不碰
        if s.get("keep"):
            continue                      # 显式保护 → 不碰
        # 🔴 node_id 未知（PENDING / 空）⇒ **不许打印一条会打错的删除命令**
        if not nid or not nid.isdigit():
            unknown_nid.append({"domain": dom, "ver": s.get("ver", ""),
                                 "ts": s.get("ts", "")})
            continue
        doomed.append({"domain": dom, "node_id": nid, "ver": s.get("ver", "")})

    if unknown_nid:
        print("  ⚠️ 这些站**可删**，但登记表里没有 node_id（要人工从云盘「网站」列表查）：")
        for s in unknown_nid:
            print("   - %-16s v%-8s (%s)" % (s["domain"], s.get("ver", ""), s.get("ts", "")))
        print()

    if not doomed:
        print("  ✓ 没有需要清理的站（引导页当前站与保护站都保留）")
        return 0

    print(f"  引导页当前站：{cur or '(读不到)'}")
    print(f"  待删 {len(doomed)} 个旧站：\n")
    for s in doomed:
        print(f"   - {s['domain']:16} v{s.get('ver','')}  ({s.get('ts','')})")
    print("\n  执行下面这些命令（一条一条跑，删完再跑 verify）：\n")
    for s in doomed:
        print(f'   mcp_invoke drive files delete {s["node_id"]}')
    print()
    return 0


def cmd_add(reg, node_id, domain, ver, keep=False):
    reg.setdefault("sites", [])
    for s in reg["sites"]:
        if s["node_id"] == node_id:
            s.update({"domain": domain, "ver": ver, "ts": datetime.now().strftime("%Y-%m-%d")})
            save_reg(reg)
            print(f"  ✓ 已更新登记：{domain}")
            return 0
    reg["sites"].append({
        "node_id": node_id, "domain": domain, "project": PROJ,
        "ver": ver, "ts": datetime.now().strftime("%Y-%m-%d"), "keep": bool(keep),
    })
    save_reg(reg)
    print(f"  ✓ 已登记：{domain}（node {node_id}）")
    return 0


def cmd_mark(reg, domain, keep):
    hit = False
    for s in reg.get("sites", []):
        if (s.get("domain") or s.get("dom")) == domain:
            s["keep"] = keep
            hit = True
    if hit:
        save_reg(reg)
        print(f"  ✓ {domain} → keep={keep}")
    else:
        print(f"  ⚠️ 表里没有 {domain}")
    return 0


def cmd_prune(reg):
    """把已经不在了的站从登记表里去掉 —— 免得每天 plan 都要去删一遍不存在的站。"""
    site, _ = boot_site()
    cur = site.replace("https://", "").replace(".space.mcode.cn", "").strip("/") if site else ""
    keep, gone = [], []
    for s in reg.get("sites", []):
        dom = s.get("domain") or s.get("dom") or ""
        if not dom:
            continue
        if cur and dom == cur:
            keep.append(s); continue
        c = http_code(dom)
        if c in (404, 0):
            gone.append((dom, c))
        else:
            keep.append(s)
    reg["sites"] = keep
    save_reg(reg)
    print(f"  ✓ 移除 {len(gone)} 个已不存在的站；登记表剩 {len(keep)} 条")
    for d, c in gone:
        print(f"     - {d:16} (HTTP {c})")
    return 0


def cmd_verify(reg):
    site, _ = boot_site()
    cur = site.replace("https://", "").replace(".space.mcode.cn", "").strip("/") if site else ""
    prev = apk_fallback_domain()
    bad = 0
    for s in reg.get("sites", []):
        prj = s.get("project") or s.get("proj") or ""
        if prj and prj != PROJ:
            continue
        dom = s.get("domain") or s.get("dom") or ""
        if not dom:
            continue
        c = http_code(dom)
        # 🔴 兜底站（APK 里写死的那个）永远期望 200 —— plan 里保护了它，verify 也要一致，
        #    否则每次体检都报红，久而久之就误以为兜底站坏了。
        keep = bool(s.get("keep")) or (prev and dom == prev)
        want = "200" if (dom == cur or keep) else "404"
        mark = "✓" if str(c) == want else "✗"
        if str(c) != want:
            bad += 1
        print(f"   {mark} {dom:16} HTTP {c}（期望 {want}）")
    print(f"\n  {'✓ 全部符合预期' if bad == 0 else f'⚠️ {bad} 个不符合预期'}")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    reg = load_reg()
    if cmd == "list":
        return cmd_list(reg)
    if cmd == "plan":
        return cmd_plan(reg)
    if cmd == "add":
        if len(sys.argv) < 5:
            print("  用法：cleanup_sites.py add <node_id> <domain> <ver> [--keep]")
            return 2
        keep = "--keep" in sys.argv[5:]
        return cmd_add(reg, sys.argv[2], sys.argv[3], sys.argv[4], keep)
    if cmd == "mark":
        if len(sys.argv) < 4:
            print("  用法：cleanup_sites.py mark <domain> --keep|--nokeep")
            return 2
        return cmd_mark(reg, sys.argv[2], "--keep" in sys.argv[3:])
    if cmd == "prune":
        return cmd_prune(reg)
    if cmd == "verify":
        return cmd_verify(reg)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
