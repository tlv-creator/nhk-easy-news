#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apk_check_api.py —— APK 级「引用了 minSdk 之外的方法」静态检查器（v2，指令级守卫判定）
=========================================================================
为什么需要这个
--------------------------------------------------------------------------
老机器上 `NoSuchMethodError` / `NoClassDefFoundError` → **点图标秒退**，
而**纯 JVM 单测完全覆盖不到**（单测跑在宿主 JVM 上，方法都在）。

怎么判「这个方法是哪个 API 才有的」
--------------------------------------------------------------------------
**不靠任何第三方 API 表** —— 直接用**官方 SDK artifact**：
下载若干个 API 等级的 `android.jar`（api-24 / api-26 / ... / api-34），
把每个的方法签名做成集合 → 某个方法**第一次出现**在哪个 jar 里，就是它的起始 API。
（android.jar 里是 .class 文件，纯 Python 解析常量池即可，零依赖。）

🔴 三层判定（v2 的重点）
--------------------------------------------------------------------------
1. **类缺失** ☠️ —— dex 引用的框架类在 minSdk 里不存在 → **类加载就炸**，
   比方法缺失更致命（方法缺失要执行到那行才炸）。
2. **方法缺失 + 沿继承链查找** —— javac 发的是**限定类型**
   （`activity.getFilesDir()` 发的是 `Landroid/app/Activity;.getFilesDir`），
   运行时沿继承链解析到 `Context.getFilesDir` → 只查 owner 类自己会**误报所有继承来的方法**。
3. **指令级守卫判定** —— 这条最关键：
   「dex 里引用了 API 26 的方法」**不等于**「无条件调用」。
   如果它在 `if (Build.VERSION.SDK_INT >= 26) { … }` 的**新 API 分支**里 → **有守卫，安全**；
   如果它在**旧分支**里、或**根本没有判断** → **真 bug**。
   ⇒ 纯文本扫描（grep SDK_INT）**做不到**这件事，会同时产生假阴性和假阳性。

用法
--------------------------------------------------------------------------
    python3 apk_check_api.py <apk> \
        --jars 24=/path/android-24.jar \
        --jars 26=/path/android-26.jar \
        --jars 29=/path/android-29.jar \
        --jars 34=/path/android-34.jar

