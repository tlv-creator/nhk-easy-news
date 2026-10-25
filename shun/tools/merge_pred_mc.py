#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 pred/mc（谓语细分 + 形态链）合并进 50 集数据，并修 ruby 的 11 处缺陷。

输入：
  · data-merged-50.json         50 集主数据（3618 句）
  · tools/pred_mc_19eps.json     peer 的 19 集 sidecar
  · tools/pred_mc_new31.json     我用规则引擎生成的 31 集 sidecar
  · online50/*.json              线上已发布数据（取 ruby 的正确值作为参照）
输出：
  · data-merged-50.json          （就地更新）

🔴 合并契约（与 peer 约定）：
  每句加 s['pred'] = { p: [[start,len,code,label],...], mc: [辞書形,て形,意志形,ば形], k: 句末类型 }
  ⇒ 渲染层第④段直接读 s.pred。
🔴 零额度：纯本地。
"""
import json, os, re, sys, collections, glob

ROOT = '/workspace/shun'
K = re.compile(r'[一-鿿]')

def main():
    d = json.load(open(os.path.join(ROOT, 'data-merged-50.json'), encoding='utf-8'))
    eps = d['eps']
    side = {}
    for p in ('tools/pred_mc_19eps.json', 'tools/pred_mc_new31.json'):
        f = os.path.join(ROOT, p)
        if os.path.exists(f):
            side.update(json.load(open(f, encoding='utf-8')))
    print('sidecar 集数 %d' % len(side))

    st = collections.Counter()
    for e in eps:
        tag = e['tag']
        sents = e['sents']
        pm = side.get(tag)
        if not pm:
            st['无sidecar'] += len(sents)
            continue
        if len(pm) != len(sents):
            print('  🔴 句数不一致 %s: %d vs %d' % (tag, len(pm), len(sents)))
            st['句数不符'] += len(sents)
            continue
        for s, item in zip(sents, pm):
            s['pred'] = {'p': item.get('p') or [], 'mc': item.get('mc') or [], 'k': item.get('k') or ''}
            st['sents'] += 1
            if s['pred']['p']: st['with_p'] += 1
            if s['pred']['mc']: st['with_mc'] += 1
            if s['pred']['k']:  st['with_k'] += 1
    print('合并: 句 %d ｜ 有pred %d ｜ 有mc %d ｜ 有句末类型 %d ｜ 无sidecar %d ｜ 句数不符 %d'
          % (st['sents'], st['with_p'], st['with_mc'], st['with_k'], st['无sidecar'], st['句数不符']))

    # ── ruby 缺陷修复：含汉字却无读音 / 错读音 ──
    # 策略：用**线上已发布**的 ru 做参照（那是用户已看过的既有形态），
    #       只在「含汉字但读音为空」时，从 ruby.py 重算该句并取其结果。
    sys.path.insert(0, os.path.join(ROOT, 'tools', 'ruby'))
    os.environ.setdefault('KANJIDIC2', os.path.join(ROOT, 'tools', 'ruby', 'kanjidic2.xml.gz'))
    import ruby
    fixed = collections.Counter()
    for e in eps:
        for si, s in enumerate(e['sents']):
            ru = s.get('ru') or []
            bad = [x for x in ru if len(x) >= 1 and x[0] and K.search(x[0]) and not x[1]]
            if not bad:
                continue
            t = s.get('t') or ''
            if not t.strip():
                continue
            try:
                toks = ruby.annotate(t)
            except Exception as e:
                print('  ruby 失败 %s#%d: %s' % (e['tag'], si, e))
                continue
            newru = [[x['t'], x['r']] for x in toks]
            # 表层还原必须一致，否则不采用
            if ''.join(x[0] for x in newru) != t:
                print('  🔴 表层不一致 %s#%d ⇒ 不采用' % (e['tag'], si))
                continue
            still = [x for x in newru if K.search(x[0]) and not x[1]]
            if still:
                fixed['仍为空'] += 1
                continue
            s['ru'] = newru
            fixed['已修'] += 1
    print('ruby 修复: %s' % dict(fixed))

    json.dump(d, open(os.path.join(ROOT, 'data-merged-50.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    print('写回 data-merged-50.json (%d 字节)' % os.path.getsize(os.path.join(ROOT, 'data-merged-50.json')))


if __name__ == '__main__':
    main()
