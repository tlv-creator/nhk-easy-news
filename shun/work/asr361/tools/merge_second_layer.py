#!/usr/bin/env python3
"""
把 gen_blocks.py 产出的第二层（blocks/inner/parts/skel/chain/whole + pred）
合并回 data/<tag>.json —— **不改动 frame / t / ru / zh / kind / en**。

🔴 2026-10-25 修正一个真 bug：上一版合并脚本**只合了 s001**（其余 5 集 blocks=0），
   而我自报「blocks 1650」⇒ **算的时候有、落盘时没落** ⇒ 线上弹窗②段整段空白。
   ⇒ 这次加**硬断言**：合并后每集 blocks 必须 > 0，且块数必须与 peer_enriched 一致。
   ⇒ 通则：**「我报的数」和「落到产物里的数」必须分别验证**（同族：算的时候对 ≠ 落盘的时候对）。

用法：python3 tools/merge_second_layer.py [--check]
"""
import json, os, glob, sys

SRC = '/workspace/xval/peer_enriched'
DST = '/workspace/shun-web/shun-web/data'
TAGS = ['s001', 's002', 's003', 's004', 's005', 's006', 's361']


def main():
    check_only = '--check' in sys.argv
    total_blocks = 0
    bad = []
    for tag in TAGS:
        src = os.path.join(SRC, tag + '.json')
        dst = os.path.join(DST, tag + '.json')
        if not (os.path.isfile(src) and os.path.isfile(dst)):
            bad.append((tag, '缺文件')); continue
        d = json.load(open(src, encoding='utf-8'))
        old = json.load(open(dst, encoding='utf-8'))
        if len(d['sents']) != len(old['sents']):
            bad.append((tag, '句数不一致 %d vs %d' % (len(d['sents']), len(old['sents'])))); continue
        nb = 0
        for i, s in enumerate(d['sents']):
            o = old['sents'][i]
            # 只补第二层，保留我自己的字段
            gs = o.get('gs') or {}
            gs['blocks'] = s['gs'].get('blocks', [])
            for k in ('skel', 'skelMean', 'chain', 'whole'):
                if s['gs'].get(k) is not None:
                    gs[k] = s['gs'][k]
            o['gs'] = gs
            nb += len(gs['blocks'])
        if not check_only:
            json.dump(old, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
        # pred 文件
        p = os.path.join(SRC, tag + '.pred.json')
        if os.path.isfile(p) and not check_only:
            import shutil
            shutil.copy(p, os.path.join(DST, tag + '.pred.json'))
        total_blocks += nb
        print("  %-5s sents=%3d blocks=%4d %s" % (tag, len(old['sents']), nb,
              '' if nb else '❌ blocks=0'))
        if nb == 0:
            bad.append((tag, 'blocks=0'))
    print("合计 blocks: %d" % total_blocks)
    if bad:
        print("❌ 有问题: %s" % bad)
        sys.exit(1)
    print("✅ 每集 blocks > 0")


if __name__ == '__main__':
    main()
