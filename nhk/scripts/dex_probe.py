#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dex_probe.py —— 极简 dex 读取器：回答「某个方法的指令里到底有什么」

为什么自己写：
  沙箱重置后没有 dexdump/baksmali；而且「方法名在不在」≠「方法体做了什么」
  ⇒ 判据要落在**指令**上，不是字符串上。

两个已踩过的坑（写之前先记住）：
  ① `method_ids_off` 是 **0x5c (92)**，不是 0x58（0x58 是 method_ids_**size**）
  ② **必须按 opcode 表走指令长度**，不能按 1 走 ⇒ 否则会撞到假的 0x6e/0x74 判错
  ③ `class_data_item` 里方法索引是 `method_idx_diff`（差值），
     且 `direct_methods` / `virtual_methods` 是**两个独立列表**、各自第一个是绝对索引
  ④ `method_idx` 拿到的**方法名可能是别人的**（索引错位）⇒ 必须用
     `method_info()` 反查**所属类是不是当前类**来验证

用法：
  python3 dex_probe.py <apk> --class AudioService --method position
"""
import struct
import sys
import zipfile

# opcodes 的指令长度表（单位：16-bit code unit）
# None = 变长（需要特殊处理）
LEN = {}


def _build_len_table():
    # 0x00 nop .. 0x0e return-void：1
    for i in range(0x0e + 1):
        LEN[i] = 1
    LEN[0x0f] = 2          # return
    LEN[0x10] = 2          # return-wide
    LEN[0x11] = 2          # return-object
    for i in range(0x12, 0x1e + 1):   # const/4 .. const-string
        LEN[i] = 2
    LEN[0x1f] = 3          # check-cast
    LEN[0x20] = 2          # instance-of
    LEN[0x21] = 1          # array-length
    LEN[0x22] = 2          # new-instance
    LEN[0x23] = 2          # new-array
    LEN[0x24] = 3          # filled-new-array
    LEN[0x25] = 3          # filled-new-array/range
    LEN[0x26] = 3          # fill-array-data
    for i in range(0x27, 0x32 + 1):   # throw..cmp-long? 全部 2
        LEN[i] = 2
    for i in range(0x32, 0x3e + 1):   # if-eq..if-le
        LEN[i] = 2
    for i in range(0x44, 0x52 + 1):   # aget..aput
        LEN[i] = 2
    for i in range(0x52, 0x6e + 1):   # iget..invoke
        LEN[i] = 2
    for i in range(0x6e, 0x73):       # invoke-*/range
        LEN[i] = 3
    for i in range(0x73, 0x79 + 1):   # unop..int-to-float
        LEN[i] = 2
    for i in range(0x7b, 0x8f + 1):   # double-to-* .. int-to-short
        LEN[i] = 2
    for i in range(0x90, 0xb0 + 1):   # add-int..rem-double
        LEN[i] = 2
    for i in range(0xb0, 0xd0 + 1):   # add/2addr..rem-int/2addr
        LEN[i] = 2
    for i in range(0xd0, 0xe3 + 1):   # add-long/2addr..ushr-int/2addr
        LEN[i] = 2
    for i in range(0xfa, 0x100):      # invoke-polymorphic
        LEN[i] = 4
    LEN[0x00] = 1
    # 伪指令（数据）：nop 家族
    LEN[0x0100] = 1          # packed-switch payload（伪）
    LEN[0x0200] = 1          # sparse-switch payload
    LEN[0x0300] = 1          # fill-array-data payload


_build_len_table()

# 常用 opcode 名字
NAME = {
    0x12: "const/4", 0x13: "const/16", 0x14: "const", 0x15: "const/high16",
    0x1a: "const-string", 0x1b: "const-string/jumbo",
    0x6e: "invoke-virtual", 0x6f: "invoke-super", 0x70: "invoke-direct",
    0x71: "invoke-static", 0x72: "invoke-interface",
    0x74: "invoke-virtual/range", 0x75: "invoke-super/range",
    0x76: "invoke-direct/range", 0x77: "invoke-static/range",
    0x78: "invoke-interface/range",
    0x97: "div-int", 0x98: "div-int/2addr", 0x9d: "rem-int",
    0x90: "add-int", 0xb0: "add-int/2addr", 0x0e: "return-void",
}


class Dex:
    def __init__(self, data):
        self.d = data
        (self.string_ids_size, self.string_ids_off,
         self.type_ids_size, self.type_ids_off,
         self.proto_ids_size, self.proto_ids_off,
         self.field_ids_size, self.field_ids_off,
         self.method_ids_size, self.method_ids_off,
         self.class_defs_size, self.class_defs_off,
         self.data_size, self.data_off) = struct.unpack_from("<14I", data, 0x38)
        self.strings = []
        for i in range(self.string_ids_size):
            off = struct.unpack_from("<I", data, self.string_ids_off + 4 * i)[0]
            # uleb128 长度，然后是 MUTF-8
            p = off
            while data[p] & 0x80:
                p += 1
            p += 1
            end = data.index(b"\x00", p)
            self.strings.append(data[p:end].decode("utf-8", "replace"))

    def type_name(self, idx):
        si = struct.unpack_from("<I", self.d, self.type_ids_off + 4 * idx)[0]
        return self.strings[si]

    def method_info(self, idx):
        off = self.method_ids_off + 8 * idx
        cls, proto, name = struct.unpack_from("<HHI", self.d, off)
        return self.type_name(cls), self.strings[name]

    def field_info(self, idx):
        off = self.field_ids_off + 8 * idx
        cls, typ, name = struct.unpack_from("<HHI", self.d, off)
        return self.type_name(cls), self.type_name(typ), self.strings[name]


def uleb(d, p):
    r = 0
    s = 0
    while True:
        b = d[p]
        p += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            break
        s += 7
    return r, p


def read_class_methods(dx, class_def_off):
    """返回 [(method_idx, access_flags, code_off)]"""
    d = dx.d
    p = class_def_off
    (class_idx, access, superclass, interfaces_off, source_file_idx,
     annotations_off, class_data_off, static_values_off) = struct.unpack_from("<8I", d, p)
    p += 32
    if class_data_off == 0:
        return class_idx, []
    p = class_data_off
    n_static, p = uleb(d, p)
    n_inst, p = uleb(d, p)
    n_direct, p = uleb(d, p)
    n_virtual, p = uleb(d, p)
    out = []
    # 🔴 encoded_field = (field_idx_diff, access_flags) —— **只有 2 个 uleb**
    #    （我第一版在这里读了 3 个 → 后面全部错位 → 方法名读成别人的）
    for _ in range(n_static + n_inst):
        _fd, p = uleb(d, p)
        _fa, p = uleb(d, p)
    idx = 0
    for _ in range(n_direct):
        idx_diff, p = uleb(d, p)
        acc, p = uleb(d, p)
        code_off, p = uleb(d, p)
        idx += idx_diff
        out.append((idx, acc, code_off))
    # 🔴 virtual_methods 是**独立列表**，第一个是绝对索引
    idx = 0
    for _ in range(n_virtual):
        idx_diff, p = uleb(d, p)
        acc, p = uleb(d, p)
        code_off, p = uleb(d, p)
        idx += idx_diff
        out.append((idx, acc, code_off))
    return class_idx, out


def dump_code(dx, code_off):
    d = dx.d
    registers, ins, outs, tries, debug_off, insns_size = struct.unpack_from("<HHHHII", d, code_off)
    p = code_off + 16
    insns = d[p:p + insns_size * 2]
    out = []
    i = 0
    while i < len(insns):
        op = insns[i]
        if op == 0x00:
            # 可能是伪指令：nop / packed-switch / sparse-switch / fill-array-data
            if i + 1 < len(insns):
                ident = insns[i + 1]
                if ident == 0x0100 or ident == 0x0200 or ident == 0x0300:
                    if ident == 0x0100:
                        size = struct.unpack_from("<H", insns, i + 2)[0]
                        ln = 4 + size * 2
                    elif ident == 0x0200:
                        size = struct.unpack_from("<H", insns, i + 2)[0]
                        ln = 2 + size * 4
                    else:
                        w = struct.unpack_from("<H", insns, i + 2)[0]
                        ln = 4 + w * 2
                    out.append((i, "payload", ""))
                    i += ln * 2
                    continue
            out.append((i, "nop", ""))
            i += 2
            continue
        ln = LEN.get(op)
        if ln is None:
            out.append((i, "op_0x%02x?" % op, ""))
            i += 2
            continue
        extra = ""
        if op == 0x13:  # const/16
            extra = " = %d" % struct.unpack_from("<h", insns, i + 2)[0]
        elif op == 0x14:  # const
            extra = " = %d" % struct.unpack_from("<i", insns, i + 2)[0]
        elif op in (0x6e, 0x6f, 0x70, 0x71, 0x72):
            mid = struct.unpack_from("<H", insns, i + 2)[0]
            try:
                c, n = dx.method_info(mid)
                extra = " -> %s.%s" % (c, n)
            except Exception:
                extra = " -> ?"
        elif op in (0x74, 0x75, 0x76, 0x77, 0x78):
            mid = struct.unpack_from("<H", insns, i + 2)[0]
            try:
                c, n = dx.method_info(mid)
                extra = " -> %s.%s" % (c, n)
            except Exception:
                extra = " -> ?"
        elif op in (0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x5b,
                    0x5c, 0x5d, 0x5e, 0x5f, 0x60, 0x61):
            fid = struct.unpack_from("<H", insns, i + 2)[0]
            try:
                c, t, n = dx.field_info(fid)
                extra = " -> %s.%s:%s" % (c, n, t)
            except Exception:
                extra = ""
        out.append((i, NAME.get(op, "op_0x%02x" % op), extra))
        i += ln * 2
    return out, registers, insns_size


def main():
    apk = sys.argv[1]
    want_class = want_method = None
    if "--class" in sys.argv:
        want_class = sys.argv[sys.argv.index("--class") + 1]
    if "--method" in sys.argv:
        want_method = sys.argv[sys.argv.index("--method") + 1]
    with zipfile.ZipFile(apk) as z:
        data = z.read("classes.dex")
    dx = Dex(data)
    print("== %s ==" % apk)
    for i in range(dx.class_defs_size):
        off = dx.class_defs_off + 32 * i
        class_idx, methods = read_class_methods(dx, off)
        cname = dx.type_name(class_idx)
        if want_class and want_class not in cname:
            continue
        for mid, acc, code_off in methods:
            try:
                owner, mname = dx.method_info(mid)
            except Exception:
                continue
            if owner != cname:      # 🔴 索引错位自检
                continue
            if want_method and mname != want_method:
                continue
            if code_off == 0:
                continue
            ins, regs, size = dump_code(dx, code_off)
            print("\n-- %s.%s (method_idx=%d, regs=%d, insns=%d) --" %
                  (cname, mname, mid, regs, size))
            for off2, op, extra in ins:
                print("   %04x  %-22s%s" % (off2, op, extra))


if __name__ == "__main__":
    main()
