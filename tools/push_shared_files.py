#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_shared_files.py —— 往共享仓库 `tlv-creator/nhk-easy-news` **一次提交推多个文件**

🔴🔴 为什么不用 Contents API（逐文件 PUT）：
   每个 PUT 都会触发一次 GitHub Pages build ⇒ **新的 build 会取消上一个未完成的**
   ⇒ 连推几个文件 = 连续触发 build ⇒ 前面的 build 永远 built 不起来（站点不更新）。
   ⇒ 本脚本走 **Git Data API**（blob → tree → commit → update ref）⇒ **一次提交 = 一次 build**。

🔴🔴🔴 为什么有「比较后再交换」（compare-and-swap）：
   2026-10-24 我和 peer 都被这个坑咬过 —— **拿本地那份旧文件整份推上去，
   把对方刚推的修法整个覆盖没了**（按钮又变回超长链接 ⇒ 老师点下去直接 414）。
   ⇒ 根因：**「我改的文件」和「我基于的那个版本」是两份东西**，而整份 PUT 只管前者。
   ⇒ 修法：**先拉线上版当基底并记下它的指纹** ⇒ 推之前先比指纹：
      · 线上指纹 == 我记录的基底指纹 ⇒ 没人动过 ⇒ 放行
      · 线上指纹 != 基底指纹 ⇒ **别人改过** ⇒ **拒绝推**（让人先重拉、重新打补丁）
   ⇒ 用法：
       python3 tools/push_shared_files.py --fetch tools/foo.py > foo.py   # 拉取并记录基底指纹
       python3 tools/push_shared_files.py tools/foo.py=foo.py            # 推送（自动比对）
       python3 tools/push_shared_files.py --force tools/foo.py=foo.py    # 明知线上变了也要推
"""
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.environ.get('GH_REPO', 'tlv-creator/nhk-easy-news')
API = 'https://api.github.com/repos/' + REPO
BASEDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.base')


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


def sha256_text(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def get_remote_text(path):
    """取线上文件内容；不存在返回 None"""
    st, d = api('GET', '/contents/' + path + '?ref=main')
    if st != 200:
        return None
    return base64.b64decode(d['content']).decode('utf-8', 'replace')


def base_path(repo_path):
    return os.path.join(BASEDIR, repo_path.replace('/', '__') + '.sha256')


def record_base(repo_path, content):
    os.makedirs(BASEDIR, exist_ok=True)
    with open(base_path(repo_path), 'w') as f:
        f.write(sha256_text(content))


def check_base(repo_path):
    """返回 (状态, 说明)。状态：ok / missing / changed"""
    remote = get_remote_text(repo_path)
    if remote is None:
        return 'missing', '线上没有这个文件（新建）'
    bp = base_path(repo_path)
    if not os.path.exists(bp):
        return 'nobase', '⚠️ 没有基底指纹记录 ⇒ 可能是拿旧副本改的 ⇒ 强烈建议先 --fetch 再改'
    with open(bp) as f:
        want = f.read().strip()
    got = sha256_text(remote)
    if want == got:
        return 'ok', '基底一致（没人动过）'
    return 'changed', '🔴 线上已被别人改过（基底 %s… → 现在 %s…）' % (want[:8], got[:8])


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 1

    if args[0] == '--fetch':
        rp = args[1]
        txt = get_remote_text(rp)
        if txt is None:
            print('❌ 线上没有这个文件：%s' % rp); return 1
        record_base(rp, txt)
        sys.stdout.write(txt)
        print('\n[已记录基底指纹 %s]' % sha256_text(txt)[:16], file=sys.stderr)
        return 0

    force = '--force' in args
    args = [a for a in args if a != '--force']
    pairs = []
    for a in args:
        if '=' in a and not os.path.exists(a):
            rp, lp = a.split('=', 1)
        else:
            rp = lp = a
        pairs.append((rp, lp))

    # ── 0) 先做「比较后再交换」检查
    blocked = []
    for rp, lp in pairs:
        if not os.path.exists(lp):
            print('❌ 本地文件不存在：%s' % lp); return 1
        st, msg = check_base(rp)
        print('· 基底检查 %-42s %s' % (rp, msg))
        if st in ('changed', 'nobase') and not force:
            blocked.append((rp, msg))
    if blocked:
        print('\n❌ 有 %d 个文件的线上版本在你不注意的时候被改过 ⇒ 拒绝推送。' % len(blocked))
        for rp, msg in blocked:
            print('   · %s —— %s' % (rp, msg))
        print('\n⇒ 请先重新拉取线上版当基底：')
        print('   python3 tools/push_shared_files.py --fetch %s > 本地文件' % blocked[0][0])
        print('⇒ 重新打你的补丁 ⇒ 再推。（确认要覆盖请加 --force）')
        return 1

    # ── 1) 拿 main 的 HEAD
    st, ref = api('GET', '/git/ref/heads/main')
    if st != 200:
        print('❌ 拿 main ref 失败', st, ref); return 1
    head = ref['object']['sha']
    st, c = api('GET', '/git/commits/' + head)
    base_tree = c['tree']['sha']
    print('· main HEAD = %s' % head[:12])

    # ── 2) 每个文件建 blob
    entries = []
    for rp, lp in pairs:
        raw = open(lp, 'rb').read()
        st, b = api('POST', '/git/blobs', {'content': base64.b64encode(raw).decode('ascii'),
                                           'encoding': 'base64'})
        if st not in (200, 201):
            print('❌ 建 blob 失败', rp, st, b); return 1
        entries.append({'path': rp, 'mode': '100644', 'type': 'blob', 'sha': b['sha']})
        print('  · blob %-46s %8d B → %s' % (rp, len(raw), b['sha'][:10]))

    # ── 3) tree → commit → ref（一次提交 ⇒ 一次 Pages build）
    st, t = api('POST', '/git/trees', {'base_tree': base_tree, 'tree': entries})
    if st not in (200, 201):
        print('❌ 建 tree 失败', st, t); return 1
    st, cm = api('POST', '/git/commits', {'message': 'chore: ' + ', '.join(p for p, _ in pairs),
                                          'tree': t['sha'], 'parents': [head]})
    if st not in (200, 201):
        print('❌ 建 commit 失败', st, cm); return 1
    new = cm['sha']
    st, r = api('PATCH', '/git/refs/heads/main', {'sha': new, 'force': False})
    if st not in (200, 201):
        st, _ = api('GET', '/git/ref/heads/main')
        st, r = api('PATCH', '/git/refs/heads/main', {'sha': new, 'force': False})
        if st not in (200, 201):
            print('❌ 推 ref 失败', st, r); return 1
    print('✅ 已提交 %s（一次提交 ⇒ 只触发一次 Pages build）' % new[:12])

    # ── 4) 回读对账（推成功 ≠ 写还在）
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
        else:
            record_base(rp, got.decode('utf-8', 'replace'))
    st, hist = api('GET', '/commits?sha=main&per_page=5')
    if st == 200 and new[:12] not in [h['sha'][:12] for h in hist]:
        print('  ⚠️ 回读历史里没找到我的 commit（可能被别人的提交挤掉了）')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
