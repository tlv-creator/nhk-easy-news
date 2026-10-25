#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_android_jars.py —— 下载官方 android.jar（APK API 等级闸门的判据来源）
========================================================================
零依赖（只用标准库）。**不要用 unzip 解官方包** —— 旧版 unzip 会误报 zip bomb：
    error: invalid zip file with overlapped components (possible zip bomb)
    file #3918:  bad zipfile offset (local header sig): 58705667
官方包是正常的，是 unzip 的误判（对「overlapped components」保护过度敏感）。
⇒ 本脚本用 Python 的 zipfile，**只取需要的那个 entry**（整包里还有 ~60MB 源码包，用不上）。

🔴 **固化值必须跟 jar 同目录**（`jars/jars.sha256`）——
   脚本和固化值分开放 → 路径变量一多就容易对不上 → 闸门报「缺少固化值」，
   拦是对的，但拦的原因不是你想测的那条。
   （同 `sha256sum -c` 的惯例：清单文件放在被校验文件旁边。）

🔴 **sha256 是从「已经结构自检通过」的文件上算的** ——
   sha 只能证明「跟固化时一样」；固化时拿到的就可能是坏文件
   ⇒ 必须有一道**不依赖 sha256** 的独立证据 = 结构自检。

用法
--------------------------------------------------------------------------
    python3 fetch_android_jars.py --out ./jars            # 下载 + 提取 + 自检 + 写固化值
    python3 fetch_android_jars.py --out ./jars --verify   # 只校验已有 jar（sha + 结构）

结构自检三样（都不依赖 sha256）：
    ① zipfile 能打开（合法 zip）
    ② 里有 .class 条目
    ③ 抽一个 class，读 magic == b'\\xca\\xfe\\xba\\xbe'
       ⚠️ class 是**大端**的（major_version = 00 34）；用小端解析会**静默返回 0 个方法 = 假绿灯**

🔴 **不要用「jar 里必须有 android/app/NotificationChannel.class」当结构判据**：
   NotificationChannel 本身就是 API 26 才有的类 → API 24 的 jar 里本来就不该有它
   → 这么判会把好的 API 24 jar 判成坏的。
   「有没有 NotificationChannel」应该交给调用方**按 API 版本**判断。
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

# API 等级 → (官方包名, 包内 android.jar 的路径前缀)
PLATFORMS = [
    (24, 'platform-24_r02.zip', 'android-7.0'),
    (26, 'platform-26_r02.zip', 'android-8.0.0'),
    (29, 'platform-29_r05.zip', 'android-10'),
    (34, 'platform-34-ext7_r03.zip', 'android-34'),
]
BASE = 'https://dl.google.com/android/repository/'

# class 文件 magic（字节序列，**不能**用 <I 小端读 —— 读出来是 0xBEBAFECA）
CLASS_MAGIC = b'\xca\xfe\xba\xbe'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def structure_check(jar_path):
    """不依赖 sha256 的结构自检 → (ok, 说明)"""
    # ① 合法 zip？
    try:
        z = zipfile.ZipFile(jar_path)
    except Exception as e:
        return False, '不是合法 zip：%s' % e
    names = z.namelist()
    classes = [n for n in names if n.endswith('.class')]
    # ② 有 .class 条目？
    if not classes:
        return False, 'jar 里没有 .class 条目（共 %d 个条目）' % len(names)
    # ③ 抽一个 class 读 magic
    try:
        with z.open(classes[0]) as src:
            head = src.read(4)
    except Exception as e:
        return False, '读 .class 失败：%s' % e
    if head != CLASS_MAGIC:
        return False, ('class magic 不对：%s（期望 ca fe ba be）'
                       % head.hex())
    return True, 'OK（%d 个 .class）' % len(classes)


def download(url, dest):
    """🔴 沙箱里测外网一律用 curl（urllib 解析到错 IP 的老问题）。"""
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return True
    for attempt in (1, 2, 3):
        r = subprocess.run(['curl', '-sSL', '--max-time', '300', '--retry', '2',
                            '-o', dest, url], capture_output=True)
        if r.returncode == 0 and os.path.exists(dest) \
                and os.path.getsize(dest) > 1000:
            return True
        print('  下载重试 %d：%s' % (attempt, url))
    return False


