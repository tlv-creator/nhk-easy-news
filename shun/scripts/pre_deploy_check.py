#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_deploy_check.py —— 发版前的**事实量**体检（只报事实，判据留给调用方）。

🔴 为什么要有它：发版前要确认的东西太多，靠记忆一定会漏。
   ⇒ 把「必须成立的事实」写成代码，跑一次就知道能不能发。

检查项：
 ① 13 集数据齐全（index ↔ 文件 ↔ 句数一致）
 ② 每句的 gs 字段：所有原文片段**逐字来自原句**（防幻觉数据上线）
 ③ gs 覆盖率（低于阈值 ⇒ 警告，不阻断）
 ④ APK 存在 + 签名证书指纹 = 期望值（换签名 = 用户装不上）
 ⑤ 工程内没有密钥文件
 ⑥ 墨水屏闸门通过（CSS 合规）

用法：
    python3 scripts/pre_deploy_check.py            # 全查
    python3 scripts/pre_deploy_check.py --min-gs 0.8
退出码：0 = 可以发；1 = 有阻断项。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
# 🔴 归一化的唯一权威在 tools/gs_text.py（等长 ⇒ 位置不失真）
sys.path.insert(0, os.path.join(WS, "tools"))
from gs_text import norm as _norm  # noqa: E402
EXPECT_CERT_SHA1 = "9c04f344921d31fd7258a571e263b6e475e42f94"   # 用户手机里那把密钥

BLOCK = []
WARN = []
OK = []


def ok(name, extra=""):
    OK.append(name)
    print("  ✅ %s%s" % (name, ("  " + extra) if extra else ""))


def warn(name, extra=""):
    WARN.append(name)
    print("  ⚠️  %s%s" % (name, ("  " + extra) if extra else ""))


def bad(name, extra=""):
    BLOCK.append(name)
    print("  ❌ %s%s" % (name, ("  " + extra) if extra else ""))


# ── ① 数据完整性 ──────────────────────────────────────────────
def check_eps():
    idx_p = os.path.join(WS, "web", "data", "eps", "index.json")
    if not os.path.exists(idx_p):
        bad("index.json 不存在", idx_p)
        return []
    with open(idx_p, encoding="utf-8") as f:
        idx = json.load(f)
    eps = idx.get("episodes") or []
    if not eps:
        bad("index.json 里没有集数")
        return []
    ok("index.json 有 %d 集" % len(eps))
    total = 0
    for e in eps:
        tag = e.get("tag")
        p = os.path.join(WS, "web", "data", "eps", tag + ".json")
        if not os.path.exists(p):
            bad("%s 的数据文件不存在" % tag, p)
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        n = len(d.get("cues") or [])
        total += n
        if e.get("n") and n != e["n"]:
            bad("%s 句数与 index 不一致" % tag, "index=%s 文件=%s" % (e.get("n"), n))
    ok("13 集数据文件齐全，合计 %d 句" % total)
    return eps


# ── ② gs 的事实校验 ───────────────────────────────────────────
def _all_fragments(gs):
    """把 gs 里所有「应该是原文片段」的字段取出来。"""
    out = []
    for f in (gs.get("frame") or []):
        out.append(("frame.s", f.get("s")))
    for i, b in enumerate(gs.get("blocks") or []):
        out.append(("blocks[%d].s" % i, b.get("s")))
        for j, p in enumerate(b.get("parts") or []):
            out.append(("blocks[%d].parts[%d].s" % (i, j), p.get("s")))
    for i, c in enumerate(gs.get("chain") or []):
        out.append(("chain[%d]" % i, c))
    return out


def _strip(s):
    s = (s or "").strip()
    for a, b in (("［", "］"), ("【", "】"), ("〈", "〉"), ("（", "）"), ("[", "]"), ("(", ")")):
        if s.startswith(a) and s.endswith(b) and len(s) > 2:
            return s[1:-1].strip()
    return s


