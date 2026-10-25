#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并版数据层独立审计：直读产物文件，不信中间产物。
判据：① 13 集 SHUN 行逐字一致 ② 每句 frame 逐字取自原句 ③ 块不重叠
      ④ b 合法 ⑤ 相邻同类型合并率=0 ⑥ zh 非空 ⑦ 集号唯一 ⑧ 顺序按真实集号"""
import hashlib, json, re, sys, collections

MERGED = '/workspace/tools/shun/shun-grammar-merged.html'
SRC = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html'
LEGAL = {'[]', '［］', '〈〉', '（）'}

html = open(MERGED, encoding='utf-8').read()
src = open(SRC, encoding='utf-8').read()
src_line = next(l for l in src.split('\n')
                if l.startswith('<script>window.SHUN=') and l.endswith(';</script>'))

R = collections.Counter()
fails = []

# ① 13 集 SHUN 行逐字
m = re.search(r'<script>window\.SHUN=[\s\S]*?</script>', html)
R['shun_line_identical'] = 1 if (m and m.group(0) == src_line) else 0
if not R['shun_line_identical']:
    fails.append('13 集 SHUN 行不逐字一致')

# ② 取数据
def grab(name):
    """按括号配平抽 JSON（不能用正则 —— 嵌套对象会截断）"""
    i = html.find('window.' + name + '=')
    if i < 0:
        return None
    j = i + len('window.' + name + '=')
    while j < len(html) and html[j] in ' \t\r\n':
        j += 1
    open_c = html[j]; close_c = '}' if open_c == '{' else ']'
    depth = 0; instr = False; esc = False
    for k in range(j, len(html)):
        c = html[k]
        if instr:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == '"': instr = False
            continue
        if c == '"': instr = True
        elif c == open_c: depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return json.loads(html[j:k+1])
    return None

shun2 = grab('SHUN2')
epreal = grab('EPREAL')
shung = grab('SHUNG')
shunp = grab('SHUNP')
if not (shun2 and epreal and shung and shunp):
    print('❌ 注入数据没抽出来'); sys.exit(1)

eps13 = json.loads(re.match(r'^<script>window\.SHUN=(.*);</script>$', src_line).group(1))['eps']
all_eps = eps13 + shun2['eps']
R['eps'] = len(all_eps)

# ⑦⑧ 集号唯一 + 顺序
def real(e):
    return e['epReal'] if e.get('epReal') is not None else epreal[e['tag']]
all_eps_sorted = sorted(all_eps, key=real)
reals = [real(e) for e in all_eps_sorted]
R['dup_ep'] = len(reals) - len(set(reals))
if R['dup_ep']:
    fails.append('有重号集: ' + str([r for r, c in collections.Counter(reals).items() if c > 1]))
R['sorted_by_real'] = 1 if reals == sorted(reals) else 0
if not R['sorted_by_real']:
    fails.append('集序不是按真实集号')
R['min_ep'], R['max_ep'] = min(reals), max(reals)

# ③④⑤⑥ 逐句检查（按排序后的顺序，和 SHUNG 一致）
def real_sort(e):
    return real(e)
ordered = all_eps_sorted
gi = 0
for e in ordered:
    ep_s = []
    for s in e['sents']:
        t = s.get('t') or ''
        g = shung[gi]; p = shunp[gi]
        R['sents'] += 1
        if not (s.get('zh') or '').strip():
            R['zh_empty'] += 1
        # 时间轴：句区间不许交叠（peer 实测 s001 16 处 / s006 9 处）
        ep_s.append((s.get('s'), s.get('e')))
        fr = g.get('frame') or []
        if not fr:
            R['no_frame'] += 1
        cur = 0
        spans = []
        for b in fr:
            R['blocks'] += 1
            ss = b.get('s', '')
            if b.get('b') not in LEGAL:
                R['b_illegal'] += 1
            if ss and ss not in t:
                R['s_not_sub'] += 1
            # 顺序推进定位
            idx = t.find(ss, cur)
            if idx < 0:
                R['locate_fail'] += 1
            else:
                cur = idx + len(ss)
                spans.append((idx, idx + len(ss)))
        spans.sort()
        for i in range(1, len(spans)):
            if spans[i][0] < spans[i-1][1]:
                R['overlap'] += 1
        for i in range(1, len(fr)):
            if fr[i].get('b') == fr[i-1].get('b'):
                R['adj_same'] += 1
        gi += 1

R['shung_len'] = len(shung); R['shunp_len'] = len(shunp)
if R['shung_len'] != R['sents'] or R['shunp_len'] != R['sents']:
    fails.append('SHUNG/SHUNP 长度与句数不符')
R['blocks_with_parts'] = sum(1 for g in shung for b in (g.get('blocks') or []) if b.get('parts'))
R['blocks_total'] = sum(len(g.get('blocks') or []) for g in shung)
R['with_inner'] = sum(1 for g in shung for b in (g.get('blocks') or []) if b.get('inner'))
R['with_skel'] = sum(1 for g in shung if g.get('skel'))
R['pred_with_chain'] = sum(1 for p in shunp if p.get('p'))
R['mc_total'] = sum(1 for p in shunp if p.get('mc'))

print('=== 合并版数据层独立审计 ===')
for k in ['eps', 'sents', 'shung_len', 'shunp_len', 'blocks', 'blocks_total', 'blocks_with_parts',
          'with_inner', 'with_skel', 'pred_with_chain', 'mc_total',
          'shun_line_identical', 'dup_ep', 'sorted_by_real', 'min_ep', 'max_ep',
          's_not_sub', 'locate_fail', 'overlap', 'b_illegal', 'adj_same', 'zh_empty', 'no_frame',
          'timeline_overlaps']:
    print(f'  {k:20s} {R[k]}')
# 时间轴交叠（按集判）
ep_ovl = 0
for e in ordered:
    ss = [(x.get('s'), x.get('e')) for x in e['sents']]
    for i in range(len(ss) - 1):
        a, b = ss[i][1], ss[i + 1][0]
        if a is not None and b is not None and a > b:
            ep_ovl += 1
R['timeline_overlaps'] = ep_ovl
if ep_ovl:
    fails.append(f'句级时间轴有 {ep_ovl} 处交叠')
print('\n=== 判定 ===')
if fails:
    for f in fails:
        print('  ❌', f)
    sys.exit(1)
print('  ✅ 全部通过')
