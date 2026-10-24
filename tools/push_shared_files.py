#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_shared_files.py —— 往共享仓库 `tlv-creator/nhk-easy-news` **一次提交推多个文件**

🔴 为什么不用 Contents API（逐文件 PUT）：
   每个 PUT 都会触发一次 GitHub Pages build ⇒ **新的 build 会取消上一个未完成的**
   ⇒ 连推几个文件 = 连续触发 build ⇒ 前面的 build 永远 built 不起来（站点不更新）。
   ⇒ 本脚本走 **Git Data API**（blob → tree → commit → update ref）⇒ **一次提交 = 一次 build**。

用法：
   python3 tools/push_shared_files.py path1 path2 ...        # 推本地文件到仓库同名路径
   python3 tools/push_shared_files.py --repo-path tools/x.py=本地路径.py
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.environ.get('GH_REPO', 'tlv-creator/nhk-easy-news')
API = 'https://api.github.com/repos/' + REPO


def api(method, path, payload=None):
    tok = os.environ.get('GH_TOKEN')
    if not tok:
        print('❌ 没有 GH_TOKEN'); sys.exit(1)
    h = {'Authorization': 'Bearer ' + tok, 'Accept': 'application/vnd.github+json',
         'User-Agent': 'mavis-push'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(API + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        return e.code, {'error': e.read().decode('utf-8', 'replace')[:400]}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 1
    pairs = []
    for a in args:
        if '=' in a and not os.path.exists(a):
            rp, lp = a.split('=', 1)
            pairs.append((rp, lp))
        else:
            pairs.append((a, a))

    # 1) 拿 main 的 HEAD
    st, ref = api('GET', '/git/ref/heads/main')
    if st != 200:
        print('❌ 拿 main ref 失败', st, ref); return 1
    head = ref['object']['sha']
    st, c = api('GET', '/git/commits/' + head)
    base_tree = c['tree']['sha']
    print('· main HEAD = %s' % head[:12])

    # 2) 每个文件建 blob
    entries = []
    for rp, lp in pairs:
        if not os.path.exists(lp):
            print('❌ 本地文件不存在:', lp); return 1
        raw = open(lp, 'rb').read()
        st, b = api('POST', '/git/blobs', {'content': base64.b64encode(raw).decode('ascii'),
                                           'encoding': 'base64'})
        if st not in (200, 201):
            print('❌ 建 blob 失败', rp, st, b); return 1
        entries.append({'path': rp, 'mode': '100644', 'type': 'blob', 'sha': b['sha']})
        print('  · blob %-46s %8d B → %s' % (rp, len(raw), b['sha'][:10]))

    # 3) 建 tree（基于 main 的 tree，只替换这几个路径）
    st, t = api('POST', '/git/trees', {'base_tree': base_tree, 'tree': entries})
    if st not in (200, 201):
        print('❌ 建 tree 失败', st, t); return 1
    tree_sha = t['sha']

    # 4) commit
    msg = 'chore: %s' % ', '.join(p for p, _ in pairs)
    st, cm = api('POST', '/git/commits', {'message': msg, 'tree': tree_sha, 'parents': [head]})
    if st not in (200, 201):
        print('❌ 建 commit 失败', st, cm); return 1
    new = cm['sha']

    # 5) 推进 ref（并发保护：失败了重取 main 再试一次）
    st, r = api('PATCH', '/git/refs/heads/main', {'sha': new, 'force': False})
    if st not in (200, 201):
        st, ref2 = api('GET', '/git/ref/heads/main')
        st2, r = api('PATCH', '/git/refs/heads/main', {'sha': new, 'force': False})
        if st2 not in (200, 201):
            print('❌ 推 ref 失败', st2, r); return 1
    print('✅ 已提交 %s（一次提交 ⇒ 只触发一次 Pages build）' % new[:12])

    # 6) 回读对账（推成功 ≠ 写还在）
    bad = 0
    for rp, lp in pairs:
        st, c2 = api('GET', '/contents/' + rp + '?ref=main')
        if st != 200:
            print('  ❌ 回读失败', rp, st); bad += 1; continue
        got = base64.b64decode(c2['content'])
        want = open(lp, 'rb').read()
        same = (got == want)
        print('  %s 回读 %-46s %d B' % ('✅' if same else '❌', rp, len(got)))
        if not same:
            bad += 1
    # 7) 确认我的 commit 在远程历史里
    st, hist = api('GET', '/commits?sha=main&per_page=5')
    if st == 200 and new[:12] not in [h['sha'][:12] for h in hist]:
        print('  ⚠️ 回读历史里没找到我的 commit（可能被别人的提交挤掉了）')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