def extract_jar(zip_path, prefix, out_jar):
    """🔴 用 zipfile 精确取 entry（不要 unzip）。"""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        target = None
        for n in names:
            if n.endswith('/android.jar') and prefix in n:
                target = n
                break
        if target is None:
            # 兜底：任何 android.jar
            for n in names:
                if n.endswith('/android.jar'):
                    target = n
                    break
        if target is None:
            return False, '包里没有 android.jar（前几个条目：%s）' % names[:5]
        with z.open(target) as src, open(out_jar, 'wb') as dst:
            while True:
                b = src.read(1 << 20)
                if not b:
                    break
                dst.write(b)
    return True, target


def verify(out_dir):
    """校验已有 jar：sha256 + 结构自检（两道独立证据）"""
    manifest = os.path.join(out_dir, 'jars.sha256')
    if not os.path.exists(manifest):
        print('[FAIL] 缺少固化值文件：%s' % manifest)
        print('       → 跟 jar 同目录放（sha256sum -c 的惯例）')
        return 1
    bad = 0
    checked = 0
    with open(manifest, encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw or raw.startswith('#'):
                continue
            parts = raw.split()
            if len(parts) < 2:
                continue
            want, name = parts[0], parts[-1]
            path = os.path.join(out_dir, name)
            if not os.path.exists(path):
                print('[FAIL] 缺文件：%s' % path)
                bad += 1
                continue
            got = sha256_file(path)
            if got != want:
                print('[FAIL] sha256 不符：%s' % name)
                print('       期望 %s' % want)
                print('       实际 %s' % got)
                bad += 1
                continue
            ok, why = structure_check(path)
            if not ok:
                print('[FAIL] 结构自检不过：%s —— %s' % (name, why))
                bad += 1
                continue
            checked += 1
            print('[ OK ] %s  sha256 ✓  结构 ✓  %s' % (name, why))
    if bad:
        print('[FAIL] %d 个 jar 不可信 —— 判据不可信，拒绝出包' % bad)
        return 1
    print('[ OK ] %d 个 jar 全部通过（sha256 + 结构自检）' % checked)
    return 0


def fetch(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    lines = []
    bad = 0
    for api, zname, prefix in PLATFORMS:
        print('[fetch] API %d …' % api)
        zpath = os.path.join(out_dir, zname)
        if not download(BASE + zname, zpath):
            print('[FAIL] 下载失败：%s' % zname)
            bad += 1
            continue
        out_jar = os.path.join(out_dir, 'android-%d.jar' % api)
        ok, info = extract_jar(zpath, prefix, out_jar)
        if not ok:
            print('[FAIL] 取 jar 失败：%s —— %s' % (zname, info))
            bad += 1
            continue
        # 🔴 **先结构自检，通过了再算 sha** ——
        #    sha 必须来自「已经验证过的文件」，否则固化的是一个坏基线
        ok2, why = structure_check(out_jar)
        if not ok2:
            print('[FAIL] 结构自检不过：android-%d.jar —— %s' % (api, why))
            bad += 1
            continue
        digest = sha256_file(out_jar)
        print('[ OK ] android-%d.jar  %s  %s' % (api, digest[:16] + '…', why))
        lines.append('%s  android-%d.jar' % (digest, api))
        # zip 本体留着太大，用完删（要重下就再跑一次）
        try:
            os.remove(zpath)
        except OSError:
            pass
    if lines:
        with open(os.path.join(out_dir, 'jars.sha256'), 'w',
                  encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print('[ OK ] 固化值已写：%s（与 jar 同目录）'
              % os.path.join(out_dir, 'jars.sha256'))
    if bad:
        print('[FAIL] %d 个 API 等级下载/自检失败' % bad)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='./jars',
                    help='jar 存放目录（固化值也写在这里）')
    ap.add_argument('--verify', action='store_true',
                    help='只校验已有 jar（sha256 + 结构自检）')
    args = ap.parse_args()
    if args.verify:
        sys.exit(verify(args.out))
    sys.exit(fetch(args.out))


if __name__ == '__main__':
    main()
