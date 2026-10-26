#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shun 播客 · 网络版 APK 交付验收

判据全部落在**事实量**上（从 APK 二进制里读出来的事实），不是读自己的构建日志。

检查项：
  1. ZIP 清单：只含预期条目；**不含 assets 里的 apk/zip**；旧内置版 APK 不在里面
  2. APK Manifest：包名 / versionCode / versionName / 权限 / 启动 Activity / label
  3. APK 体积：网络版应远小于内置版（内置版 42.6MB）
  4. APK 内容：**不含内置音频 m4a**、**不含内置集数数据 json**
  5. 签名：证书 SHA-1 == 用户手机里那把；apksigner verify 通过
  6. 站点可达：线上 50 集站点在线 + index.json 集数 == 50

用法：
  python3 verify_apk.py <zip> [--apk <apk>] [--expect-cert <sha1>]
退出码：0 = 全绿；1 = 有 FAIL
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

SDK = os.environ.get("ANDROID_SDK", "/opt/android-sdk")
BT = os.path.join(SDK, "build-tools", "34.0.0")
AAPT2 = os.path.join(BT, "aapt2")
APKSIGNER = os.path.join(BT, "apksigner")


def tool_env():
    """apksigner / aapt2 是**壳脚本**，需要 java 在 PATH 上
    🔴 不给的话它自己起不来 ⇒ verify 返回非 0 ⇒ **假红**（踩过：沙箱重置后 JDK 挪到了 /opt/jdk）
    """
    env = dict(os.environ)
    cands = [os.environ.get("JAVA_HOME"), "/opt/jdk", "/usr/lib/jvm/java-17-openjdk-amd64",
             "/usr/lib/jvm/default-java"]
    for jh in cands:
        if jh and os.path.exists(os.path.join(jh, "bin", "java")):
            env["JAVA_HOME"] = jh
            env["PATH"] = os.path.join(jh, "bin") + os.pathsep + env.get("PATH", "")
            return env
    # 退一步：系统 PATH 上有 java 就行
    return env

EXPECT_PKG = "com.shun.study"
EXPECT_VC = "8"
EXPECT_VN = "1.3.0"
EXPECT_CERT = "9c04f344921d31fd7258a571e263b6e475e42f94"
SITE = "https://tlv-creator.github.io/nhk-easy-news/shun-web/data/index.json"
OLD_BUILTIN_APK_BYTES = 42_615_309

PASS, FAIL = [], []


def ok(name, detail=""):
    PASS.append(name)
    print("  ✓ %s%s" % (name, ("  " + detail) if detail else ""))


def bad(name, detail=""):
    FAIL.append(name)
    print("  ✗ %s%s" % (name, ("  " + detail) if detail else ""))


def check(name, cond, detail_ok="", detail_bad=""):
    if cond:
        ok(name, detail_ok)
    else:
        bad(name, detail_bad)
    return cond


