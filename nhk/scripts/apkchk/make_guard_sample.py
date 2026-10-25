#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_guard_sample.py —— 造一个**最小**的 API 守卫样本 APK（约 900 B）

🔴 为什么要它：
   闸门/检查器最怕「恒绿」—— 一个永远返回 OK 的实现也能通过正向测试。
   ⇒ 必须有**能自己造的最小样本**：
      · 同时含 **if-lt** 和 **if-ge** 两种守卫写法
      · 再加一个**故意没有守卫**的阴性对照
   ⇒ 拿到样本后可以：
      · 正向：被守卫的必须全认出、且只漏那一个阴性对照
      · 反向：把 if-lt 单独拆掉 → 必须报「无守卫」
      · 反向：把 if-ge 单独拆掉 → 必须报「无守卫」
      （「两种写法都要认」这件事，**只能靠单独拆来证明**）

两种写法的编译产物（实测，dexdump 实证）：
    if (SDK_INT >= O) { … }        →  if-lt（0x34）跳到 else 侧
    if (SDK_INT < O) return null;   →  if-ge（0x35）跳到 else 侧
    两者都是 22t 格式、**语义相反** → 检查器必须两种都认

🔴 依赖：
    · JDK（javac）
    · d8（Android build-tools 里的 lib/d8.jar）
    · android.jar（用同包的 fetch_android_jars.py 拿，API 24/26/29/34 都可以）

用法：
    python3 fetch_android_jars.py --out ./jars
    python3 make_guard_sample.py --android-jar ./jars/android-34.jar --out guard_sample.apk
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

# 🔴 类名按**源码写法**命名，不按编译后的指令名 ——
#    早期那批叫 GuardGe/GuardLt 的其实写反了（Ge 写的是 >=、Lt 写的是 <），
#    名字必须表达源码语义，否则以后一定看错。
SRC = {}

SRC['GuardGe'] = """package com.test;
/** 源码写法 A：`if (SDK_INT >= O) { ... }`  ->  javac 产出 **if-lt**（跳到 else 侧） */
public class GuardGe {
    public Object build() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            return new android.app.NotificationChannel("id", "n", 1);
        }
        return null;
    }
}
"""

SRC['GuardLt'] = """package com.test;
/** 源码写法 B：`if (SDK_INT < O) return null;`  ->  javac 产出 **if-ge**（跳到 else 侧）
 *  这一种最容易让检查器误判「无守卫」—— 语义与 A 相反，但格式同为 22t */
public class GuardLt {
    public Object build() {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.O) {
            return null;
        }
        return new android.app.NotificationChannel("id", "n", 1);
    }
}
"""

SRC['GuardNone'] = """package com.test;
/** 阴性对照：完全没有守卫 -> 检查器**必须**报「无守卫」 */
public class GuardNone {
    public Object build() {
        return new android.app.NotificationChannel("id", "n", 1);
    }
}
"""

SRC['GuardImport'] = """package com.test;
/** 顶部 import（编译期行为，不产生任何字节码引用）+ 方法内守卫
 *  -> import 本身安全；真正要判的是**字节码引用**落在哪个分支 */
import android.app.NotificationChannel;
public class GuardImport {
    public Object build() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            return new NotificationChannel("id", "n", 1);
        }
        return null;
    }
}
"""


SRC['GuardIsoSafe'] = """package com.test;
/** 纯隔离类（官方推荐形态）：整个类只在新系统上加载，**方法体内不写 SDK_INT 判断**
 *  安全性来自「所有调用点都在守卫分支里」→ 老设备根本走不到这个类 */
public class GuardIsoSafe {
    public static Object build() {
        return new android.app.NotificationChannel("id", "n", 1);
    }
}
"""

SRC['GuardCallerSafe'] = """package com.test;
/** 调用方：守卫分支里调 GuardIsoSafe → GuardIsoSafe 的引用安全 */
public class GuardCallerSafe {
    public Object go() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            return GuardIsoSafe.build();
        }
        return null;
    }
}
"""

SRC['GuardIsoUnsafe'] = """package com.test;
/** 反向对照：同样是隔离类，但**被无守卫的地方调用** → 必须判不安全 */
public class GuardIsoUnsafe {
    public static Object build() {
        return new android.app.NotificationChannel("id", "n", 1);
    }
}
"""

SRC['GuardCallerUnsafe'] = """package com.test;
/** 无守卫地调用 GuardIsoUnsafe → 危险 */
public class GuardCallerUnsafe {
    public Object go() {
        return GuardIsoUnsafe.build();
    }
}
"""


