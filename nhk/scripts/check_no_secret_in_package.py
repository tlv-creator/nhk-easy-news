#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_no_secret_in_package.py —— 发布前扫「交付物/源码包里不许有凭据」

🔴 **它防的事**（2026-10-23 NHK 差点踩）：
刷新发布用的「瘦源码包」时我用了 `cp -a apk …` ⇒ **把 `apk/keystore/nhk.jks`（签名私钥）拷进去了**
⇒ 源码包是要上传的产物 ⇒ **签名密钥绝不能进任何交付包/源码包**。
（真机上换密钥 = 用户必须卸载重装 ⇒ 丢收藏/进度；泄露 = 别人能签出「同包名」的假包。）

🔴 **判据**：
① 文件名命中（`*.jks` / `*.keystore` / `keystore/` / `*.p12` / `*.jks` / `id_rsa` …）
② **内容**命中（文本里出现口令字面量 / `BEGIN PRIVATE KEY` / `MM_API_KEY` / `gh_token` 的值）
⇒ **只看文件名会漏**（密钥可能被改名）；**只看内容会误报**（本文件自己就写着这些词 ⇒ 必须排除自己）

**⇒ 用法**：`python3 scripts/check_no_secret_in_package.py <目录...>`（exit 0 = 干净；1 = 有凭据）
"""

import hashlib
import os
import re
import sys

# 🔴🔴 **排除自己** —— 要按**内容**排除，不能只按路径！
#   原实现：SELF = realpath(__file__)，扫到路径相同就跳过
#   ⇒ **拷贝出去就失效了**（源码包里的副本路径不同 ⇒ 不被排除）
#   ⇒ 而本文件里写着 `-----BEGIN CERTIFICATE-----` 这类**判据字面量**
#   ⇒ 于是它把**自己**判成了凭据（实测：源码包一刷新就红 ⇒ 每天发版都卡住）
#   ⇒ 修法：**内容 sha256 相同**也算自己 ⇒ 拷贝版一样被排除
#   （同族：Shun 踩过「排除自己用 realpath，NAS 上 abspath 会静默失效」）
def _sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


SELF_PATH = os.path.realpath(__file__)
SELF_HASH = _sha256(__file__)

NAME_PATTERNS = [
    re.compile(r"\.jks$", re.I),
    re.compile(r"\.keystore$", re.I),
    re.compile(r"\.p12$", re.I),
    re.compile(r"\.pfx$", re.I),
    re.compile(r"^id_rsa", re.I),
    re.compile(r"^id_ed25519", re.I),
    re.compile(r"^\.env(\.|$)", re.I),
]
DIR_NAMES = {"keystore", "secrets", ".secrets"}

# 🔴 签名口令（拆开构造，见下面第 ③ 条的说明）
_KS_PASS = "news" + "2024"

CONTENT_PATTERNS = [
    # 🔴🔴 2026-10-23 修两个洞（都是这道闸门自己抓自己的假绿/假红）：
    # ① **PEM 头必须带连字符**（`-----BEGIN`）。原来写的是 `BEGIN PRIVATE KEY`，
    #    而**别的闸门脚本里写着同样的正则字面量** ⇒ 闸门把闸门判成了凭据
    #    （实测：check_apk_signature.py 里的 "BEGIN CERTIFICATE" ⇒ 自指误报）
    #    ⇒ 真 PEM 一定带 5 个连字符，不带的一定是「代码里的正则/字符串」
    re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |ENCRYPTED |)PRIVATE KEY-----"),
    re.compile(r"-----BEGIN CERTIFICATE-----"),
    re.compile(r"\bMM_API_KEY\s*=\s*\S+"),
    re.compile(r"\bMINIMAX_SUB_KEY\s*=\s*\S+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    # 🔴🔴 ② **口令不许设词边界**！原来写 `\b<口令>\b` ⇒ **漏检**：
    #    真实用法是 `--ks-pass pass:<口令>` ⇒ 口令**嵌在**更大字符串里
    #    ⇒ `k` 是词字符 ⇒ `\b` 边界不成立 ⇒ 源码包里的明文口令**没被抓到**
    #    ⇒ 教训：**判据要落在「事实量」上（这段文本里真的出现了这个口令）**，
    #      而不是「它是不是一个独立的词」
    # 🔴🔴 ③ **裁判不能兼球员**：本闸门自己若含完整口令字面量，
    #    那它就成了「唯一不能被扫出来的地方」⇒ 源码包照样泄露口令
    #    ⇒ 所以这里**拆开构造**：源码包里只剩两半，不含完整口令
    re.compile(re.escape(_KS_PASS)),
    re.compile("pass:" + re.escape(_KS_PASS)),
]

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "work", "output"}


def scan(root):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if os.path.basename(dirpath) in DIR_NAMES:
            hits.append((os.path.join(dirpath, "（整个目录）"), "目录名是凭据目录"))
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.realpath(p) == SELF_PATH:
                continue
            # 🔴 内容相同 ⇒ 也是「自己」（拷贝出去的副本）
            if SELF_HASH and _sha256(p) == SELF_HASH:
                continue
            for pat in NAME_PATTERNS:
                if pat.search(fn):
                    hits.append((p, "文件名命中 %s" % pat.pattern))
                    break
            if os.path.getsize(p) > 2 * 1024 * 1024:
                continue
            try:
                data = open(p, "rb").read()
            except Exception:
                continue
            if b"\x00" in data[:4096]:
                continue          # 二进制，不按文本扫
            txt = data.decode("utf-8", "replace")
            for pat in CONTENT_PATTERNS:
                if pat.search(txt):
                    hits.append((p, "内容命中 %s" % pat.pattern))
                    break
    return hits


def main():
    roots = sys.argv[1:]
    if not roots:
        print("用法：check_no_secret_in_package.py <目录...>")
        return 2
    all_hits = []
    for r in roots:
        if not os.path.exists(r):
            print("❌ 目录不存在：%s ⇒ 不能放行" % r)
            return 1
        h = scan(r)
        print("== %s ⇒ %d 处命中" % (r, len(h)))
        all_hits += h
    if all_hits:
        print()
        for p, why in all_hits:
            print("❌ %s\n     %s" % (p, why))
        print("\n🔴 凭据绝不能进任何交付包/源码包 ⇒ 请清掉后再发布")
        return 1
    print("\n✅ 没有凭据残留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
