#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mirror_css.py —— 从一个线上站把所有 css / html 拉下来（用于离线跑闸门）
零依赖。🔴 沙箱里测外网一律用 curl（urllib 解析到错 IP 的老问题）。
"""
import argparse
import os
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse


def curl(url, dest):
    for _ in range(3):
        r = subprocess.run(['curl', '-sSL', '--max-time', '40', '--retry', '2',
                            '-o', dest, url], capture_output=True)
        if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            return True
    return False


USAGE = '''用法: mirror_css.py <站点根URL> [输出目录] [--paths 子页1 子页2 ...]
   --paths  手动指定 JS 动态生成的子页面（爬虫顺着 <a href> 抓不到的那些）
   -h/--help 显示本说明
   例: python3 mirror_css.py https://xxx.space.mcode.cn /tmp/mirror --paths pony sakura'''


def main():
    # 🔴 原来用 argv 手搓解析 → `--help` 被当成路径 → 输出「跳过 --help/ CSS 文件 0 个」
    #    （NHK 反馈的；无害但会让人一头雾水）
    # 🔴 无参数 / -h / --help → 打印用法并 return 0（NHK 的 patch 行为：别让人对着报错一头雾水）
    _argv = sys.argv[1:]
    if not _argv or '-h' in _argv or '--help' in _argv:
        print(USAGE)
        return 0
    ap = argparse.ArgumentParser(
        description='把线上站的 css / html 拉下来（用于离线跑墨水屏闸门）')
    ap.add_argument('base', help='站点根 URL，例如 https://xxx.space.mcode.cn')
    ap.add_argument('out', nargs='?', default='/tmp/mirror', help='输出目录（默认 /tmp/mirror）')
    ap.add_argument('--paths', nargs='*', default=[],
                    help='额外要抓的子路径（子页面由 JS 动态生成时用），例如 --paths pony sakura')
    args = ap.parse_args()

    extra_paths = list(args.paths)
    base = args.base.rstrip('/')
    out = args.out
    os.makedirs(out, exist_ok=True)
    seen = set()
    queue = [base + '/']
    css_files = []
    html_files = []

    # 🔴 有些站的子页面是**JS 动态生成**的（hub 的总目录页就是）
    #    → 爬虫顺着 <a href> 抓不到 → 必须手动指定
    for p in extra_paths:
        p = p.strip('/')
        queue.append(base + '/' + p + '/')
        queue.append(base + '/' + p + '/index.html')

    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        p = urlparse(url)
        if p.netloc != urlparse(base).netloc:
            continue
        rel = url[len(base):].lstrip('/') or 'index.html'
        dest = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not curl(url, dest):
            print('  跳过 %s' % url)
            continue
        if rel.endswith('.css'):
            css_files.append(dest)
            continue
        if not (rel.endswith('.html') or rel.endswith('.htm') or rel == 'index.html'):
            continue
        html_files.append(dest)
        try:
            txt = open(dest, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        for m in re.finditer(r'(?:href|src)\s*=\s*["\']([^"\']+)["\']', txt):
            link = m.group(1)
            if link.startswith(('http://', 'https://', 'data:', '#', 'mailto:',
                                'javascript:')):
                continue
            if link.endswith(('.css', '.html', '.htm')):
                queue.append(urljoin(url, link))

    # 去重（同一文件可能被多条路径命中）
    css_files = sorted(set(css_files))
    html_files = sorted(set(html_files))

    print('CSS 文件 %d 个：' % len(css_files))
    for f in sorted(css_files):
        print('  %s  (%d B)' % (f, os.path.getsize(f)))
    print('HTML 文件 %d 个：' % len(html_files))
    for f in sorted(html_files):
        print('  %s  (%d B)' % (f, os.path.getsize(f)))
    print('\n---ALL---')
    for f in sorted(css_files) + sorted(html_files):
        print(f)


if __name__ == '__main__':
    main()
