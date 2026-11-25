#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_apk_signature.py —— 闸门：**APK 的签名证书必须与期望的指纹一致**

🔴🔴🔴 **它防的那个事故**（2026-10-23 Shun 先发现，我同款中也中招）：
很多项目的 build 脚本里有这么一段「保险」：
```bash
if [ ! -f "$KEY" ]; then keytool -genkeypair ...; fi     # ← 🔴 自动生成一把新密钥
```
⇒ **密钥文件一丢（沙箱重置 / 拷工程时没带 / 误删），build 就会悄悄换一把新钥匙**
⇒ ⇒ 产出的 APK **签名变了** ⇒ **用户手机上的旧 App 装不上**（签名不一致 ⇒ 覆盖安装被拒）
⇒ ⇒ **用户的收藏和播放进度全丢**，而且**现场看不出来**（包能装、能跑，只是升级不了）。

⇒ **通则**（我自己的纪律）：
> **任何「默认值 / 找不到 / 没东西可检」的分支一律 FAIL，不许静默兜底。**
> ⇒ 「找不到原密钥」的正确处置是 **die**，不是「生成一个新的」。

**⇒ 用法**：
```bash
python3 scripts/apkchk/check_apk_signature.py --apk x.apk --expect-sha1 572bc92473f5bb1e569ffd9973e898969b302032
```
（`--expect-sha1` 也接受冒号分隔的 `57:2B:C9:…` 形式；exit 0 = 一致；1 = 不一致或读不出）
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


def cert_sha1_of_apk(apk):
    """从 APK 里取签名证书的 SHA-1（走 apksigner，拿不到就退回自己解 cert.RSA）"""
    apksigner = shutil.which("apksigner") or _find_apksigner()
    if apksigner:
        p = subprocess.run([apksigner, "verify", "--print-certs", apk],
                           capture_output=True, text=True, timeout=180)
        m = re.search(r"SHA-1 digest:\s*([0-9a-fA-F:]+)", p.stdout)
        if m:
            return _norm(m.group(1)), "apksigner"
    # 兜底：解出 META-INF/*.RSA，用 openssl 算证书指纹
    try:
        with zipfile.ZipFile(apk) as z:
            names = [n for n in z.namelist()
                     if n.upper().startswith("META-INF/") and n.upper().endswith((".RSA", ".DSA", ".EC"))]
            if not names:
                return None, "没有 META-INF 证书文件"
            data = z.read(names[0])
        with tempfile.NamedTemporaryFile(suffix=".rsa", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            p = subprocess.run(["openssl", "pkcs7", "-inform", "DER", "-in", tmp, "-print_certs"],
                               capture_output=True, text=True, timeout=120)
            pem = p.stdout
            if "BEGIN CERTIFICATE" not in pem:
                return None, "openssl 解不出证书"
            p2 = subprocess.run(["openssl", "x509", "-noout", "-fingerprint", "-sha1", "-sha1"],
                                input=pem, capture_output=True, text=True, timeout=120)
            m = re.search(r"sha1 Fingerprint=([0-9A-F:]+)", p2.stdout)
            if m:
                return _norm(m.group(1)), "openssl"
        finally:
            os.unlink(tmp)
    except Exception as e:
        return None, "解证书失败：%s" % e
    return None, "取不到指纹"


def _find_apksigner():
    for base in ("/opt/android", "/opt"):
        for root, dirs, files in os.walk(base):
            if "apksigner" in files:
                return os.path.join(root, "apksigner")
            if root.count(os.sep) - base.count(os.sep) > 4:
                dirs[:] = []
    return None


def _norm(s):
    return re.sub(r"[^0-9a-fA-F]", "", s).lower()


def main():
    ap = argparse.ArgumentParser(description="APK 签名证书指纹闸门")
    ap.add_argument("--apk", required=True)
    ap.add_argument("--expect-sha1", required=True, help="期望的证书 SHA-1（可带冒号）")
    a = ap.parse_args()

    if not os.path.exists(a.apk):
        print("❌ APK 不存在：%s ⇒ 不能放行" % a.apk)
        return 1
    got, how = cert_sha1_of_apk(a.apk)
    if not got:
        print("❌ 取不到 APK 的证书指纹（%s）⇒ 验不了 ⇒ 不能放行" % how)
        return 1
    want = _norm(a.expect_sha1)
    if got != want:
        print("❌ 签名证书不一致")
        print("   期望：%s" % want)
        print("   实际：%s" % got)
        print("   🔴 换签名 = 用户手机上的旧 App 装不上（收藏/进度全丢）⇒ 绝不能悄悄换")
        return 1
    print("✅ 签名证书一致：%s（%s）" % (got, how))
    return 0


if __name__ == "__main__":
    sys.exit(main())
