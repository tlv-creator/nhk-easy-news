#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补本地生成的两层（零额度）：
  ① 注音 ru   —— ruby.py（上下文读音表 + kanjidic2 DP，逐字 ruby）
  ② 第二层   —— analyze.py 规则引擎在**平台给的 frame 范围内**补
                 blocks[].inner（块内骨架）/ parts（词级拆解）+ skel/skelMean

🔴 frame 一字不动（平台的语义分组已经过闸门）⇒ 只补「块内」信息。
🔴 KANJIDIC2 必须指向 workspace 里的文件（/tmp 会被沙箱回收）。
"""
import json, os, sys, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'ruby'))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'xval'))
os.environ.setdefault('KANJIDIC2', os.path.join(ROOT, 'tools', 'ruby', 'kanjidic2.xml.gz'))

import ruby                                   # noqa: E402
from analyze import analyze_sentence         # noqa: E402
from frame import inner_of                   # noqa: E402
from gen_blocks import parts_for, make_skel  # noqa: E402


def enrich(tag, tokens_path):
    d = json.load(open(os.path.join(ROOT, 'data-new', tag + '.json'), encoding='utf-8'))
    toks = json.load(open(tokens_path, encoding='utf-8'))
    assert len(toks) == len(d['sents']), (tag, len(toks), len(d['sents']))
    st = collections.Counter()
    for s, rec in zip(d['sents'], toks):
        # ① 注音
        try:
            s['ru'] = [[t['t'], t['r']] for t in ruby.annotate(s['t'])]
        except Exception:
            s['ru'] = []
            st['ru_fail'] += 1
        # ② 第二层
        gs = s.get('gs') or {}
        fr = gs.get('frame') or []
        if fr:
            try:
                a = analyze_sentence(rec)
            except Exception:
                a = {'nodes': [0, len(s['t']), 0, 0, 0], 'c': [], '_toks': []}
            blocks = []
            for b in fr:
                p = b.get('p')
                if not (p and len(p) == 2):
                    continue
                st_, en_ = p
                try:
                    inner = inner_of(a, s['t'], st_, en_)
                except Exception:
                    inner = ''
                parts = parts_for(rec['toks'], st_, en_)
                blocks.append({'s': b['s'], 'inner': inner, 'parts': parts, 'mean': ''})
                st['blocks'] += 1
                if inner and ('【' in inner or '[' in inner):
                    st['inner_labelled'] += 1
                if len(parts) >= 2:
                    st['parts_multi'] += 1
            sk, skm = make_skel(fr)
            gs['blocks'] = blocks
            gs['skel'] = sk
            gs['skelMean'] = skm
            gs['chain'] = [f['b'][0] + f['s'] + f['b'][-1] for f in fr]
            gs['whole'] = s.get('zh', '')
            s['gs'] = gs
            st['sents'] += 1
    json.dump(d, open(os.path.join(ROOT, 'data-new', tag + '.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    return st


def main():
    rows = json.load(open(os.path.join(ROOT, 'new-episodes.json'), encoding='utf-8'))
    tokdir = os.path.join(ROOT, 'tokens-new')
    allst = collections.Counter()
    for r in rows:
        tp = os.path.join(tokdir, r['tag'] + '.json')
        if not os.path.exists(tp):
            print('%-8s 跳过（无 tokens）' % r['tag'])
            continue
        st = enrich(r['tag'], tp)
        allst.update(st)
        line = '  '.join('%s=%s' % (k, v) for k, v in sorted(st.items()))
        print('%-8s %s' % (r['tag'], line), flush=True)
    print('== 合计 ==', dict(allst))
    if allst['blocks']:
        print("  inner 有标注率 %.1f%% ｜ parts≥2 的块 %.1f%%" % (
            allst['inner_labelled'] / allst['blocks'] * 100,
            allst['parts_multi'] / allst['blocks'] * 100))


if __name__ == '__main__':
    main()
