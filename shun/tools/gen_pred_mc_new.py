#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给新 31 集生成 pred/mc（谓语细分 + 形态链），与 peer 的 pred_mc_19eps.json 同构。

输入：
  · tokens-new/<tag>.json   （IPADIC 词级 token，供 analyze_sentence 用）
  · data-new/<tag>.json     （句子原文，校验用）
输出：
  · tools/pred_mc_new31.json  { tag: [ {p:[[start,len,typeCode,label],...], mc:[...], k:句末类型}, ... ] }

🔴 与 peer 的 sidecar 契约完全一致（按句序 0-based 一一对应）：
  p  = [[start, len, typeCode, label], ...]   start/len 是**字符偏移**
  mc = [辞書形, て形, 意志形, ば形]
  k  = 句末类型

零额度：纯本地规则引擎（analyze.py + conj.py）。
"""
import json, os, sys, collections

ROOT = '/workspace/shun'
sys.path.insert(0, os.path.join(ROOT, 'tools', 'xval'))
os.environ.setdefault('SHUN_XVAL_DIR', os.path.join(ROOT, 'tools', 'xval'))
os.environ.setdefault('NODE_PATH', os.path.join(ROOT, 'tools', 'xval', 'node_modules'))

from analyze import analyze_sentence, T          # noqa: E402
from conj import chain as conj_chain             # noqa: E402

TYPE_CODE = {
    '主语': 0, '动词': 0, '形容词': 1, '形容动词': 1, '判断': 3,
    '过去（た）': 5, '过去（ました）': 5, '否定': 4, '意志': 6, '可能': 7,
    '被动': 8, '使役': 9, '丁寧': 10, '命令': 11, '条件': 12, '并列': 13,
}


def _code(label):
    for k, v in TYPE_CODE.items():
        if k in label:
            return v
    return 0


def pred_of(a, text):
    """从分析树里抽「谓语细分」= a['p']（扁平数组，每 4 个一组：
       start, len, typeCode, label）。
    🔴 契约（与 peer 的 pred_mc_19eps.json 完全一致）：
       p = [[start, len, typeCode, label], ...]
    """
    flat = a.get('p') or []
    out = []
    for i in range(0, len(flat) - 3, 4):
        st, ln, code, label = flat[i], flat[i + 1], flat[i + 2], flat[i + 3]
        try:
            st = int(st); ln = int(ln)
        except Exception:
            continue
        if st < 0 or ln <= 0:
            continue
        seg = text[st:st + ln]
        if not seg:
            continue
        out.append([st, ln, int(code) if str(code).lstrip('-').isdigit() else 0, label])
    return out


def build(tag):
    tp = os.path.join(ROOT, 'tokens-new', tag + '.json')
    dp = os.path.join(ROOT, 'data-new', tag + '.json')
    if not os.path.exists(tp):
        return None
    toks = json.load(open(tp, encoding='utf-8'))
    d = json.load(open(dp, encoding='utf-8'))
    sents = d['sents']
    assert len(toks) == len(sents), (tag, len(toks), len(sents))
    out = []
    for rec, s in zip(toks, sents):
        text = rec.get('t') or s.get('t') or ''
        try:
            a = analyze_sentence(rec)
        except Exception:
            a = {}
        p = pred_of(a, text)
        mc = a.get('mc') or []
        k = a.get('k') or ''
        out.append({'p': p, 'mc': mc, 'k': k})
    return out


def main():
    res = {}
    stats = collections.Counter()
    for tag in sorted(os.listdir(os.path.join(ROOT, 'tokens-new'))):
        if not tag.endswith('.json') or tag.startswith('_'):
            continue
        tag = tag[:-5]
        try:
            v = build(tag)
        except Exception as e:
            print('  skip %s: %s' % (tag, e))
            continue
        if not v:
            continue
        res[tag] = v
        stats['sents'] += len(v)
        stats['with_p'] += sum(1 for x in v if x['p'])
        stats['with_mc'] += sum(1 for x in v if x['mc'])
        stats['with_k'] += sum(1 for x in v if x['k'])
    outp = os.path.join(ROOT, 'tools', 'pred_mc_new31.json')
    json.dump(res, open(outp, 'w', encoding='utf-8'), ensure_ascii=False)
    print('写 %s' % outp)
    print('集数 %d ｜ 句 %d ｜ 有pred %d ｜ 有mc %d ｜ 有句末类型 %d'
          % (len(res), stats['sents'], stats['with_p'], stats['with_mc'], stats['with_k']))
    print('大小 %d 字节' % os.path.getsize(outp))


if __name__ == '__main__':
    main()
