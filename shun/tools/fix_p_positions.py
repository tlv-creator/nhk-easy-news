#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一 frame[].p 为 [start, end] 区间（cursor 推进重定位）

为什么要统一：
  · 线上现状两套并存（13 集 int 3082 / 区间 1497 / None 2）⇒ 校验器、富化脚本都得写兼容分支
  · p=None 的 2 处（ep03#7、ep02#18）让「块在句内的哪个位置」无从判断

实测（本次全量，不是抽样）：
  · cursor 推进定位 **4581/4581 全部命中**
  · int p 指向正确位置的 **3082/3082**（⇒ int 值本身没错，只是缺终点）

⇒ 统一用 cursor 推进重新定位（不信任旧值），找不到就保留旧值并报告。
"""
import json, os, sys, collections

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'data-merged.json')
    data = json.load(open(path, encoding='utf-8'))
    st = collections.Counter()
    for e in data['eps']:
        for s in e['sents']:
            t = s.get('t') or ''
            gs = s.get('gs') or {}
            fr = gs.get('frame') or []
            cur = 0
            for f in fr:
                seg = f.get('s') or ''
                if not seg:
                    continue
                old = f.get('p')
                if isinstance(old, list) and len(old) == 2:
                    st['already_range'] += 1
                elif isinstance(old, int):
                    st['was_int'] += 1
                elif old is None:
                    st['was_none'] += 1
                idx = t.find(seg, cur)
                if idx < 0:
                    idx = t.find(seg)
                    if idx < 0:
                        st['locate_fail'] += 1
                        continue
                f['p'] = [idx, idx + len(seg)]
                cur = idx + len(seg)
                st['fixed'] += 1
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('p 归一化:', dict(st))
    if st['locate_fail']:
        print('🔴 有 %d 块定位失败 ⇒ 未改动它们' % st['locate_fail'])
        sys.exit(1)


if __name__ == '__main__':
    main()
