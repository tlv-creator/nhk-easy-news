#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一次发版的站点目录同步到 GitHub Pages 永久地址（nhk-web/）。

为什么需要这个
--------------
mcode 站点每次 redeploy 都换域名 ⇒ 收藏不住。
GitHub Pages 上的 https://tlv-creator.github.io/nhk-easy-news/nhk-web/
是永久地址 ⇒ 同步脚本负责让它的内容跟着每天的新一期走。

它做什么
--------
1. 从 --from 目录取静态资源（**排除 media/**，音频 60MB 放不进 GitHub）
2. config.js 的 MEDIA_BASE 自动指向**当前现役 mcode 站**（从 version.json 读）
   —— 音频仍然从 mcode 站下载，Pages 只管界面/字幕/词库
3. 同步升 APP_VER（app.js）与 CACHE（sw.js）到同一个号
   —— 不升的话 Service Worker 会直接把旧字幕发给用户（用户看不到更新）
4. 升 index.html 里资源的 ?v= 查询串
5. GitHub Data API 一次提交 = 一次 Pages build（逐文件 PUT 会连续触发 build，
   新 build 取消上一个未完成的 ⇒ 站点永远 built 不起来）

用法
----
    python3 sync_pages.py --from /path/to/deploy_dir [--repo tlv-creator/nhk-easy-news]
                           [--dry-run]

判据（每一步都要有，没得可检就报错，不许静默跳过）
----------------------------------------------------
· --from 目录里必须有 index.html / app.js / sw.js
· 推完回读线上文件，确认新版本号真的在（不能只看 API 返回 ok）
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

# ── 固定参数 ────────────────────────────────────────────────────────────
REPO = 'tlv-creator/nhk-easy-news'
PAGES_SUB = 'nhk-web'
# 永久兜底站（域名冻结，永不 redeploy/删除）⇒ 音频的第二候选
FALLBACK_MEDIA = 'https://vkwwuh9wgkzo0.space.mcode.cn/'
# 不进 Pages 的东西：媒体大；APK 单独放（37KB，可以进）
EXCLUDE_DIRS = {'media'}
# 复制到 Pages 的文件
COPY_FILES = ['index.html', 'app.js', 'style.css', 'config.js', 'sw.js',
              'manifest.webmanifest', 'icon-192.png', 'icon-512.png', 'NHK.apk']
COPY_DIRS = ['data', 'subs']


def die(msg, code=1):
    print(f'  ❌ {msg}', file=sys.stderr)
    sys.exit(code)


def info(msg):
    print(f'  {msg}')


# ── GitHub API（走 curl，不用 urllib —— 沙箱里 urllib 连 api.github.com 会被网关打成 400）──
class GH:
    def __init__(self, token, repo):
        self.token = token
        self.api = f'https://api.github.com/repos/{repo}'
        self.tmpdir = '/workspace/.push_tmp'
        os.makedirs(self.tmpdir, exist_ok=True)

    def call(self, method, path, data=None, raw=False):
        cmd = ['curl', '-sS', '-m', '180', '-X', method, f'{self.api}{path}',
               '-H', f'Authorization: Bearer {self.token}',
               '-H', 'Content-Type: application/json']
        if data is not None:
            tmp = os.path.join(self.tmpdir, 'body.json')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            cmd += ['--data-binary', '@' + tmp]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        if raw:
            return out
        try:
            j = json.loads(out)
        except Exception:
            die(f'GitHub 返回的不是 JSON：{out[:200]}')
        if isinstance(j, dict) and j.get('message') and 'sha' not in j:
            die(f'GitHub {method} {path} 失败：{j.get("message")}')
        return j

    def head(self):
        return self.call('GET', '/git/ref/heads/main')['object']['sha']

    def base_tree(self, head):
        return self.call('GET', f'/git/commits/{head}')['tree']['sha']

    def remote_blob(self, path):
        try:
            return self.call('GET', f'/contents/{urllib.parse.quote(path)}')['sha']
        except SystemExit:
            return None

    def content(self, path):
        """取线上文件内容（bytes）"""
        import base64 as _b
        j = self.call('GET', f'/contents/{urllib.parse.quote(path)}')
        return _b.b64decode(j['content'])

    def put_blob(self, raw):
        b = self.call('POST', '/git/blobs',
                      {'content': base64.b64encode(raw).decode(), 'encoding': 'base64'})
        return b['sha']

    def push(self, entries, message):
        """entries: [{path, sha}]；基于当前 HEAD 建 tree → commit → 更新 ref"""
        head = self.head()
        tree = self.call('POST', '/git/trees',
                         {'base_tree': self.base_tree(head),
                          'tree': [{'path': e['path'], 'mode': '100644',
                                    'type': 'blob', 'sha': e['sha']} for e in entries]})
        commit = self.call('POST', '/git/commits',
                           {'message': message, 'tree': tree['sha'], 'parents': [head]})
        self.call('PATCH', '/git/refs/heads/main', {'sha': commit['sha'], 'force': False})
        return commit['sha']


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def read_version_site(gh):
    """从 GitHub 上的 version.json 读现役 mcode 站域名（音频源）"""
    out = gh.call('GET', f'/contents/version.json', raw=True)
    import base64 as _b
    j = json.loads(_b.b64decode(json.loads(out)['content']).decode())
    site = (j.get('site') or '').rstrip('/')
    if not site:
        die('version.json 里没有 site 字段 ⇒ 无法确定音频源地址')
    return site, j


def bump_version(src_dir, ver, online_ver=None):
    """把 APP_VER / CACHE / ?v= 统一升到 ver 号

    🔴 目标号 = max(线上现役号 + 1, 源目录里的号)
       —— 只按源目录的号 +1 是不够的：源目录的 APP_VER 可能没涨
       （例如源目录是别的 agent 更新的），那样线上号不变
       ⇒ Service Worker 直接返回旧字幕 ⇒ **用户看不到更新**。
    """
    ap = os.path.join(src_dir, 'app.js')
    s = open(ap, encoding='utf-8').read()
    m = re.search(r"var APP_VER = 'v(\d+)';", s)
    if not m:
        die('app.js 里找不到 var APP_VER ⇒ 无法升版本')
    old = int(m.group(1))
    if ver is None:
        ver = old + 1
        if online_ver is not None and online_ver >= ver:
            ver = online_ver + 1
    if ver <= old:
        ver = old + 1
    s = re.sub(r"var APP_VER = 'v\d+';", f"var APP_VER = 'v{ver}';", s)
    open(ap, 'w', encoding='utf-8').write(s)

    sp = os.path.join(src_dir, 'sw.js')
    sw = open(sp, encoding='utf-8').read()
    m2 = re.search(r"const CACHE = 'nhk-easynews-v(\d+)';", sw)
    if not m2:
        die('sw.js 里找不到 const CACHE ⇒ 无法升缓存版本')
    sw = re.sub(r"const CACHE = 'nhk-easynews-v\d+';", f"const CACHE = 'nhk-easynews-v{ver}';", sw)
    open(sp, 'w', encoding='utf-8').write(sw)

    ip = os.path.join(src_dir, 'index.html')
    h = open(ip, encoding='utf-8').read()
    # 所有 ?v=xx 统一换成 ?v=<新号>（data / app.js / config.js）
    h = re.sub(r'\?v=v\d+', f'?v=v{ver}', h)
    open(ip, 'w', encoding='utf-8').write(h)
    return ver, old


def set_media_base(src_dir, site, fallback=FALLBACK_MEDIA):
    """config.js 的 MEDIA_BASE 指向现役 mcode 站（+ 永久兜底站做第二候选）"""
    cp = os.path.join(src_dir, 'config.js')
    c = open(cp, encoding='utf-8').read()
    bases = [site + '/']
    if fallback and fallback.rstrip('/') != site.rstrip('/'):
        bases.append(fallback)
    block = ("window.CFG.MEDIA_BASE = [\n"
             + ",\n".join(f"  '{b}'" for b in bases)
             + "\n];")
    if 'window.CFG.MEDIA_BASE' in c:
        c = re.sub(r'window\.CFG\.MEDIA_BASE\s*=\s*\[[^\]]*\];', block, c, flags=re.S)
    else:
        c += '\n' + block + '\n'
    open(cp, 'w', encoding='utf-8').write(c)
    return bases


def collect(src_dir):
    """收集要推的文件 → {Pages 相对路径: 本地绝对路径}"""
    files = {}
    for f in COPY_FILES:
        p = os.path.join(src_dir, f)
        if os.path.isfile(p):
            files[f'{PAGES_SUB}/{f}'] = p
        else:
            info(f'  ⚠️  {f} 不在源目录里（跳过）')
    for d in COPY_DIRS:
        root = os.path.join(src_dir, d)
        if not os.path.isdir(root):
            die(f'源目录里没有 {d}/ ⇒ 同步不完整，不许推')
        for dp, dn, fn in os.walk(root):
            for f in fn:
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, root).replace('\\', '/')
                files[f'{PAGES_SUB}/{d}/{rel}'] = p
    for k in list(files):
        if any(f'/{ex}/' in k or k.endswith(f'/{ex}') for ex in EXCLUDE_DIRS):
            del files[k]
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', '--from', dest='src', required=True, help='发版后的站点目录（含 index.html/app.js/data/subs/…）')
    ap.add_argument('--repo', default=REPO)
    ap.add_argument('--ver', type=int, default=None, help='目标版本号（默认 = 现役+1）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        die(f'源目录不存在：{src}')
    for must in ('index.html', 'app.js', 'sw.js'):
        if not os.path.isfile(os.path.join(src, must)):
            die(f'源目录里缺 {must} ⇒ 同步会不完整，不许推')

    tok_path = '/workspace/.secrets/gh_token'
    if not os.path.isfile(tok_path):
        die(f'找不到 {tok_path}（token 绝不能进仓库）')
    token = open(tok_path).read().strip()
    gh = GH(token, args.repo)

    site, ver_json = read_version_site(gh)
    info(f'现役 mcode 站（音频源）：{site}')

    # 在临时副本上改，避免污染发版目录
    import shutil
    work = '/workspace/.pages_sync'
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns(*EXCLUDE_DIRS))

    online_ver = None
    try:
        live = gh.content(f'{PAGES_SUB}/app.js').decode('utf-8', 'replace')
        mo = re.search(r"var APP_VER = 'v(\d+)';", live)
        if mo:
            online_ver = int(mo.group(1))
    except SystemExit:
        online_ver = None
    info(f'线上 Pages 现役版本：v{online_ver}' if online_ver is not None else '线上 Pages 还没有版本号（首次）')
    ver, old = bump_version(work, args.ver, online_ver)
    bases = set_media_base(work, site)
    info(f'版本：v{old} → v{ver}（APP_VER 与 CACHE 同号）')
    info(f'MEDIA_BASE：{bases}')

    files = collect(work)
    info(f'待推 {len(files)} 个文件'
         f'（{sum(os.path.getsize(p) for p in files.values())/1048576:.1f} MB）')

    if args.dry_run:
        for k in sorted(files):
            print('   ', k)
        return

    entries = []
    for path, local in files.items():
        raw = open(local, 'rb').read()
        rsha = gh.remote_blob(path)
        lsha = blob_sha(raw)
        if rsha == lsha:
            continue          # 没变就不重推（省 blob 请求）
        entries.append({'path': path, 'sha': gh.put_blob(raw)})
    if not entries:
        info('线上已是最新，无需提交')
    else:
        sha = gh.push(entries, f'NHK Pages 同步：v{ver}（{len(entries)} 个文件）· 音频源 {site}')
        info(f'✅ 已提交 {sha[:12]}（{len(entries)} 个文件）⇒ 触发一次 Pages build')

    # 回读核对：不能只看 API 返回 ok
    time.sleep(8)
    raw = gh.call('GET', f'/contents/{PAGES_SUB}/app.js', raw=True)
    live = json.loads(raw)
    live_sha = live['sha']
    local_sha = blob_sha(open(os.path.join(work, 'app.js'), 'rb').read())
    if live_sha != local_sha:
        die(f'回读不一致：线上 {live_sha[:12]} ≠ 本地 {local_sha[:12]}（Pages build 还没完成，稍后再查）')
    info(f'✅ 回读一致 {live_sha[:12]}')


if __name__ == '__main__':
    main()
