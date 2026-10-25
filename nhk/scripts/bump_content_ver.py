#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 **版本号的唯一权威**（2026-10-25 重构：把 APP_VER / CACHE 也收进来）

🔴 为什么之前会「用户说没更新」：
    ① 字幕/词库的 URL 是 `path + '?v=' + APP_VER`（app.js 的 subUrl()）
       sw.js 的预缓存桶名是 `nhk-easynews-vNN`
       ⇒ 这两个号**必须同时升**，否则 Service Worker 先返回旧字幕（stale-while-revalidate）
    ② 之前 **update.py --build 从来不调用本脚本** ⇒ 日常管线根本不升版本号
       ⇒ 每天发版，用户的手机照样拿旧文件（这就是「我更新了但没变化」的真因）
    ⇒ 现在：**prepare 阶段（发布闸门）必跑本脚本**，且只有本脚本能写版本号。

本脚本负责写四处（**一个配方，不许在别处再写一遍**）：
    ① site/app/version.json 的 ver        = 发布版本号 N（引导页的 ver 也用它）
    ② site/app/app.js     的 APP_VER      = 'vN'
    ③ site/app/sw.js      的 CACHE        = 'nhk-easynews-vN'
    ④ site/app/index.html 里的 ?v= 戳：
         data/*.js?v=vD   （D = 数据版本，内容变了才 +1）
         app.js?v=vA / style.css?v=vS（各自 md5 变了才 +1 ⇒ 不让用户白下 200KB）

用法：
    python3 scripts/bump_content_ver.py                 # 发布版本号 +1，其余按 md5 判断
    python3 scripts/bump_content_ver.py --release-ver 31
    python3 scripts/bump_content_ver.py --data-ver 55
    python3 scripts/bump_content_ver.py --dry-run

判据（落在事实量上，缺一即 exit 1 —— 不许静默改一半）：
    · 四个位置任何一个读不出来 ⇒ 退出（写法变了就先改本脚本）
    · 版本号不许倒退
    · 改完 data/*.js?v= 必须全部一致
    · 改完 app.js 的 APP_VER 与 sw.js 的 CACHE 必须同号（自检）
"""
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "site", "app")
STATE = os.path.join(ROOT, ".contentver.json")
IDX = os.path.join(APP, "index.html")
SW = os.path.join(APP, "sw.js")
APPJS = os.path.join(APP, "app.js")
VERJSON = os.path.join(APP, "version.json")

DATA_FILES = ["episodes.js", "patterns.js", "syntax.js", "reading.js",
              "chains.js", "cdn.js"]


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def content_fingerprint():
    """🔴 用户能看到的所有内容的指纹（data/*.js + subs/*.js + **sw.js**）。

    为什么用它决定要不要升发布版本号：
        APP_VER 决定**字幕 URL**（`subs/*.js?v=APP_VER`）
        ⇒ 字幕/数据变了就必须升号，否则 Service Worker 先返回旧的
        ⇒ 但**内容没变**就别升（用户白下 200KB 也没必要）
        ⇒ 所以：**按内容指纹升**，不是每次都 +1

    🔴🔴 为什么 sw.js 也要算进去（2026-10-25 补）：
        Service Worker 的注册 URL 是 `sw.js?v=APP_VER`
        ⇒ **sw.js 本身改了而 APP_VER 没升** ⇒ 注册 URL 不变
        ⇒ 浏览器可能继续用**缓存里的旧 sw.js** ⇒ 新加的离线预热（4.6MB 预热）
           根本到不了用户手里（而且不报错！）
        ⇒ 所以 sw.js 变了也算「内容变了」⇒ 必须升号
        （哈希前先把 CACHE 那行抹平 ⇒ 否则本脚本自己写 CACHE 会让自己永远算「变了」）
    """
    items = []
    for sub in ("data", "subs"):
        d = os.path.join(APP, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".js"):
                items.append([sub + "/" + fn, md5(os.path.join(d, fn))])
    sp = os.path.join(APP, "sw.js")
    if os.path.exists(sp):
        raw = open(sp, encoding="utf-8").read()
        norm = re.sub(r"const\s+CACHE\s*=\s*'nhk-easynews-v\d+'", "const CACHE = 'nhk-easynews-vNN'",
                      raw)
        items.append(["sw.js", hashlib.md5(norm.encode("utf-8")).hexdigest()])
    return hashlib.md5(json.dumps(items, ensure_ascii=False).encode("utf-8")).hexdigest()


def read(p, what):
    if not os.path.exists(p):
        sys.exit("❌ 找不到 %s（%s）⇒ 先修本脚本" % (p, what))
    return open(p, encoding="utf-8").read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-ver", type=int, default=None,
                    help="发布版本号（默认 = 当前 ver + 1）")
    ap.add_argument("--data-ver", type=int, default=None,
                    help="data/*.js 的版本号（默认 = 数据变了就 +1）")
    ap.add_argument("--app-ver", type=int, default=None)
    ap.add_argument("--style-ver", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    st = read_state()
    idx = read(IDX, "index.html")
    sw = read(SW, "sw.js")
    appjs = read(APPJS, "app.js")
    vj = json.loads(read(VERJSON, "version.json"))

    def cur_of(pattern, text, what):
        m = re.search(pattern, text)
        if not m:
            sys.exit("❌ index.html 里找不到 %s 的版本号（模式 %s）⇒ "
                     "index.html 的写法变了 ⇒ 先改本脚本，别手改" % (what, pattern))
        return int(m.group(1))

    data_ver = cur_of(r'data/episodes\.js\?v=v(\d+)', idx, "data/episodes.js")
    app_ver = cur_of(r'app\.js\?v=v(\d+)', idx, "app.js")
    style_ver = cur_of(r'style\.css\?v=v(\d+)', idx, "style.css")

    m = re.search(r"var\s+APP_VER\s*=\s*'v(\d+)'", appjs)
    if not m:
        sys.exit("❌ app.js 里找不到 var APP_VER = 'vNN' ⇒ 先改本脚本")
    cur_app_ver = int(m.group(1))
    m = re.search(r"const\s+CACHE\s*=\s*'nhk-easynews-v(\d+)'", sw)
    if not m:
        sys.exit("❌ sw.js 里找不到 const CACHE = 'nhk-easynews-vNN' ⇒ 先改本脚本")
    cur_sw_ver = int(m.group(1))

    cur_rel = vj.get("ver")
    if not isinstance(cur_rel, int):
        sys.exit("❌ version.json 的 ver 不是整数（%r）⇒ 先修本脚本" % cur_rel)

    # ---- 目标发布版本号 ----
    # 🔴 内容变了才升（内容指纹）：APP_VER 是字幕 URL 的破缓存号
    #    ⇒ 字幕/数据变了必须升；没变就别升（省流量）
    cur_fp = content_fingerprint()
    old_fp = st.get("content_sha")
    content_changed = (old_fp != cur_fp)      # 没有基线 ⇒ 算「变了」（往安全的方向取）
    if a.release_ver is not None:
        new_rel = a.release_ver
    else:
        new_rel = cur_rel + (1 if content_changed else 0)
    if new_rel < cur_rel:
        sys.exit("❌ 发布版本号 %d 比当前的 %d 还小 ⇒ 版本号不许倒退" % (new_rel, cur_rel))
    if new_rel == cur_rel:
        print("   （内容%s ⇒ 发布版本号保持 %d）" % ("变了但被 --release-ver 固定" if content_changed else "没变", new_rel))

    # ---- 数据版本：内容变了才 +1（md5 比对）----
    sha = {}
    for key, rel in (("app", "app.js"), ("style", "style.css")):
        sha[key] = md5(os.path.join(APP, rel))
    data_sha = {}
    for rel in DATA_FILES:
        p = os.path.join(APP, "data", rel)
        if os.path.exists(p):
            data_sha[rel] = md5(p)
    old_data_sha = st.get("data_sha", {})
    # 🔴 没有基线就算「变了」：判断不了的时候**往「升」的方向取** ——
    #   少升一次 = 用户看不到新一期（正确性事故）；多升一次 = 多下几百 KB（流量代价）
    data_changed = (old_data_sha != data_sha)

    if a.data_ver is not None:
        new_data_ver = a.data_ver
    else:
        new_data_ver = data_ver + 1 if data_changed else data_ver
    if new_data_ver < data_ver:
        sys.exit("❌ 数据版本号 %d 比当前的 %d 还小 ⇒ 版本号不许倒退" % (new_data_ver, data_ver))

    # ---- app.js / style.css：内容变了才 +1 ----
    old = st.get("sha", {})
    new_app_ver, new_style_ver = app_ver, style_ver
    if old.get("app") and old["app"] != sha["app"]:
        new_app_ver = app_ver + 1
        print("   app.js 变了 ⇒ ?v= %d → %d" % (app_ver, new_app_ver))
    if old.get("style") and old["style"] != sha["style"]:
        new_style_ver = style_ver + 1
        print("   style.css 变了 ⇒ ?v= %d → %d" % (style_ver, new_style_ver))
    if not old:
        print("   （首次记录 md5，本次不自动 +1；下次开始自动判断）")
    if a.app_ver is not None:
        new_app_ver = a.app_ver
    if a.style_ver is not None:
        new_style_ver = a.style_ver

    # ---- 写：app.js 的 APP_VER（先写，后面要用它的 md5 算戳）----
    new_appjs = re.sub(r"(var\s+APP_VER\s*=\s*')v\d+(')",
                       lambda m: m.group(1) + "v%d" % new_rel + m.group(2), appjs, count=1)
    if new_appjs == appjs and cur_app_ver != new_rel:
        sys.exit("❌ app.js 的 APP_VER 没改成 v%d ⇒ 写法变了？" % new_rel)
    sha["app"] = hashlib.md5(new_appjs.encode("utf-8")).hexdigest()
    if old.get("app") and old["app"] != sha["app"] and new_app_ver == app_ver:
        new_app_ver = app_ver + 1

    # ---- 写：sw.js 的 CACHE ----
    new_sw = re.sub(r"(const\s+CACHE\s*=\s*'nhk-easynews-v)\d+(')",
                    lambda m: m.group(1) + str(new_rel) + m.group(2), sw, count=1)
    if new_sw == sw and cur_sw_ver != new_rel:
        sys.exit("❌ sw.js 的 CACHE 没改成 v%d ⇒ 写法变了？" % new_rel)

    # ---- 写：version.json 的 ver ----
    new_vj = dict(vj)
    new_vj["ver"] = new_rel

    # ---- 写：index.html 的 ?v= 戳 ----
    new_idx = idx
    for f in DATA_FILES:
        pat = r'(src="data/%s\?v=v)\d+(")' % re.escape(f)
        if not re.search(pat, new_idx):
            sys.exit("❌ index.html 里找不到 data/%s?v=vNN ⇒ 先改本脚本" % f)
        new_idx = re.sub(pat, lambda m: m.group(1) + str(new_data_ver) + m.group(2), new_idx)
    new_idx = re.sub(r'(app\.js\?v=v)\d+(")',
                     lambda m: m.group(1) + str(new_app_ver) + m.group(2), new_idx)
    new_idx = re.sub(r'(style\.css\?v=v)\d+(")',
                     lambda m: m.group(1) + str(new_style_ver) + m.group(2), new_idx)

    # ---- 自检：四处必须同号 ----
    vers = set(re.findall(r'data/[a-z]+\.js\?v=v(\d+)', new_idx))
    if len(vers) != 1:
        sys.exit("❌ index.html 里 data/*.js 的版本号不一致：%s ⇒ 改了一半" % vers)
    if new_rel != cur_rel and "v%d" % new_rel not in new_appjs:
        sys.exit("❌ app.js 的 APP_VER 不是 v%d" % new_rel)
    if new_rel != cur_rel and "'nhk-easynews-v%d'" % new_rel not in new_sw:
        sys.exit("❌ sw.js 的 CACHE 不是 nhk-easynews-v%d" % new_rel)

    print("== 版本号 ==")
    print("   发布版本号 ver : %d → %d（内容%s）"
          % (cur_rel, new_rel, "变了" if content_changed else "没变"))
    print("   APP_VER        : v%d → v%d" % (cur_app_ver, new_rel))
    print("   CACHE          : v%d → v%d" % (cur_sw_ver, new_rel))
    print("   data ?v=       : %d → %d%s" % (data_ver, new_data_ver,
                                              "（内容变了）" if data_changed else "（内容没变）"))
    print("   app.js ?v=     : %d → %d" % (app_ver, new_app_ver))
    print("   style.css ?v=  : %d → %d" % (style_ver, new_style_ver))

    if a.dry_run:
        print("== dry-run（没写盘）==")
        return

    open(APPJS, "w", encoding="utf-8").write(new_appjs)
    open(SW, "w", encoding="utf-8").write(new_sw)
    open(IDX, "w", encoding="utf-8").write(new_idx)
    json.dump(new_vj, open(VERJSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"release": new_rel, "data": new_data_ver, "app": new_app_ver,
               "style": new_style_ver, "sw": new_rel, "sha": sha,
               "data_sha": data_sha, "content_sha": cur_fp},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("✅ 版本号已更新：ver=%d APP_VER=v%d CACHE=v%d data=v%d app=v%d style=v%d"
          % (new_rel, new_rel, new_rel, new_data_ver, new_app_ver, new_style_ver))
    print("   state →", STATE)


if __name__ == "__main__":
    main()
