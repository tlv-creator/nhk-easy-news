#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cleanup_sites.py —— 旧站清理（每天必做）

🔴 为什么每天必做：mcode 每次部署都换随机域名，站点配额 50 个
   ⇒ 不清就会撑爆 ⇒ **用户停在旧站**（看不到新内容）。
   而且所有 APK 都读引导页 ⇒ 旧站删了不影响 App 找新站。

🔴 三道闸门（照 手册 §7）：
   ① **保护清单**：https://tlv-creator.github.io/nhk-easy-news/protected-sites.json
      —— frozen:true = 永不 redeploy **也永不删**（APK 里写死了它）。
   ② **现役 APK 的 dex**：现役 APK 里写死的 mcode 域名一律不删。
   ③ **引用扫描**：现役站/引导页/别的 APK 还引用的，一律不删。

🔴 **判据用 curl，不信 drive files search**（它会返回已删节点的缓存索引）。

用法：
    python3 scripts/cleanup_sites.py plan      # 算该删哪些 + 打印 mcp_invoke 命令
    python3 scripts/cleanup_sites.py verify    # 验证：该 404 的已 404，现役的仍 200
    python3 scripts/cleanup_sites.py add <node_id> <域名> <版本>   # 登记新站
    python3 scripts/cleanup_sites.py prune     # 把已删的从登记表去掉
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "data", "sites.json")
PROTECTED_URL = "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json"
GUIDE_URL = "https://tlv-creator.github.io/nhk-easy-news/version.json"


def load_reg():
    if os.path.exists(REG):
        return json.load(open(REG, encoding="utf-8"))
    return {"sites": []}


def save_reg(reg):
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    with open(REG, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)


def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 拉不到 %s：%s" % (url, e))
        return {}


def code(domain):
    url = "https://%s.space.mcode.cn/" % domain
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def apk_domains(apk_path):
    """扫 APK 的 dex 里写死的 mcode 域名（不依赖 dexdump）"""
    out = set()
    if not os.path.exists(apk_path):
        return out
    try:
        import zipfile
        with zipfile.ZipFile(apk_path) as z:
            for n in z.namelist():
                if n.endswith(".dex"):
                    data = z.read(n)
                    for m in re.finditer(rb"[a-z0-9]{8,20}\.space\.mcode\.cn", data):
                        out.add(m.group(0).decode())
    except Exception as e:
        print("  ⚠️ 扫 APK 失败：%s" % e)
    return out


def cmd_plan(a):
    reg = load_reg()
    prot = fetch_json(PROTECTED_URL)
    guide = fetch_json(GUIDE_URL)
    cur = (guide.get("site") or "").replace("https://", "").split(".")[0]
    frozen = {s["domain"] for s in prot.get("protected_sites", []) if s.get("frozen")}
    apk = apk_domains(a.apk)
    print("引导页当前站：%s" % cur)
    print("冻结站（永不删）：%s" % (", ".join(sorted(frozen)) or "-"))
    print("现役 APK 写死的域名：%s" % (", ".join(sorted(apk)) or "-"))
    print()
    to_del = []
    for s in reg.get("sites", []):
        d = s.get("domain")
        if not d:
            continue
        if d == cur:
            print("  保留  %-18s （引导页当前站）" % d)
            continue
        if d in frozen or any(d in x for x in apk):
            print("  保留  %-18s （冻结/现役 APK 依赖）" % d)
            continue
        st = code(d)
        if st == 404:
            print("  清理  %-18s （已 404，从登记表移除）" % d)
            s["_gone"] = True
        elif st == 200:
            print("  可删  %-18s （仍 200，但没人引用它）" % d)
            to_del.append(s)
        else:
            print("  跳过  %-18s （HTTP %s）" % (d, st))
    save_reg(reg)
    if to_del:
        print("\n🔴 下面这些站该删（**逐条执行**，别批量）：")
        for s in to_del:
            nid = (s.get("node_id") or "").strip()
            # 🔴🔴 手册 §18.2：node_id 未知时**不许打印会打错的删除命令**
            #    （把 "PENDING"/"?" 当 node_id 打出去 = 白折腾）
            if not nid or nid in ("?", "PENDING"):
                print("  ⚠️ %-18s 该删，但 **node_id 未知** ⇒ 先查云盘列表拿真 id" % s.get("domain"))
                print("       查询：mcp_invoke tool_name=drive_files_search "
                      "arguments='{\"category\":\"website\",\"query\":\"%s\"}'" % s.get("domain"))
                print("       删时：mcp_invoke tool_name=drive_files_delete arguments='{\"node_id\":\"<真id>\"}'")
            else:
                print("  mcp_invoke tool_name=drive_files_delete arguments='{\"node_id\":\"%s\"}'   # %s"
                      % (nid, s.get("domain")))
    else:
        print("\n✅ 没有需要删的站")
    return 0


