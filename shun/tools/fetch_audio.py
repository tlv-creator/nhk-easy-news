#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载官方 mp3（零额度）。幂等 + 自证：
  · 文件存在且 >100KB 且 magic 是 ID3/MPEG frame ⇒ 跳过
  · magic 判定用本文件内的 python（🔴 沙箱没有 xxd，且 bash 按字节增量读脚本 ⇒ 不要在 bash 里内嵌 python -c）
"""
import json, os, sys, urllib.request, time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'audio')
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
MAGIC_OK = (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2', b'\xff\xfa')


def good(path):
    try:
        if os.path.getsize(path) < 100000:
            return False
        head = open(path, 'rb').read(4)
    except OSError:
        return False
    return any(head.startswith(m) for m in MAGIC_OK)


def fetch(url, path, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if len(data) < 100000:
                raise IOError('too small %d' % len(data))
            with open(path, 'wb') as f:
                f.write(data)
            if not good(path):
                raise IOError('magic mismatch %r' % open(path, 'rb').read(4))
            return len(data)
        except Exception as e:
            sys.stderr.write('   retry %d/%d %s: %s\n' % (i + 1, tries, os.path.basename(path), e))
            time.sleep(2 + 3 * i)
    try:
        os.remove(path)
    except OSError:
        pass
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = json.load(open(os.path.join(ROOT, 'new-episodes.json'), encoding='utf-8'))
    ok = skip = bad = 0
    for r in rows:
        tag = r['tag']
        path = os.path.join(OUT, tag + '.mp3')
        if good(path):
            skip += 1
            continue
        n = fetch(r['url'], path)
        if n:
            ok += 1
            print('OK   %-7s %5.1f MB' % (tag, n / 1048576), flush=True)
        else:
            bad += 1
            print('FAIL %-7s' % tag, flush=True)
    print('=== 下载完成: ok=%d skip=%d bad=%d ===' % (ok, skip, bad), flush=True)
    # 🔴 判据落在计数上（记忆里的坑：ffmpeg/curl 在循环里偷吃 stdin ⇒ 「没处理」和「跳过」日志一样）
    if ok + skip != len(rows):
        print('🔴 数量对不上：清单 %d 集，实到 %d 集' % (len(rows), ok + skip))
        sys.exit(1)


if __name__ == '__main__':
    main()