SRC['GuardLowThreshold'] = """package com.test;
/** 阈值不够：`if (SDK_INT >= 25)` 却调了 **API 26** 才有的东西
 *  -> 这是**最阴的一种错**：自带守卫，一眼看去是安全的，但守卫的版本不够
 *  -> 必须报「守卫阈值不够」，而不是「已守卫」
 *
 *  🔴🔴 **阈值必须 > d8 的 --min-api（这里 24）**，否则：
 *     d8 做常量折叠 → `SDK_INT >= 21` 恒真 → **整个 if 分支被优化掉**
 *     → 字节码里根本没有守卫 → 测的就不是「阈值不够」而是「无守卫」了。
 *     （我第一版写 21 就是这么翻车的：字节码里 new-instance 直接裸奔，
 *       守卫被 d8 优化没了 —— 差点以为是 checker 的锅。）
 *  ⇒ 25 > 24 → 分支保留 → 才会真的判「阈值不够」（25 < 26）
 */
public class GuardLowThreshold {
    public Object build() {
        if (android.os.Build.VERSION.SDK_INT >= 25) {
            return new android.app.NotificationChannel("id", "n", 1);
        }
        return null;
    }
}
"""


def find_d8(explicit=None):
    if explicit:
        return explicit
    cands = []
    for env in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        root = os.environ.get(env)
        if root:
            cands.append(os.path.join(root, 'build-tools'))
    cands += [os.path.join(b, 'build-tools') for b in
              ('.', '/opt/android-sdk', '/usr/local/android-sdk',
               os.path.expanduser('~/android-sdk'))]
    for bt in cands:
        if not os.path.isdir(bt):
            continue
        for v in sorted(os.listdir(bt), reverse=True):
            p = os.path.join(bt, v, 'lib', 'd8.jar')
            if os.path.exists(p):
                return p
    return None


def main():
    ap = argparse.ArgumentParser(description='造一个最小的 API 守卫样本 APK')
    ap.add_argument('--android-jar', default='./jars/android-34.jar',
                    help='用于编译的 android.jar（API 24/26/29/34 都可以）')
    ap.add_argument('--d8', default=None, help='d8.jar 路径（默认自动找）')
    ap.add_argument('--out', default='guard_sample.apk', help='输出 APK')
    ap.add_argument('--javac', default='javac', help='javac 路径')
    ap.add_argument('--java', default=None,
                    help='java 路径（默认跟 javac 同目录，再退到 PATH）')
    a = ap.parse_args()

    if not os.path.exists(a.android_jar):
        print('[FAIL] 找不到 android.jar：%s' % a.android_jar)
        print('       先跑：python3 fetch_android_jars.py --out ./jars')
        return 1
    d8 = find_d8(a.d8)
    if not d8:
        print('[FAIL] 找不到 d8.jar —— 装 Android build-tools，或用 --d8 指定')
        return 1
    if not shutil.which(a.javac) and not os.path.exists(a.javac):
        print('[FAIL] 找不到 javac —— 装 JDK，或用 --javac 指定')
        return 1

    tmp = tempfile.mkdtemp(prefix='guard-sample-')
    try:
        src = os.path.join(tmp, 'src', 'com', 'test')
        os.makedirs(src)
        for name, body in SRC.items():
            with open(os.path.join(src, name + '.java'), 'w', encoding='utf-8') as f:
                f.write(body)
        classes = os.path.join(tmp, 'classes')
        os.makedirs(classes)
        cmd = [a.javac, '--release', '8', '-nowarn', '-encoding', 'UTF-8',
               '-classpath', a.android_jar, '-d', classes]
        cmd += [os.path.join(src, n + '.java') for n in SRC]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print('[FAIL] javac 失败：\n%s' % (r.stderr or r.stdout))
            return 1

        dex_dir = os.path.join(tmp, 'dex')
        os.makedirs(dex_dir)
        java = a.java
        if not java:
            cand = os.path.join(os.path.dirname(os.path.abspath(a.javac)), 'java')
            java = cand if os.path.exists(cand) else 'java'
        cmd = [java, '-cp', d8, 'com.android.tools.r8.D8',
               '--min-api', '24', '--output', dex_dir]
        cmd += [os.path.join(classes, 'com', 'test', n + '.class') for n in SRC]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print('[FAIL] d8 失败：\n%s' % (r.stderr or r.stdout))
            return 1

        dex = os.path.join(dex_dir, 'classes.dex')
        if not os.path.exists(dex):
            print('[FAIL] d8 没产出 classes.dex')
            return 1

        with zipfile.ZipFile(a.out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(dex, 'classes.dex')
        print('[ OK ] 样本已生成：%s（%d B）' % (a.out, os.path.getsize(a.out)))
        print('        内含：%s' % '、'.join(SRC))
        print('        期望：被守卫的必须全认出 + 只漏 1 处（阴性对照 GuardNone）')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
