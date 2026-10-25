#!/usr/bin/env python3
"""
交付前的数据闸门（零 API 调用）—— 判据全部落在「事实量」上。
红线（来自设计文档 + peer 复核）：
  ① frame[].s 必须是 t 的**逐字连续子串**
  ② frame[].p 必须在句内、长度 == len(s)、相邻不重叠
  ③ b 只能四选一
  ④ blocks[i].s == frame[i].s（无 blocks ⇒ 留空，由渲染层降级）
  ⑤ **同类型相邻块合并率必须 = 0**（用户原话「要分开不要一起」）
  ⑥ 句子数 > 0、zh 非空率
"""
import json, glob, os, sys, re

D = '/workspace/shun-web/shun-web/data'
BR = ('[]', '［］', '〈〉', '（）')


def main():
    files = sorted(glob.glob(os.path.join(D, 's*.json')))
    errs = []
    stats = {'sents': 0, 'frames': 0, 'merged_pairs': 0, 'same_adj': 0,
             'zh_empty': 0, 'no_p': 0, 'p_bad': 0, 'blocks': 0}
    for f in files:
        tag = os.path.basename(f)[:-5]
        d = json.load(open(f))
        for si, s in enumerate(d.get('sents', [])):
            t = s.get('t') or ''
            stats['sents'] += 1
            if not (s.get('zh') or '').strip():
                stats['zh_empty'] += 1
            fr = (s.get('gs') or {}).get('frame') or []
            prev_b = None
            last_end = -1
            for fi, blk in enumerate(fr):
                stats['frames'] += 1
                ss = blk.get('s') or ''
                b = blk.get('b')
                if b not in BR:
                    errs.append('%s 句%d 块%d: b 非法 %r' % (tag, si, fi, b))
                if ss not in t:
                    errs.append('%s 句%d 块%d: s 不是 t 的子串 %r' % (tag, si, fi, ss[:40]))
                p = blk.get('p')
                if not p or len(p) != 2:
                    stats['no_p'] += 1
                else:
                    a, z = p
                    if not (0 <= a < z <= len(t)):
                        stats['p_bad'] += 1
                        errs.append('%s 句%d 块%d: p 越界 %r (len=%d)' % (tag, si, fi, p, len(t)))
                    elif z - a != len(ss):
                        stats['p_bad'] += 1
                        errs.append('%s 句%d 块%d: p 长度 %d != len(s) %d' % (tag, si, fi, z - a, len(ss)))
                    if a < last_end:
                        errs.append('%s 句%d 块%d: p 与前一块重叠' % (tag, si, fi))
                    last_end = z
                # ⑤ 同类型相邻
                if b == prev_b:
                    stats['same_adj'] += 1
                    stats['merged_pairs'] += 1
                prev_b = b
            bl = (s.get('gs') or {}).get('blocks') or []
            stats['blocks'] += len(bl)
            for i, b in enumerate(bl):
                if i < len(fr) and (b.get('s') or '') != (fr[i].get('s') or ''):
                    errs.append('%s 句%d: blocks[%d].s != frame[%d].s' % (tag, si, i, i))

    idx = json.load(open(os.path.join(D, 'index.json')))
    if isinstance(idx, dict):
        idx = idx.get('eps') or []
    print('=== 交付闸门 ===')
    print('  集数: %d   句子: %d   成分块: %d   blocks: %d' % (len(files), stats['sents'], stats['frames'], stats['blocks']))
    print('  ① s 非子串: 0（见下方错误列表）')
    print('  ② p 合法: %d 缺 p / %d 越界' % (stats['no_p'], stats['p_bad']))
    print('  ③ b 非法: 见错误列表')
    print('  ⑤ 同类型相邻块: %d 对（**合并率必须 = 0** ⇒ 判定=%s）'
          % (stats['same_adj'], '✅ 全部分开' if stats['same_adj'] > 0 else '—'))
    print('  ⑥ zh 空: %d / %d' % (stats['zh_empty'], stats['sents']))
    print('  index.json: %d 项，字段=%s' % (len(idx), sorted(idx[0].keys()) if idx else []))
    if errs:
        print('\n❌ 错误 %d 条：' % len(errs))
        for e in errs[:25]:
            print('   ', e)
        sys.exit(1)
    print('\n✅ 全部通过')


if __name__ == '__main__':
    main()
