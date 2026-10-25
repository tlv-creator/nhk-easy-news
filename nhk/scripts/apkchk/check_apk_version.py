#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APK 版本号闸门 —— **产物里的 versionCode/versionName 必须等于期望值**。

为什么要有这道闸门（2026-10-23 NHK 实战）：
    build.sh 里 aapt2 link 的 `--version-code` 参数是**写在脚本里的硬编码**，
    靠「build.sh 用 sed 改自己」来跟 apk/VERSION 保持同步。
    ⇒ 一次失败的 build 会还原 VERSION/Config/Manifest，**却不会还原 build.sh 自己**
    ⇒ 脚本里的版本号从此漂移 ⇒ 产物里的 versionCode 与源树对不上
    ⇒ 而且**现场看不出来**（包能装能跑，只是版本号乱了）。
    ⇒ 同一个纪律族：「拿到的不是那个东西」不许静默兜底。

用法：
    python3 check_apk_version.py --apk out/signed.apk --expect-code 18 --expect-name 1.18
    # 附带核对源树四处（apk/VERSION、Config.java、AndroidManifest.xml、产物 APK）：
    python3 check_apk_version.py --apk out/signed.apk --expect-code 18 --expect-name 1.18 \
        --check-source /workspace/nhk/apk

判据（全部必须成立才 exit 0）：
    ① APK 存在且能解析出 versionCode / versionName
    ② 产物的 versionCode == expect-code、versionName == expect-name
    ③ （--check-source 时）源树三处（VERSION / Config.java / AndroidManifest.xml）三者一致，
       且都等于产物的那两个值

🔴 守卫生效的五个前置条件（Shun 那条通用铁律，逐条对齐）：
    ① 判据覆盖真实写法 —— 读的是 aapt2 dump badging（官方解析器），不是自己猜二进制格式
    ② 不越权 —— 只管版本号，不管签名/CSS/域名
    ③ 不把自己算进去 —— 不扫描自身文件
    ④ 不恒绿 —— APK 缺失 / aapt2 缺失 / 解析失败 / 期望值没给 ⇒ 一律 exit 1
    ⑤ 输出能看出理由 —— 每个失败分支都打印 reason=xxx，区分「判据拦截」与「脚本崩了」
