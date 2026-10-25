#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_apk_check_api.py —— apk_check_api.py 的**自检**（正反两向对照）
=========================================================================
🔴🔴 **为什么必须两个方向都有**

我原来的自检只有「已知**有**守卫的样本 → 必须判 ✅」这一侧。
**一个恒返回 ✅ 的空实现，也能通过这种自检。**

Shun 补的那条更狠：
> **「已知没守卫的样本必须扫不到」** —— 两个方向都要有，
> 否则工具可能恒绿而你不知道。

⇒ 本文件的做法：**拿真 APK 改字节**，造出「无守卫」和「阈值不够」两种反例。

做法（不改源码，只改 dex 里的 2 个字节）：
  ① 正向：hub v1.8 原样（源码里 `if (SDK_INT >= O)` 守着 NotificationChannel）→ 必须判 OK
  ② 反向 A：把那条 `if-lt` 改成 `nop` → 新 API 变成**无守卫** → 必须判 ❌ 无守卫
  ③ 反向 B：把阈值 `26` 改成 `21` → 守卫**阈值不够**（判断 >=21 但要 API 26）→ 必须判 ❌ 阈值不够

用法：
    python3 test_apk_check_api.py <hub18.apk> --jars 24=... --jars 26=... --jars 29=... --jars 34=...