退出码：0 = 没发现未守卫的高 API 调用；1 = 有
"""
import os
import re
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from apk_check_formats import Dex, OP_LEN, _u16, _u32, uleb128  # noqa: E402
except ImportError:
    sys.stderr.write(
        '[apk-api] 找不到同目录的 apk_check_formats.py\n'
        '        → 这两个文件要放在**同一个目录**里（apk_check_api.py 复用它的 dex 解析器）。\n')
    raise SystemExit(2)


# =====================================================================
# 1) 解析 .class 文件（android.jar 里是 class 不是 dex）
# =====================================================================
def parse_class_file(data):
    """→ (super_class 名, [(方法名, 描述符), ...])；解析失败返回 None

    🔴🔴 **Android SDK 的 android.jar 里的 .class 是大端的**（不是标准 class 文件的小端）！
       实测：major_version 字节是 `00 34`（大端读 = 52 = Java 8；小端读 = 13312），
       Utf8 长度字节是 `00 0d`（大端 = 13 = "HitTestResult"）。
       用小端解析 → 常量池第 13 项就崩（tag 读成 101）→ 静默返回 0 个方法 → 假绿灯。
    """
    if len(data) < 10 or data[0:4] != b'\xca\xfe\xba\xbe':
        return None

    def u2(o):
        return struct.unpack_from('>H', data, o)[0]

    def u4(o):
        return struct.unpack_from('>I', data, o)[0]

    cp_count = u2(8)
    cp = [None] * cp_count
    off = 10
    i = 1
    while i < cp_count:
        if off + 1 > len(data):
            return None
        tag = data[off]
        off += 1
        if tag == 1:      # Utf8
            if off + 2 > len(data):
                return None
            n = u2(off); off += 2
            cp[i] = data[off:off + n].decode('utf-8', 'replace'); off += n
        elif tag in (3, 4):        # Integer / Float
            off += 4
        elif tag in (5, 6):        # Long / Double
            off += 8
        elif tag == 7:             # Class → 记下 name_index
            cp[i] = ('Class', u2(off)); off += 2
        elif tag == 8:
            cp[i] = ('String', u2(off)); off += 2
        elif tag in (16, 19, 20):
            off += 2
        elif tag in (9, 10, 11, 12, 17, 18):
            off += 4
        elif tag == 15:
            off += 3
        else:
            return None
        if tag in (5, 6):
            i += 2
        else:
            i += 1
    if off + 8 > len(data):
        return None

    this_class = u2(off + 2)
    super_idx = u2(off + 4)
    off += 6                       # access_flags / this_class / super_class
    ifn = u2(off); off += 2        # interfaces_count
    off += ifn * 2
    fcount = u2(off); off += 2
    for _ in range(fcount):
        off += 6
        ac = u2(off); off += 2
        for _ in range(ac):
            off += 2              # attribute_name_index
            alen = u4(off); off += 4
            off += alen
    mcount = u2(off); off += 2
    methods = []
    for _ in range(mcount):
        off += 2                  # access_flags
        name = cp[u2(off)]; off += 2
        desc = cp[u2(off)]; off += 2
        ac = u2(off); off += 2
        for _ in range(ac):
            off += 2              # attribute_name_index
            alen = u4(off); off += 4
            off += alen
        methods.append((name, desc))

    super_name = None
    try:
        ent = cp[super_idx]
        if isinstance(ent, tuple) and ent[0] == 'Class':
            nm = cp[ent[1]]
            if isinstance(nm, str):
                super_name = 'L' + nm.replace('.', '/') + ';'
    except Exception:
        pass
    return super_name, methods


def load_jar_api_set(jar_path):
    """→ (方法集合, 类集合, 继承表, 解析失败数)

    🔴 **继承表是必须的**：javac 编译 `activity.getFilesDir()` 时发的是
       **限定类型**（Activity），不是声明类（Context）—— 运行时沿继承链解析。
       ⇒ 只查「owner 类自己有没有这个方法」→ **会误报所有继承来的方法**。
    """
    out = set()
    classes = set()
    supers = {}
    failed = 0
    zf = zipfile.ZipFile(jar_path)
    for n in zf.namelist():
        if not n.endswith('.class') or not n.startswith('android/'):
            continue
        cls = 'L' + n[:-6] + ';'      # android/webkit/WebView.class → Landroid/webkit/WebView;
        try:
            res = parse_class_file(zf.read(n))
        except Exception:
            failed += 1
            continue
        if res is None:                 # 真解析失败
            failed += 1
            continue
        super_name, methods = res
        # 🔴 「0 个方法」不等于解析失败 —— 注解接口（如 android.webkit.JavascriptInterface）
        #    就是一个声明了 0 个方法的 interface → 当成失败会把它误报成「类不存在」
        classes.add(cls)
        if super_name:
            supers[cls] = super_name
        for (mname, desc) in methods:
            out.add((cls, mname, desc))
    return out, classes, supers, failed


# =====================================================================
# 2) 指令级分析：调用点 + 版本守卫
# =====================================================================
def method_only_called_guarded(mkey, callers_of):
    """这个方法里的新 API 引用，是否因为「**所有调用点都在守卫分支内**」而安全？

    🔴 **这是「纯隔离类」形态的判据**（Android 官方推荐）：
       把新 API 的调用放进版本特定的静态内部类，**方法体内不重复写 SDK_INT 判断**。
       老设备上那个类压根不会被加载 → 不会有验证期/执行期的问题。

       官方依据（AndroidX compat.md）：
       "Non-reflective calls to new APIs gated on SDK_INT **must** be made from
        version-specific static inner classes to avoid verification errors…"
       lint 的 `ClassVerificationFailure` 检查强制这个形态。

    🔴 **判据（不需要递归）**：
       只要 M 的**每一个**调用点都落在守卫分支里，老设备就永远走不到 M 的方法体
       —— 至于 M 的调用者自己是怎么被调用的，**完全无关**
       （调用者内部那个 `if (SDK_INT >= N)` 照样会把老设备挡在外面）。

    ⚠️ **返回 False 的三种情况**（都要判不安全）：
       · 有一个调用点没有守卫
       · 有一个调用点落在守卫的**旧分支**里
       · **一个调用点都找不到**（顶层入口 / @JavascriptInterface）
         —— 找不到就不能证明「老设备走不到」，必须保守判不安全。
    """
    callers = callers_of.get(mkey)
    if not callers:
        return False                       # 无调用者 → 无法证明 → 不安全
    for (_cc, _cm, _cd, g) in callers:
        if g is None or g[1] != 'new':
            return False                   # 有调用点没被守卫包住
    return True


def all_code_named(dex):
    """遍历所有 class_data → code_item，产出 (class_name, method_name, method_desc, insns)

    🔴 **为什么自己写一份而不改 `apk_check_formats.py` 的 `all_code`**：
       `apk_check_formats.py` 是**已发布、md5 固定**的共享文件（`ac49a10c…`），
       改它会让所有引用方的对拍失效。→ 这里在 api checker 内部自己解析。

    🔴 **要方法名/描述符的原因**：要建「谁调用了谁」的调用图，
       才能判「纯隔离类」（方法内无守卫，但所有调用点都在守卫分支里）。
    """
    d = dex.d
    out = []
    for ci in range(dex.class_count):
        cbase = dex.class_off + ci * 32
        class_idx = _u32(d, cbase)
        class_data_off = _u32(d, cbase + 24)
        if class_data_off == 0:
            continue
        cname = dex.type_name(class_idx)
        p = class_data_off
        sf, p = uleb128(d, p)
        inf, p = uleb128(d, p)
        dm, p = uleb128(d, p)
        vm, p = uleb128(d, p)
        for _ in range(sf + inf):
            _, p = uleb128(d, p)
            _, p = uleb128(d, p)
        # 🔴🔴 method_idx 是 **method_idx_diff**（差值），而且
        #    **direct_methods 和 virtual_methods 是两个独立列表，
        #      各自的第一个元素是「绝对索引」，之后的才是差值**。
        #    （实测：延续累加会把 virtual[0] 算成别的类的 <init> → 方法名全错位）
        for list_size in (dm, vm):
            prev_idx = 0
            for _ in range(list_size):
                diff, p = uleb128(d, p)
                midx = prev_idx + diff
                prev_idx = midx
                _, p = uleb128(d, p)
                code_off, p = uleb128(d, p)
                if code_off == 0:
                    continue
                insns_size = _u32(d, code_off + 12)
                insns_off = code_off + 16
                try:
                    _mc, mname, proto_idx = dex.method_info(midx)
                    params = dex.proto_params(proto_idx)
                    ret = dex.type_name(_u16(d, dex.proto_off + proto_idx * 12 + 4))
                    mdesc = '(' + ''.join(params) + ')' + ret
                except Exception:
                    mname, mdesc = '<unknown>', '()V'
                out.append((cname, mname, mdesc, d, insns_off, insns_size))
    return out


# if-eq / if-ne / if-lt / if-ge / if-gt / if-le —— 22t 格式
IF_OPS = {0x32: 'eq', 0x33: 'ne', 0x34: 'lt', 0x35: 'ge', 0x36: 'gt', 0x37: 'le'}
# sget family —— 21c 格式
SGET_OPS = (0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66)


class MethodScan(object):
    """一个方法的指令级分析结果"""

    def __init__(self):
        self.invocations = []      # [(instr_index, cls, name, desc)]
        self.typerefs = []         # [(instr_index, type_name)]  ← new-instance / const-class / check-cast / instance-of
        self.guard_of = {}         # {instr_index: (threshold, branch)} branch ∈ new/old/none


def scan_method(dex, d, insns_off, insns_size):
    """扫描一个方法：找出 invoke 调用点 / 类型引用 + 判定每条指令落在哪个 SDK_INT 分支

    🔴 **必须单遍按指令顺序推进**（不能先扫完整个方法再回头查寄存器）：
       寄存器会被重新赋值。实测 hub v1.8 的 ensureChannel()：
           @2   const/16 v1, 26      ← SDK_INT 的比较阈值
           @12  const/4  v1, 2       ← v1 被覆盖！
       我原来「扫完全程再取最后值」→ 阈值算成 2/0 → **守卫被误判成「阈值不够」**。
    """
    ms = MethodScan()
    strings = dex.strings()

    field_names = {}
    try:
        for i in range(dex.field_count):
            _c, nm, _t = dex.field_info(i)
            field_names[i] = nm
    except Exception:
        pass

    sdk_regs = set()      # 持有 Build$VERSION.SDK_INT 的寄存器
    const_regs = {}       # 寄存器 → 常量值（**当前值**，被覆盖就换）
    ranges = []           # [(k, N, new_start, new_end)]

    i = 0
    while i < insns_size:
        unit = _u16(d, insns_off + i * 2)
        op = unit & 0xff
        length = OP_LEN.get(op, 1)

        if op in SGET_OPS:                       # sget vAA, field
            fi = _u16(d, insns_off + (i + 1) * 2)
            if field_names.get(fi, '') == 'SDK_INT':
                sdk_regs.add((unit >> 8) & 0xff)
        elif op == 0x14:                         # const vAA, #+BBBBBBBB
            const_regs[(unit >> 8) & 0xff] = _u32(d, insns_off + (i + 1) * 2)
        elif op == 0x13:                         # const/16 vAA, #+BBBB
            const_regs[(unit >> 8) & 0xff] = _u16(d, insns_off + (i + 1) * 2)
        elif op == 0x12:                         # const/4 vA, #+B
            reg = (unit >> 8) & 0x0f
            v = (unit >> 12) & 0x0f
            if v & 0x08:
                v -= 16
            const_regs[reg] = v
        elif op == 0x15:                         # const/high16 vAA, #+BBBB0000
            const_regs[(unit >> 8) & 0xff] = _u16(d, insns_off + (i + 1) * 2) << 16
        elif op in (0x6e, 0x6f, 0x70, 0x71, 0x72, 0x74, 0x75, 0x76, 0x77, 0x78):
            mi = _u16(d, insns_off + (i + 1) * 2)
            if mi < dex.method_count:
                cls, mname, proto_idx = dex.method_info(mi)
                params = dex.proto_params(proto_idx)
                ret = dex.type_name(_u16(d, dex.proto_off + proto_idx * 12 + 4))
                desc = '(' + ''.join(params) + ')' + ret
                ms.invocations.append((i, cls, mname, desc))
        elif op in (0x1c, 0x1f, 0x20, 0x22):
            ti = _u16(d, insns_off + (i + 1) * 2)
            if ti < dex.type_count:
                ms.typerefs.append((i, dex.type_name(ti)))
        elif op in IF_OPS:
            # 🔴 22t 格式是 **B|A|op**：A 在 bits 8-11、B 在 bits 12-15；
            #    第二个码字是完整的 16 位有符号偏移（跳 N 个「指令」= 2N 个码字）
            reg_a = (unit >> 8) & 0x0f
            reg_b = (unit >> 12) & 0x0f
            b2 = _u16(d, insns_off + (i + 1) * 2)
            off = b2 - 0x10000 if (b2 & 0x8000) else b2
            target = i + off
            if reg_a in sdk_regs and reg_b in const_regs:
                n = const_regs[reg_b]
                kind = IF_OPS[op]
                nxt = i + 2          # if 占 2 个码字
                if kind == 'lt':
                    # if SDK_INT < N → 跳走；所以 [nxt,target) 是「>= N」的新分支
                    ranges.append((i, n, nxt, target))
                elif kind == 'ge':
                    # if SDK_INT >= N → 跳走；所以 [target,end) 是新分支
                    ranges.append((i, n, target, insns_size))
                elif kind == 'gt':
                    ranges.append((i, n + 1, target, insns_size))
                elif kind == 'le':
                    ranges.append((i, n, nxt, target))
                # ne/eq 没有有意义的版本语义，跳过
        i += length

    # 给每条 invoke / 类型引用判定分支归属（取最内层的一条 range）
    for idx in [x[0] for x in ms.invocations] + [x[0] for x in ms.typerefs]:
        best = None
        for (k, n, s2, e) in ranges:
            if k < idx and s2 <= idx < e:
                if best is None or k > best[0]:
                    best = (k, n, 'new')
            elif k < idx and idx >= e and e <= insns_size:
                if best is None or k > best[0]:
                    best = (k, n, 'old')
        if best:
            ms.guard_of[idx] = (best[1], best[2])
    return ms


def method_available(api, cls, name, desc):
    """这个方法在该 API 等级**可用**吗？（沿继承链往上找）"""
    methods, _classes, supers, _nf = api
    c = cls
    seen = 0
    while c and seen < 30:
        if (c, name, desc) in methods:
            return True
        c = supers.get(c)
        seen += 1
    return False


def class_available(api, cls):
    _m, classes, _s, _nf = api
    return cls in classes


def collect_class_refs(dex):
    """dex 里引用到的所有框架类（type_ids）"""
    out = set()
    for i in range(dex.type_count):
        t = dex.type_name(i)
        if t.startswith('Landroid/'):
            out.add(t)
    return out


# =====================================================================
# main
# =====================================================================
def main():
    argv = sys.argv[1:]
    jars = []
    for i, a in enumerate(argv):
        if a == '--jars' and i + 1 < len(argv):
            spec = argv[i + 1]
            if '=' in spec:
                lvl, path = spec.split('=', 1)
                jars.append((int(lvl), path))
    apks = [a for a in argv if a.endswith('.apk')]
    # 🔴 --list-guarded：OK 时也把「被判为已守卫」的清单打出来
    #    理由（Shun 2026-10-23）：checker 判「全过」时，
    #    「真扫到并判有守卫」和「压根没扫到」**输出长得一样** → 无法对账。
    list_guarded = '--list-guarded' in argv

    if not apks or not jars:
        print(__doc__)
        return 2
    jars.sort()

    api = {}
    for lvl, path in jars:
        if not os.path.exists(path):
            print('[apk-api] FAIL android.jar 不存在: %s' % path)
            return 1
        m, c, sp, nf = load_jar_api_set(path)
        api[lvl] = (m, c, sp, nf)
        print('[apk-api] 已载入 API %d：%d 个类 / %d 个方法（%d 个 class 解析失败）'
              % (lvl, len(c), len(m), nf))

    min_api = jars[0][0]
    failed = 0

    for apk in apks:
        if not os.path.exists(apk):
            print('[apk-api] FAIL %s 不存在' % apk)
            failed += 1
            continue
        zf = zipfile.ZipFile(apk)

        class_refs = set()
        # 收集：调用点 + 守卫
        invocations = []       # [(caller_cls, caller_mname, caller_desc, cls, name, desc, guard)]
        # 🔴 调用图：被调方法 (cls, name, desc) → [(调用方类, 调用方方法, 调用方desc, 守卫)]
        #    用途：判「纯隔离类」—— 方法自身没有守卫，但**所有调用点都在守卫分支里**
        callers_of = {}
        typeref_guards = {}    # {类型名: [(caller, guard), ...]}
        for dn in [n for n in zf.namelist() if re.match(r'classes\d*\.dex$', n)]:
            try:
                dex = Dex(zf.read(dn))
            except Exception as e:
                print('[apk-api] WARN %s/%s 解析失败: %s' % (apk, dn, e))
                continue
            class_refs |= collect_class_refs(dex)
            for cname, mname, mdesc, d, insns_off, insns_size in all_code_named(dex):
                ms = scan_method(dex, d, insns_off, insns_size)
                for (idx, cls, mname2, desc) in ms.invocations:
                    g = ms.guard_of.get(idx)
                    invocations.append((cname, mname, mdesc, cls, mname2, desc, g))
                    # 🔴 建「谁调用了谁」调用图（为了判「纯隔离类」）
                    callers_of.setdefault((cls, mname2, desc), []).append(
                        (cname, mname, mdesc, g))
                for (idx, tname) in ms.typerefs:
                    typeref_guards.setdefault(tname, []).append(
                        (cname, mname, mdesc, ms.guard_of.get(idx)))

        # ── 类级检查 ──
        missing_classes = []
        guarded_classes = []
        for cls in sorted(class_refs):
            if class_available(api[min_api], cls):
                continue
            added = None
            for lvl, _ in jars:
                if class_available(api[lvl], cls):
                    added = lvl
                    break
            # 这个类的每一次类型引用都在「新 API 分支」里？
            #  —— 或者所在方法只从守卫分支被调用（纯隔离类形态）→ 安全
            refs = typeref_guards.get(cls, [])
            if refs and all(
                    (g is not None and g[1] == 'new')
                    or method_only_called_guarded((c, mn, md), callers_of)
                    for (c, mn, md, g) in refs):
                guarded_classes.append((cls, added, refs[0][3][0]))
            else:
                missing_classes.append((cls, added))

        # ── 方法级检查（沿继承链）──
        seen = set()
        suspects = []
        for (caller, cmname, cmdesc, cls, mname, desc, guard) in invocations:
            if not cls.startswith('Landroid/'):
                continue
            key = (cls, mname, desc, caller)
            if key in seen:
                continue
            seen.add(key)
            if method_available(api[min_api], cls, mname, desc):
                continue
            added = None
            for lvl, _ in jars:
                if method_available(api[lvl], cls, mname, desc):
                    added = lvl
                    break
            suspects.append((cls, mname, desc, caller, added, guard, cmname, cmdesc))

        # ── 分类：真 bug（未守卫 / 守卫在旧分支 / 阈值不够）vs 已被守卫 ──
        real = []
        guarded = []
        n_unguarded = 0
        n_threshold = 0
        n_oldbranch = 0
        for (cls, mname, desc, caller, added, guard, cmname, cmdesc) in suspects:
            if guard is None:
                # 🔴 兜底：**纯隔离类**形态 —— 方法体内没有守卫，但所有调用点都在守卫分支里
                #    （Android 官方推荐：版本特定的静态内部类，lint ClassVerificationFailure 强制）
                if method_only_called_guarded((caller, cmname, cmdesc), callers_of):
                    guarded.append((cls, mname, desc, caller, added,
                                    '✅ 纯隔离类形态：方法内无守卫，但所有调用点都在守卫分支里'
                                    '（老设备不会加载到这一层）'))
                else:
                    n_unguarded += 1
                    real.append((cls, mname, desc, caller, added,
                                 '❌ 无守卫 —— 同方法里找不到 SDK_INT 判断，'
                                 '且该方法存在**不受守卫**的调用点'
                                 '\n       💡 若源码里写了 `if (SDK_INT >= N)` 而 **N ≤ min-api**，'
                                 'd8 会**常量折叠**掉整个 if 分支 → 字节码里根本看不到那个判断'
                                 ' → 这里就会报「无守卫」而不是「阈值不够」。'
                                 '（看到这条先核对源码阈值：那种守卫本来就是无效的，d8 帮你删了。）'))
            elif guard[1] == 'old':
                n_oldbranch += 1
                real.append((cls, mname, desc, caller, added,
                             '❌ 落在**旧 API 分支**里（`if (SDK_INT < %d)` 的 else 侧）' % guard[0]))
            elif added is not None and guard[0] < added:
                n_threshold += 1
                real.append((cls, mname, desc, caller, added,
                             '❌ 守卫阈值不够：判断的是 SDK_INT >= %d，但这个方法要 API %d' % (guard[0], added)))
            else:
                guarded.append((cls, mname, desc, caller, added,
                                '✅ 被 `if (SDK_INT >= %d)` 的**新 API 分支**包着' % guard[0]))

        print('')
        # 🔴🔴 **扫描统计永远打印** —— 让「压根没扫到」变成**可见的**
        #    （Shun 的原话：全过和没扫到输出长得一样 → 无法对账）
        n_typeref = sum(len(v) for v in typeref_guards.values())
        # 🔴🔴 **结构化汇总行** —— 给脚本读的（自检/统计都靠它）
        #    🔴 **别用「子串 count」当判据**：提示文案里也会出现「无守卫」「阈值不够」这些词
        #       → count 会把**提示文案**也数进去（我踩过：实际 2/1，count 出来 5/3）
        #    ⇒ 这就是「判据要落在事实/结构上，不要落在表示/子串上」的又一例。
        print('[apk-api] SUMMARY ok=%s methods=%d classes=%d unguarded=%d threshold=%d oldbranch=%d'
              % ('1' if (not real and not missing_classes) else '0',
                 len(seen), len(class_refs), n_unguarded, n_threshold, n_oldbranch))
        print('[apk-api] 扫描统计：代码项 %d 个 / invoke 调用点 %d 处 / 类型引用 %d 处 '
              '/ 需检查的框架方法 %d 个 / 需检查的框架类 %d 个  (min-api %d)'
              % (len(all_code_named(dex)), len(invocations), n_typeref, len(seen),
                 len(class_refs), min_api))
        print('         ⚠️ 阈值 **≤ min-api** 的守卫会被 d8 **常量折叠**掉，'
              '字节码里根本不存在 → 不会出现在上面的统计里（NHK 2026-10-23 指出）')
        if not real and not missing_classes:
            print('[apk-api] OK   %s' % apk)
            print('         框架方法 %d 个 / 框架类 %d 个 —— 全部在 API %d 内' % (len(seen), len(class_refs), min_api))
            if list_guarded:
                print('   ── 已被版本守卫包住（安全，仅供参考）%d 条 ──' % (len(guarded) + len(guarded_classes)))
                for (cls, added, thr) in guarded_classes:
                    print('   %s' % cls)
                    print('       ☠️ 类缺失，API %s 才引入 —— 但引用都在 `if (SDK_INT >= %d)` 分支内 ✅' % (added, thr))
                for (cls, mname, desc, caller, added, why) in guarded:
                    print('   %s.%s %s' % (cls, mname, desc))
                    print('       ⚠️ API %s 才引入' % added)
                    print('       调用点: %s' % caller)
                    print('       %s' % why)
        else:
            failed += 1
            print('[apk-api] FAIL %s —— %d 个类缺失 + %d 个方法未守卫' % (apk, len(missing_classes), len(real)))
            for (cls, added) in missing_classes:
                if added is None:
                    why = '❗ 在 API %d 也不存在 —— 可能写错了/类名拼错' % jars[-1][0]
                else:
                    why = '☠️ **类缺失**，API %d 才引入 —— 类加载就炸，比方法缺失更致命' % added
                print('   %s' % cls)
                print('       %s' % why)
            for (cls, mname, desc, caller, added, why) in real:
                if added is None:
                    why2 = '❗ 在 API %d 也不存在 —— 可能写错了/类名拼错' % jars[-1][0]
                else:
                    why2 = '⚠️ API %d 才引入' % added
                print('   %s.%s %s' % (cls, mname, desc))
                print('       %s' % why2)
                print('       调用点: %s' % caller)
                print('       %s' % why)
            if guarded_classes:
                print('   ── 已被版本守卫包住（安全，仅供参考）%d 条 ──' % (len(guarded) + len(guarded_classes)))
                for (cls, added, thr) in guarded_classes:
                    print('   %s' % cls)
                    print('       ☠️ 类缺失，API %s 才引入 —— 但引用都在 `if (SDK_INT >= %d)` 分支内 ✅' % (added, thr))
            if guarded:
                print('   ── 已被版本守卫包住（安全，仅供参考）%d 条 ──' % len(guarded))
                for (cls, mname, desc, caller, added, why) in guarded:
                    print('   %s.%s %s' % (cls, mname, desc))
                    print('       ⚠️ API %s 才引入' % added)
                    print('       调用点: %s' % caller)
                    print('       %s' % why)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
