#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用新 ruby.py 重算 13 集注音，与线上已发布 ru 逐句对拍。"""
import json, os, sys, glob, collections
sys.path.insert(0, '/workspace/shun/tools/ruby')
os.environ['KANJIDIC2'] = '/workspace/shun/tools/ruby/kanjidic2.xml.gz'
import ruby

same_t = same_r = tot = 0
pat = collections.Counter(); ex = []
for f in sorted(glob.glob('/workspace/shun/site-web-19/data/ep*.json')):
    d = json.load(open(f, encoding='utf-8'))
    for si, s in enumerate(d['sents']):
        t = s.get('t', ''); on = s.get('ru') or []
        if not t.strip() or not on:
            continue
        try:
            toks = ruby.annotate(t)
        except Exception as e:
            pat['异常:%s' % type(e).__name__] += 1
            continue
        new = [(x['t'], x.get('r') or '') for x in toks]
        tot += 1
        if [a for a, _ in new] == [a for a, _ in on]:
            same_t += 1
        else:
            i = 0
            while i < min(len(new), len(on)) and new[i][0] == on[i][0]:
                i += 1
            if len(new[i][0]) > 1 and len(on[i][0]) == 1:
                pat['新整块/线上逐字'] += 1
            elif len(on[i][0]) > 1 and len(new[i][0]) == 1:
                pat['线上整块/新逐字'] += 1
            else:
                pat['切分点不同'] += 1
            if len(ex) < 8:
                ex.append((f.split('/')[-1][:-5] + '#%d' % si, t[:26],
                           '|'.join(a for a, _ in on[i:i + 2]),
                           '|'.join(a for a, _ in new[i:i + 2])))
        if ''.join(r for _, r in new) == ''.join(r for _, r in on if r):
            same_r += 1
    print('  ...%s' % f.split('/')[-1], flush=True)
print('\n  样本 %d 句' % tot)
print('  表层完全一致 %d (%.1f%%) ｜ 读音字符流一致 %d (%.1f%%)'
      % (same_t, same_t * 100 / max(1, tot), same_r, same_r * 100 / max(1, tot)))
print('  差异分类:', dict(pat))
for x in ex:
    print('   %-12s %-28s 线上 %-18s 新 %s' % x)
