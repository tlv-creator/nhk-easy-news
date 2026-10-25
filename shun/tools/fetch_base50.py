#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拉线上基线 + 内容级 diff（只读，不推）
用途：① 建 push_base50.json 的 sha 基线
      ② 确认线上 19 集与我本地 data-merged-50.json 的差异**只在预期清单内**
"""
import base64, json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TOK = os.environ.get('GH_TOKEN')
API = 'https://api.github.com/repos/tlv-creator/nhk-easy-news'
H = {'Authorization': 'Bearer ' + TOK, 'Accept': 'application/vnd.github+json',
     'User-Agent': 'mavis-push'}


def get(path):
    r = urllib.request.Request(API + '/contents/' + path, headers=H)
    return json.loads(urllib.request.urlopen(r, timeout=120).read().decode())


def fetch(rel):
    o = get(rel)
    return base64.b64decode(o['content']).decode(), o['sha']


def main():
    mine = json.load(open(os.path.join(ROOT, 'data-merged-50.json'), encoding='utf-8'))
    bytag = {e['tag']: e for e in mine['eps']}
    base = {}
    print('拉线上基线…')
    for rel in ['shun-web/index.html', 'shun-web/data/index.json', 'shun-text.html']:
        try:
            _, sha = fetch(rel)
            base[rel] = sha
            print('  %-28s %s' % (rel, sha[:10]))
        except Exception as e:
            print('  %-28s 取不到: %s' % (rel, e))
    oidx, sha = fetch('shun-web/data/index.json')
    base['shun-web/data/index.json'] = sha
    oidx = json.loads(oidx)
    print('\n线上 index: %d 集' % len(oidx['eps']))
    otags = [e['tag'] for e in oidx['eps']]
    diffs = []
    for e in oidx['eps']:
        rel = 'shun-web/data/%s.json' % e['tag']
        try:
            txt, sha = fetch(rel)
        except Exception as ex:
            print('  %-28s 取不到: %s' % (rel, ex))
            continue
        base[rel] = sha
        old = json.loads(txt)
        new = bytag.get(e['tag'])
        if not new:
            continue
        d = []
        if old.get('ep') != new.get('ep'):
            d.append('ep %s→%s' % (old.get('ep'), new.get('ep')))
        oS, nS = old.get('sents') or [], new.get('sents') or []
        if len(oS) != len(nS):
            d.append('句数 %d→%d' % (len(oS), len(nS)))
        ot = [s.get('t') for s in oS]
        nt = [s.get('t') for s in nS]
        if ot != nt:
            n = sum(1 for a, b in zip(ot, nt) if a != b)
            d.append('文本变 %d 句' % n)
        oa = [(s.get('s'), s.get('e')) for s in oS]
        na = [(s.get('s'), s.get('e')) for s in nS]
        if oa != na:
            d.append('时间轴变')
        oz = [s.get('zh') for s in oS]
        nz = [s.get('zh') for s in nS]
        if oz != nz:
            d.append('中文变')
        diffs.append((e['tag'], d))
    print('\n线上 19 集 vs 本地差异：')
    for tag, d in diffs:
        print('  %-8s %s' % (tag, ' / '.join(d) if d else '完全一致'))
    newt = [t for t in bytag if t not in otags]
    print('\n新增集（线上没有）: %d 集 -> %s' % (len(newt), ' '.join(sorted(newt)[:8]) + ' …'))
    json.dump(base, open(os.path.join(ROOT, 'push_base50.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n基线已写 push_base50.json（%d 路径）' % len(base))


if __name__ == '__main__':
    main()
