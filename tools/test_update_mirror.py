#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：tools/update_mirror.py（一条命令改所有引用方）

造反例去撞它：
  ① 只改**含旧域**的文件，别的域一个字不动
  ② 二进制文件跳过（改了会坏）**但必须显式报告跳过了几个**
  ③ 大文件跳过也要报告
  ④ 旧域一个都没找到 ⇒ exit 1（域名写错了不能当成功）
  ⑤ 幂等：改完再跑 ⇒ 报「无变化」且不改文件
  ⑥ --dry-run 不写盘
  ⑦ 参数错（old==new / 空 / root 不存在）⇒ exit 2
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "update_mirror.py")
OLD = "oldoldoldold.space.mcode.cn"
NEW = "newnewnewnew.space.mcode.cn"
OTHER = "keepkeepkeepkeep.space.mcode.cn"

FAIL = []


def check(cond, msg):
    print("   %s %s" % ("✅" if cond else "❌", msg))
    if not cond:
        FAIL.append(msg)


def run(root, *args):
    p = subprocess.run([sys.executable, TOOL, "--root", root] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def build_tree(d):
    os.makedirs(os.path.join(d, "sub", "deep"), exist_ok=True)
    with open(os.path.join(d, "a.html"), "w", encoding="utf-8") as f:
        f.write('<a href="https://%s/">旧站</a>\n' % OLD)
    with open(os.path.join(d, "b.js"), "w", encoding="utf-8") as f:
        f.write('var A="https://%s/"; var B="https://%s/";\n' % (OLD, OTHER))
    with open(os.path.join(d, "sub", "deep", "c.txt"), "w", encoding="utf-8") as f:
        f.write("老链接 %s\n" % OLD)
    with open(os.path.join(d, "d.bin"), "wb") as f:
        f.write(b"\x00\x01\x02" + OLD.encode() + b"\x00")
    with open(os.path.join(d, "e.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + OLD.encode() + b"\x00")


def main():
    print("=== 自检：update_mirror.py ===")
    d = tempfile.mkdtemp(prefix="updmirror_")
    try:
        build_tree(d)

        # ① dry-run 不写盘
        code, out = run(d, "--old", OLD, "--new", NEW, "--dry-run")
        a = open(os.path.join(d, "a.html"), encoding="utf-8").read()
        check(OLD in a, "dry-run 没有改盘 ✅")
        check("a.html" in out and "b.js" in out, "dry-run 报告了要改哪些文件")
        check("d.bin" not in out.split("含旧域名")[0].split("─ ─")[-1], "dry-run 没把二进制列进要改的文件")

        # ② 真改
        code, out = run(d, "--old", OLD, "--new", NEW)
        a = open(os.path.join(d, "a.html"), encoding="utf-8").read()
        b = open(os.path.join(d, "b.js"), encoding="utf-8").read()
        c = open(os.path.join(d, "sub", "deep", "c.txt"), encoding="utf-8").read()
        check(code == 0, "退出码 = 0（实际 %d）" % code)
        check(NEW in a and OLD not in a, "a.html 换掉了")
        check(NEW in b and OTHER in b, "b.js 换掉了旧域，**别的域没动** ✅")
        check(NEW in c, "子目录里的文件也换了")
        check("跳过" in out, "报告里说明了跳过了几个（二进制/非文本）")

        # ③ 二进制没被改
        raw = open(os.path.join(d, "d.bin"), "rb").read()
        check(OLD.encode() in raw, "二进制文件没被改（改了会坏）✅")

        # ④ 幂等
        before = open(os.path.join(d, "a.html"), encoding="utf-8").read()
        code, out = run(d, "--old", OLD, "--new", NEW)
        after = open(os.path.join(d, "a.html"), encoding="utf-8").read()
        check(before == after, "再跑一次：文件没变（幂等）")
        check("一个文件都没找到" in out, "再跑一次：明确说「没找到」")
        check(code == 1, "再跑一次：退出码 = 1（实际 %d）—— 旧域不存在不能当成功" % code)

        # ⑤ 旧域写错 ⇒ exit 1
        code, out = run(d, "--old", "nosuchdomain.space.mcode.cn", "--new", NEW)
        check(code == 1, "旧域写错 ⇒ exit 1（实际 %d）" % code)
        check("可能写错" in out, "提示了「可能写错」")

        # ⑥ --allow-missing ⇒ 0
        code, out = run(d, "--old", "nosuchdomain.space.mcode.cn", "--new", NEW, "--allow-missing")
        check(code == 0, "--allow-missing ⇒ exit 0（实际 %d）" % code)

        # ⑦ 参数错
        code, _ = run(d, "--old", OLD, "--new", OLD)
        check(code == 2, "old == new ⇒ exit 2（实际 %d）" % code)
        code, _ = run(d, "--old", OLD, "--new", NEW, "--root", os.path.join(d, "nope"))
        check(code == 2, "root 不存在 ⇒ exit 2（实际 %d）" % code)

        # ⑧ 大文件跳过要报告
        d2 = tempfile.mkdtemp(prefix="updmirror_big_")
        try:
            big = os.path.join(d2, "big.txt")
            with open(big, "w", encoding="utf-8") as f:
                f.write(OLD + "\n")
                f.write("x" * (6 * 1024 * 1024))
            code, out = run(d2, "--old", OLD, "--new", NEW)
            check("跳过了 1 个" in out, "大文件被跳过且**显式报告了数量**（不是静默跳过）")
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print()
    if FAIL:
        print("❌ 失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("   -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