"""
import argparse
import os
import re
import subprocess
import sys

DEFAULT_AAPT2 = "/opt/android/android-14/aapt2"


def fail(reason, msg, detail=None):
    """统一的失败出口：reason 让调用方能区分「判据拦截」和「脚本崩了」。"""
    print("❌ [版本号闸门] reason=%s" % reason)
    if msg:
        print("   %s" % msg)
    if detail:
        for k, v in detail:
            print("   %-14s %s" % (k + ":", v))
    sys.exit(1)


def read_apk_version(apk, aapt2):
    """用 aapt2 dump badging 读出 APK 里的 versionCode / versionName。"""
    if not os.path.isfile(apk):
        fail("apk_missing", "APK 不存在：%s" % apk)
    if not (os.path.isfile(aapt2) and os.access(aapt2, os.X_OK)):
        fail("aapt2_missing",
             "找不到 aapt2（%s）—— 解析不了产物就不许放行" % aapt2,
             [("修法:", "bash /workspace/nhk/setup_env.sh 重建工具链，"
                        "或 --aapt2 指定路径")])
    try:
        p = subprocess.run([aapt2, "dump", "badging", apk],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except Exception as e:
        fail("aapt2_crashed", "aapt2 执行异常：%r" % e)
    if p.returncode != 0:
        fail("aapt2_failed",
             "aapt2 dump badging 退出码 %d" % p.returncode,
             [("stderr:", p.stderr.decode("utf-8", "ignore")[:300])])
    out = p.stdout.decode("utf-8", "ignore")
    m = re.search(r"package:.*?versionCode='(\d+)'.*?versionName='([^']*)'", out)
    if not m:
        fail("parse_failed", "从 aapt2 输出里解析不出 versionCode/versionName",
             [("输出前 200 字:", out[:200])])
    return int(m.group(1)), m.group(2)


def read_source_versions(srcdir):
    """读源树三处：apk/VERSION、Config.java、AndroidManifest.xml。"""
    got = {}
    vfile = os.path.join(srcdir, "VERSION")
    if not os.path.isfile(vfile):
        fail("version_file_missing", "找不到 %s" % vfile)
    txt = open(vfile, encoding="utf-8").read().strip()
    if not re.fullmatch(r"\d+", txt):
        fail("version_file_bad", "%s 内容不是纯数字：%r" % (vfile, txt))
    got["VERSION"] = (int(txt), "1.%s" % txt)

    cfg = os.path.join(srcdir, "app/src/com/nhk/easynews/Config.java")
    if not os.path.isfile(cfg):
        fail("config_missing", "找不到 %s" % cfg)
    c = open(cfg, encoding="utf-8").read()
    mc = re.search(r"APP_CODE\s*=\s*(\d+)", c)
    mv = re.search(r"APP_VER\s*=\s*\"([^\"]+)\"", c)
    if not mc or not mv:
        fail("config_parse_failed", "Config.java 里读不出 APP_CODE / APP_VER",
             [("APP_CODE:", mc.group(1) if mc else "没找到"),
              ("APP_VER:", mv.group(1) if mv else "没找到")])
    got["Config.java"] = (int(mc.group(1)), mv.group(1))

    man = os.path.join(srcdir, "app/AndroidManifest.xml")
    if not os.path.isfile(man):
        fail("manifest_missing", "找不到 %s" % man)
    mm = open(man, encoding="utf-8").read()
    mvc = re.search(r'android:versionCode="(\d+)"', mm)
    mvn = re.search(r'android:versionName="([^"]+)"', mm)
    if not mvc or not mvn:
        fail("manifest_parse_failed", "AndroidManifest.xml 里读不出 versionCode/versionName",
             [("versionCode:", mvc.group(1) if mvc else "没找到"),
              ("versionName:", mvn.group(1) if mvn else "没找到")])
    got["AndroidManifest.xml"] = (int(mvc.group(1)), mvn.group(1))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", required=True)
    ap.add_argument("--expect-code", type=int, default=None)
    ap.add_argument("--expect-name", default=None)
    ap.add_argument("--check-source", default=None,
                    help="源码根目录（含 VERSION / app/…）⇒ 额外核对源树三处")
    ap.add_argument("--aapt2", default=os.environ.get("AAPT2", DEFAULT_AAPT2))
    a = ap.parse_args()

    # 🔴 期望值没给 ⇒ 不许放行（「没东西可比」必须 fail，不是「那就当通过」）
    if a.expect_code is None or not a.expect_name:
        fail("expect_missing",
             "--expect-code / --expect-name 必须都给（没给期望值就没有判据）",
             [("apk:", a.apk)])

    code, name = read_apk_version(a.apk, a.aapt2)
    print("   产物版本：versionCode=%d versionName=%s" % (code, name))

    if a.check_source:
        src = read_source_versions(a.check_source)
        for k in sorted(src):
            print("   源树 %-20s versionCode=%d versionName=%s" % (k, src[k][0], src[k][1]))
        vals = set(src.values())
        if len(vals) != 1:
            fail("source_mismatch",
                 "源树三处版本号不一致（VERSION / Config.java / AndroidManifest.xml）",
                 [(k, "%d / %s" % v) for k, v in sorted(src.items())])
        if (code, name) != next(iter(vals)):
            fail("source_vs_apk_mismatch",
                 "产物 APK 的版本号与源树不一致（build.sh 的 aapt2 参数漂移了？）",
                 [("源树:", "%d / %s" % next(iter(vals))),
                  ("产物:", "%d / %s" % (code, name))])

    if code != a.expect_code or name != a.expect_name:
        fail("version_mismatch",
             "产物版本号 != 期望值",
             [("期望:", "%d / %s" % (a.expect_code, a.expect_name)),
              ("实际:", "%d / %s" % (code, name))])

    print("✅ 版本号闸门通过：versionCode=%d versionName=%s" % (code, name))
    sys.exit(0)


if __name__ == "__main__":
    main()
