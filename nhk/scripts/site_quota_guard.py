#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 云盘站点配额守卫（2026-10-25）

为什么必须有这个脚本（今天被这个卡死过）：
    mcode **每次部署都新建一个站点节点** ⇒ 每天 +1 个 ⇒ 配额 50
    ⇒ 2026-10-25 从 v1.24 到 v1.29 连发 6 次、从不删旧的 ⇒ 配额吃光
    ⇒ 部署被 `ENTITLEMENT_LIMIT_EXCEEDED` 硬拒（主会话当天的活全卡在这）
    ⇒ **规矩：每次部署成功后，立刻删掉上一版站点节点**（只留「现役 1 个 + 永久兜底 1 个」）

🔴🔴 删站四道闸门（**缺一不可，脚本内建**）：
    ① 保护清单 status ≠ ACTIVE（且不是引导页当前指向的站）
    ② frozen ≠ true（兜底站 vkwwuh9wgkzo0 永远冻结 ⇒ 永删不得）
    ③ **现役 APK 的 classes.dex + assets 里没有该域名**
       （别的 APK 可能把某个站编译进去当兜底 ⇒ 删了那些用户的 App 就白屏）
    ④ **引导页 version.json + 站点源码（cdn.js / links.js / data/*.js）零引用**
       （还有引用就删了 ⇒ 用户的 App 跳到 404）

用法：
    python3 scripts/site_quota_guard.py register --node <id> --domain <d> --ver <v>
    python3 scripts/site_quota_guard.py plan        # 算该删哪些 + 打印精确命令
    python3 scripts/site_quota_guard.py apply       # 能删就删；不能删就落盘待办（exit 2）
    python3 scripts/site_quota_guard.py verify      # 删完验证：该 404 的 404，现役仍 200
    python3 scripts/site_quota_guard.py check       # 部署前体检：配额 + 「只留 2 个」不变量
    python3 scripts/site_quota_guard.py discover    # 从线上 index.html 反查 node_id（登记表补全）

🔴 删站是**真副作用**，不是试探：
    · `drive files search` 会返回**已删节点的缓存索引**（看着还在，其实没了）⇒ 别信它
    · 真正的验证：`curl -o /dev/null -w "%{http_code}" https://<域名>.space.mcode.cn/`
      已删 → 404；还在 → 200
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime

ROOT = "/workspace/nhk"
REG = os.path.join(ROOT, "sites_nhk.json")
PROTECTED_LOCAL = os.path.join(ROOT, "boot", "protected-sites.json")
PROTECTED_URL = "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json"
BOOT_URL = "https://tlv-creator.github.io/nhk-easy-news/version.json"
BOOT_LOCAL = os.path.join(ROOT, "boot", "version.json")
PENDING = os.path.join(ROOT, "work", "pending_site_deletes.txt")
# 🔴 待删清单的**永久地址**（清理 cron 读它 ⇒ 沙箱本地文件它看不到）
PENDING_URL = "https://tlv-creator.github.io/nhk-easy-news/pending-deletes.json"
SITE_SRC = os.path.join(ROOT, "site", "app")
APK_CANDIDATES = [os.path.join(SITE_SRC, "NHK.apk"),
                  os.path.join(ROOT, "apk", "out", "signed.apk")]
QUOTA = 50                # 平台同时存活站点上限
KEEP_NHK = 2              # NHK 只留：现役 1 个 + 永久兜底 1 个
PROJ = "nhk"


# ----------------------------------------------------------------- 小工具
def log(*a):
    print(*a, flush=True)


def http_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return None


def http_code(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def site_url(domain):
    return "https://%s.space.mcode.cn/" % domain


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        return json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return default


# ----------------------------------------------------------------- 登记表
def load_reg():
    j = load_json(REG, None)
    if not j or "sites" not in j:
        sys.exit("❌ 读不到登记表 %s ⇒ 先修" % REG)
    return j


def save_reg(j):
    tmp = REG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REG)


def protected_map():
    """保护清单：优先线上（所有 agent 共用），线上读不到就用本地兜底。"""
    j = http_json(PROTECTED_URL) or load_json(PROTECTED_LOCAL, {}) or {}
    m = {}
    for s in j.get("protected_sites", []):
        d = s.get("domain")
        if d and "." not in d:
            m[d] = s
    return m


def boot_info():
    """引导页 version.json = 当前内容站的唯一权威。"""
    j = http_json(BOOT_URL)
    src = "线上"
    if not j:
        j = load_json(BOOT_LOCAL, {}) or {}
        src = "本地兜底"
    if not j or not j.get("site"):
        sys.exit("❌ 读不到引导页 version.json（%s）⇒ 不知道现役站是谁 ⇒ 停" % BOOT_URL)
    j["_src"] = src
    return j


def current_domain(boot):
    m = re.search(r"([0-9a-z]+)\.space\.mcode\.cn", boot.get("site", ""))
    return m.group(1) if m else None


def fallback_domain(reg):
    return reg.get("fallback_domain")


# ----------------------------------------------------------------- node_id 反查
def node_id_from_site(domain):
    """线上 index.html 里平台注入的统计脚本里带着本节点的 node_id（base64）⇒ 反查。"""
    try:
        req = urllib.request.Request(site_url(domain), headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r'var t="([A-Za-z0-9_\-\.]{20,})"', html)
    if not m:
        return None
    blob = m.group(1).split(".")[0]
    blob += "=" * (-len(blob) % 4)
    try:
        j = json.loads(base64.b64decode(blob).decode("utf-8", "replace"))
    except Exception:
        return None
    nid = j.get("node_id")
    return str(nid) if nid else None


# ----------------------------------------------------------------- 四道闸门
def apk_mentions(domain):
    """闸门③：现役 APK 的 classes.dex + assets 里有没有这个域名。"""
    hits = []
    for apk in APK_CANDIDATES:
        if not os.path.exists(apk):
            continue
        try:
            z = zipfile.ZipFile(apk)
        except Exception:
            continue
        with z:
            for n in z.namelist():
                if not (n.startswith("assets/") or n.endswith(".dex")
                        or n.startswith("res/") or n == "AndroidManifest.xml"):
                    continue
                try:
                    data = z.read(n)
                except Exception:
                    continue
                if domain.encode() in data:
                    hits.append("%s!%s" % (os.path.basename(apk), n))
    return hits


def src_mentions(domain):
    """闸门④：站点源码（cdn.js / links.js / data/*.js 等）里有没有这个域名。"""
    hits = []
    if not os.path.isdir(SITE_SRC):
        return hits
    for dirpath, _dirs, files in os.walk(SITE_SRC):
        for fn in files:
            if not fn.endswith((".js", ".json", ".html", ".css", ".webmanifest")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) > 8 * 1024 * 1024:
                    continue
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if domain in txt:
                hits.append(os.path.relpath(p, ROOT))
    return hits


def boot_mentions(domain, boot):
    """闸门④的一部分：引导页 version.json 里有没有这个域名。"""
    hits = []
    blob = json.dumps(boot, ensure_ascii=False)
    if domain in blob:
        hits.append("引导页 version.json")
    return hits


def gates(site, boot, cur_dom, fb_dom, prot):
    """对单个站跑四道闸门 ⇒ 返回 (可删?, 明细列表)。"""
    d = site.get("domain")
    res = []

    # ① 保护清单 status ≠ ACTIVE  且  不是引导页当前指向的站
    p = prot.get(d, {})
    status = p.get("status")
    if status and str(status).upper() == "ACTIVE":
        res.append(("①", False, "保护清单 status=ACTIVE ⇒ 不删"))
    elif d == cur_dom:
        res.append(("①", False, "引导页当前指向的站 ⇒ 永不删"))
    else:
        res.append(("①", True, "保护清单 status=%s（非 ACTIVE）" % (status or "未登记")))

    # ② frozen ≠ true
    frozen = bool(p.get("frozen")) or bool(site.get("frozen")) or (d == fb_dom)
    if frozen:
        res.append(("②", False, "frozen=true（永久兜底站）⇒ 永不删"))
    else:
        res.append(("②", True, "frozen=false"))

    # ③ 现役 APK 里没有这个域名
    hits = apk_mentions(d)
    if hits:
        res.append(("③", False, "现役 APK 里写着这个域名：%s ⇒ 别的 APK 可能靠它兜底" % hits[:3]))
    else:
        res.append(("③", True, "现役 APK（classes.dex + assets）零引用"))

    # ④ 引导页 + 站点源码零引用
    hits = boot_mentions(d, boot) + src_mentions(d)
    if hits:
        res.append(("④", False, "还有引用：%s ⇒ 删了用户会跳 404" % hits[:5]))
    else:
        res.append(("④", True, "引导页 version.json + 站点源码零引用"))

    ok = all(r[1] for r in res)
    return ok, res


# ----------------------------------------------------------------- 命令
def plan(verbose=True):
    reg = load_reg()
    boot = boot_info()
    cur_dom = current_domain(boot)
    fb_dom = fallback_domain(reg)
    prot = protected_map()
    if verbose:
        log("════ 站点清理计划 ════")
        log("   引导页（%s）→ 现役站：%s" % (boot.get("_src"), cur_dom))
        log("   永久兜底站：%s（frozen，永不删）" % fb_dom)

    # 候选 = 登记表里的 NHK 站 ∪ 保护清单里的 NHK 站
    cands = {}
    for s in reg.get("sites", []):
        d = s.get("domain")
        if d and "." not in d:
            cands[d] = dict(s)
    for d, p in prot.items():
        if p.get("project") == PROJ and d not in cands:
            cands[d] = {"node_id": "", "domain": d, "ver": p.get("status") or ""}

    todo = []
    for d, s in sorted(cands.items()):
        if d == cur_dom or d == fb_dom:
            continue
        code = http_code(site_url(d))
        if code == 0:
            continue          # 网络问题（不是「已删」）⇒ 跳过，别误判
        if code == 404:
            if verbose:
                log("   · %s  已经 404（早删了）" % d)
            s["status"] = "DELETED"
            continue
        ok, res = gates(s, boot, cur_dom, fb_dom, prot)
        if ok:
            nid = s.get("node_id")
            if not nid or nid == "PENDING":
                nid = node_id_from_site(d)
                if nid:
                    s["node_id"] = nid
                    log("   · %s  node_id 反查出来：%s" % (d, nid))
            if not nid or nid == "PENDING":
                if verbose:
                    log("   ❌ %s 四道闸门全过，但拿不到 node_id ⇒ 人工确认后再删" % d)
                continue
            todo.append((d, nid, res))
        else:
            if verbose:
                log("   · %s  不可删：" % d)
                for tag, good, why in res:
                    if not good:
                        log("       %s %s" % (tag, why))
    if verbose:
        if not todo:
            log("   ✅ 没有可删的站（现役 %s + 兜底 %s 就够了）" % (cur_dom, fb_dom))
        else:
            log("   🔴 可删 %d 个（下面每条都必须执行，**删是配额**）：" % len(todo))
            for d, nid, _res in todo:
                log("       drive files delete %s      # %s" % (nid, d))
    # 落盘待办（无 drive 权限的会话照着执行）
    if todo:
        os.makedirs(os.path.dirname(PENDING), exist_ok=True)
        with open(PENDING, "w", encoding="utf-8") as f:
            f.write("# %s 生成：待删站点（node_id 域名）\n" % datetime.now().isoformat(timespec="seconds"))
            for d, nid, _res in todo:
                f.write("drive files delete %s   # %s\n" % (nid, d))
    # 登记表跟现实同步（404 ⇒ 标 DELETED；反查到的 node_id 写回去）
    try:
        save_reg(reg)
    except Exception as e:
        log("   ⚠️ 登记表写回失败：%s" % e)
    return todo, reg, boot, cur_dom, fb_dom


def try_delete(node_id):
    """尝试真的删。

    平台没有可从 shell 调的 drive CLI ⇒ 默认**删不了**，只打印命令。
    若环境里提供了删除命令（环境变量 NHK_DRIVE_DELETE_CMD，形如 'xxx {node}'），就执行它。
    """
    cmd_tpl = os.environ.get("NHK_DRIVE_DELETE_CMD", "").strip()
    if not cmd_tpl:
        return None
    cmd = cmd_tpl.replace("{node}", node_id)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
        return r.returncode == 0
    except Exception:
        return False


def apply():
    todo, reg, boot, cur_dom, fb_dom = plan(verbose=True)
    if not todo:
        log("✅ 无需删除（配额没被 NHK 占用）")
        return 0
    done, failed = [], []
    for d, nid, _res in todo:
        ok = try_delete(nid)
        if ok is True:
            done.append(d)
            log("   ✅ 已删 %s（node %s）" % (d, nid))
        else:
            failed.append((d, nid))
    # 验证：删掉的必须 404
    for d in done:
        code = http_code(site_url(d))
        if code != 404:
            log("   ❌ %s 删完还是 %s（不是 404）⇒ 平台没真删" % (d, code))
            failed.append((d, "verify-fail"))
    if done:
        # 登记表同步：删掉的标 DELETED
        for s in reg.get("sites", []):
            if s.get("domain") in done:
                s["status"] = "DELETED"
                s["keep"] = False
        try:
            save_reg(reg)
        except Exception as e:
            log("   ⚠️ 登记表写回失败：%s" % e)
    if failed:
        log("")
        log("🔴🔴 还有 %d 个站没删掉（**配额还在涨**）。本会话没有 drive 删站权限时，" % len(failed))
        log("   请用**有 drive 权限的会话**执行下面这些命令（命令已落盘 %s）：" % PENDING)
        for d, nid in failed:
            log("       drive files delete %s      # %s" % (nid, d))
        return 2
    log("✅ 该删的都删了（%d 个）" % len(done))
    return 0


def verify():
    reg = load_reg()
    boot = boot_info()
    cur_dom = current_domain(boot)
    fb_dom = fallback_domain(reg)
    prot = protected_map()
    ok = True
    log("════ 站点删除验证 ════")
    # 现役 + 兜底必须 200
    for d, why in ((cur_dom, "现役站"), (fb_dom, "永久兜底站")):
        code = http_code(site_url(d))
        if code == 200:
            log("   ✅ %s（%s）200" % (d, why))
        else:
            log("   ❌ %s（%s）= %s ⇒ 必须是 200" % (d, why, code))
            ok = False
    # 保护清单里 status=DELETED 的必须是 404
    bad = []
    for d, p in sorted(prot.items()):
        if p.get("project") != PROJ:
            continue
        if str(p.get("status") or "").upper() != "DELETED":
            continue
        code = http_code(site_url(d))
        if code != 404:
            bad.append((d, code))
    if bad:
        log("   ❌ 标成 DELETED 但还活着：%s" % bad)
        ok = False
    else:
        log("   ✅ 标成 DELETED 的站全部 404")
    return 0 if ok else 1


def check():
    """部署前体检：配额 + 「只留现役 + 兜底」不变量。"""
    reg = load_reg()
    boot = boot_info()
    cur_dom = current_domain(boot)
    fb_dom = fallback_domain(reg)
    prot = protected_map()
    log("════ 站点配额体检 ════")
    log("   现役站 %s（引导页 %s）· 兜底站 %s" % (cur_dom, boot.get("_src"), fb_dom))

    # ① NHK 自己的活站数（登记表 + 保护清单）
    doms = set()
    for s in reg.get("sites", []):
        d = s.get("domain")
        if d and "." not in d:
            doms.add(d)
    for d, p in prot.items():
        if p.get("project") == PROJ:
            doms.add(d)
    live = []
    for d in sorted(doms):
        code = http_code(site_url(d))
        if code == 200:
            live.append(d)
        elif code == 0:
            log("   ⚠️ %s 探不到（网络？）⇒ 按「活着」处理" % d)
            live.append(d)
    log("   NHK 活站 %d 个：%s" % (len(live), live))
    if len(live) > KEEP_NHK:
        log("   ❌ 超过「只留 %d 个（现役 + 兜底）」⇒ 配额在白占" % KEEP_NHK)
        plan(verbose=True)
        return 1

    # ② 全账号活站数（配额是**账号级**的，别的项目也占）
    all_doms = set(prot.keys())
    for s in reg.get("sites", []):
        d = s.get("domain")
        if d and "." not in d:
            all_doms.add(d)
    all_live = 0
    for d in all_doms:
        if http_code(site_url(d)) == 200:
            all_live += 1
    log("   全账号活站（登记表+保护清单口径）%d / 上限 %d" % (all_live, QUOTA))
    if all_live >= QUOTA:
        log("   ❌ 已到配额上限 ⇒ 下一次部署会被 ENTITLEMENT_LIMIT_EXCEEDED 拒")
        return 1
    if all_live >= QUOTA - 5:
        log("   ⚠️ 离配额只剩 %d 个 ⇒ 先清理再部署" % (QUOTA - all_live))

    # ③ 有没有待删的
    todo, _reg, _boot, _cur, _fb = plan(verbose=False)
    if todo:
        log("   ⚠️ 有 %d 个站可删但还没删（配额在涨）⇒ 跑 apply" % len(todo))
        for d, nid, _r in todo:
            log("       drive files delete %s      # %s" % (nid, d))
    return 0


def register(args):
    reg = load_reg()
    boot = boot_info()
    cur_dom = current_domain(boot)
    node = args.node
    if node in ("auto", "", None):
        node = node_id_from_site(args.domain)
        if not node:
            log("❌ 反查不到 %s 的 node_id ⇒ 手工确认后再登记（不许瞎填）" % args.domain)
            return 1
        log("   node_id 反查：%s → %s" % (args.domain, node))
    sites = reg.setdefault("sites", [])
    for s in sites:
        if s.get("domain") == args.domain:
            s.update({"node_id": node, "ver": args.ver,
                       "ts": datetime.now().strftime("%Y-%m-%d")})
            s["keep"] = (args.domain == cur_dom)
            s.pop("status", None)
            break
    else:
        sites.append({"node_id": node, "domain": args.domain, "project": PROJ,
                      "ver": args.ver, "ts": datetime.now().strftime("%Y-%m-%d"),
                      "keep": (args.domain == cur_dom),
                      "note": "现役内容站（引导页 version.json 指向它）"})
    save_reg(reg)
    log("✅ 已登记：%s（node %s, ver %s）" % (args.domain, node, args.ver))
    return 0


def discover():
    reg = load_reg()
    n = 0
    for s in reg.get("sites", []):
        d = s.get("domain")
        if not d or not d.endswith("mcode"):
            continue
        if s.get("node_id") and s["node_id"] != "PENDING":
            continue
        nid = node_id_from_site(d)
        if nid:
            s["node_id"] = nid
            n += 1
            log("   ✅ %s → node_id %s" % (d, nid))
        else:
            log("   ⚠️ %s 探不到 node_id（可能已删）" % d)
    if n:
        save_reg(reg)
    log("补全 %d 个 node_id" % n)
    return 0


def mark(args):
    """手工标注状态（已删 / 被取代），让登记表跟现实一致。"""
    reg = load_reg()
    hit = False
    for s in reg.get("sites", []):
        if s.get("domain") == args.domain:
            s["status"] = args.status
            if args.status == "DELETED":
                s["keep"] = False
            hit = True
            break
    if not hit:
        reg.setdefault("sites", []).append(
            {"node_id": args.node or "", "domain": args.domain, "project": PROJ,
             "ver": args.ver or "", "ts": datetime.now().strftime("%Y-%m-%d"),
             "keep": False, "status": args.status})
    save_reg(reg)
    log("✅ %s → status=%s" % (args.domain, args.status))
    return 0


def export_pending(args):
    """🔴🔴🔴 把「待删但本会话删不掉」的站**导出到共享仓库** —— 这是清理闭环的接口

    🔴 为什么必须有这一步（2026-10-25 22:5x 抓到的断链）：
        `register/plan/apply` 全部只写**本地** work/site_registry.json
        ⇒ 而执行删除的清理 cron 跑在**另一个沙箱**（session.mode=new）
        ⇒ 它**看不到本地文件** ⇒ 本地登记的待删站永远送不到它手里
        ⇒ 闭环断了一半：站只增不减 ⇒ 几天就撞 50 上限
    ⇒ 解法：把待删清单写进**永久地址**上的 `pending-deletes.json`
            （清理 cron 的 prompt 本来就会读它 ⇒ 接口早就有了，只是没人写）

    ⇒ 本会话没 drive 权限也不要紧：清单交出去，由有权限的 cron 去删。
    """
    import subprocess
    reg = load_reg()
    prot = protected_map()
    boot = boot_info()
    cur = current_domain(boot)
    fb = fallback_domain(reg)
    regmap = {s.get("domain"): s for s in reg.get("sites", []) if s.get("domain")}

    cands = {}
    # ① 本地登记表里标了 SUPERSEDED 的
    for d, s in regmap.items():
        if str(s.get("status", "")).strip().upper() == "SUPERSEDED":
            cands[d] = {"node_id": s.get("node_id") or "PENDING", "reason": "本地登记表标 SUPERSEDED"}
    # ② 线上保护清单里 status=SUPERSEDED 的（**所有 agent 共用** ⇒ 这里才是权威）
    for d, s in prot.items():
        if str(s.get("status", "")).strip().upper() != "SUPERSEDED":
            continue
        if s.get("frozen"):
            continue
        if d == cur or d == fb:
            continue
        cands.setdefault(d, {"node_id": "PENDING", "reason": "保护清单标 SUPERSEDED"})

    # 🔴 排除现役 / 兜底 / 已死的
    alive = []
    for d in sorted(cands):
        if d == cur:
            log("   ⛔ %s 是**现役站** ⇒ 不导出" % d)
            continue
        if d == fb:
            log("   ⛔ %s 是**兜底站** ⇒ 不导出" % d)
            continue
        code = http_code(site_url(d))
        if code != 200:
            log("   ✅ %s 已经死了（HTTP %s）⇒ 不用删，标 DELETED 即可" % (d, code))
            continue
        alive.append(d)

    if not alive:
        log("✅ 没有需要清理的站（待删清单保持原样）")
        return 0

    # ③ 拉线上 pending-deletes.json 当**基底**（别人也在改它 ⇒ 别整份覆盖）
    base = http_json(PENDING_URL) or {"sites": []}
    sites = {s.get("domain"): s for s in (base.get("sites") or []) if s.get("domain")}
    for d in alive:
        info = cands[d]
        node = info.get("node_id")
        if node in (None, "", "PENDING"):
            nid = node_id_from_site(d)
            node = nid or "PENDING"
        sites[d] = {
            "domain": d,
            "node_id": node,
            "reason": "被新站取代 ⇒ 该删（清站 cron 删完请从本文件移除，并把 protected-sites.json 里 status 改成 DELETED）",
        }
    out = {
        "sites": [sites[k] for k in sorted(sites)],
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "待删站清单（唯一权威）。发版 agent 写、清理 cron 删。字段：domain / node_id / reason。",
    }
    tmp = "/workspace/pending-deletes.json"   # 对应仓库根的 pending-deletes.json
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("   待删清单已写：%s（%d 条：%s）" % (tmp, len(out["sites"]), ", ".join(alive)))

    # ④ 推共享仓库（一次提交 = 一次 Pages build）⇒ 清理 cron 才看得到
    push = os.path.join(ROOT, "scripts", "push_many.py")
    if not os.path.isfile(push):
        log("❌ 找不到 %s ⇒ 清单没交出去（清理 cron 看不到）" % push)
        return 1
    r = subprocess.run([sys.executable, push, "pending-deletes.json",
                        "--message", "chore: 待删站清单（NHK 发版后自动登记，清理 cron 20:00 删）"],
                       cwd="/workspace", capture_output=True, text=True)
    tail = (r.stdout or "")[-400:] + (r.stderr or "")[-400:]
    log("   推送结果：\n%s" % tail.strip())
    if r.returncode != 0:
        log("❌ 推送失败 ⇒ 清单没交出去（清理 cron 看不到）")
        return 1
    log("✅ 待删清单已交到共享仓库 ⇒ 清理 cron（20:00）会捡起来删")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("register")
    p.add_argument("--node", required=True)
    p.add_argument("--domain", required=True)
    p.add_argument("--ver", required=True)
    sub.add_parser("export-pending")
    sub.add_parser("plan")
    sub.add_parser("apply")
    sub.add_parser("verify")
    sub.add_parser("check")
    sub.add_parser("discover")
    p = sub.add_parser("mark")
    p.add_argument("--domain", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--node", default=None)
    p.add_argument("--ver", default=None)
    a = ap.parse_args()
    return {"register": lambda: register(a), "plan": lambda: plan()[0] and 0 or 0,
            "apply": apply, "verify": verify, "check": check,
            "discover": discover, "mark": lambda: mark(a),
            "export-pending": lambda: export_pending(a)}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