def check_gs():
    total = 0
    have = 0
    badfrag = 0
    samples = []
    for p in sorted(glob.glob(os.path.join(WS, "web", "data", "eps", "ep*.json"))):
        if p.endswith(".blk.json"):
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        for c in (d.get("cues") or []):
            for r in (c.get("rows") or []):
                total += 1
                gs = r.get("gs")
                if not gs:
                    continue
                have += 1
                ja = _norm(r.get("t") or "")
                for where, frag in _all_fragments(gs):
                    fr = _strip(_norm(frag))
                    if not fr or "〜" in fr:
                        continue          # 公式占位，不是原文片段
                    if fr not in ja:
                        badfrag += 1
                        if len(samples) < 5:
                            samples.append("%s %s=%r 不在原句 %r" % (
                                os.path.basename(p), where, frag, ja[:30]))
    ok("gs 覆盖率 %d/%d = %.1f%%" % (have, total, have * 100.0 / max(total, 1)))
    if badfrag:
        bad("gs 里有 %d 个原文片段不在原句（幻觉数据不能上线）" % badfrag)
        for s in samples:
            print("       %s" % s)
    else:
        ok("gs 的原文片段全部逐字来自原句（无幻觉）")
    return have, total


# ── ④ APK 签名 ────────────────────────────────────────────────
def check_apk():
    apks = sorted(glob.glob(os.path.join(WS, "output", "*.apk")))
    if not apks:
        warn("output/ 里没有 APK")
        return
    apk = apks[-1]
    tool = os.path.join(HERE, "apkchk", "check_apk_signature.py")
    if not os.path.exists(tool):
        warn("签名检查工具不在（%s）" % tool)
        return
    r = subprocess.run([sys.executable, tool, "--apk", apk,
                        "--expect-sha1", EXPECT_CERT_SHA1],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok("APK 签名指纹正确（%s）" % os.path.basename(apk))
    else:
        bad("APK 签名指纹不对 ⇒ 用户装不上", (r.stdout + r.stderr).strip()[:200])


# ── ⑤ 密钥不在工程里 ──────────────────────────────────────────
def check_secret():
    hits = []
    for root, dirs, files in os.walk(WS):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "node_modules", "__pycache__", "models", "venv", "deploy", "output")]
        for fn in files:
            if fn.lower().endswith((".jks", ".keystore", ".p12", ".pfx")):
                hits.append(os.path.join(root, fn))
    if hits:
        bad("工程里有密钥文件（绝不能进包/进仓）", "; ".join(hits[:3]))
    else:
        ok("工程里没有密钥文件")


# ── ⑥ 墨水屏闸门 ──────────────────────────────────────────────
def check_eink():
    app = os.path.join(WS, "web")
    htmls = [os.path.join(app, f) for f in ("index.html", "download.html")
             if os.path.exists(os.path.join(app, f))]
    csss = [os.path.join(app, f) for f in ("ai-client.css", "style.css", "shell.css")
            if os.path.exists(os.path.join(app, f))]
    if not csss:
        warn("没有 CSS 可查")
        return
    r = subprocess.run(["node", os.path.join(HERE, "check_eink_css.js")] + htmls + csss,
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok("墨水屏描边闸门通过")
    else:
        bad("墨水屏描边闸门未通过", (r.stdout or r.stderr).strip().splitlines()[-1][:160])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-gs", type=float, default=0.75, help="gs 覆盖率下限（低于则警告）")
    args = ap.parse_args()

    print("【1】数据完整性")
    check_eps()
    print("\n【2】gs 事实校验")
    have, total = check_gs()
    if total and have * 1.0 / total < args.min_gs:
        warn("gs 覆盖率 %.1f%% 低于下限 %.1f%%" % (have * 100.0 / total, args.min_gs * 100))
    print("\n【3】APK 签名")
    check_apk()
    print("\n【4】密钥")
    check_secret()
    print("\n【5】墨水屏闸门")
    check_eink()

    print("\n═══ 通过 %d / 警告 %d / 阻断 %d ═══" % (len(OK), len(WARN), len(BLOCK)))
    if BLOCK:
        print("阻断项：")
        for b in BLOCK:
            print("  - %s" % b)
        return 1
    if WARN:
        print("警告项（不阻断，但要看一眼）：")
        for w in WARN:
            print("  - %s" % w)
    print("🟢 可以发版")
    return 0


if __name__ == "__main__":
    sys.exit(main())
