#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兜底站闸门（check_fallback_site.py）的**自检** —— 正反两向。

🔴 为什么必须两个方向都有：
  「一个恒返回 ✅ 的空实现，也能通过只测正向的自检。」
  ⇒ 正向：真包 → 必须过
  ⇒ 反向 A：把 dex 里的域名换成非 mcode 的 → 必须拦
  ⇒ 反向 B：把域名换成 PLACEHOLDER → 必须拦
  ⇒ 反向 C：不给 APK → 必须拦（**「没东西可检」不许放行**）

做法：**不改源码，只改 dex 里的那 2 个字节**（跟 APK 工具的守卫自检同构）
—— 验的是真实产物的字节布局，不是我自己造的样本。

用法：
  python3 test_fallback_gate.py <某个真的 .apk>
"""
import os
import re
import shutil
import sys
import tempfile
import zipfile
import hashlib
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_fallback_site as gate  # noqa: E402

# 🔴 每个反向用例的**期望理由**（输出里必须出现这段话）——
#    「拦住了」不等于「理由对了」：判据改松了自检照样会绿。
REASONS = {
    '反向 A · 域名换成 fake.invalid（不再是 mcode 站）': '没有任何 mcode 站域名',
    '反向 B · 域名里塞 PLACEHOLDER': 'PLACEHOLDER',
    '反向 C · 不给 APK（没东西可检）': '没给 APK',
}


def patch_dex(src_apk, dst_apk, old, new):
    """把 dex 里的 old 字节串替换成 new（等长），重打包。

    🔴🔴 **必须重算 dex 头**（pony 2026-10-23 指出，我原来漏了）：
       dex 头布局：magic(0..8) | checksum(8..12) | signature(12..32) | …
       · signature = SHA-1(bytes[32:])
       · checksum = Adler-32(bytes[12:])
       ⇒ 改过字节不重算 → dex 头是坏的 → **真机 ART 会拒绝加载（装不上）**
       ⇒ **自检就等于在测一个「现实里不存在的东西」**。

    🔴 **必须确认改动真的生效**：偏移错 / 字符串没匹配上 → 照样返回一个「反例 APK」，
       而那个 APK 其实什么都没被改 → **反向用例是假的，却会「通过」**。
    """
    zin = zipfile.ZipFile(src_apk)
    items = [(i, zin.read(i.filename)) for i in zin.infolist()]
    zin.close()
    with zipfile.ZipFile(dst_apk, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info, data in items:
            if info.filename == 'classes.dex':
                before = data
                data = data.replace(old, new)
                if data == before:
                    raise AssertionError(
                        'patch_dex: 目标串没匹配上 —— **改坏没生效，反向用例就是假的**')
                b = bytearray(data)
                b[12:32] = hashlib.sha1(bytes(b[32:])).digest()
                b[8:12] = (zlib.adler32(bytes(b[12:])) & 0xffffffff).to_bytes(4, 'little')
                data = bytes(b)
            zout.writestr(info, data)


def dex_header_ok(apk):
    """验 APK 里 classes.dex 的头自洽（sha1 + adler32 都对）→ 反例 APK 是「真机上装得上的那种坏」"""
    dex = zipfile.ZipFile(apk).read('classes.dex')
    if dex[:4] != b'dex\n':
        return False, 'magic 不对'
    sig = dex[12:32]
    if sig != hashlib.sha1(bytes(dex[32:])).digest():
        return False, 'signature(SHA-1) 不自洽'
    chk = int.from_bytes(dex[8:12], 'little')
    if chk != (zlib.adler32(bytes(dex[12:])) & 0xffffffff):
        return False, 'checksum(Adler-32) 不自洽'
    return True, 'dex 头自洽'


def run(apk):
    return gate.main.__wrapped__ if False else _run(apk)


def _run(apk):
    """调 main() 但不让它读 sys.argv —— 直接调内部逻辑。"""
    import io
    import contextlib
    old = sys.argv
    sys.argv = ['check_fallback_site.py', apk]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = gate.main()
    finally:
        sys.argv = old
    return rc, buf.getvalue()


def main():
    argv = sys.argv[1:]
    apks = [a for a in argv if a.endswith('.apk')]
    if not apks:
        print(__doc__)
        return 2
    src = apks[0]
    print('[selftest] 样本: %s' % src)

    doms, ph, _dex = gate.scan(src)
    if not doms:
        print('[selftest] FAIL 样本里没有 mcode 站域名 —— 换一个带兜底站的 APK')
        return 1
    dom = doms[0]
    print('[selftest] 样本里的兜底站: %s' % dom)

    tmp = tempfile.mkdtemp(prefix='fbgate')
    cases = []

    # ① 正向
    cases.append(('正向 · 原样（有兜底站、无占位符）', src, 'pass'))

    # ② 反向 A：把 ".space.mcode.cn" 换成非 mcode 的 → 判「没有 mcode 域名」
    # ③ 反向 B：把 ".space.mcode.cn" 换成含 PLACEHOLDER 的 → 判「占位符混进产物」
    # 🔴 替换必须**等长**：dex 里字符串长度变了 → dex 结构坏掉 → 测的就不是那道闸门了
    OLD = b'.space.mcode.cn'
    assert len(OLD) == 15
    NEW_A = b'.fake.invalid00'     # 15 字节，非 mcode 域名
    NEW_B = b'PLACEHOLDERXXXX'     # 15 字节，含占位符
    assert len(NEW_A) == len(OLD) and len(NEW_B) == len(OLD)

    p_a = os.path.join(tmp, 'a-nodomain.apk')
    patch_dex(src, p_a, OLD, NEW_A)
    cases.append(('反向 A · 域名换成 fake.invalid（不再是 mcode 站）', p_a, 'fail'))

    p_b = os.path.join(tmp, 'b-placeholder.apk')
    patch_dex(src, p_b, OLD, NEW_B)
    cases.append(('反向 B · 域名里塞 PLACEHOLDER', p_b, 'fail'))

    # ④ 反向 C：不给 APK → 必须拦
    cases.append(('反向 C · 不给 APK（没东西可检）', None, 'fail'))

    # ⑤ 🔴🔴 反例 APK 的 dex 头必须自洽（pony 2026-10-23 指出）
    #    改了 dex 字节却不重算头 → dex 头是坏的 → **真机 ART 拒绝加载（装不上）**
    #    ⇒ 那个反例 APK 在现实里根本不存在 → 自检等于在测一个不存在的东西。
    for label, pth in (('反例A', p_a), ('反例B', p_b)):
        ok, why = dex_header_ok(pth)
        if ok:
            print('[selftest] PASS  正向 · %s 的 dex 头自洽（合法 dex，真机装得上）' % label)
        else:
            print('[selftest] ❌ %s 的 dex 头不自洽：%s' % (label, why))
            print('[selftest] ❌ 反例 APK 在真机上根本装不上 —— 这些反例是假的')
            return 1

    ok = True
    for name, path, want in cases:
        if path is None:
            rc, out = 0, ''
            import io
            import contextlib
            old_argv = sys.argv
            sys.argv = ['check_fallback_site.py']
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = gate.main()
            finally:
                sys.argv = old_argv
            out = buf.getvalue()
        else:
            rc, out = _run(path)
        good = (rc == 0) if want == 'pass' else (rc != 0)
        print('[selftest] %s  %s' % ('PASS' if good else 'FAIL', name))
        if not good:
            ok = False
            print('    → 期望 %s，实际 exit=%d' % (want, rc))
            print('    ' + out.strip().replace('\n', '\n    '))
        else:
            # 🔴🔴 **「拦住了」和「理由对」必须分开断言**（pony 2026-10-23）
            #   只断言「它拦了」是不够的：把判据改松（比如「只要不是期望域名就拦」），
            #   自检照样全绿，但闸门已经变松了。
            #   ⇒ 还要断言 **它说的理由正是那个理由**。
            reason = REASONS.get(name)
            if reason and reason not in out:
                ok = False
                print('    → ❌ 拦住了，但**理由不对**：期望输出里含 %r' % reason)
                print('    ' + out.strip().replace('\n', '\n    '))
            elif reason:
                print('           └ 理由也对：%r' % reason)
    shutil.rmtree(tmp, ignore_errors=True)

    if ok:
        print('[selftest] ✅ 正反两向都过 —— 这道闸门不是恒绿')
        return 0
    print('[selftest] ❌ 有反例没被拦住 —— 这道闸门不可信，不能拿去当闸门')
    return 1


if __name__ == '__main__':
    sys.exit(main())
