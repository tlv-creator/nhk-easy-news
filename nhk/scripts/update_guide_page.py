#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hub 永久引导页更新器 —— 推 GitHub Pages（`tlv-creator/nhk-easy-news` 的 `hub.html`）

🔴 为什么需要这个脚本：
   mcode 每次部署都换随机域名 ⇒ 直接发在对话里的链接**下次发版就死**。
   引导页是**唯一不变**的入口 —— 但**它里面的链接会过期** ⇒ 必须每次发版后更新。
   ⇒ 「更新引导页」这一步不能靠「顺手」（pony 2026-10-23 的判断，我同意）→ 必须脚本化 + 有断言。

🔴 为什么用 REST API 而不是 git / gh CLI：
   沙箱里**没有 gh CLI**，但**有 GH_TOKEN 环境变量** ⇒ 直接 PUT 就行。

用法：
    python3 scripts/update_guide_page.py --site <hub站根> --deploy <部署包站根> \
            --ver 1.15 --date 2026-10-23 --note "说明" [--push]

不带 --push 时只做本地改写 + 打印将要推送的内容（dry-run）。
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.request

OWNER = 'tlv-creator'
REPO = 'nhk-easy-news'
PATH = 'hub.html'
API = 'https://api.github.com/repos/%s/%s/contents/%s' % (OWNER, REPO, PATH)
HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(os.path.dirname(HERE), 'guide', 'hub.html')
GUIDE_URL = 'https://tlv-creator.github.io/nhk-easy-news/hub.html'
PROTECTED_JSON = 'https://tlv-creator.github.io/nhk-easy-news/protected-sites.json'


def api(method, url, token, payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'mavis-guide-updater')
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, data, timeout=45) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8') or '{}')


def bare(u):
    """🔴 归一化：带不带 https://、带不带结尾斜杠，都要能比。
    （pony 2026-10-23 踩过：两边格式不一致 ⇒ 永远不相等 ⇒ 连着失败两次）"""
    s = (u or '').strip().lower()
    s = re.sub(r'^https?://', '', s)
    return s.rstrip('/')


def replace_block(html, tag, new_body):
    """替换 <!-- TAG --> ... <!-- /TAG --> 之间的内容（版式不动）"""
    pat = re.compile(r'(<!--\s*%s\s*-->)(.*?)(<!--\s*/%s\s*-->)' % (tag, tag), re.S)
    m = pat.search(html)
    if not m:
        raise SystemExit('❌ 页面里找不到标记块 <!-- %s --> / <!-- /%s -->' % (tag, tag))
    return html[:m.start(2)] + new_body + html[m.end(2):]


# 🔴🔴🔴 **DEPLOY / SITE 块必须「按条目替换」，不能整块覆盖**
#   （hub pony 2026-10-23 踩到：我跑本脚本时，把整个 DEPLOY 块换成本地模板 ⇒
#    **他加的 ai-proxy-rollback.zip 按钮被抹掉了**）
#   ⇒ 整块替换 = 共享文件上的 lost-update（同 protected-sites.json 那次）
#   ⇒ 治本：**只动「文件名相同」的那一条**，块内 peer 新增的条目原样保留
DEPLOY_ITEMS = (
    ('ai-proxy-code.zip', '下载部署包（腾讯云函数用这个）'),
    ('ai-proxy-rollback.zip', '下载回滚包（传错了就用这个退回去）'),
    ('audio-config.zip', '下载音频配置包（可选）'),
)
SITE_ITEMS = (
    ('jpstudy-apk.zip', '下载 APK（zip，解压后安装）'),
)


