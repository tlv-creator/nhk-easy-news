#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立复核 peer 推上来的 7 集（s001..s361）：
  A. 用「事实量」复核它自报的交付闸门数字
  B. 用我的规则引擎 + gs_quality.py 做交叉验证
判据全部落在「事实量」上，顺序推进定位（find(s,cursor)）。
"""
import json, os, re, sys, glob
from collections import Counter, defaultdict

BASE = '/workspace/tools/shun/repo2/nhk-easy-news-main/shun-web/data'
LEGAL = {'[]', '［］', '〈〉', '（）'}

def find_adv(t, s, cur):
    """按出现顺序推进定位（铁律：不能用 text.find(s)）"""
    i = t.find(s, cur)
    return i

def audit_one(d, tag):
    r = dict(tag=tag, sents=0, blocks=0, empty_frame=0,
             s_not_sub=0, p_missing=0, p_mismatch=0, p_overlap=0, p_oob=0,
             b_illegal=0, adj_same=0, zh_empty=0, pred_not_last=0,
             cov_sum=0.0, cov_n=0, cov_ge80=0, gap_nonspace=0,
             roles=Counter(), bkinds=Counter(), en_nonempty=0, ru_nonempty=0,
             frame_src=Counter(), short_blocks=0, block_lens=Counter(),
             sents_with_frame=0, frames=0)
    for s in d['sents']:
        r['sents'] += 1
        t = s.get('t') or ''
        if s.get('zh', '').strip() == '': r['zh_empty'] += 1
        if s.get('en', '').strip(): r['en_nonempty'] += 1
        if s.get('ru'): r['ru_nonempty'] += 1
        gs = s.get('gs') or {}
        fr = gs.get('frame') or []
        r['frame_src'][gs.get('src', '?')] += 1
        if not fr:
            r['empty_frame'] += 1
            r['sents_with_frame'] += 0
            continue
        r['sents_with_frame'] += 1
        r['frames'] += 1
        # ① s 逐字
        # ② p 合法
        spans = []
        cur = 0
        for b in fr:
            r['blocks'] += 1
            ss = b.get('s', '')
            if b.get('b') not in LEGAL:
                r['b_illegal'] += 1
            r['bkinds'][b.get('b')] += 1
            r['roles'][b.get('role')] += 1
            r['block_lens'][len(ss)] += 1
            if len(ss) <= 2 and not re.search(r'[、。！？]', ss):
                r['short_blocks'] += 1
            p = b.get('p')
            if not p or len(p) != 2:
                r['p_missing'] += 1
                continue
            a, z = p
            if not (0 <= a <= z <= len(t)):
                r['p_oob'] += 1
                continue
            if t[a:z] != ss:
                r['p_mismatch'] += 1
            if ss and ss not in t:
                r['s_not_sub'] += 1
            spans.append((a, z, b))
        # ③ 不重叠（按 p 区间）
        spans.sort(key=lambda x: (x[0], x[1]))
        for i in range(1, len(spans)):
            if spans[i][0] < spans[i-1][1]:
                r['p_overlap'] += 1
        # ④ 相邻同类型不合并
        for i in range(1, len(fr)):
            if fr[i].get('b') == fr[i-1].get('b'):
                r['adj_same'] += 1
        # ⑤ 谓语块在末尾
        preds = [i for i, b in enumerate(fr) if b.get('b') == '（）']
        if preds and max(preds) != len(fr) - 1:
            r['pred_not_last'] += 1
        # ⑥ 覆盖（去标点）
        covered = set()
        for b in fr:
            p = b.get('p')
            if p and len(p) == 2:
                covered.update(range(p[0], p[1]))
        nonspace = [i for i, ch in enumerate(t) if ch not in ' 　\t\n']
        cov = len(covered & set(nonspace)) / max(1, len(nonspace))
        r['cov_sum'] += cov; r['cov_n'] += 1
        if cov >= 0.80: r['cov_ge80'] += 1
        gaps = [i for i in nonspace if i not in covered]
        # 连续空隙算 1 处
        gap_cnt = 0; prev = -9
        for i in gaps:
            if i != prev + 1: gap_cnt += 1
            prev = i
        r['gap_nonspace'] += gap_cnt
    return r

def main():
    files = sorted(glob.glob(os.path.join(BASE, 's*.json')))
    files = [f for f in files if os.path.basename(f) != 'index.json']
    tot = defaultdict(int)
    print('=== A. 独立复核（逐字/区间/括号/合并/覆盖）===')
    for f in files:
        d = json.load(open(f, encoding='utf-8'))
        r = audit_one(d, d.get('tag'))
        for k, v in r.items():
            if k in ('roles', 'bkinds', 'frame_src', 'block_lens', 'tag'): continue
            tot[k] += v
        print(f"  {r['tag']}: 句{r['sents']:4d} 帧{r['frames']:4d} 块{r['blocks']:4d} "
              f"无帧{r['empty_frame']:3d} 覆盖≥80%:{r['cov_ge80']:4d}/{r['cov_n']:4d} "
              f"谓语非末{r['pred_not_last']:3d} 同型相邻{r['adj_same']:3d} "
              f"src={dict(r['frame_src'])}")
    print('--- 合计 ---')
    for k in ['sents','frames','blocks','empty_frame','s_not_sub','p_missing','p_mismatch',
              'p_overlap','p_oob','b_illegal','adj_same','zh_empty','pred_not_last',
              'gap_nonspace','en_nonempty','ru_nonempty','short_blocks']:
        print(f'  {k:16s} {tot[k]}')
    print(f"  覆盖≥80%  {tot['cov_ge80']}/{tot['cov_n']} = {tot['cov_ge80']/max(1,tot['cov_n'])*100:.1f}%")
    # 块长分布
    bl = Counter()
    for f in files:
        d = json.load(open(f, encoding='utf-8'))
        for s in d['sents']:
            for b in ((s.get('gs') or {}).get('frame') or []):
                bl[len(b.get('s',''))] += 1
    tot_b = sum(bl.values())
    print(f"  块总数 {tot_b} ｜ ≤2字且无标点 {sum(v for k,v in bl.items() if k<=2)} "
          f"({sum(v for k,v in bl.items() if k<=2)/max(1,tot_b)*100:.1f}%) ｜ 平均 "
          f"{sum(k*v for k,v in bl.items())/max(1,tot_b):.2f} 字")
    # 角色分布（〈〉是否只标宾语）
    roles = Counter(); bk = Counter(); bad_bracket_role = []
    for f in files:
        d = json.load(open(f, encoding='utf-8'))
        for s in d['sents']:
            for b in ((s.get('gs') or {}).get('frame') or []):
                roles[b.get('role')] += 1; bk[b.get('b')] += 1
                if b.get('b') == '〈〉' and b.get('role') not in ('宾语','宾语内容','宾语＋が','内容'):
                    bad_bracket_role.append((d['tag'], b.get('s'), b.get('role')))
    print('  括号类型:', dict(bk))
    print('  〈〉 但 role 非宾语:', len(bad_bracket_role), bad_bracket_role[:5])
    print('  role top:', roles.most_common(14))

if __name__ == '__main__':
    main()
