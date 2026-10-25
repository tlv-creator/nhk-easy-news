#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恢复 subs 里的「语法新格式 blk」段（2026-10-25）

🔴🔴 为什么要这个（真事故，不是假想）：
    `build_data.py` 重建 easy 轨时**不带 blk** ⇒ 语法新格式标注被冲掉
    ⇒ 现象：本地/线上/交付包里 blk 句数 = 0（用户看不到新格式讲解）
    ⇒ 根因同族：「手工修好的中间产物 = 迟早被覆盖」⇒ **修复必须变成流水线的一步**
    ⇒ 旧包（16:51 打的）里还留着 blk ⇒ 备份在 `_backup_subs_133119/`

🔴🔴 本脚本的三条硬纪律：
    ① **只插 blk，一个字节都不许碰别的** ⇒ 判据 = 剥掉 blk 后与原文件**逐字节相同**
    ② **blk 的 text 必须逐字取自该句 ja** ⇒ 判据 = 每个 layer.text ∈ ja（子串）
    ③ **不许用 json.dumps 重建整个文件**（会改分隔符/转义/字段顺序 ⇒ 破坏①）
       ⇒ 改成**文本级手术**：在目标句的最后一个 `}` 前插入 `,"blk":{...}`

用法：
    python3 scripts/restore_blk.py --backup _backup_subs_133119 --app site/app/subs [--dry-run]
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = "/workspace/nhk"


def die(msg):
    print("❌ " + msg, file=sys.stderr)
    sys.exit(1)


def find_subs_assign(text):
    """找 `window.__SUBS__["<ep>"] = {...}` 的赋值起点与整个对象的范围"""
    m = re.search(r'window\.__SUBS__\["([^"]+)"\]\s*=\s*', text)
    if not m:
        return None
    start = text.index("{", m.end() - 1)
    depth = 0
    instr = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return m.group(1), start, i + 1
    return None


def iter_sentences(obj_text):
    """在 `{"easy":[...], "web":[...]}` 里逐句返回 (start,end) —— 句对象的括号范围"""
    out = []
    i = 0
    n = len(obj_text)
    while i < n:
        c = obj_text[i]
        if c == "[":
            depth = 0
            instr = False
            esc = False
            j = i
            while j < n:
                d = obj_text[j]
                if instr:
                    if esc:
                        esc = False
                    elif d == "\\":
                        esc = True
                    elif d == '"':
                        instr = False
                elif d == '"':
                    instr = True
                elif d in "[{":
                    depth += 1
                elif d in "]}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            # 数组区间 [i, j+1)
            k = i + 1
            while k < j:
                if obj_text[k] == "{":
                    d2 = 0
                    instr = False
                    esc = False
                    m2 = k
                    while m2 < n:
                        e = obj_text[m2]
                        if instr:
                            if esc:
                                esc = False
                            elif e == "\\":
                                esc = True
                            elif e == '"':
                                instr = False
                        elif e == '"':
                            instr = True
                        elif e == "{":
                            d2 += 1
                        elif e == "}":
                            d2 -= 1
                            if d2 == 0:
                                break
                        m2 += 1
                    out.append((k, m2 + 1))
                    k = m2 + 1
                else:
                    k += 1
            i = j + 1
        else:
            i += 1
    return out


def extract_blk(sentence_text):
    """从一句里取 blk 的**原始 JSON 文本**（保持原样，不重新序列化）"""
    i = sentence_text.find('"blk"')
    if i < 0:
        return None
    j = sentence_text.index(":", i) + 1
    while j < len(sentence_text) and sentence_text[j] in " \t":
        j += 1
    if sentence_text[j] != "{":
        return None
    depth = 0
    instr = False
    esc = False
    k = j
    while k < len(sentence_text):
        c = sentence_text[k]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return sentence_text[j:k + 1]
        k += 1
    return None


def _match_brace(text, j):
    """text[j] 必须是 '{' ⇒ 返回配对 '}' 的下标（考虑字符串与转义）"""
    depth = 0
    instr = False
    esc = False
    k = j
    while k < len(text):
        c = text[k]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return -1


def strip_all_blk(text):
    """剥掉所有 `,"blk":{...}` 段（用于「只加 blk」的反向验证）

    🔴 剪掉的范围 = 前面的逗号 + 空格 + `"blk":` + 配对的 `{...}`
    ⇒ 剥完必须与插入前的文件**逐字节相同**
    """
    i = 0
    while True:
        i = text.find('"blk"', i)
        if i < 0:
            return text
        # 往前吃掉逗号与空白
        start = i
        while start > 0 and text[start - 1] in " \t\r\n":
            start -= 1
        if start > 0 and text[start - 1] == ",":
            start -= 1
        else:
            # 没有前置逗号（理论上不会）⇒ 只剪 blk 本身
            start = i
        j = text.index(":", i) + 1
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text) or text[j] != "{":
            i += 5
            continue
        k = _match_brace(text, j)
        if k < 0:
            raise ValueError("blk 的花括号没配对")
        text = text[:start] + text[k + 1:]
        i = start


