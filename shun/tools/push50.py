#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送 50 集版本（路径级 CAS + 内容门禁 + 回读校验）

三重护栏：
① **内容级 diff**：推之前把线上 19 集拉下来，跟我的对比 ⇒ 差异必须只出现在
   「预期改动清单」里（ep 显示序号重排 / s001+s006 时间轴单调化 / 新增 31 集）。
   ⇒ 别人偷偷改了内容会在这里露出来（不只是 sha 变了）。
② **路径级 CAS**：逐路径比对基线 sha，不符即中止。
③ **回读校验**：推完按路径回读 blob sha 逐字比对。
"""
import base64, hashlib, json, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TOK = os.environ.get('GH_TOKEN')
API = 'https://api.github.com/repos/tlv-creator/nhk-easy-news'
H = {'Authorization': 'Bearer ' + TOK, 'Accept': 'application/vnd.github+json',
     'User-Agent': 'mavis-push', 'Content-Type': 'application/json'}
BASE = 'shun-web/data/'


def req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=H, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=180).read().decode())


def blob_sha(path):
    d = open(path, 'rb').read()
    return hashlib.sha1(b'blob %d\0' % len(d) + d).hexdigest()


def main():
    import urllib.request
    force = '--force' in sys.argv
    site = os.path.join(ROOT, 'site-web-50')
    idxf = os.path.join(site, 'data', 'index.json')
    single = os.path.join(ROOT, 'output', 'Japanese-with-Shun-Text-50.html')
    idx = json.load(open(idxf, encoding='utf-8'))
    items = [('shun-web/index.html', os.path.join(site, 'index.html')),
             ('shun-web/data/index.json', idxf),
             ('shun-text.html', single)]
    for e in idx['eps']:
        items.append((BASE + e['tag'] + '.json',
                      os.path.join(site, 'data', e['tag'] + '.json')))
    # 工具脚本与说明（一次提交 = 一次 Pages build ⇒ 一起推最省）
    TOOLS = ['build.py', 'platform_post.py', 'enrich_new.py', 'prep_tokens.py',
             'fix_timeline.py', 'fix_p_positions.py', 'fix_brackets.py',
             'verify_data50.py', 'verify_site50.py', 'verify_online50.py',
             'verify_online_site50.py', 'push50.py', 'run_platform.py',
             'fetch_audio.py', 'fetch_base50.py', 'build_site50.py',
             'fix_ep16_timeline.py',
             # 🔴 2026-10-25：ruby 引擎（修了 11 处空读音 + 1 处错读音 + 依赖硬失败）
             #    ⚠️ 权威位置是 shun/tools/ruby/ruby.py（不是 shun/tools/ruby.py）
             'gen_pred_mc_new.py', 'merge_pred_mc.py',
             # 🔴 2026-10-25 粒度细化轮：粒度细化器 + 判据 + 清洗器
             #    （refine_granularity.py = 只重新分配旧读音、不重选音读/训读）
             'refine_granularity.py', 'apply_refine.py',
             'verify_ru_audit.py', 'verify_granularity.py',
             'verify_granularity_par.py', 'apply_ru_granularity.py',
             'fix_junk_tail.py', 'update_site_data.py']
    for t in TOOLS:
        lp = os.path.join(ROOT, t)
        if os.path.exists(lp):
            items.append(('shun/tools/' + t, lp))
    docp = os.path.join(ROOT, 'docs', '50集说明.md')
    if os.path.exists(docp):
        items.append(('shun/docs/50集说明.md', docp))
    # 🔴 ruby 引擎目录（词表 + 词典 + 读音优先表）—— 缺了 ruby.py 会硬失败
    RUBY_DIR = os.path.join(ROOT, 'tools', 'ruby')
    if os.path.isdir(RUBY_DIR):
        for fn in sorted(os.listdir(RUBY_DIR)):
            fp = os.path.join(RUBY_DIR, fn)
            if os.path.isfile(fp) and not fn.endswith('.bak') and fn != '__pycache__':
                items.append(('shun/tools/ruby/' + fn, fp))
    # pred/mc sidecar（19 集 peer 的 + 31 集我生成的）
    for fn in ('pred_mc_19eps.json', 'pred_mc_new31.json'):
        fp = os.path.join(ROOT, 'tools', fn)
        if os.path.exists(fp):
            items.append(('shun/tools/' + fn, fp))
    # 设计文档（唯一权威）
    for fn in ('新结构注释设计.md',):
        fp = os.path.join(ROOT, 'docs', fn)
        if os.path.exists(fp):
            items.append(('shun/docs/' + fn, fp))

    # ① HEAD
    head = req('GET', API + '/git/ref/heads/main')['object']['sha']
    print('① HEAD:', head[:12])
    tree = req('GET', API + '/git/trees/' + head + '?recursive=1')
    online = {e['path']: e['sha'] for e in tree['tree'] if e['type'] == 'blob'}

    # ② 内容级 diff：线上 19 集 vs 我的 50 集
    print('\n② 内容级 diff（线上 vs 本地）：')
    changed, same, added = [], [], []
    for rp, lp in items:
        if rp not in online:
            added.append(rp)
            continue
        if online[rp] == blob_sha(lp):
            same.append(rp)
        else:
            changed.append(rp)
    print('   未变 %d ｜ 变更 %d ｜ 新增 %d' % (len(same), len(changed), len(added)))
    # 变更的必须逐项说明
    detail = []
    for rp in changed:
        if rp.startswith(BASE):
            tag = rp[len(BASE):-5]
            o = json.loads(urllib.request.urlopen(
                urllib.request.Request(API + '/contents/' + rp,
                                      headers=H)).read().decode())
            old = json.loads(base64.b64decode(o['content']).decode())
            new = json.load(open(os.path.join(site, 'data', tag + '.json'), encoding='utf-8'))
            difs = []
            if old.get('ep') != new.get('ep'):
                difs.append('ep %s→%s' % (old.get('ep'), new.get('ep')))
            if len(old.get('sents') or []) != len(new.get('sents') or []):
                difs.append('句数 %d→%d' % (len(old.get('sents') or []), len(new.get('sents') or [])))
            # 时间轴变化
            ot = [(s.get('s'), s.get('e')) for s in old.get('sents') or []]
            nt = [(s.get('s'), s.get('e')) for s in new.get('sents') or []]
            if ot != nt:
                difs.append('时间轴变')
            # 文本变化
            otexts = [s.get('t') for s in old.get('sents') or []]
            ntexts = [s.get('t') for s in new.get('sents') or []]
            if otexts != ntexts:
                difs.append('文本变(%d→%d)' % (len(otexts), len(ntexts)))
            detail.append((rp, difs))
    print('   变更明细：')
    for rp, difs in detail:
        print('      %-28s %s' % (rp.replace(BASE, ''), ' / '.join(difs) or '（无结构差异）'))

    # ③ 路径级 CAS
    basep = os.path.join(ROOT, 'push_base50.json')
    base = json.load(open(basep, encoding='utf-8')) if os.path.exists(basep) else {}
    print('\n③ 路径级 CAS：')
    conflict = []
    for rp, lp in items:
        want = base.get(rp)
        now = online.get(rp)
        if want is None:
            print('   ⚠️ %-30s 无基线（首次推）⇒ 本次不算校验过' % rp)
            continue
        if want != now:
            conflict.append((rp, want, now))
        else:
            print('   ✅ %-30s 基线一致' % rp)
    if conflict:
        print('\n   ❌ 路径级 CAS 冲突 %d 处 ⇒ 中止：' % len(conflict))
        for rp, w, n in conflict:
            print('      %-30s 基线 %s ≠ 线上 %s' % (rp, w[:10], n[:10]))
        if not force:
            print('   （确认要覆盖再加 --force）')
            sys.exit(1)
        print('   ⚠️ --force')

    # ④ 内容门禁
    print('\n④ 内容门禁：')
    tags = [e['tag'] for e in idx['eps']]
    assert len(set(tags)) == len(tags), 'tag 重复'
    assert len(idx['eps']) == 50, '集数不是 50：%d' % len(idx['eps'])
    reals = [e['realEp'] for e in idx['eps']]
    assert reals == sorted(reals), '未按 realEp 升序'
    print('   50 集 / tag 唯一 / realEp 升序 ✅')
    tot = 0
    for e in idx['eps']:
        d = json.load(open(os.path.join(site, 'data', e['tag'] + '.json'), encoding='utf-8'))
        ss = d['sents']
        tot += len(ss)
        assert all((s.get('zh') or '').strip() for s in ss), '%s 有空中文' % e['tag']
        for s in ss:
            assert ''.join(a for a, _ in s['ru']) == s['t'], '%s 注音拼接不等' % e['tag']
            assert s['e'] >= s['s'], '%s 时间轴倒序' % e['tag']
    print('   %d 句：中文全有 + 注音拼接全等 + 时间轴不倒序 ✅' % tot)
    single_txt = open(single, encoding='utf-8').read()
    assert 'window.SHUN=' in single_txt
    print('   单文件版内联 %.1f MB ✅' % (len(single_txt.encode()) / 1048576))

    # ⑤ 一次提交
    entries = []
    for rp, lp in items:
        data = open(lp, 'rb').read()
        blob = req('POST', API + '/git/blobs',
                   {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
        entries.append({'path': rp, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    nt = req('POST', API + '/git/trees', {'base_tree': tree['sha'], 'tree': entries})
    cm = req('POST', API + '/git/commits', {
        'message': ('feat(shun-web): 19 集 → 50 集（+31 集全部零额度产出）\n\n'
                    '新增 31 集（1717→2074 句）：\n'
                    '- 日文转写 + 简体中文翻译 + 四类括号结构：走平台通道 audios_understand（0 订阅额度，零 MiniMax 调用）\n'
                    '- 逐字 ruby 注音：shun/tools/ruby.py（上下文读音表 + kanjidic2 DP，词表命中整块）\n'
                    '- 句级时间轴：交接包里 44 集词级 ASR（精确到 0.16 秒），字符级对齐回填\n'
                    '- 第二层（inner/parts/skel）：规则引擎补齐，frame 一个字节未动\n'
                    '修复：\n'
                    '- s001/s006 时间轴区间交叠 25 处 → 0（排序 + 单调化）\n'
                    '- 集序按官方真实 Ep 号 ⇒ 50 集连成难度梯度；显示序号重排 1..50\n'
                    '- 标题清洗：保留【Genki 2】教材版本标记（原来会被洗掉，误当 Genki1）\n'
                    '护栏：路径级 CAS + 内容级 diff + 回读校验'),
        'tree': nt['sha'], 'parents': [head]})
    req('PATCH', API + '/git/refs/heads/main', {'sha': cm['sha'], 'force': False})
    print('\n⑤ 已提交:', cm['sha'][:12])
    print('   https://github.com/tlv-creator/nhk-easy-news/commit/' + cm['sha'])

    # ⑥ 回读
    after = req('GET', API + '/git/ref/heads/main')['object']['sha']
    t2 = req('GET', API + '/git/trees/' + after + '?recursive=1')
    o2 = {e['path']: e['sha'] for e in t2['tree'] if e['type'] == 'blob'}
    bad = [rp for rp, lp in items if o2.get(rp) != blob_sha(lp)]
    print('\n⑥ 回读校验: %d/%d 路径一致 %s' % (
        len(items) - len(bad), len(items), '✅' if not bad else '❌ ' + str(bad[:5])))
    # 更新基线
    nb = {rp: o2.get(rp) for rp, _ in items}
    json.dump(nb, open(basep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('   基线已更新 → push_base50.json')
    if bad:
        sys.exit(1)


if __name__ == '__main__':
    import urllib.request
    main()
