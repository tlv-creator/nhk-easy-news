#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apk_version_code.py —— 不装 aapt2 也能读出 APK 的 versionCode（2026-11-22）

🔴 为什么需要：
    手册 §29.2 要求「引导页的 apkVer 必须等于**站上 APK 的真实 versionCode**」
    ⇒ 平时用 `aapt2 dump badging` 读
    ⇒ 但沙箱重置后 /opt（Android 工具链）没了 ⇒ aapt2 不在
    ⇒ 而 apkVer 手填 = 会跟真实 APK 脱节 ⇒ App 的「发现新版本」判据恒真/恒假

🔴 为什么**不自己解析 AXML**（今天试过，结论记在这里）：
    二进制 AndroidManifest.xml 的 chunk 偏移（属性表起点 = attrExt 起点 + attributeStart）
    在不同 aapt 版本上不一致 ⇒ 自己写的解析器在真 APK 上读出空属性（今天踩了）
    ⇒ **用成熟库**（pyaxmlparser，纯 Python、无依赖）⇒ 少写一个会骗人的解析器

用法：
    python3 scripts/apk_version_code.py site/app/NHK.apk
"""
import re
import sys
import zipfile


def read_manifest(path):
    with zipfile.ZipFile(path) as z:
        return z.read("AndroidManifest.xml")


def manifest_xml(path):
    try:
        from pyaxmlparser.axmlprinter import AXMLPrinter
    except ImportError:
        raise SystemExit(
            "❌ 缺 pyaxmlparser ⇒ pip install pyaxmlparser"
            "（或跑 setup_env.sh；它是读 APK versionCode 的唯一可靠办法）")
    xml = AXMLPrinter(read_manifest(path)).get_xml()
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    return xml


def main():
    if len(sys.argv) < 2:
        print("用法：python3 scripts/apk_version_code.py <apk>")
        return 2
    path = sys.argv[1]
    try:
        xml = manifest_xml(path)
    except Exception as e:
        print("❌ 读不出 manifest：%s" % e)
        return 1
    got = {}
    for k in ("versionCode", "versionName", "package", "minSdkVersion", "targetSdkVersion"):
        m = re.search(r'android:%s="([^"]*)"' % k, xml) or re.search(r'%s="([^"]*)"' % k, xml)
        got[k] = m.group(1) if m else None
    print("versionCode   = %s" % got["versionCode"])
    print("versionName   = %s" % got["versionName"])
    print("package       = %s" % got["package"])
    print("minSdkVersion = %s" % got["minSdkVersion"])
    print("targetSdk     = %s" % got["targetSdkVersion"])
    if not got["versionCode"]:
        print("❌ 没读到 versionCode ⇒ 不许拿它填引导页的 apkVer")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