def sentence_ja(sentence_text):
    m = re.search(r'"ja"\s*:\s*"((?:[^"\\]|\\.)*)"', sentence_text)
    if not m:
        return None
    return json.loads('"' + m.group(1) + '"')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", default="_backup_subs_133119")
    ap.add_argument("--app", default="site/app/subs")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    bdir = os.path.join(ROOT, a.backup)
    adir = os.path.join(ROOT, a.app)
    if not os.path.isdir(bdir):
        die("备份目录不存在：%s" % bdir)
    if not os.path.isdir(adir):
        die("目标目录不存在：%s" % adir)

    total_ep = 0
    total_sent = 0
    for fn in sorted(os.listdir(bdir)):
        if not fn.endswith(".js"):
            continue
        bpath = os.path.join(bdir, fn)
        apath = os.path.join(adir, fn)
        if not os.path.isfile(apath):
            print(f"  ⚠️  {fn}: 目标没有 ⇒ 跳过")
            continue
        btxt = open(bpath, encoding="utf-8").read()
        atxt = open(apath, encoding="utf-8").read()

        binfo = find_subs_assign(btxt)
        ainfo = find_subs_assign(atxt)
        if not binfo or not ainfo:
            print(f"  ⚠️  {fn}: 解析不出 subs 赋值 ⇒ 跳过")
            continue
        bep, bstart, bend = binfo
        aep, astart, aend = ainfo
        if bep != aep:
            die(f"{fn}: 期号不一致（备份 {bep} vs 目标 {aep}）")

        bobj = btxt[bstart:bend]
        aobj = atxt[astart:aend]
        bsent = iter_sentences(bobj)
        asent = iter_sentences(aobj)
        if len(bsent) != len(asent):
            die(f"{fn}: 句数不一致（备份 {len(bsent)} vs 目标 {len(asent)}）⇒ 不许动")

        # 逐句配对：备份里有 blk、目标里没有 ⇒ 要插
        inserts = []   # (目标句内的绝对偏移, blkJSON 文本)
        for (bs, be), (as_, ae) in zip(bsent, asent):
            bs_txt = bobj[bs:be]
            as_txt = aobj[as_:ae]
            if '"blk"' in as_txt:
                continue        # 目标已有 ⇒ 不重复插
            blk = extract_blk(bs_txt)
            if not blk:
                continue
            ja = sentence_ja(as_txt)
            if ja is None:
                die(f"{fn}: 句里没有 ja 字段 ⇒ 解析不对")
            # 🔴 硬判据：blk 的每个 layer.text 必须逐字取自该句
            try:
                bj = json.loads(blk)
            except Exception as e:
                die(f"{fn}: blk 不是合法 JSON：{e}")
            layers = bj.get("layers") or bj.get("blocks") or []
            if not layers:
                die(f"{fn}: blk 里没有 layers/blocks ⇒ 结构不对")
            for L in layers:
                t = L.get("text", "")
                if t and t not in ja:
                    die(f"{fn}: blk 片段 {t!r} 不是该句 ja 的子串 ⇒ 拒绝插入")
            # 插入点 = 该句对象最后一个 `}` 之前
            # 🔴 偏移是**相对 aobj** 的 ⇒ 用 ae-1，**不是 as_+ae-1**（多加了起点）
            inserts.append((ae - 1, blk))

        if not inserts:
            print(f"  ➖ {fn}: 无需恢复（{len(bsent)} 句，目标已含全部 blk）")
            continue

        # 从后往前插（偏移才不会错位）
        new_obj = aobj
        for off, blk in sorted(inserts, key=lambda x: -x[0]):
            new_obj = new_obj[:off] + ',"blk":' + blk + new_obj[off:]
        new_txt = atxt[:astart] + new_obj + atxt[aend:]

        # 🔴 反向验证①：剥掉全部 blk 后，必须与原文件**逐字节相同**
        if strip_all_blk(new_txt) != atxt:
            die(f"{fn}: 剥 blk 后与原文件不一致 ⇒ 只加 blk 这条判据没过 ⇒ 拒绝写盘")
        # 🔴 反向验证②：blk 句数必须 = 插入数
        if new_txt.count('"blk"') != len(inserts):
            die(f"{fn}: blk 数量对不上")
        # 🔴 反向验证③：node --check 语法
        import subprocess
        tmp = os.path.join(ROOT, "work", "_blk_check.js")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        open(tmp, "w", encoding="utf-8").write(new_txt)
        # ⚠️ stderr 可能被截断在多字节字符中间 ⇒ errors="replace"（否则解码就崩）
        r = subprocess.run(["node", "--check", tmp], capture_output=True,
                           text=True, errors="replace")
        if r.returncode != 0:
            die(f"{fn}: node --check 失败：{(r.stderr or '')[:300]}")

        if not a.dry_run:
            open(apath, "w", encoding="utf-8").write(new_txt)
        total_ep += 1
        total_sent += len(inserts)
        print(f"  ✅ {fn}: 恢复 {len(inserts)} 句的 blk（{len(bsent)} 句里）"
              f"{'（dry-run 未写盘）' if a.dry_run else ''}")

    print(f"\n{'✅' if total_ep else '➖'} 共 {total_ep} 期、{total_sent} 句恢复了 blk")
    if a.dry_run:
        print("   （dry-run：没写盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
