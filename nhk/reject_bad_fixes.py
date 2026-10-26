#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人工审阅后的剔除清单 —— 把 LLM 改坏的地方还原成原句
（审阅依据：逐条看旧/新对照。判定标准 = 新句是否仍是「这句播报稿的正确写法」）
"""
import json, re, os, glob, shutil, sys

# (期号, 句序) —— 人工判定为「改坏了 / 改过头」⇒ 还原
REJECT = {
    '20261009de57124': [23],   # カラリコ → rarities（塞了英文垃圾）
    '20261014de59139': [6, 8, 12],
                           # [6] 塞了中文「了」；[8] 禁錮（应为懲役）；[12] 十分適切（原文应是「不当な」）
    '20261015de59709': [3],    # 整句被改写成「今月中にさらなる」⇒ 完全改写
    '20261016de60485': [11, 35],
                           # [11] 「黙示」不对；[35] 删掉了「-EarthProcesses enticな影响」
    '20261020de62106': [7],    # っています → れています（无意义改动）
    '20261021de62763': [7],    # 小吉 → 小千（正确是「小千谷」⇒ 不确定，宁可保留原句）
    '20261023de64170': [8, 20],
                           # [8] 「浦添城も外れて…」改坏了；[20] 弹属的 → 弹力的（两版都不对）
}

SITE = '/workspace/nhk/site/app'
BK = sorted(glob.glob('/workspace/nhk/_backup_subs_*'))[-1]

def load(p):
    s = open(p, encoding='utf-8').read()
    m = re.search(r'=\s*(\{.*\})\s*;?\s*$', s, re.S)
    return json.loads(m.group(1), strict=False)

def dump(d, path):
    s = 'window.__SUBS__=window.__SUBS__||{};window.__SUBS__[' + json.dumps('') .__class__('str') + ']'
    return None

total_reject = 0
for f in sorted(glob.glob(f'{SITE}/subs/*.js')):
    eid = os.path.basename(f)[:-3]
    if eid not in REJECT:
        continue
    of = os.path.join(BK, os.path.basename(f))
    if not os.path.exists(of):
        continue
    src = open(f, encoding='utf-8').read()
    d = json.loads(re.search(r'=\s*(\{.*\})\s*;?\s*$', src, re.S).group(1), strict=False)
    od = load(of)
    sp = d.get('sp') or []
    osp = od.get('sp') or []
    n = 0
    for i in REJECT[eid]:
        if i < len(sp) and i < len(osp) and sp[i].get('ja') != osp[i].get('ja'):
            print(f'  ↩ {eid}[{i}] 还原')
            print(f'      改坏的: {sp[i].get("ja","")[:66]}')
            print(f'      还原为: {osp[i].get("ja","")[:66]}')
            sp[i]['ja'] = osp[i]['ja']
            n += 1
    if n:
        total_reject += n
        new_src = re.sub(r'=\s*\{.*\}\s*;?\s*$',
                         '= ' + json.dumps(d, ensure_ascii=False, separators=(',', ':')) + ';',
                         src, flags=re.S)
        open(f, 'w', encoding='utf-8').write(new_src)
print(f'\n  共还原 {total_reject} 句')
