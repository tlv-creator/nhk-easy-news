#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「更新提示」判据闸门 —— 防止**死循环升级提示**被改回来

🔴🔴🔴 它防的事（2026-10-23 实测，用户正在被影响）：
    App 判断「有没有新 APK」原来写的是 `bootInfo.ver > Config.APP_CODE`
    而这两个是**完全不同的计数器**：
      ver      = 站点**内容**版本（每天发内容都涨，一天能涨好几次）
      APP_CODE = APK 的 versionCode（只有原生层代码改了才重打，很久才涨一次）
    ⇒ 内容涨得比 APK 快 ⇒ `ver > APP_CODE` **几乎永远成立**
    ⇒ 用户顶栏常驻「發現新版本 v1.4　點此下載安裝」
    ⇒ 点进去下载的却是**站上那个同一个版本** ⇒ 装完横幅还在 ⇒ **死循环**
    ⇒ 而且现场看不像 bug（横幅在、下载也能用）⇒ 极难被发现。

⇒ 修法：`version.json` 增加 `apkVer`（站上 APK 的真实 versionCode）
   ⇒ App 只用 `apkVer` 与本地 `APP_CODE` 比（两者语义一致）
   ⇒ 老引导页没有 apkVer 时回退用 ver ⇒ 老用户仍看到提示，
     而站上的 APK 已经是新的 ⇒ 装完就有 apkVer ⇒ 不再提示 ⇒ **能收敛**

用法：
    python3 test_apk_version_gate.py                 # 全跑
    python3 test_apk_version_gate.py --offline-only   # 只查源码（不联网）
退出码：0 = 全过；1 = 有失败
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = "/workspace/nhk"
APPSRC = os.path.join(ROOT, "apk/app/src/com/nhk/easynews")
BOOT = "https://tlv-creator.github.io/nhk-easy-news/version.json"
AAPT2 = "/opt/android/android-14/aapt2"

FAILED = []
PASSED = 0


def check(name, ok, detail=""):
    global PASSED
    if ok:
        PASSED += 1
        print("  OK    %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL  %s %s" % (name, ("— " + detail) if detail else ""))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def apk_version_code(path):
    if not (os.path.isfile(AAPT2) and os.path.exists(path)):
        return None
    try:
        out = subprocess.run([AAPT2, "dump", "badging", path],
                             capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"versionCode='(\d+)'", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-only", action="store_true")
    a = ap.parse_args()

    print("== 源码侧 ==")
    boot = read(os.path.join(APPSRC, "Bootstrap.java"))
    main_java = read(os.path.join(APPSRC, "MainActivity.java"))

    check("Bootstrap 解析 apkVer",
          'optInt("apkVer"' in boot,
          "没找到 optInt(\"apkVer\" —— 引导页的 apkVer 不会被读进来")
    check("Bootstrap.Info 有 apkVer 字段",
          re.search(r"public\s+int\s+apkVer\s*=", boot) is not None)

    # 🔴 核心：比较必须走 remoteApkVer(...)（ apkVer 优先），不能直接拿 .ver 比
    raw_cmp = re.findall(r"(\w+)\.ver\s*>\s*Config\.APP_CODE", main_java)
    check("MainActivity 不再用裸 .ver 比 APP_CODE",
          not raw_cmp,
          "发现 %s —— 会退回死循环升级提示" % raw_cmp)
    check("MainActivity 用 remoteApkVer(...) 比较",
          "remoteApkVer(" in main_java
          and re.search(r"remoteApkVer\([^)]*\)\s*>\s*Config\.APP_CODE", main_java) is not None)
    check("remoteApkVer 优先 apkVer、回退 ver",
          re.search(r"apkVer\s*>\s*0\s*\?\s*\w+\.apkVer\s*:\s*\w+\.ver", main_java) is not None)
    check("提示文案显示 APK 版本号而不是站点内容版本名",
          '"發現新版本（版本 "' in main_java
          and '"發現新版本 v" + info.verName' not in main_java)

    print("== 推送侧 ==")
    pb = read(os.path.join(ROOT, "push_boot.py"))
    check("push_boot.py 会写 apkVer",
          '"apkVer"' in pb and "--apk-ver" in pb)

    if a.offline_only:
        print("\n== 结果 ==\n通过 %d，失败 %d" % (PASSED, len(FAILED)))
        return 1 if FAILED else 0

    print("== 线上引导页 ==")
    try:
        req = urllib.request.Request(BOOT, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            cfg = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        check("读得到引导页 version.json", False, repr(e))
        cfg = {}

    if cfg:
        check("引导页有 apkVer 字段",
              isinstance(cfg.get("apkVer"), int) and cfg.get("apkVer", 0) > 0,
              "当前值 %r" % cfg.get("apkVer"))
        site = (cfg.get("site") or "").rstrip("/")
        if site:
            try:
                with urllib.request.urlopen(site + "/NHK.apk", timeout=60) as r:
                    data = r.read()
                p = "/tmp/_gate_apk.apk"
                with open(p, "wb") as f:
                    f.write(data)
                vc = apk_version_code(p)
                check("apkVer == 站上 APK 的真实 versionCode",
                      vc is not None and vc == cfg.get("apkVer"),
                      "线上 APK=%r / apkVer=%r" % (vc, cfg.get("apkVer")))
            except Exception as e:
                check("能取到线上 APK", False, repr(e))
        # 🔴 反向验证：若 apkVer 缺失 ⇒ 老 App 会退回 ver 比较 ⇒ 必须能看出「老 App 会误报」
        if cfg.get("apkVer", 0) > 0:
            print("  ℹ️  老 App（没有 apkVer 逻辑的版本）仍会用 ver 比较，"
                  "会看到升级提示 ⇒ 点下载拿到的是新版 ⇒ 装完即收敛（这是预期行为）")

    print("\n== 结果 ==\n通过 %d，失败 %d" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("   ❌ %s" % f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
