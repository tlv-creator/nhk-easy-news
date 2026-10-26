#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人工补正 —— 那些「LLM 改得不完全对、但我判断得出正确写法」的句子
（依据：NHK 播报稿的固定表达 + 同一新闻的参照文本）
"""
import json, re, os

SITE = '/workspace/nhk/site/app'

# (期号, 句序) -> 正确句子（人工判定）
MANUAL = {
    ('20261014de59139', 6):
        'また、記者がモスクワの裁判所で1ヶ月と29日間の拘禁を決定したと言い渡された際の様子',
    ('20261014de59139', 8):
        '有罪になった場合、懲役10年から20年が科されるということです。',
    ('20261014de59139', 12):
        '日本政府は、人道的な配慮、不当な取扱いについて申し入れを行っているところであります。',
    ('20261015de59709', 3):
        '議活動を続け、これを受けてフランス政府は予算の増額や臨時教員の確保を発表し、大規模な',
    ('20261016de60485', 11):
        '緊急地震速報発表時に示されたのは、赤い枠で示した場所で、当初推定されたマグニチュ',
    ('20261021de62763', 7):
        '川にはレベル4洪水警報が発表されています。',
    ('20261023de64170', 8):
        '今朝も強風で、この状態に、西表島大原では最大瞬間風速51.7メートルを観測しました。',
}

for (eid, idx), text in MANUAL.items():
    p = f'{SITE}/subs/{eid}.js'
    if not os.path.exists(p):
        print(f'  ⚠️ 缺文件 {eid}'); continue
    src = open(p, encoding='utf-8').read()
    d = json.loads(re.search(r'=\s*(\{.*\})\s*;?\s*$', src, re.S).group(1), strict=False)
    sp = d.get('sp') or []
    if idx >= len(sp):
        print(f'  ⚠️ {eid}[{idx}] 越界（共 {len(sp)} 句）'); continue
    old = sp[idx].get('ja', '')
    if old == text:
        print(f'  ＝ {eid}[{idx}] 已是目标值'); continue
    sp[idx]['ja'] = text
    new_src = re.sub(r'=\s*\{.*\}\s*;?\s*$',
                     '= ' + json.dumps(d, ensure_ascii=False, separators=(',', ':')) + ';',
                     src, flags=re.S)
    open(p, 'w', encoding='utf-8').write(new_src)
    print(f'  ✎ {eid}[{idx}]')
    print(f'      旧: {old[:70]}')
    print(f'      新: {text[:70]}')
print(f'\n  人工补正 {len(MANUAL)} 句')
