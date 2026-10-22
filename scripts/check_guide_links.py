#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删站前的闸门：**扫引导页里每一个链接**，有打不开的就拒绝删站。

🔴 为什么要有这个（peer 2026-10-23 的建议，我同意并立即做了）：
   「先部署新站 → 验 → 再删旧站」这条纪律，一直靠**记得**扫引用。
   ⇒ 挂载点太弱（「记忆」是最不可靠的挂载点）。
   ⇒ 把「扫引用」做成**一条命令** = 提到「闸门」级。

🔴 判据：**每个链接都必须能打开**（200/3xx）。
   —— 链接打不开 = 引导页指向死链 = 用户点进来一脸懵 = **静默失败**。

用法：
    python3 scripts/check_guide_links.py                    # 扫线上引导页
    python3 scripts/check_guide_links.py --file guide/hub.html   # 扫本地文件
"""
import argparse
import re
import sys
import urllib.request

DEFAULT_URL = 'https://tlv-creator.github.io/nhk-easy-news/hub.html'
LINK_RE = re.compile(r'(?:href|src)="(https?://[^"]+)"')


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': 'mavis-link-check'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', help='扫本地 html（默认扫线上）')
    ap.add_argument('--url', default=DEFAULT_URL)
    a = ap.parse_args()

    if a.file:
        html = open(a.file, encoding='utf-8').read()
        src = a.file
    else:
        try:
            st, raw = fetch(a.url)
        except Exception as e:
            print('❌ 读不到引导页 %s：%s' % (a.url, e))
            return 1
        html = raw.decode('utf-8', 'replace')
        src = a.url

    links = sorted(set(LINK_RE.findall(html)))
    if not links:
        print('❌ 页面里一个链接都没有 —— 是不是页面读错了？拒绝放行')
        return 1

    # 🔴🔴🔴 **「链都能打开」≠「该有的都在」**
    #   （hub pony 2026-10-23：引导页该有 5 个链接，线上只有 4 个 ⇒ 全部 200 ⇒ 我放行了 ❌）
    #   ⇒ 少一个必需条目 = 用户点不到那个功能 = **静默失败**（链没坏，功能没了）
    #   ⇒ 闸门必须同时验两件事：**① 每个现存链能打开 ② 每个必需条目都在**
    REQUIRED = ('jpstudy-apk.zip', 'ai-proxy-code.zip',
                'ai-proxy-rollback.zip', 'audio-config.zip')
    missing = [r for r in REQUIRED if not re.search(
        r'href="[^"]*%s"' % re.escape(r), html)]
    if missing:
        print('\n❌ 缺少必需链接 %d 个：%s' % (len(missing), ' '.join(missing)))
        print('   ⇒ **不许放行**（链都通 ≠ 功能齐全）')
        return 1
    print('  ✅ 必需链接齐全：%s' % ' '.join(REQUIRED))

    print('=== 扫 %s（%d 个链接）===' % (src, len(links)))
    bad = []
    for l in links:
        # 🔴 CDN 冷启动会 503 → 重试 3 次（别把冷启动当成死链）
        code = 0
        for i in range(3):
            try:
                req = urllib.request.Request(l, method='GET', headers={'User-Agent': 'mavis-link-check'})
                with urllib.request.urlopen(req, timeout=25) as r:
                    code = r.status
            except Exception as e:
                code = getattr(e, 'code', 0) or 0
            if code and code < 400:
                break
            import time
            time.sleep(2)
        ok = (code and code < 400)
        print('  %s %s  %s' % ('✅' if ok else '❌', code or 'ERR', l))
        if not ok:
            bad.append(l)

    if bad:
        print('\n❌ %d 个链接打不开 —— **不许删站**（引导页会指向死链）' % len(bad))
        for b in bad:
            print('   ', b)
        return 1
    print('\n✅ 全部链接可打开 —— 可以删站了')
    return 0


if __name__ == '__main__':
    sys.exit(main())
