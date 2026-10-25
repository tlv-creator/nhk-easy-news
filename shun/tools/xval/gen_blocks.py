#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 peer 的 frame **补第二层**（不改动 frame 本身）：
  gs.blocks[] : {s, inner, parts:[{s,note}], mean}
      · inner  = 块内骨架（【主题】【主语】[连体修饰]）—— 我的规则引擎在**他的块范围内**标注
      · parts  = 词级拆解（词性 + 活用 + 中文说明）
  gs.skel / gs.skelMean = 主句骨架公式
  gs.chain  = 层层修饰
  gs.whole  = 整句中文（用他的 zh）

🔴 原则：**frame 一个字节都不动**（他的语义分组 99.6% 过我的闸门，比我的成分级切分更贴
   shun/docs/新结构注释设计.md 的四类括号定义）⇒ 只补「块内」信息。
"""
import json, os, sys, collections

HERE = os.environ.get('SHUN_XVAL_DIR') or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze import analyze_sentence, T
from frame import inner_of

BASE = os.environ.get('SHUN_DATA_DIR') or os.path.join(HERE, 'repo2/nhk-easy-news-main/shun-web/data')

# ── 词性 → 中文说明 ─────────────────────────────────────────────
POS_CN = {
    ('名詞', '一般'): '名词', ('名詞', '固有名詞'): '专有名词',
    ('名詞', 'サ変接続'): '名词（サ变，用作动词）', ('名詞', '形容動詞語幹'): '形容动词词干',
    ('名詞', '副詞可能'): '名词（也可作副词）', ('名詞', '数詞'): '数词',
    ('動詞', '一般'): '动词', ('動詞', '非自立'): '补助动词',
    ('形容詞', '*'): '形容词', ('形容詞,アウオ段', '*'): '形容词',
    ('形状詞', '*'): '形容词（形容动词）',
    ('助詞', '格助詞'): '格助词', ('助詞', '係助詞'): '主题助词',
    ('助詞', '副助詞'): '副助词', ('助詞', '接続助詞'): '连接助词',
    ('助詞', '並立助詞'): '并列助词', ('助詞', '終助詞'): '句末助词',
    ('助詞', '連体化'): '连体化助词', ('助詞', '格助詞'): '格助词',
    ('助動詞', '*'): '助动词', ('接続詞', '*'): '连词', ('副詞', '*'): '副词',
    ('感動詞', '*'): '感叹词', ('連体詞', '*'): '连体词', ('代名詞', '*'): '代词',
    ('数詞', '*'): '数词', ('接頭辞', '*'): '前缀', ('接尾辞', '*'): '后缀',
    ('記号', '*'): '符号', ('フィラー', '*'): '语气填充词', ('非自立語', '*'): '补助词',
}
# 活用形（动词/形容词的连用形等）
CONJ_CN = {
    '基本形': '基本形', '未然形': '未然形', '連用形': '连用形', '終止形': '终止形',
    '連体形': '连体形', '仮定形': '假定形', '命令形': '命令形',
    '過去形': '过去形', '否定形': '否定形',
}
AUX_CN = {
    '否定': '否定', '過去': '过去', '丁寧': '礼貌', '意志': '意志/提议',
    '可能': '可能', '受身': '被动', '未来': '推测', '未完成': '未完成',
    '命令': '命令',
}


def pos_note(tok):
    p = tok.get('p', ''); d1 = tok.get('d1', '')
    base = POS_CN.get((p, d1)) or POS_CN.get((p, '*'))
    if not base:
        for (pp, dd), cn in POS_CN.items():
            if pp == p and dd == '*':
                base = cn; break
    if not base:
        if p in ('助動詞',): base = '助动词'
        elif p in ('助詞',): base = '助词'
        elif p in ('動詞',): base = '动词'
        elif p in ('名詞',): base = '名词'
        elif p in ('副詞',): base = '副词'
        elif p in ('接続詞',): base = '连词'
        else: base = p or '词'
    ct = tok.get('ct', ''); cf = tok.get('cf', '')
    extra = []
    if p == '助動詞':
        # 特殊/否定 · 特殊/過去 · 特殊/丁寧 …
        seg = [x for x in cf.split('-') if x]
        for s in seg:
            if s in AUX_CN and AUX_CN[s]: extra.append(AUX_CN[s])
        if not extra and '否定' in ct: extra.append('否定')
        if not extra and '過去' in ct: extra.append('过去')
        if not extra and '丁寧' in ct: extra.append('礼貌')
    elif p in ('動詞', '形容詞', '形状詞'):
        for s in cf.split('-'):
            if s in CONJ_CN: extra.append(CONJ_CN[s])
    if extra:
        return base + '（' + '・'.join(dict.fromkeys(extra)) + '）'
    return base


def parts_for(toks, st, en):
    """把 [st,en) 范围内的 token 切成 parts"""
    out = []
    off = 0
    for t in toks:
        s = t['s']; L = len(s)
        if off >= st and off + L <= en:
            out.append({'s': s, 'note': pos_note(t)})
        off += L
    return out


def make_skel(frame):
    """主句骨架公式（用四类括号抽象主句）"""
    if not frame: return '', ''
    pred = [f for f in frame if f.get('b') == '（）']
    subs = [f for f in frame if f.get('b') == '［］']
    objs = [f for f in frame if f.get('b') == '〈〉']
    if not pred:
        return ('〈〜〉', '名词短语') if objs else ('', '')
    parts = []
    for f in subs:
        tail = f['s'][-1] if f.get('s') else ''
        parts.append('［〜' + tail + '］')
    for f in objs:
        s = f.get('s') or ''
        tail = 'が' if s.endswith('が') else ('は' if s.endswith('は') else '')
        parts.append('〈〜' + tail + '〉')
    parts.append('（〜）')
    return '＋'.join(parts), ''


def main():
    toks = json.load(open(os.path.join(HERE, 'tokens_peer.json'), encoding='utf-8'))
    by_text = {}
    for r in toks: by_text.setdefault(r['tag'], []).append(r)
    stats = collections.Counter()
    outdir = os.path.join(HERE, 'peer_enriched')
    os.makedirs(outdir, exist_ok=True)
    for tag, recs in by_text.items():
        d = json.load(open(os.path.join(BASE, tag + '.json'), encoding='utf-8'))
        # 句子按顺序对齐
        assert len(d['sents']) == len(recs), (tag, len(d['sents']), len(recs))
        for s, rec in zip(d['sents'], recs):
            assert s['t'] == rec['t'], tag
            gs = s.get('gs') or {}
            fr = gs.get('frame') or []
            if not fr:
                stats['no_frame'] += 1
                continue
            a = analyze_sentence(rec)
            blocks = []
            for b in fr:
                p = b.get('p')
                if not (p and len(p) == 2):
                    continue
                st, en = p
                try:
                    inner = inner_of(a, s['t'], st, en)
                except Exception:
                    inner = ''
                parts = parts_for(rec['toks'], st, en)
                blocks.append({'s': b['s'], 'inner': inner, 'parts': parts, 'mean': ''})
                stats['blocks'] += 1
                if inner and inner.count('【') + inner.count('[') >= 1:
                    stats['inner_labelled'] += 1
                if len(parts) >= 2:
                    stats['parts_multi'] += 1
            sk, skm = make_skel(fr)
            gs['blocks'] = blocks
            gs['skel'] = sk
            gs['skelMean'] = skm
            gs['chain'] = [f['b'][0] + f['s'] + f['b'][-1] for f in fr]
            gs['whole'] = s.get('zh', '')
            s['gs'] = gs
            stats['sents'] += 1
        # 附上 ruby
        for s, ru in zip(d['sents'], json.load(open(os.path.join(HERE, 'ruby_peer.json'), encoding='utf-8'))[tag]):
            s['ru'] = ru
        json.dump(d, open(os.path.join(outdir, tag + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
        # 谓语细分 + 形态链（喂给渲染层的 window.SHUNP）
        pr = []
        for s, rec in zip(d['sents'], recs):
            a2 = analyze_sentence(rec)
            pr.append({'p': a2.get('p', []), 'mc': a2.get('mc', [])})
            if a2.get('mc'): stats['mc_ok'] += 1
        json.dump(pr, open(os.path.join(outdir, tag + '.pred.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
    print('== 补第二层（blocks/inner/parts/skel/chain）+ ruby ==')
    for k, v in stats.items(): print(f'  {k:18s} {v}')
    if stats['blocks']:
        print(f"  inner 有标注率 {stats['inner_labelled']/stats['blocks']*100:.1f}% ｜ "
              f"parts≥2 的块 {stats['parts_multi']/stats['blocks']*100:.1f}%")

if __name__ == '__main__':
    main()
