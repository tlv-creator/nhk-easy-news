#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apk_check_formats.py —— APK 级「getString 格式串实参数量」静态检查器
=========================================================================
为什么需要这个
--------------------------------------------------------------------------
真实事故（小丸子项目）：把 `list_subtitle` 从 `%1$d` 改成 `%1$d ... %2$s`，
忘了同步 `MainActivity.buildUi()` 里那行 `getString(R.string.list_subtitle, 0)`。
⇒ 实参比占位符少 → **MissingFormatArgumentException**
⇒ 发生在 **Activity.onCreate 里** → **点图标秒退**，而纯 JVM 单测根本覆盖不到。

🔴 **已有的 `check_formats.py` 只能查源码** → 对「已经打好的 APK」是盲区。
   而用户手机上闪退的那个包，恰恰就是盲区。
   ⇒ **这个工具直接读 APK**：解析 resources.arsc 拿占位符数 + 解析 classes.dex
   找 getString 调用点并数实参 → 对不上就报。
   **谁有 APK 就能查，不需要源码树、不需要 Java 工具链。**

用法
--------------------------------------------------------------------------
    python3 apk_check_formats.py <apk> [<apk> ...]
    python3 apk_check_formats.py --dump-strings <apk>     # 只列带占位符的字符串资源

退出码：0 = 没有发现问题；1 = 有（发布前应拒绝）
"""
import os
import re
import struct
import sys
import zipfile

# =====================================================================
# 1) resources.arsc —— 拿到「资源 ID → 字符串值」
# =====================================================================
RES_STRING_POOL_TYPE = 0x0001
RES_TABLE_TYPE = 0x0002
RES_TABLE_PACKAGE_TYPE = 0x0200
RES_TABLE_TYPE_TYPE = 0x0201
RES_TABLE_TYPE_SPEC_TYPE = 0x0202

UTF8_FLAG = 1 << 8


def _u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def parse_string_pool(data, off):
    """解析 RES_STRING_POOL → (list[str], 下一块偏移)"""
    if _u16(data, off) != RES_STRING_POOL_TYPE:
        return [], off + _u16(data, off + 2)
    chunk_size = _u32(data, off + 4)
    string_count = _u32(data, off + 8)
    style_count = _u32(data, off + 12)
    flags = _u32(data, off + 16)
    strings_start = _u32(data, off + 20)
    is_utf8 = bool(flags & UTF8_FLAG)

    offsets = []
    base = off + 28
    for i in range(string_count):
        offsets.append(_u32(data, base + i * 4))

    strs = []
    for i in range(string_count):
        p = off + strings_start + offsets[i]
        if is_utf8:
            # uleb128 长度（字符数），然后 uleb128 字节数
            n1 = data[p]; p += 1
            if n1 & 0x80:
                n1 = ((n1 & 0x7f) << 8) | data[p]; p += 1
            n2 = data[p]; p += 1
            if n2 & 0x80:
                n2 = ((n2 & 0x7f) << 8) | data[p]; p += 1
            raw = data[p:p + n2]
            try:
                s = raw.decode('utf-8', 'replace')
            except Exception:
                s = ''
        else:
            n = _u16(data, p); p += 2
            if n & 0x8000:
                n = ((n & 0x7fff) << 16) | _u16(data, p); p += 2
            raw = data[p:p + n * 2]
            try:
                s = raw.decode('utf-16-le', 'replace')
            except Exception:
                s = ''
        strs.append(s)
    return strs, off + chunk_size


def parse_arsc(data):
    """→ dict: resource_id → 字符串值（只取 string 类型）"""
    if _u16(data, 0) != RES_TABLE_TYPE:
        return {}
    header_size = _u16(data, 2)
    total = _u32(data, 4)

    global_strings, off = parse_string_pool(data, header_size)

    out = {}
    while off + 8 <= total:
        ctype = _u16(data, off)
        cheader = _u16(data, off + 2)
        csize = _u32(data, off + 4)
        if csize == 0:
            break
        if ctype == RES_TABLE_PACKAGE_TYPE:
            out.update(_parse_package(data, off, global_strings))
        off += csize
    return out


def _parse_package(data, off, global_strings):
    pkg_id = _u32(data, off + 8)
    type_strings, o2 = parse_string_pool(data, off + 288)
    key_strings, o3 = parse_string_pool(data, o2)

    res = {}
    o = o3
    end = off + _u32(data, off + 4)
    while o + 8 <= end:
        ctype = _u16(data, o)
        csize = _u32(data, o + 4)
        if csize == 0:
            break
        if ctype == RES_TABLE_TYPE_TYPE:
            tid = data[o + 8]
            entry_count = _u32(data, o + 16)
            entries_start = _u32(data, o + 20)
            tname = type_strings[tid - 1] if 0 < tid <= len(type_strings) else ''
            if tname == 'string':
                for idx in range(entry_count):
                    eo = _u32(data, o + cheader_of(o, data) + idx * 4)
                    if eo == 0xFFFFFFFF:
                        continue
                    ep = o + entries_start + eo
                    if _u16(data, ep) == 0:      # size == 0 → 空项
                        continue
                    eflags = _u16(data, ep + 2)
                    if eflags & 0x0001:          # FLAG_COMPLEX → map，跳过
                        continue
                    data_type = data[ep + 7]
                    dval = _u32(data, ep + 8)
                    if data_type == 0x03:        # TYPE_STRING
                        if dval < len(global_strings):
                            rid = (pkg_id << 24) | (tid << 16) | idx
                            res[rid] = global_strings[dval]
        o += csize
    return res


def cheader_of(off, data):
    return _u16(data, off + 2)


# =====================================================================
# 2) classes.dex —— 找 getString 调用点，数实参
# =====================================================================
# Dalvik 每条指令占多少个 16-bit code unit
OP_LEN = {}
for _o in range(0x00, 0x0e):
    OP_LEN[_o] = 1
OP_LEN.update({
    0x0e: 1, 0x0f: 1, 0x10: 1, 0x11: 1, 0x12: 1,
    0x13: 2, 0x14: 3, 0x15: 2, 0x16: 2, 0x17: 3, 0x18: 5, 0x19: 2,
    0x1a: 2, 0x1b: 3, 0x1c: 2, 0x1d: 1, 0x1e: 1, 0x1f: 2,
    0x20: 2, 0x21: 1, 0x22: 2, 0x23: 2, 0x24: 3, 0x25: 3, 0x26: 3,
    0x27: 1, 0x28: 1, 0x29: 2, 0x2a: 3, 0x2b: 3, 0x2c: 3,
})
for _o in range(0x2d, 0x32):
    OP_LEN[_o] = 2
for _o in range(0x32, 0x44):
    OP_LEN[_o] = 2
for _o in range(0x44, 0x52):
    OP_LEN[_o] = 2
for _o in range(0x52, 0x6e):
    OP_LEN[_o] = 2
for _o in range(0x6e, 0x73):
    OP_LEN[_o] = 3
OP_LEN[0x73] = 1
for _o in range(0x74, 0x79):
    OP_LEN[_o] = 3
OP_LEN[0x79] = 1
OP_LEN[0x7a] = 1
for _o in range(0x7b, 0x90):
    OP_LEN[_o] = 1
for _o in range(0x90, 0xb0):
    OP_LEN[_o] = 2
for _o in range(0xb0, 0xd0):
    OP_LEN[_o] = 1
for _o in range(0xd0, 0xd8):
    OP_LEN[_o] = 2
for _o in range(0xd8, 0xe3):
    OP_LEN[_o] = 2
for _o in range(0xe3, 0xfa):
    OP_LEN[_o] = 1
OP_LEN[0xfa] = 4
OP_LEN[0xfb] = 4
OP_LEN[0xfc] = 3
OP_LEN[0xfd] = 3
OP_LEN[0xfe] = 2
OP_LEN[0xff] = 2

# 关注的调用目标
WATCH = {
    ('Landroid/content/Context;', 'getString', 0): 'Context.getString(int)',
    ('Landroid/content/Context;', 'getString', 1): 'Context.getString(int,Object...)',
    ('Landroid/content/res/Resources;', 'getString', 0): 'Resources.getString(int)',
    ('Landroid/content/res/Resources;', 'getString', 1): 'Resources.getString(int,Object...)',
    ('Landroid/content/res/Resources;', 'getText', 0): 'Resources.getText(int)',
}


def uleb128(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, off


class Dex(object):
    def __init__(self, data):
        self.d = data
        self.string_count = _u32(data, 56)
        self.string_off = _u32(data, 60)
        self.type_count = _u32(data, 64)
        self.type_off = _u32(data, 68)
        self.proto_count = _u32(data, 72)
        self.proto_off = _u32(data, 76)
        self.field_count = _u32(data, 80)
        self.field_off = _u32(data, 84)
        self.method_count = _u32(data, 88)
        self.method_off = _u32(data, 92)
        self.class_count = _u32(data, 96)
        self.class_off = _u32(data, 100)
        self._strings = None

    def strings(self):
        if self._strings is not None:
            return self._strings
        out = []
        d = self.d
        for i in range(self.string_count):
            off = _u32(d, self.string_off + i * 4)
            _, p = uleb128(d, off)
            end = d.index(b'\x00', p)
            out.append(d[p:end].decode('utf-8', 'replace'))
        self._strings = out
        return out

    def type_name(self, idx):
        s = self.strings()
        desc_idx = _u32(self.d, self.type_off + idx * 4)
        return s[desc_idx]

    def proto_params(self, proto_idx):
        """→ list[str]（参数类型描述符）"""
        d = self.d
        base = self.proto_off + proto_idx * 12
        params_off = _u32(d, base + 8)
        if params_off == 0:
            return []
        size = _u32(d, params_off)
        out = []
        for i in range(size):
            tidx = _u16(d, params_off + 4 + i * 2)
            out.append(self.type_name(tidx))
        return out

    def method_info(self, idx):
        d = self.d
        base = self.method_off + idx * 8
        class_idx = _u16(d, base)
        proto_idx = _u16(d, base + 2)
        name_idx = _u32(d, base + 4)
        return self.type_name(class_idx), self.strings()[name_idx], proto_idx

    def field_info(self, idx):
        """→ (class_name, field_name, type_name)"""
        d = self.d
        base = self.field_off + idx * 8
        class_idx = _u16(d, base)
        type_idx = _u16(d, base + 2)
        name_idx = _u32(d, base + 4)
        return self.type_name(class_idx), self.strings()[name_idx], self.type_name(type_idx)

    def all_code(self):
        """遍历所有 class_data → code_item，产出 (class_name, insns)"""
        d = self.d
        for ci in range(self.class_count):
            cbase = self.class_off + ci * 32
            class_idx = _u32(d, cbase)
            class_data_off = _u32(d, cbase + 24)
            if class_data_off == 0:
                continue
            cname = self.type_name(class_idx)
            p = class_data_off
            sf, p = uleb128(d, p)
            inf, p = uleb128(d, p)
            dm, p = uleb128(d, p)
            vm, p = uleb128(d, p)
            for _ in range(sf + inf):
                _, p = uleb128(d, p)
                _, p = uleb128(d, p)
            for _ in range(dm + vm):
                _, p = uleb128(d, p)
                _, p = uleb128(d, p)
                code_off, p = uleb128(d, p)
                if code_off == 0:
                    continue
                insns_size = _u32(d, code_off + 12)
                insns_off = code_off + 16
                yield cname, d, insns_off, insns_size


def scan_getstring_calls(dex):
    """找出所有 getString/getText 调用点 → [(resId, argCount, class, callDesc)]"""
    calls = []
    for cname, d, insns_off, insns_size in dex.all_code():
        # 寄存器 → 常量值 / 数组信息
        consts = {}      # reg → int
        arrays = {}      # reg → size reg
        i = 0
        while i < insns_size:
            unit = _u16(d, insns_off + i * 2)
            op = unit & 0xff
            length = OP_LEN.get(op, 1)

            if op == 0x14:      # const vAA, #+BBBBBBBB
                reg = (unit >> 8) & 0xff
                consts[reg] = _u32(d, insns_off + (i + 1) * 2)
            elif op == 0x13:    # const/16 vAA, #+BBBB
                reg = (unit >> 8) & 0xff
                consts[reg] = _u16(d, insns_off + (i + 1) * 2)
            elif op == 0x12:    # const/4 vA, #+B
                reg = (unit >> 8) & 0x0f
                val = (unit >> 12) & 0x0f
                if val & 0x08:
                    val -= 16
                consts[reg] = val
            elif op == 0x15:    # const/high16 vAA, #+BBBB0000
                reg = (unit >> 8) & 0xff
                consts[reg] = _u16(d, insns_off + (i + 1) * 2) << 16
            elif op == 0x23:    # new-array vA, vB, type
                b = _u16(d, insns_off + (i + 1) * 2)
                dest = (unit >> 8) & 0xff
                size_reg = b & 0xff
                arrays[dest] = size_reg
            elif op == 0x24:    # filled-new-array {vC..vG}, type
                b = _u16(d, insns_off + (i + 1) * 2)
                count = (unit >> 8) & 0x0f
                arrays[0] = ('filled', count)      # dest 固定是 v0
            elif op in (0x6e, 0x6f, 0x70, 0x71, 0x72):
                # 35c: A|G|op BBBB  F|E|D|C
                method_idx = _u16(d, insns_off + (i + 1) * 2)
                b2 = _u16(d, insns_off + (i + 2) * 2)
                regs = [b2 & 0x0f, (b2 >> 4) & 0x0f, (b2 >> 8) & 0x0f, (b2 >> 12) & 0x0f]
                argc = (unit >> 12) & 0x0f
                _handle_invoke(dex, calls, method_idx, regs, argc, cname)
            elif op in (0x74, 0x75, 0x76, 0x77, 0x78):
                # 3rc: AA|op BBBB CCCC
                method_idx = _u16(d, insns_off + (i + 1) * 2)
                first = _u16(d, insns_off + (i + 2) * 2)
                argc = (unit >> 8) & 0xff
                regs = list(range(first, first + argc))
                _handle_invoke(dex, calls, method_idx, regs, argc, cname)

            i += length
    return calls


def _handle_invoke(dex, calls, method_idx, regs, argc, cname):
    if method_idx >= dex.method_count:
        return
    cls, name, proto_idx = dex.method_info(method_idx)
    params = dex.proto_params(proto_idx)
    key = (cls, name, len(params) - 1)   # 最后一个参数是返回类型
    if key not in WATCH:
        return
    res_id = consts = None
    res_id = None
    # 第一个实参寄存器 → 常量 → 资源 ID
    if regs:
        res_id = _const_of(dex, regs[0])
    argcount = 0
    if len(params) == 2 and params[1] == '[Ljava/lang/Object;':
        # varargs：数组寄存器 → 数组大小
        arr_reg = regs[1] if len(regs) > 1 else None
        arr = _array_of(dex, arr_reg)
        if isinstance(arr, tuple) and arr[0] == 'filled':
            argcount = arr[1]
        elif arr is not None:
            argcount = _const_of(dex, arr)
    calls.append((res_id, argcount, WATCH[key], cname, name))


# 第二次遍历时用的全局寄存器表（简单起见，用模块级字典）
_REGS = {}


def _const_of(dex, reg):
    return _REGS.get(('c', reg))


def _array_of(dex, reg):
    return _REGS.get(('a', reg))


def scan_getstring_calls2(dex):
    """带寄存器表的一遍扫描（供 _const_of/_array_of 用）"""
    calls = []
    for cname, d, insns_off, insns_size in dex.all_code():
        _REGS.clear()
        i = 0
        while i < insns_size:
            unit = _u16(d, insns_off + i * 2)
            op = unit & 0xff
            length = OP_LEN.get(op, 1)
            if op == 0x14:
                _REGS[('c', (unit >> 8) & 0xff)] = _u32(d, insns_off + (i + 1) * 2)
            elif op == 0x13:
                _REGS[('c', (unit >> 8) & 0xff)] = _u16(d, insns_off + (i + 1) * 2)
            elif op == 0x12:
                reg = (unit >> 8) & 0x0f
                val = (unit >> 12) & 0x0f
                if val & 0x08:
                    val -= 16
                _REGS[('c', reg)] = val
            elif op == 0x15:
                _REGS[('c', (unit >> 8) & 0xff)] = _u16(d, insns_off + (i + 1) * 2) << 16
            elif op == 0x23:
                b = _u16(d, insns_off + (i + 1) * 2)
                _REGS[('a', (unit >> 8) & 0xff)] = b & 0xff
            elif op == 0x24:
                count = (unit >> 8) & 0x0f
                _REGS[('a', 0)] = ('filled', count)
            elif op in (0x6e, 0x6f, 0x70, 0x71, 0x72):
                method_idx = _u16(d, insns_off + (i + 1) * 2)
                b2 = _u16(d, insns_off + (i + 2) * 2)
                regs = [b2 & 0x0f, (b2 >> 4) & 0x0f, (b2 >> 8) & 0x0f, (b2 >> 12) & 0x0f]
                argc = (unit >> 12) & 0x0f
                _handle_invoke2(dex, calls, method_idx, regs, cname)
            elif op in (0x74, 0x75, 0x76, 0x77, 0x78):
                method_idx = _u16(d, insns_off + (i + 1) * 2)
                first = _u16(d, insns_off + (i + 2) * 2)
                argc = (unit >> 8) & 0xff
                regs = list(range(first, first + argc))
                _handle_invoke2(dex, calls, method_idx, regs, cname)
            i += length
    return calls


def _handle_invoke2(dex, calls, method_idx, regs, cname):
    if method_idx >= dex.method_count:
        return
    cls, name, proto_idx = dex.method_info(method_idx)
    params = dex.proto_params(proto_idx)
    key = (cls, name, len(params) - 1)
    if key not in WATCH:
        return
    res_id = _const_of(dex, regs[0]) if regs else None
    argcount = 0
    if len(params) == 2 and params[1] == '[Ljava/lang/Object;':
        arr = _array_of(dex, regs[1]) if len(regs) > 1 else None
        if isinstance(arr, tuple) and arr[0] == 'filled':
            argcount = arr[1]
        elif isinstance(arr, int):
            argcount = _const_of(dex, arr) or 0
    calls.append((res_id, argcount, WATCH[key], cname))


# =====================================================================
# 3) 占位符计数
# =====================================================================
# Android 的 String.format 规格：
#   %[argument_index$][flags][width][.precision]conversion
SPEC_RE = re.compile(r'%(?:(\d+)\$)?[-#+ 0,(]*\d*(?:\.\d+)?([a-zA-Z%])')


def count_specifiers(s):
    """返回 (需要几个实参, 列表)

    🔴 注意 %% 是转义的字面百分号，不算占位符。
    """
    n = 0
    used = []
    idx = 0
    for m in SPEC_RE.finditer(s):
        idx += 1
        conv = m.group(2)
        if conv == '%':
            continue
        if m.group(1):
            used.append(int(m.group(1)))
        else:
            used.append(idx)
    # 显式指定了 argument index 的，取最大 index + 1
    if used and all(isinstance(u, int) for u in used):
        need = max(used) if any('%' in s[max(0, m.start() - 0):] for m in []) else None
    # 简单可靠：需要 = 出现的非 %% 占位符里，隐式按序编号的最大值
    implicit = 0
    explicit = 0
    seq = 0
    for m in SPEC_RE.finditer(s):
        conv = m.group(2)
        if conv == '%':
            continue
        if m.group(1):
            explicit = max(explicit, int(m.group(1)))
        else:
            seq += 1
            implicit = max(implicit, seq)
    need = max(explicit, implicit)
    return need


# =====================================================================
# main
# =====================================================================
def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    dump_only = '--dump-strings' in sys.argv
    if not args:
        print(__doc__)
        return 2

    failed = 0
    for apk in args:
        if not os.path.exists(apk):
            print('[apk-fmt] FAIL %s 文件不存在' % apk)
            failed += 1
            continue
        try:
            zf = zipfile.ZipFile(apk)
            names = zf.namelist()
            if 'resources.arsc' not in names:
                print('[apk-fmt] —   %s 没有 resources.arsc（纯资源包？跳过）' % apk)
                continue
            arsc = parse_arsc(zf.read('resources.arsc'))
        except Exception as e:
            print('[apk-fmt] FAIL %s 解析 resources.arsc 出错: %s' % (apk, e))
            failed += 1
            continue

        fmt = {}
        for rid, val in arsc.items():
            if '%' in val:
                need = count_specifiers(val)
                if need:
                    fmt[rid] = (val, need)

        if dump_only:
            print('[apk-fmt] %s —— 带占位符的字符串资源 %d 条' % (apk, len(fmt)))
            for rid in sorted(fmt):
                val, need = fmt[rid]
                print('   0x%08x  需 %d 个实参  %r' % (rid, need, val[:70]))
            continue

        calls = []
        for dn in [n for n in names if re.match(r'classes\d*\.dex$', n)]:
            try:
                dex = Dex(zf.read(dn))
                calls.extend(scan_getstring_calls2(dex))
            except Exception as e:
                print('[apk-fmt] WARN %s/%s 解析失败: %s' % (apk, dn, e))

        bad = []
        for res_id, argc, desc, cname in calls:
            if res_id is None:
                continue
            info = fmt.get(res_id)
            if not info:
                continue
            val, need = info
            if argc < need:
                bad.append((res_id, need, argc, desc, cname, val))

        if not bad:
            print('[apk-fmt] OK   %s（getString 调用 %d 处，带占位符资源 %d 条 —— 无数量不匹配）'
                  % (apk, len(calls), len(fmt)))
        else:
            failed += 1
            print('[apk-fmt] FAIL %s —— %d 处实参数量不足（会 MissingFormatArgumentException → onCreate 秒退）'
                  % (apk, len(bad)))
            for res_id, need, argc, desc, cname, val in bad:
                print('   0x%08x  %s  需要 %d 个实参、实际给了 %d 个' % (res_id, desc, need, argc))
                print('       调用点: %s' % cname)
                print('       资源值: %r' % val[:90])
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