def update_links_by_item(html, tag, bare_host, items):
    """**按条目**更新块内链接：只改文件名匹配的条目，块内其它条目原样保留。

    ⇒ 并发安全：peer 在块内加的条目不会被我抹掉 ✅
    ⇒ 缺条目时**按条目补插**（不是整块重写）✅
    """
    pat = re.compile(r'(<!--\s*%s\s*-->)(.*?)(<!--\s*/%s\s*-->)' % (tag, tag), re.S)
    m = pat.search(html)
    if not m:
        raise SystemExit('❌ 页面里找不到标记块 <!-- %s --> / <!-- /%s -->' % (tag, tag))
    body = m.group(2)
    names = [i[0] for i in items]

    def fix(mm):
        url = mm.group(1)
        fname = url.rsplit('/', 1)[-1]
        # 🔴 **所有链接都换 host**（否则旧域名残留 = 指向旧站 = 死链）
        #    但**条目本身保留**（peer 加的条目不删、不改文字）
        if fname in names:
            return 'href="https://%s/%s"' % (bare_host, fname)
        tail = url.split('://', 1)[1].split('/', 1)[1] if '/' in url.split('://', 1)[1] else ''
        return 'href="https://%s/%s"' % (bare_host, tail) if tail else mm.group(0)

    body = re.sub(r'href="(https?://[^"]+)"', fix, body)

    for fname, label in items:
        if fname not in body:
            body += ('\n<a class="btn2" href="https://%s/%s">%s</a>'
                     % (bare_host, fname, label))
    return html[:m.start(2)] + body + html[m.end(2):]