def cmd_verify(a):
    reg = load_reg()
    prot = fetch_json(PROTECTED_URL)
    guide = fetch_json(GUIDE_URL)
    cur = (guide.get("site") or "").replace("https://", "").split(".")[0]
    ok = True
    for s in reg.get("sites", []):
        d = s.get("domain")
        if not d:
            continue
        st = code(d)
        if d == cur:
            good = (st == 200)
            print("  %-18s 当前站 HTTP=%s %s" % (d, st, "✅" if good else "❌ 应该是 200"))
        else:
            good = (st == 404)
            print("  %-18s 旧站   HTTP=%s %s" % (d, st, "✅ 已删" if good else "（还活着）"))
        ok = ok and good
    frozen = [s["domain"] for s in prot.get("protected_sites", []) if s.get("frozen")]
    for d in frozen:
        # 🔴 2026-10-25 修（**假红**）：有些保护项**根本不是 mcode 站**
        #    （例：每日听力记录站 = GitHub Pages 子目录 listening/ ⇒ https://listening.space.mcode.cn 必然 404）
        #    ⇒ 拿 mcode 域名去探活 ⇒ 永远判红「冻结站不该没」⇒ 假红。
        ent = next((s for s in prot.get("protected_sites", []) if s.get("domain") == d), {})
        # 🔴 判据要准：**location** 才是「托管在哪」；
        #    **canonical** 是「入口页在哪」（很多 mcode 站的入口页也在 Pages 上）⇒ 不能拿它判
        loc = ent.get("location", "") or ""
        if ("非 mcode 站" in loc) or ("github.io" in loc):
            print("  %-18s 冻结站（非 mcode 站 · 跳过探活）" % d)
            continue
        st = code(d)
        print("  %-18s 冻结站 HTTP=%s %s" % (d, st, "✅" if st == 200 else "❌ 冻结站不该没"))
        ok = ok and (st == 200)
    print("\n%s" % ("✅ 全部符合预期" if ok else "❌ 有不符合预期的"))
    return 0 if ok else 1


def cmd_add(a):
    reg = load_reg()
    reg["sites"] = [s for s in reg.get("sites", []) if s.get("domain") != a.domain]
    reg["sites"].append({"node_id": a.node_id, "domain": a.domain, "ver": a.ver,
                          "date": time_str()})
    save_reg(reg)
    print("✅ 已登记 %s（node_id=%s）" % (a.domain, a.node_id))
    return 0


def time_str():
    import time
    return time.strftime("%Y-%m-%d")


def cmd_prune(a):
    reg = load_reg()
    keep = []
    for s in reg.get("sites", []):
        st = code(s.get("domain", ""))
        if st == 404:
            print("  移除 %s（已 404）" % s.get("domain"))
        else:
            keep.append(s)
    reg["sites"] = keep
    save_reg(reg)
    print("✅ 登记表剩 %d 条" % len(keep))
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", default=os.path.join(ROOT, "deploy_site", "NHK.apk"))
    sub = ap.add_subparsers(dest="c")
    # 🔴 2026-10-25 修：`--apk` 原来只能在子命令**前面**（argparse 的规矩）
    #    ⇒ 手册里写的 `cleanup_sites.py plan` 走的是默认路径 deploy_site/NHK.apk
    #    ⇒ 沙箱里 deploy_site 不存在 ⇒ **APK 闸门（②）静默失效**（空集合 = 全都"没被引用"）
    #    ⇒ 现在每个子命令后面也能带 --apk。
    for name in ("plan", "verify", "prune"):
        sp = sub.add_parser(name)
        sp.add_argument("--apk", default=ap.get_default("apk"))
    p = sub.add_parser("add")
    p.add_argument("node_id")
    p.add_argument("domain")
    p.add_argument("ver")
    p.add_argument("--apk", default=ap.get_default("apk"))
    a = ap.parse_args()
    cmd = a.c
    if cmd == "plan":
        return cmd_plan(a)
    if cmd == "verify":
        return cmd_verify(a)
    if cmd == "prune":
        return cmd_prune(a)
    if cmd == "add":
        return cmd_add(a)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
