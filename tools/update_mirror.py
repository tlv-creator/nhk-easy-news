#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令：把一个项目里**所有**「旧域名」改成「新域名」（换 mcode 镜像的标准动作）

为什么需要这个脚本：
  mcode 每次部署都换随机域名 ⇒ 换站 = 换域名 ⇒ **所有引用方都得改**
  ⇒ 「要记得改」是约定 = 迟早被忘 ⇒ **做成一条命令**。

用法：
    python3 tools/update_mirror.py --root <项目目录> --old <旧域名> --new <新域名>
    python3 tools/update_mirror.py --root <项目目录> --old <旧域名> --new <新域名> --dry-run
    python3 tools/update_mirror.py --root . --old https://abc.space.mcode.cn --new https://xyz.space.mcode.cn

规则：
  · 替换的是**精确的域名 token**（如 abc123.space.mcode.cn）⇒ 页面上别的域不受影响
  · **二进制文件跳过**（改了会坏）但**必须显式打印跳过了几个** —— 静默跳过 = 假装扫过了
  · **大文件跳过**（> --max-mb，默认 5MB）同样要打印
  · 旧域**一个都没找到** ⇒ exit 1（多半是域名写错了，别以为改完了）
  · **幂等**：再跑一次 ⇒ 报「无变化」

退出码：0 = 改完（或 dry-run 出计划）｜1 = 旧域没找到 / 有文件写失败｜2 = 参数错
"""
import argparse, os, sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mavis",
             ".pytest_cache", ".idea", "dist", ".next"}
TEXT_EXT = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".md", ".txt", ".yml", ".yaml",
            ".xml", ".sh", ".py", ".sql", ".php", ".java", ".kt", ".swift", ".gradle",
            ".ini", ".cfg", ".conf", ".env", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ""}
DEFAULT_MAX_MB = 5


def norm_domain(s):
    """接受 https://abc.space.mcode.cn/ 或 abc.space.mcode.cn ⇒ 只留主机名部分"""
    s = (s or "").strip()
    for p in ("https://", "http://"):
        if s.startswith(p):
            s = s[len(p):]
    s = s.split("/")[0].strip().rstrip(".")
    return s


def is_probably_text(path, max_bytes):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, 0
    if size > max_bytes:
        return None, size
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return None, size
    if b"\x00" in head:
        return None, size              # 二进制
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return None, size              # 不是 utf-8 文本
    return head, size


def scan(root, old, max_bytes):
    hits = []          # (relpath, text)
    skipped_big = 0
    skipped_bin = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in TEXT_EXT:
                skipped_bin += 1
                continue
            text, size = is_probably_text(p, max_bytes)
            if text is None:
                if size > max_bytes:
                    skipped_big += 1
                else:
                    skipped_bin += 1
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                skipped_bin += 1
                continue
            if old in content:
                hits.append((rel, content))
    return hits, skipped_big, skipped_bin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="项目目录")
    ap.add_argument("--old", required=True, help="旧域名（可带 https://）")
    ap.add_argument("--new", required=True, help="新域名（可带 https://）")
    ap.add_argument("--dry-run", action="store_true", help="只报计划，不写盘")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB)
    ap.add_argument("--allow-missing", action="store_true",
                    help="旧域一个都没找到也当成功（默认 exit 1）")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    old = norm_domain(args.old)
    new = norm_domain(args.new)
    if not old or not new:
        print("❌ --old / --new 不能为空")
        return 2
    if old == new:
        print("❌ --old 和 --new 一样：%s" % old)
        return 2
    if not os.path.isdir(root):
        print("❌ --root 不是目录：%s" % root)
        return 2

    print("=== %s → %s ===" % (old, new))
    print("    根目录：%s%s" % (root, "（dry-run，不写盘）" if args.dry_run else ""))

    hits, big, binary = scan(root, old, int(args.max_mb * 1024 * 1024))
    # 🔴 跳过统计**必须先打印**：否则「文件被跳过了」和「真的没有」看起来一样
    #    （自检抓到的真 bug：原来这里是「没找到」→ 直接 return，跳过报告根本没输出）
    if big:
        print("  ⚠️  跳过了 %d 个大于 %.1fMB 的文件（**没扫也没改** —— 里面可能也有旧域名，要自己确认）"
              % (big, args.max_mb))
    if binary:
        print("  ⚠️  跳过了 %d 个非文本/二进制文件（**没扫也没改**）" % binary)
    if not big and not binary:
        print("  ✅ 没跳过任何文件")

    if not hits:
        print("🔴 **一个文件都没找到「%s」** —— 域名可能写错了，或者真的已经改过" % old)
        print("    ⇒ 拒绝报成功（宁可报错，也不要悄悄用错的）")
        return 0 if args.allow_missing else 1

    changed = 0
    failed = []
    for rel, content in hits:
        n = content.count(old)
        new_content = content.replace(old, new)
        if new_content == content:
            continue
        if not args.dry_run:
            try:
                with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception as e:
                failed.append((rel, str(e)))
                continue
        changed += 1
        print("  %-46s 改 %d 处" % (rel, n))

    if failed:
        for rel, e in failed:
            print("  ❌ %s 写失败：%s" % (rel, e))
        return 1

    print("  ── 共 %d 个文件含旧域名%s" % (changed, "（dry-run 只是计划）" if args.dry_run else "，已全部替换"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
