#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复时间轴：排序 + 单调化

发现的线上 bug（2026-10-25）：
  6 集（peer 产出的 s001/s006）的**句子数组顺序 ≠ 音频顺序**，
  而 s/e 是按音频顺序给的 ⇒ 渲染层按数组顺序找「当前句」会跳错位置。
  s001 有 16 处区间交叠、s006 有 9 处；13 集 0 处（我这边没问题）。

修法（零额度，不重跑 ASR）：
  ① 按 (s, e) 升序重排 sents ⇒ 显示顺序 = 音频顺序
  ② 单调化：e = min(e, 下一句.s)；若 e <= s 则 e = 下一句.s
     （只截断「本来就错」的 e，不动其它句）

⚠️ 改的是**已有发布数据** ⇒ 推送必须走路径级 CAS。
"""
import json, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def fix(ep):
    ss = ep['sents']
    before = sum(1 for i in range(1, len(ss))
                 if float(ss[i].get('s') or 0) < float(ss[i - 1].get('e') or 0) - 0.01)
    orig = [s.get('t') for s in ss]
    ss.sort(key=lambda s: (float(s.get('s') or 0), float(s.get('e') or 0)))
    # 单调化
    for i in range(len(ss) - 1):
        cur, nxt = ss[i], ss[i + 1]
        cs, ce = float(cur.get('s') or 0), float(cur.get('e') or 0)
        ns = float(nxt.get('s') or 0)
        if ce > ns:
            cur['e'] = ns
        if float(cur.get('e') or 0) <= cs:
            cur['e'] = ns
    after = sum(1 for i in range(1, len(ss))
                if float(ss[i].get('s') or 0) < float(ss[i - 1].get('e') or 0) - 0.01)
    reordered = sum(1 for a, b in zip(orig, [s.get('t') for s in ss]) if a != b)
    return before, after, reordered


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'data-merged.json')
    data = json.load(open(path, encoding='utf-8'))
    tot_b = tot_a = tot_r = 0
    for e in data['eps']:
        b, a, r = fix(e)
        if b or r:
            print('  %-6s 交叠 %2d → %2d ｜ 位置变动的句子 %d' % (e['tag'], b, a, r))
        tot_b += b; tot_a += a; tot_r += r
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('合计：交叠 %d → %d ｜ 位置变动 %d 句' % (tot_b, tot_a, tot_r))


if __name__ == '__main__':
    main()
