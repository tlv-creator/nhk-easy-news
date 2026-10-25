#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
19 集（已有中文/frame）+ 31 集（平台通道新产出）→ data-merged-50.json

集序：**官方编号 realEp 升序** ⇒ 天然是教学难度梯度
      Ep1-6（Genki 1 基础）→ Ep8-23（教材课）→ Ep41-255（正式集）→ Ep266-278（N5-N4）
显示序号 ep 重排为 1..N 连续（渲染层显示「第N集 · EpXXX」）。
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def clean_title(raw):
    """标题清洗。
    🔴 只去**难度级别**前缀（【N5-N4】这类），**保留教材版本**（【Genki 2】）
       —— 洗掉「Genki 2」会让用户把 Genki2 的课当成 Genki1 的 L18。
    """
    t = re.sub(r'^【(?!Genki)[^】]*】\s*', '', raw or '')   # 去【N5-N4】但保留【Genki 2】
    t = re.sub(r'\s*/\s*Japanese.*$', '', t, flags=re.I)   # 去 " / Japanese Podcast for Beginners"
    t = re.sub(r'\s*/\s*$', '', t)
    t = re.sub(r'^Ep\d+\s+', '', t)                    # 去 "Ep266 "
    return t.strip() or (raw or '')


def main():
    base = json.load(open(os.path.join(ROOT, 'data-merged.json'), encoding='utf-8'))
    tm = {t['tag']: t for t in json.load(open(
        os.path.join(ROOT, 'output', 'shun-361-handoff', 'tagmap.json'), encoding='utf-8'))}
    rows = json.load(open(os.path.join(ROOT, 'new-episodes.json'), encoding='utf-8'))

    eps = list(base['eps'])
    have = {e['tag'] for e in eps}
    added = skipped = 0
    for r in rows:
        if r['tag'] in have:
            skipped += 1
            continue
        p = os.path.join(ROOT, 'data-new', r['tag'] + '.json')
        if not os.path.exists(p):
            print('  ⚠️ %s 无产出，跳过' % r['tag'])
            skipped += 1
            continue
        d = json.load(open(p, encoding='utf-8'))
        t = tm[r['tag']]
        desc = t['title']
        if t.get('kind') == 'l':
            # 教材课要标清是 Genki 1 还是 Genki 2（标题里已带 【Genki 2】/ Genki 1）
            ver = 'Genki 2' if 'Genki 2' in t['title'] or 'Genki2' in t['title'] else 'Genki 1'
            desc = ver + ' 教材课 · ' + t['title']
        eps.append({
            'tag': r['tag'],
            'ep': 0,                       # 稍后重排
            'title': clean_title(t['title']),
            'desc': desc,
            'dur': r.get('dur') or t['dur'],   # r['dur'] 已用 ffprobe 实测值校正过
            'audio': t['url'],
            'realEp': t['no'],
            'sents': d['sents'],
        })
        added += 1

    # 排序：realEp 升序；同号时教材课在前（kind 顺序靠 desc 前缀判断）
    eps.sort(key=lambda e: (e.get('realEp') or e.get('ep') or 0,
                            0 if '教材课' in (e.get('desc') or '') else 1,
                            e.get('ep') or 0))
    for i, e in enumerate(eps):
        e['ep'] = i + 1
    out = {'eps': eps}
    json.dump(out, open(os.path.join(ROOT, 'data-merged-50.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    print('合并完成: 原 %d + 新 %d = %d 集（跳过 %d）｜ %d 句' % (
        len(base['eps']), added, len(eps), skipped, sum(len(e['sents']) for e in eps)))
    print()
    print('%-5s %-8s %-6s %-6s %-38s %s' % ('#', 'tag', 'realEp', '句数', '标题', '来源'))
    for i, e in enumerate(eps):
        src = '原有' if e['tag'] in {x['tag'] for x in base['eps']} else '新增'
        print('%-5d %-8s %-6s %-6d %-38s %s' % (
            e['ep'], e['tag'], e.get('realEp'), len(e['sents']), e['title'][:36], src))


if __name__ == '__main__':
    main()