退出码：0 = 自检全过；1 = 有（说明工具本身不可信，不能拿去当闸门）
"""
import os
import re
import shutil
import struct
import hashlib
import zlib
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from apk_check_formats import Dex, _u16, _u32  # noqa: E402
from apk_check_api import OP_LEN, scan_method  # noqa: E402

# 22t 的 if-lt：第一个码字 B|A|op，B 在 bits 12-15，A 在 bits 8-11
OP_IFLT = 0x34
OP_NOP = 0x00
OP_CONST16 = 0x13


def find_guards(dex):
    """找出 APK 里**所有** SDK_INT 版本守卫 → [{if_off, const_off, threshold, cls}]

    🔴 **必须找全部，不能只找一个**（pony 2026-10-22 指出）：
       真实 APK（hub v1.10）里有 **8 个以上**守卫（Api26.ensureChannel / Api26.builder /
       ensureStarted / PlaybackService…）→ 自检的反向用例如果只拆**一个**，
       期望「全部变成无守卫」就**必然落空**（拆一个只是少了一层保护，还有 7 层）。
       ⇒ 反向 A 改成「**把所有守卫都拆掉**」。

    找法与 checker 同款：**按指令顺序单遍推进**（寄存器会被重新赋值）。
    """
    guards = []
    # 🔴 用 all_code_named（带方法名）—— 定向用例要能只拆「某一个方法」的守卫
    try:
        from apk_check_api import all_code_named
        _items = all_code_named(dex)
    except Exception:
        _items = [(c, '<m>', '()V', d, o, s) for (c, d, o, s) in dex.all_code()]
    for cname, mname, mdesc, d, insns_off, insns_size in _items:
        sdk_regs = set()
        const_regs = {}     # reg -> (value, byte offset of the value)
        i = 0
        while i < insns_size:
            unit = _u16(d, insns_off + i * 2)
            op = unit & 0xff
            length = OP_LEN.get(op, 1)
            if op in (0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66):   # sget family
                try:
                    _c, nm, _t = dex.field_info(_u16(d, insns_off + (i + 1) * 2))
                except Exception:
                    nm = ''
                if nm == 'SDK_INT':
                    sdk_regs.add((unit >> 8) & 0xff)
            elif op == 0x13:      # const/16
                const_regs[(unit >> 8) & 0xff] = (_u16(d, insns_off + (i + 1) * 2),
                                                  insns_off + (i + 1) * 2)
            elif op == 0x14:      # const
                const_regs[(unit >> 8) & 0xff] = (_u32(d, insns_off + (i + 1) * 2),
                                                  insns_off + (i + 1) * 2)
            elif op == 0x12:      # const/4
                reg = (unit >> 8) & 0x0f
                v = (unit >> 12) & 0x0f
                if v & 0x08:
                    v -= 16
                const_regs[reg] = (v, insns_off + (i + 1) * 2)
            elif op in (0x34, 0x35, 0x36, 0x37, 0x32, 0x33):   # if-*
                reg_a = (unit >> 8) & 0x0f
                reg_b = (unit >> 12) & 0x0f
                if reg_a in sdk_regs and reg_b in const_regs:
                    val, coff = const_regs[reg_b]
                    guards.append({
                        'if_off': insns_off + i * 2,
                        'const_off': coff,
                        'threshold': val,
                        'cls': cname,
                        'mname': mname,
                        # 🔴 记下是 **哪种** 分支指令：
                        #    0x34 = if-lt（`if (SDK_INT >= N) {…}` 的产物）
                        #    0x35 = if-ge（`if (SDK_INT < N) return null;` 的产物）
                        #    两种都是 22t 格式、语义相反 → 都要认
                        'op': op,
                    })
            i += length
    return guards


def _recompute_dex_header(data):
    """重算 dex 头的 signature(sha1) 与 checksum(adler32) —— 改过字节必须重算

    🔴 dex 头布局：magic(0..8) | checksum(8..12) | signature(12..32) | ...
       · signature = SHA-1 of bytes[32:]
       · checksum = Adler-32 of bytes[12:]
    🔴 **不重算的话，这个 APK 是「非法 dex」** —— 真机 ART 会拒绝加载（装不上），
       自检就等于在测一个「现实里不存在的东西」。
    """
    data[12:32] = hashlib.sha1(bytes(data[32:])).digest()
    data[8:12] = (zlib.adler32(bytes(data[12:])) & 0xffffffff).to_bytes(4, 'little')


def dex_header_ok(dex_bytes):
    """dex 头自洽性检查 → (ok, 说明)"""
    d = bytes(dex_bytes)
    want_sha1 = hashlib.sha1(d[32:]).digest()
    want_adler = (zlib.adler32(d[12:]) & 0xffffffff).to_bytes(4, 'little')
    if d[12:32] != want_sha1:
        return False, 'signature(sha1) 不符'
    if d[8:12] != want_adler:
        return False, 'checksum(adler32) 不符'
    return True, 'dex 头自洽 ✅'


def patch_apk(src_apk, dst_apk, guard_offs=(), threshold_offs=(), new_threshold=21):
    """复制 APK 并（可选）改 dex 里的守卫字节

    🔴🔴 两条自检纪律（NHK 2026-10-23 提的，我当晚就自查补上）：
    ① **改完必须断言「确实改动了」** —— 「反向验证失败」的第一反应不该是「断言没用」，
       而是「我的『改坏』到底生效了吗」。⇒ 逐个偏移比对 before/after，**没变就报错**。
    ② **改完必须重算 dex 头**（sha1 + adler32）—— 否则产出的 APK 是「非法 dex」，
       真机装不上 ⇒ 自检测的不是现实里存在的东西。
    """
    zin = zipfile.ZipFile(src_apk)
    data = bytearray(zin.read('classes.dex'))
    changed = []
    for go in guard_offs:
        before = bytes(data[go:go + 4])
        # if-lt/if-ge  →  nop nop（2 个码字）
        struct.pack_into('<H', data, go, OP_NOP)
        struct.pack_into('<H', data, go + 2, 0)
        after = bytes(data[go:go + 4])
        if before == after:
            raise AssertionError('patch_apk: 偏移 0x%x **没被改动**（before==after）'
                                 ' —— 改坏没生效，反向用例就是假的' % go)
        changed.append(('guard', go, before.hex(), after.hex()))
    for co in threshold_offs:
        before = bytes(data[co:co + 2])
        struct.pack_into('<H', data, co, new_threshold)
        after = bytes(data[co:co + 2])
        if before == after:
            raise AssertionError('patch_apk: 阈值偏移 0x%x **没被改动**（before==after）'
                                 ' —— 改坏没生效，反向用例就是假的' % co)
        changed.append(('threshold', co, before.hex(), after.hex()))
    if changed:
        _recompute_dex_header(data)
    with zipfile.ZipFile(dst_apk, 'w', zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            payload = bytes(data) if it.filename == 'classes.dex' else zin.read(it.filename)
            zout.writestr(it, payload)
    zin.close()
    return changed


def run_checker(apk, jars):
    cmd = [sys.executable, os.path.join(HERE, 'apk_check_api.py'), apk]
    for lvl, path in jars:
        cmd += ['--jars', '%d=%s' % (lvl, path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    argv = sys.argv[1:]
    jars = []
    for i, a in enumerate(argv):
        if a == '--jars' and i + 1 < len(argv):
            lvl, path = argv[i + 1].split('=', 1)
            jars.append((int(lvl), path))
    apks = [a for a in argv if a.endswith('.apk')]
    if not jars:
        print(__doc__)
        return 2
    # 🔴 没给 APK → 用**内置的最小样本** guard_sample.apk（1005 B）
    #    （里面同时含 if-lt / if-ge 两种守卫写法 + 一个无守卫的阴性对照）
    if apks:
        src = apks[0]
    else:
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'guard_sample.apk')
        if not os.path.exists(src):
            print(__doc__)
            return 2
        print('[selftest] 未指定 APK → 用内置最小样本 %s' % os.path.basename(src))

    zf = zipfile.ZipFile(src)
    dex = Dex(zf.read('classes.dex'))
    guards = find_guards(dex)
    if not guards:
        print('[selftest] FAIL 样本 %s 里找不到 SDK_INT 守卫 —— 换一个带版本守卫的 APK' % src)
        return 1
    print('[selftest] 样本 %s：找到 **%d 个** SDK_INT 守卫（阈值 %s）'
          % (src, len(guards), sorted(set(g['threshold'] for g in guards))))
    for g in guards[:12]:
        print('           · %-46s 阈值 %s' % (g['cls'].split('/')[-1].rstrip(';'), g['threshold']))

    tmp = tempfile.mkdtemp(prefix='einkselftest')
    cases = []
    # ① 正向
    p1 = os.path.join(tmp, 'a-orig.apk')
    shutil.copy(src, p1)
    cases.append(('正向 · 原样（有守卫）', p1, 'OK'))
    # ② 反向 A：**拆掉全部守卫**（不是只拆一个 —— 样本可能有很多个）
    p2 = os.path.join(tmp, 'b-noguard.apk')
    patch_apk(src, p2, guard_offs=[g['if_off'] for g in guards])
    cases.append(('反向 A · 拆掉全部 %d 个守卫' % len(guards), p2, '无守卫'))
    # ③ 反向 B：**全部阈值** 26 → 21
    p3 = os.path.join(tmp, 'c-threshold.apk')
    patch_apk(src, p3, threshold_offs=[g['const_off'] for g in guards], new_threshold=21)
    cases.append(('反向 B · 全部阈值 → 21', p3, '阈值不够'))

    # ④ 🔴🔴 **双形式守卫**专项（v6 新增，Shun 2026-10-23 提出）
    #    `if (SDK_INT >= O) {…}` → javac 产出 **if-lt**（跳 else 侧）
    #    `if (SDK_INT < O) return null;` → javac 产出 **if-ge**（跳 else 侧）
    #    两种语义相反、格式都是 22t → **两种都必须认**
    #    验证方式：把其中一种单独拆掉 → 必须报「无守卫」
    if os.path.basename(src) == 'guard_sample.apk':
        lt = [g for g in guards if g.get('op') == 0x34]
        ge = [g for g in guards if g.get('op') == 0x35]
        print('[selftest] 样本里守卫分布：if-lt %d 个 / if-ge %d 个' % (len(lt), len(ge)))
        if not lt or not ge:
            print('[selftest] FAIL 样本里没有同时出现 if-lt 与 if-ge 两种守卫 —— 换样本')
            return 1
        # 只拆 if-ge 那一类 → GuardIfGe 的引用必须变成「无守卫」
        p_ge = os.path.join(tmp, 'd-only-ifge.apk')
        patch_apk(src, p_ge, guard_offs=[g['if_off'] for g in ge])
        cases.append(('反向 C · 只拆 **if-ge** 形式守卫（`if (SDK_INT < N) return null`）',
                      p_ge, '无守卫'))
        # 只拆 if-lt 那一类 → 那几处也必须变成「无守卫」
        p_lt = os.path.join(tmp, 'e-only-iflt.apk')
        patch_apk(src, p_lt, guard_offs=[g['if_off'] for g in lt])
        cases.append(('反向 D · 只拆 **if-lt** 形式守卫（`if (SDK_INT >= N) {…}`）',
                      p_lt, '无守卫'))

    # ⑤ 🔴🔴 **纯隔离类**定向用例（v7 新增）
    #    形态：方法体内**没有** SDK_INT 守卫（整个类只在新系统上加载），
    #          但**所有调用点**都在守卫分支里 → 安全（Android 官方推荐形态）
    #    正向：GuardIsoSafe 必须判安全
    #    反向 E：**只拆掉调用点的守卫** → GuardIsoSafe 必须变不安全
    if os.path.basename(src) == 'guard_sample.apk':
        caller_g = [g for g in guards
                    if g.get('cls', '').endswith('GuardCallerSafe;')
                    and g.get('mname') == 'go']
        print('[selftest] 纯隔离类用例：调用点守卫 %d 个（GuardCallerSafe.go）' % len(caller_g))
        if not caller_g:
            print('[selftest] FAIL 样本里找不到 GuardCallerSafe.go 的守卫 —— 换样本')
            return 1
        p_iso = os.path.join(tmp, 'f-iso-noguard.apk')
        patch_apk(src, p_iso, guard_offs=[g['if_off'] for g in caller_g])
        cases.append(('反向 E · 拆掉**调用点**的守卫（纯隔离类必须变不安全）',
                      p_iso, '无守卫'))
        # 正向：GuardLowThreshold 必须报「**阈值不够**」（自带守卫但版本不够 —— 最阴的一种错）
        _rc, _out = run_checker(src, jars)
        if 'GuardLowThreshold' not in _out:
            print('[selftest] FAIL 样本里找不到 GuardLowThreshold 的判定')
            return 1
        _i = _out.find('GuardLowThreshold')
        _seg = _out[_i:(_i + 300)]
        if '阈值不够' not in _seg:
            print('[selftest] FAIL GuardLowThreshold 没被判成「阈值不够」—— 判据没生效')
            return 1
        print('[selftest] PASS  正向 · GuardLowThreshold 判为「守卫阈值不够」')

        # 正向：GuardIsoSafe 必须被判为「纯隔离类形态」而不是「无守卫」
        _rc, _out = run_checker(src, jars)
        _bad = '❌'
        _idx = _out.find('GuardIsoSafe')
        _seg = _out[_idx:(_idx + 300)] if _idx >= 0 else ''
        if _idx < 0 or _bad in _seg:
            print('[selftest] FAIL GuardIsoSafe 没被判成「纯隔离类形态」—— 判据没生效')
            return 1
        print('[selftest] PASS  正向 · 纯隔离类 GuardIsoSafe 判为安全')

    # ⑥ 🔴 反例 APK 必须是**合法 dex**（dex 头自洽）——
    #    否则真机 ART 拒绝加载（装不上）→ 自检就等于在测一个「现实里不存在的东西」
    _cands = [c[1] for c in cases if c[1].endswith('.apk')]
    if _cands:
        import zipfile as _zf
        _bad = []
        for _p in _cands:
            try:
                _ok, _why = dex_header_ok(_zf.ZipFile(_p).read('classes.dex'))
            except Exception as e:
                _ok, _why = False, str(e)
            if not _ok:
                _bad.append('%s: %s' % (os.path.basename(_p), _why))
        if _bad:
            for _b in _bad:
                print('[selftest] FAIL 反例 APK 的 dex 头不自洽 → %s' % _b)
            return 1
        print('[selftest] PASS  正向 · %d 个反例 APK 的 dex 头全部自洽（合法 dex）' % len(_cands))

    failed = 0
    # 🔴 正向用例的基线：**原样**跑一次，记下它报了几条「无守卫」
    #    （样本里可能有**故意留的阴性对照**，那不是误报，是设计）
    #    → 正向 = 「不多不少仍报这么多条」；多报（假阳性）/ 少报（漏判）都算 FAIL
    _rc0, _out0 = run_checker(src, jars)
    # 🔴🔴 **读结构化 SUMMARY 行**，不要用「子串 count」
    #    （提示文案里也含「无守卫」「阈值不够」→ count 会把提示也算进去；
    #      我踩过：实际 2/1，count 出来 5/3 → 基线失真）
    def _summary(out, key, default=-1):
        m = re.search(r'\[apk-api\] SUMMARY .*?\b%s=(-?\d+)' % key, out)
        return int(m.group(1)) if m else default
    base_bad = _summary(_out0, 'unguarded')
    base_thr = _summary(_out0, 'threshold')
    if base_bad < 0 or base_thr < 0:
        print('[selftest] FAIL checker 输出里没有 SUMMARY 行 —— 判据读不到')
        return 1
    print('[selftest] 原样基线：%d 条无守卫 / %d 条阈值不够（读自 SUMMARY 行）' % (base_bad, base_thr))

    for (name, path, expect) in cases:
        rc, out = run_checker(path, jars)
        if expect == 'OK':
            # 🔴 同样读 SUMMARY 行，别用子串 count（提示文案会污染）
            ok = (_summary(out, 'unguarded') == base_bad) and (_summary(out, 'threshold') == base_thr)
            if base_bad == 0 and base_thr == 0:
                ok = ok and (rc == 0)
        else:
            ok = (expect in out) and (rc != 0)
        print('[selftest] %s  %s' % ('PASS' if ok else 'FAIL', name))
        if not ok:
            failed += 1
            print('    ---- 实际输出 ----')
            for ln in out.splitlines():
                print('    ' + ln)
        else:
            for ln in out.splitlines():
                if expect in ln or (expect == 'OK' and 'OK ' in ln):
                    print('    ' + ln.strip())
    shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print('')
        print('[selftest] ❌ 自检失败 —— 工具本身不可信，不能拿去当闸门！')
        return 1
    print('')
    print('[selftest] ✅ 正反两向都过 —— 工具被验证过了（不是恒绿）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
