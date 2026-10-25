#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
挑出仓库 gs 里**可信**的句, 其余用规则引擎兜底。

🔴 实测 (2026-10-25) 仓库 shun-web/data/*.json 的 gs 现状:
   * frame[].p (块起点) **是对的** (3082/3082: t[p:p+len(s)]==s)
   * 但 frame[].s 被截成**单个 ruby 单元** (89.3% 的块)
   ⇒ 用「p_i … p_{i+1}」可把真块文本重建出来 (repair_gs.py 已做)
   * 但少数句 LLM 本身就分错了 (例: ep01:19 幻觉出中文「原始人」)
     ⇒ 重建后会出现「谓语块不在句尾」这种自相矛盾

判据 (一条一条都要过):
  1. 块文本逐字来自原句
  2. 块之间不重叠
  3. 若存在「谓语」角色的块 ⇒ 它必须在**末尾** (或后面只跟语气/连接类块)
  4. 块覆盖原句主干 ≥ 80% (去标点后)
"""
import json, os, re

HERE = os.environ.get('SHUN_XVAL_DIR') or os.path.dirname(os.path.abspath(__file__))
OLD = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html'
PUNCT = set('、，,。．.!?！？；;：:…・「」『』（）()[]「」 　\n')


def norm_punct(s):
    return ''.join(ch for ch in s if ch not in PUNCT)


def check_frame(fr, t):
    """返回 (ok, reason)"""
    if not fr:
        return False, 'frame 空'
    # 1) 逐字 + 不重叠
    pos = 0
    for f in fr:
        p = f.get('p')
        if not (isinstance(p, list) and len(p) == 2):
            return False, 'p 非 [start,end]'
        st, en = p
        if t[st:en] != f.get('s'):
            return False, 's != t[p]'
        if st < pos:
            return False, '块重叠'
        pos = max(pos, en)
    # 2) 谓语块必须在末尾
    pred_idx = [i for i, f in enumerate(fr) if '谓语' in (f.get('role') or '')]
    if pred_idx:
        last = pred_idx[-1]
        for f in fr[last + 1:]:
            r = f.get('role') or ''
            if not any(k in r for k in ('语气', '连接', '副词', '感叹', '应答', '称呼')):
                return False, '谓语块不在末尾'
    # 3) 覆盖度
    cov = sum(len(norm_punct(f['s'])) for f in fr)
    eff = len(norm_punct(t))
    if eff and cov < eff * 0.8:
        return False, '覆盖不足 80%'
    return True, 'ok'


def main():
    src = open(OLD, encoding='utf-8').read()
    line = [l for l in src.split('\n') if l.startswith('<script>window.SHUN=')][0]
    S = json.loads(re.match(r'^<script>window\.SHUN=(.*);</script>$', line).group(1))['eps']
    ok = 0
    bad = []
    reasons = {}
    for e in S:
        D = json.load(open(os.path.join(HERE, f"{e['tag']}.json"), encoding='utf-8'))
        for i, (a, b) in enumerate(zip(D['sents'], e['sents'])):
            g = a.get('gs')
            if not g or not g.get('frame'):
                continue
            good, why = check_frame(g['frame'], b['t'])
            if good:
                ok += 1
            else:
                bad.append((e['tag'], i, why, b['t'][:40]))
                reasons[why] = reasons.get(why, 0) + 1
    print('仓库 gs 可信句:', ok, ' 不可信:', len(bad))
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f'   {k}: {v}')
    for x in bad[:10]:
        print('   ', x)
    return ok, bad


if __name__ == '__main__':
    main()
