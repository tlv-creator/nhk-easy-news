#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 kuromoji（node）给新集分词 → tokens-new/<tag>.json
（analyze.py 的词性体系是 kuromoji 的，janome 不兼容 ⇒ 必须走 node）
"""
import json, os, subprocess, sys, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
XVAL = os.path.join(ROOT, 'tools', 'xval')
OUT = os.path.join(ROOT, 'tokens-new')
os.makedirs(OUT, exist_ok=True)


def main():
    rows = json.load(open(os.path.join(ROOT, 'new-episodes.json'), encoding='utf-8'))
    eps = []
    for r in rows:
        p = os.path.join(ROOT, 'data-new', r['tag'] + '.json')
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        eps.append({'tag': r['tag'], 'sents': [{'t': s['t'], 'zh': s.get('zh', '')}
                                               for s in d['sents']]})
    inp = os.path.join(OUT, '_in.json')
    json.dump({'eps': eps}, open(inp, 'w', encoding='utf-8'), ensure_ascii=False)
    print('输入 %d 集 / %d 句' % (len(eps), sum(len(e['sents']) for e in eps)), flush=True)
    allf = os.path.join(OUT, '_all.json')
    env = dict(os.environ)
    env['NODE_PATH'] = os.path.join(XVAL, 'node_modules')
    p = subprocess.run(['node', os.path.join(XVAL, 'extract_generic.js'), inp, allf],
                       capture_output=True, text=True, env=env, cwd=XVAL)
    print(p.stdout.strip())
    if p.returncode != 0:
        print(p.stderr[:800])
        sys.exit(1)
    recs = json.load(open(allf, encoding='utf-8'))
    by = collections.defaultdict(list)
    for r in recs:
        by[r['tag']].append(r)
    for tag, rs in by.items():
        json.dump(rs, open(os.path.join(OUT, tag + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
    print('写出 %d 个 tag 的 tokens' % len(by))


if __name__ == '__main__':
    main()
