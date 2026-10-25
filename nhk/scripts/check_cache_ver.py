#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布闸门：app.js 的 APP_VER 与 sw.js 的 CACHE 名必须**同时**升版
   ⇒ 否则用户手机上的 Service Worker 会把旧文件直接返回（stale-while-revalidate）
   ⇒ 症状 = 「我改了但用户看不到」
用法：python3 check_cache_ver.py <app.js> <sw.js> <期望APP_VER>
"""
import re, sys
def main():
    app_p, sw_p = sys.argv[1], sys.argv[2]
    want = sys.argv[3] if len(sys.argv) > 3 else None
    app = open(app_p, encoding='utf-8').read()
    sw  = open(sw_p, encoding='utf-8').read()
    m = re.search(r"var APP_VER\s*=\s*'([^']+)'", app)
    n = re.search(r"const CACHE\s*=\s*'nhk-easynews-([^']+)'", sw)
    if n: cv = n.group(1)
    av, cv = (m.group(1) if m else None), (n.group(1).lstrip('v') if n else None)
    print(f'  APP_VER = {av}    CACHE = nhk-easynews-v{cv}')
    if not av or not cv:
        print('  ❌ 取不到版本号'); return 1
    if av.lstrip('v') != cv:
        print(f'  ❌ APP_VER({av}) 与 CACHE(v{cv}) 不同步 ⇒ 改了也发不到用户'); return 1
    if want and av != want:
        print(f'  ❌ APP_VER 应为 {want}，实际 {av}'); return 1
    print(f'  ✅ 两边同步（{av}）')
    return 0
if __name__ == '__main__':
    sys.exit(main())