def http_json(url):
    """网络抖动重试 3 次（沙箱偶发 TLS EOF；别把网络抖动当代码 bug）"""
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "shun-apk-verify"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def launchable_ok(apk_path, badging):
    """启动 Activity 判据

    🔴 `aapt2 dump badging` **不输出 intent 类别** ⇒ 只在里面找
       "android.intent.category.LAUNCHER" 会永远假红（实测踩过）。
    ⇒ LAUNCHER 类别必须读 **manifest 树**（aapt2 dump xmltree）。
    """
    if "com.shun.study.MainActivity" not in badging:
        return False
    r = subprocess.run([AAPT2, "dump", "xmltree", "--file", "AndroidManifest.xml", apk_path],
                       capture_output=True, text=True, env=tool_env())
    if r.returncode != 0:
        return False
    t = r.stdout
    return ("android.intent.category.LAUNCHER" in t
            and "android.intent.action.MAIN" in t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--apk", default=None, help="同时验一个裸 APK")
    ap.add_argument("--expect-cert", default=EXPECT_CERT)
    ap.add_argument("--skip-site", action="store_true")
    args = ap.parse_args()

    zpath = os.path.abspath(args.zip)
    print("=" * 70)
    print("Shun 网络版 APK 验收")
    print("  ZIP: %s" % zpath)
    print("=" * 70)

    # ---------- 1. ZIP 清单 ----------
    print("\n[1] ZIP 清单")
    if not os.path.exists(zpath):
        bad("ZIP 存在", zpath)
        return report()
    zsize = os.path.getsize(zpath)
    ok("ZIP 存在", "%d 字节" % zsize)

    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        infos = {i.filename: i for i in z.infolist()}
    print("    内容：")
    for n in names:
        print("      %s  (%d 字节)" % (n, infos[n].file_size))

    apks = [n for n in names if n.lower().endswith(".apk")]
    check("ZIP 里有且仅有 1 个 APK", len(apks) == 1, "→ " + apks[0] if apks else "实际 %d 个" % len(apks))
    nested_zip = [n for n in names if n.lower().endswith(".zip") and n != os.path.basename(zpath)]
    check("ZIP 里没有嵌套 zip", len(nested_zip) == 0, str(nested_zip))
    if apks:
        an = apks[0]
        check("APK 文件名是 ASCII", all(ord(c) < 128 for c in an), an)
        check("APK 文件名带项目前缀", "shun" in an.lower(), an)
        base = os.path.basename(an)
        check("APK 文件名不是旧内置版名", base not in ("shun.apk", "shun.apk.zip"), base)
        # 解出来
        tmpd = tempfile.mkdtemp()
        apk_path = os.path.join(tmpd, base)
        with zipfile.ZipFile(zpath) as z:
            z.extract(an, tmpd)
        apk_real = os.path.join(tmpd, an)
    else:
        return report()

    # ---------- 2. Manifest ----------
    print("\n[2] APK Manifest（从二进制读，不看构建日志）")
    r = subprocess.run([AAPT2, "dump", "badging", apk_real],
                       capture_output=True, text=True, env=tool_env())
    bd = r.stdout
    check("aapt2 能读出 badging", r.returncode == 0 and "package:" in bd)
    import re
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", bd)
    if m:
        pkg, vc, vn = m.group(1), m.group(2), m.group(3)
        check("包名", pkg == EXPECT_PKG, "%s" % pkg, "期望 %s 实际 %s" % (EXPECT_PKG, pkg))
        check("versionCode", vc == EXPECT_VC, vc, "期望 %s 实际 %s" % (EXPECT_VC, vc))
        check("versionName", vn == EXPECT_VN, vn, "期望 %s 实际 %s" % (EXPECT_VN, vn))
    else:
        bad("解析 package 行")
    check("有 INTERNET 权限", "android.permission.INTERNET" in bd)
    check("启动 Activity 正确", launchable_ok(apk_real, bd),
          "com.shun.study.MainActivity + LAUNCHER")
    m = re.search(r"application-label:'([^']*)'", bd)
    check("应用名非空", bool(m and m.group(1).strip()), m.group(1) if m else "空")
    check("minSdkVersion = 24", "sdkVersion:'24'" in bd)
    check("targetSdkVersion = 34", "targetSdkVersion:'34'" in bd)

    # ---------- 3. 体积 ----------
    print("\n[3] 体积")
    asize = os.path.getsize(apk_real)
    ok("APK 大小", "%d 字节 (%.1f KB)" % (asize, asize / 1024.0))
    check("网络版显著小于内置版 (%d 字节)" % OLD_BUILTIN_APK_BYTES,
          asize < OLD_BUILTIN_APK_BYTES / 10,
          "只有内置版的 %.1f%%" % (asize * 100.0 / OLD_BUILTIN_APK_BYTES),
          "太大了：%d" % asize)

    # ---------- 4. 内容 ----------
    print("\n[4] APK 内容（网络版必须什么都不内置）")
    with zipfile.ZipFile(apk_real) as z:
        inner = z.namelist()
    assets = [n for n in inner if n.startswith("assets/")]
    check("没有 assets 目录", len(assets) == 0, str(assets[:5]))
    m4a = [n for n in inner if n.lower().endswith(".m4a") or n.lower().endswith(".mp3")]
    check("没有内置音频", len(m4a) == 0, str(m4a[:5]))
    eps = [n for n in inner if n.startswith("data/") and n.endswith(".json")]
    check("没有内置集数数据", len(eps) == 0, str(eps[:5]))
    check("有 classes.dex", "classes.dex" in inner)
    check("有图标", any("icon" in n for n in inner))
    nested = [n for n in inner if n.lower().endswith(".apk") or n.lower().endswith(".zip")]
    check("APK 里没有嵌套 apk/zip", len(nested) == 0, str(nested))

    # ---------- 5. 签名 ----------
    print("\n[5] 签名")
    r = subprocess.run([APKSIGNER, "verify", "--print-certs", apk_real],
                       capture_output=True, text=True, env=tool_env())
    check("apksigner verify 通过", r.returncode == 0, r.stderr.strip()[:120])
    mm = re.search(r"SHA-?1[^:]*:\s*([0-9a-fA-F:]+)", r.stdout)
    cert = mm.group(1).replace(":", "").lower() if mm else ""
    check("证书 SHA-1 == 用户手机里那把", cert == args.expect_cert.lower(),
          cert, "期望 %s 实际 %s" % (args.expect_cert.lower(), cert or "(读不到)"))
    vm = re.search(r"verified using v[123] scheme[^\n]*", r.stdout)
    if vm:
        print("    " + vm.group(0))

    # ---------- 6. 站点 ----------
    if not args.skip_site:
        print("\n[6] 线上站点（APK 加载的就是它）")
        try:
            idx = http_json(SITE + "?v=verify%d" % os.getpid())
            eps_list = idx.get("eps") or idx.get("episodes") or []
            n = len(eps_list)
            check("线上 index.json 可达", True)
            check("线上集数 == 50", n == 50, "实际 %d 集" % n,
                  "实际 %d 集" % n)
            total = sum(int(e.get("n") or 0) for e in eps_list) if eps_list else 0
            check("线上总句数 == 3618", total == 3618, "实际 %d 句" % total,
                  "实际 %d 句" % total)
        except Exception as e:
            bad("线上 index.json 可达", str(e)[:120])

    return report()


def report():
    print("\n" + "=" * 70)
    print("结果：%d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        print("=" * 70)
        sys.exit(1)
    print("✅ 全绿")
    print("=" * 70)
    sys.exit(0)


if __name__ == "__main__":
    main()
