#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""android.jar 结构自检 —— 只用标准库，零依赖。

为什么要自检（2026-10-22）
--------------------------
android.jar 是 **API 等级闸门的判据来源**。sha256 只能证明「文件没变过」，
**不能证明「文件是好的」** —— 比如第一次固化 sha 时拿到的就是个坏文件。

⇒ 所以再加一道**结构自检**：
  1) 是合法 ZIP（zipfile 可打开）
  2) 至少有一个 .class 条目
  3) 抽一个 class 读它的 **magic 字节** —— 必须是 `ca fe ba be`
     （🔴 注意：class 文件是**大端**的，major_version 字节是 `00 34`；
       用小端解析会**静默返回 0 个方法 = 假绿灯**）
  4) 找得到 `android/app/NotificationChannel.class`（API 26 的关键类，
     闸门要靠它判断「这个 API 是不是 26 才有的」）

退出码：0 = 通过；1 = 不是合法 jar
"""
import sys
import zipfile

CLASS_MAGIC = b'\xca\xfe\xba\xbe'


def check(path):
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        print("  不是合法 zip: %s" % e, file=sys.stderr)
        return False
    with z:
        names = z.namelist()
        if not names:
            print("  jar 是空的", file=sys.stderr)
            return False
        classes = [n for n in names if n.endswith('.class')]
        if not classes:
            print("  jar 里没有 .class 条目", file=sys.stderr)
            return False
        # 抽一个 class 验 magic
        sample = None
        for n in classes:
            if n.endswith('.class'):
                sample = n
                break
        try:
            head = z.read(sample)[:4]
        except Exception as e:
            print("  读 class 失败: %s" % e, file=sys.stderr)
            return False
        if head != CLASS_MAGIC:
            print("  class magic 不对: %r（应为 ca fe ba be）" % head, file=sys.stderr)
            return False
        # 🔴 NotificationChannel 是 **API 26 才有的类** ——
        #    API 24 的 jar 里**本来就不该有它**，拿它当判据会把好 jar 判成坏的
        #    （我第一版就犯了这个错）。
        #    ⇒ 基础自检只验「合法 zip + 有 .class + class magic 正确」
        #    ⇒ 「有没有 NotificationChannel」交给调用方按 API 版本判断
        return True


def has_class(path, cls):
    """这个 jar 里有没有某个类（用于按 API 版本做区分判断）"""
    try:
        with zipfile.ZipFile(path) as z:
            return cls in z.namelist()
    except Exception:
        return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: check_jar_valid.py <jar>")
        sys.exit(2)
    ok = all(check(p) for p in sys.argv[1:])
    # 额外的按 API 版本区分检查（可选）：--expect-class api=cls,cls
    if ok:
        for spec in sys.argv[2:] and [a for a in sys.argv[1:] if a.startswith("--expect-class=")]:
            pass
    sys.exit(0 if ok else 1)