def ensure_rollback_link(html, deploy_bare):
    """保证部署块里有 rollback 的下载链接（幂等）"""
    if 'ai-proxy-rollback.zip' in html and 'href=' in html and re.search(
            r'href="[^"]*ai-proxy-rollback\.zip"', html):
        return html, False
    link = ('\n<a class="btn2" href="https://%s/ai-proxy-rollback.zip">'
            '下载回滚包（传错了就用这个退回去）</a>' % deploy_bare)
    # 插到 code.zip 那条链接后面
    m = re.search(r'<a class="btn2" href="https://[^"]*ai-proxy-code\.zip">[^<]*</a>', html)
    if not m:
        raise SystemExit('❌ 找不到 ai-proxy-code.zip 的链接块，插不进去')
    return html[:m.end()] + link + html[m.end():], True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--site', required=True, help='hub 站根，如 https://xxxx.space.mcode.cn')
    ap.add_argument('--deploy', required=True, help='部署包站根')
    ap.add_argument('--ver', required=True)
    ap.add_argument('--date', required=True)
    ap.add_argument('--note', default='')
    ap.add_argument('--push', action='store_true')
    a = ap.parse_args()

    # 🔴🔴🔴 **基底必须是「线上引导页」，不是本地模板**
    #   （hub pony 2026-10-23 踩到：我拿本地模板整块覆盖线上 ⇒ 他加的按钮被抹掉）
    #   ⇒ 本地模板只作**兜底**（线上拉不到时用）
    html = None
    try:
        import urllib.request
        req = urllib.request.Request(GUIDE_URL, headers={'User-Agent': 'mavis-guide-update'})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode('utf-8', 'replace')
        print('  ✅ 基底取自线上 %s' % GUIDE_URL)
    except Exception as e:
        print('  ⚠️ 线上拉不到（%s）⇒ 退回本地模板' % e)
    if html is None:
        if not os.path.exists(TPL):
            raise SystemExit('❌ 线上拉不到，且找不到本地模板 %s' % TPL)
        html = open(TPL, encoding='utf-8').read()
        # 🔴🔴🔴 **兜底模板必须被校验，否则拒绝写入**（hub pony 2026-10-23 给的更硬的解法）
        #   「兜底不是『有个差不多的就行』，是『万一出事时的最后一道正确』」
        #   ⇒ 兜底过期 = **最坏情况下把世界带回旧版**（引导页指回已删的站 = 直接 404）
        #   ⇒ 侥幸没中不算防线（pony 自己那份模板就因为上次跑脚本被「顺带」刷新了）
        try:
            import urllib.request as _u
            _req = _u.Request(PROTECTED_JSON, headers={'User-Agent': 'mavis-guide-guard'})
            with _u.urlopen(_req, timeout=25) as _r:
                _pl = json.loads(_r.read().decode('utf-8'))
        except Exception as _e:
            # 🔴 **校验清单本身拉不到 ⇒ 也要拒绝**（「查不到就不能放行」= exit 2 的延伸）
            raise SystemExit('❌ 线上拉不到，且**保护清单也拉不到**（%s）\n'
                             '   ⇒ 不许用未校验的兜底模板写入（查不到就不能放行）' % _e)
        _dead = [s2.get('domain') for s2 in (_pl.get('sites') or _pl.get('protected_sites') or [])
                 if (s2.get('status') or '').strip().upper() == 'DELETED' and s2.get('domain')]
        _bad = [d for d in _dead if d in html]
        if _bad:
            raise SystemExit('❌ 本地兜底模板里含有**已删站**：%s\n'
                             '   ⇒ 拒绝写入：兜底过期会把世界带回旧版（引导页指回死链）\n'
                             '   ⇒ 请先把 %s 刷新到最新再跑' % (' '.join(_bad), TPL))
        print('  ✅ 兜底模板已校验：不含任何已删站（检查了 %d 个 DELETED 域）' % len(_dead))

    site_b, dep_b = bare(a.site), bare(a.deploy)

    # ① SITE 块
    m = re.search(r'<!--\s*SITE\s*-->(.*?)<!--\s*/SITE\s*-->', html, re.S)
    if not m:
        raise SystemExit('❌ 找不到 SITE 块')
    html = update_links_by_item(html, 'SITE', site_b, SITE_ITEMS)

    # ② DEPLOY 块
    m = re.search(r'<!--\s*DEPLOY\s*-->(.*?)<!--\s*/DEPLOY\s*-->', html, re.S)
    if not m:
        raise SystemExit('❌ 找不到 DEPLOY 块')
    html = update_links_by_item(html, 'DEPLOY', dep_b, DEPLOY_ITEMS)

    # ③ VER 块
    m = re.search(r'<!--\s*VER\s*-->(.*?)<!--\s*/VER\s*-->', html, re.S)
    if not m:
        raise SystemExit('❌ 找不到 VER 块')
    body = m.group(1)
    body = re.sub(r'<b>v[\d.]+</b>', '<b>v%s</b>' % a.ver, body)
    body = re.sub(r'<b>\d{4}-\d{2}-\d{2}</b>', '<b>%s</b>' % a.date, body)
    if a.note:
        body = re.sub(r'<span>说明</span><b[^>]*>.*?</b>',
                      lambda mm: '<span>说明</span><b style="font-size:14px;text-align:right">%s</b>' % a.note,
                      body, flags=re.S)
    html = replace_block(html, 'VER', body)

    # ④ 唯一 build 标记
    # 🔴🔴 pony 2026-10-23 抓到的同族 bug（我原样也犯了）：
    #   「只插新的、不删旧的」⇒ 页面里**新旧两个标记并存**
    #   ⇒ 回读校验 `if marker in page` 会**匹配到那个旧的** ⇒ **「线上已生效」判不出来 = 假绿灯**。
    #   而且我原来的算法**把旧标记也算进哈希** ⇒ **每跑一次标记都变** ⇒ 永远「有更新」⇒ 也是假信号。
    # ⇒ 修法三件套：
    #   ① 先把**所有**旧标记清干净（我的格式 + peer 的 <!-- hub-build: xxx --> 格式）
    #   ② 对「**去掉标记后**的内容」算哈希 ⇒ **幂等**（内容没变，连跑两次标记不变）
    #   ③ 回读校验：标记必须**唯一**且等于新的
    html = re.sub(r'<!--\s*hub-build:[^>]*-->\s*', '', html)
    html = re.sub(r'<div class="row"><span>构建</span>.*?</div>\s*', '', html)
    build = hashlib.sha256(html.encode('utf-8')).hexdigest()[:12]
    html = re.sub(r'(<head[^>]*>)',
                  lambda m: m.group(1) + '\n<!-- hub-build: %s -->' % build, html, count=1)

    print('=== 本地改写完成 ===')
    print('  build 标记 :', build)
    print('  站根       :', site_b)
    print('  部署包站   :', dep_b)
    print('  版本/日期  : v%s / %s' % (a.ver, a.date))
    print('  部署块链接 :', '按条目更新（peer 的条目不抹）')

    # ---- 四项自检 ----
    errs = []
    # ① 新站根必须写进页面
    if site_b not in html:
        errs.append('新站根没写进页面')
    if dep_b not in html:
        errs.append('部署包站没写进页面')
    # ② 旧站地址一个都不许残留（残留 = 引导页指向死链 = 静默失败）
    # 🔴🔴 比较必须用**同一套归一**：这里取的是完整 host（含 .space.mcode.cn），
    #    而 site_b/dep_b 也是完整 host —— 早期版本我一边取裸名一边取全名 ⇒ 永远减不掉
    #    ⇒ 「残留旧站」永远报 ⇒ **自检本身成了假阳性**（pony 2026-10-23 同族：拿苹果比橘子）
    olds = set(re.findall(r'https://([a-z0-9]+\.space\.mcode\.cn)', html))
    olds -= {site_b, dep_b}
    if olds:
        errs.append('页面里还残留别的站地址：%s' % ', '.join(sorted(olds)))
    # ③ build 标记必须在
    if build not in html:
        errs.append('build 标记没写进去')
    # ④ 关键链接齐全
    for need in ('ai-proxy-code.zip', 'ai-proxy-rollback.zip', 'jpstudy-apk.zip'):
        if need not in html:
            errs.append('缺链接：%s' % need)
    if errs:
        for e in errs:
            print('  ❌', e)
        raise SystemExit('❌ 自检没过 —— 不推')
    print('  ✅ 四项自检全过')

    if not a.push:
        print('\n(dry-run：加 --push 才真正推)')
        return 0

    token = os.environ.get('GH_TOKEN')
    if not token:
        raise SystemExit('❌ 没有 GH_TOKEN 环境变量')

    # 取现有 sha（404 = 新建，要容忍）
    st, cur = api('GET', API, token)
    sha = cur.get('sha') if st == 200 else None
    if st not in (200, 404):
        raise SystemExit('❌ GET 失败 %s %s' % (st, cur.get('message')))

    payload = {
        'message': 'chore(hub): 更新引导页 v%s（站 %s）' % (a.ver, site_b),
        'content': base64.b64encode(html.encode('utf-8')).decode('ascii'),
    }
    if sha:
        payload['sha'] = sha
    st, res = api('PUT', API, token, payload)
    if st not in (200, 201):
        raise SystemExit('❌ PUT 失败 %s %s' % (st, res.get('message')))
    print('  ✅ 已推送（commit %s）' % (res.get('content', {}).get('sha', '?')[:12]))

    # 回读线上确认
    import time
    for i in range(8):
        time.sleep(6)
        try:
            # 🔴🔴 **不要加 query**：我加过 `?b=<build>` 想绕缓存，
            #    结果 Pages 对带 query 的请求命中了**另一条不刷新的路径** →
            #    连着 4 次回读都报「没更新」，而实际早已更新 ⇒ **我差点把成功当成失败**。
            #    ⇒ 回读**就裸 GET**，多试几次（Pages 生成要几十秒到几分钟）。
            with urllib.request.urlopen('https://tlv-creator.github.io/nhk-easy-news/hub.html',
                                        timeout=30) as r:
                live = r.read().decode('utf-8', 'replace')
            marks = re.findall(r'hub-build:\s*([a-f0-9]+)', live)
            if build in live and marks == [build]:
                print('  ✅ 线上 build 标记唯一且等于新的：%s' % build)
                break
            if build in live and marks != [build]:
                print('  🔴 线上有 %d 个标记 %s —— **旧标记没清掉**（假绿灯）' % (len(marks), marks))
            else:
                print('  … Pages 还没更新（第 %d 次）' % (i + 1))
        except Exception as e:
            print('  … 回读失败：%s' % e)
    else:
        raise SystemExit('❌ 线上没出现 build 标记 —— 可能没推成功')
    return 0


if __name__ == '__main__':
    sys.exit(main())
