#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 analyze.py 的分析树转成「四类括号」大框架 (与 shun/docs/新结构注释设计.md 同构)。

输出 schema (与仓库 gs 一致):
  frame : [ {s, b, role} ]      b ∈ '[]' '［］' '〈〉' '（）'
  chain : [ '［的教育］', ... ]   层层修饰主句
  blocks: [ {s, inner, parts, mean} ]

块级口径 (与仓库 gs 的粒度一致):
  · 每个**成分**(主题/主语/宾语/状语/形式名词) 单独成块 ⇒ 〈〉
  · 连体修饰 单独成块 ⇒ []
  · 从句 单独成块 ⇒ ［］  (角色标签按连接词细分: 原因从句/条件从句/…)
  · 谓语 (动词链) 单独成块 ⇒ （）
  · 连接词/副词/感叹词 单独成块 ⇒ []
  · 整句没有成分可切 ⇒ 〈〉名词短语

判据:
  * 块文本必须逐字来自原句 (子串)
  * 块按出现顺序, **不重叠**、**不合并相邻同类型**
"""
import json, os, sys

HERE = os.environ.get('SHUN_XVAL_DIR') or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze import T, BRACKET

# 成分类型 → (括号, 角色标签)
COMP = {
    T['主题']:   ('〈〉', '主题'),
    T['主语']:   ('〈〉', '主语'),
    T['宾语']:   ('〈〉', '宾语'),
    T['状语']:   ('〈〉', '状语'),
    T['形式名词']: ('〈〉', '形式名词'),
    T['引用内容']: ('〈〉', '引用内容'),
    T['连体修饰']: ('[]', '连体修饰'),
}
PRED_B = '（）'

# 连词 ⇒ 从句小类
CONN_ROLE = {
    'から': '原因从句', 'ので': '原因从句', 'のに': '转折从句', 'ながら': '同时从句',
    'たら': '条件从句', 'ば': '条件从句', 'なら': '条件从句', 'と': '条件从句',
    'けど': '转折从句', 'けれど': '转折从句', 'が': '转折从句', 'し': '并列从句',
    'でも': '让步从句', 'though': '让步从句',
}


def gap_role(a, text, st, en):
    """空隙 (没被任何成分块覆盖的文字) 的角色: 按里面的 token 词性判"""
    import analyze as AZ
    toks = a.get('_toks') or []
    kinds = []
    for t in toks:
        if st <= t['off'] < en:
            kinds.append(t)
    ps = set(t['p'] for t in kinds)
    # 实词优先: 有动词/形容词 ⇒ 这个空隙本身是谓语性成分
    if '動詞' in ps or '形容詞' in ps:
        return '主干'
    if '副詞' in ps:
        return '状语'
    if '感動詞' in ps:
        return '感叹词'
    if '接続詞' in ps:
        return '连接'
    if '名詞' in ps:
        return '主干'
    if '助詞' in ps:
        return '助词'
    return '连接'


def strip_spans(text, lo, hi, spans):
    """去掉 [lo,hi) 内落在 spans 里的字符"""
    keep = []
    for i in range(lo, hi):
        if any(s0 <= i < s1 for s0, s1 in spans):
            continue
        keep.append(text[i])
    out = ''.join(keep).strip()
    out = out.strip('、，,。．.！!？? 　')
    return out


def frame_from_tree(a, text):
    """把树转成 frame 列表 (粒度: 每个成分一块)"""
    nodes = a['nodes']
    frame = []

    def find_roots():
        childs = set()
        for k in range(0, len(nodes), 5):
            cc, fc = nodes[k + 3], nodes[k + 4]
            for c in range(fc, fc + cc):
                childs.add(c)
        rs = [k // 5 for k in range(0, len(nodes), 5) if k // 5 not in childs]
        rs.sort(key=lambda i: nodes[i * 5])
        return rs

    def clause_conn(st, ln):
        for j in range(0, len(a['c']), 4):
            if a['c'][j] == st and a['c'][j + 1] == ln:
                return a['c'][j + 3]
        return ''

    def emit(node_i):
        st, ln, ty, cc, fc = nodes[node_i * 5:node_i * 5 + 5]
        t = text[st:st + ln]
        _st = st
        if ty == T['从句']:
            role = CONN_ROLE.get(clause_conn(st, ln), '从句')
            frame.append({'s': t, 'b': '［］', 'role': role, '_o': st})
            return
        if ty in (T['连接'], T['独立成分']):
            frame.append({'s': t, 'b': '[]', 'role': '连接' if ty == T['连接'] else '独立成分', '_o': st})
            return
        if ty == T['谓语']:
            frame.append({'s': t, 'b': PRED_B, 'role': '谓语', '_o': st})
            return
        if ty in COMP:
            b, role = COMP[ty]
            frame.append({'s': t, 'b': b, 'role': role, '_o': st})
            return
        if ty == T['句体成分']:
            # 句体成分是容器: 它的子节点各自成块 (逐字、不重叠、不合并)
            kids = []
            for c in range(fc, fc + cc):
                s2, l2 = nodes[c * 5], nodes[c * 5 + 1]
                kids.append((s2, s2 + l2, c))
            kids.sort()
            for s2, e2, c in kids:
                emit(c)
            if not kids:
                frame.append({'s': text[st:st + ln].rstrip('。．.!?！？'),
                              'b': '〈〉', 'role': '名词短语', '_o': st})
            return
        for c in range(fc, fc + cc):
            emit(c)

    for r in find_roots():
        emit(r)

    # 整句没有任何成分 (如「277。」) ⇒ 〈〉名词短语
    if not frame:
        frame.append({'s': text.rstrip('。．.!?！？'), 'b': '〈〉', 'role': '名词短语', '_o': 0})
        return frame

    # 🔴 补齐「没被任何块覆盖」的文字 (助词/连接词/叹词等)
    #    ⇒ 块的并集要能拼回原句主干, 否则渲染出来会「漏字」
    covered = []
    for f in frame:
        st = f.get('_o')
        if st is None:
            st = text.find(f['s'])
        if st is not None and st >= 0:
            covered.append((st, st + len(f['s'])))
    covered.sort()
    gaps = []
    pos = 0
    for st, en in covered:
        if st > pos:
            gaps.append((pos, st))
        pos = max(pos, en)
    if pos < len(text):
        gaps.append((pos, len(text)))
    extra = []
    for st, en in gaps:
        seg = text[st:en]
        if not seg.strip():
            continue
        # 纯标点的空隙 ⇒ 忽略
        if all(ch in PUNCT_SET for ch in seg):
            continue
        extra.append({'s': seg, 'b': '[]', 'role': gap_role(a, text, st, en), '_o': st})
    if extra:
        frame = frame + extra
        frame.sort(key=lambda f: f.get('_o') if f.get('_o') is not None else text.find(f['s']))
    return frame


PUNCT_SET = set('、，,。．.!?！？；;：:…・「」『』（）()[]「」 　\n')


def chain_from_frame(frame, text):
    """层层修饰主句: 按出现顺序, 末块是主句谓语"""
    return [f['b'][0] + f['s'] + f['b'][-1] for f in frame]


def inner_of(a, text, st, en):
    """块内骨架: 【主题】【主语】【宾语】【状语】[连体修饰]"""
    nodes = a['nodes']
    out = []

    def walk(i):
        s2, l2, t2, c2, f2 = nodes[i * 5:i * 5 + 5]
        if s2 >= st and s2 + l2 <= en:
            if t2 == T['谓语']:
                out.append((s2, l2, t2))
                return
            if t2 in COMP:
                out.append((s2, l2, t2))
                for c in range(f2, f2 + c2):
                    walk(c)
                return
        for c in range(f2, f2 + c2):
            walk(c)

    for k in range(0, len(nodes), 5):
        walk(k // 5)
    out = sorted(set(out), key=lambda x: (x[0], -x[1]))
    res = []
    last = -1
    for s2, l2, t2 in out:
        if s2 < last:
            continue
        last = s2 + l2
        seg = text[s2:s2 + l2]
        if not seg:
            continue
        if t2 == T['连体修饰']:
            res.append('[' + seg + ']')
        else:
            res.append('【' + seg + '】')
    return ' '.join(res)


def build_fallback(a, text, zh):
    """从规则树生成 gs 兼容结构"""
    frame = frame_from_tree(a, text)
    if not frame:
        return None
    blocks = []
    for f in frame:
        p = f.get('p')
        blocks.append({
            's': f['s'],
            'inner': '',
            'parts': [],
            'mean': '',
        })
    return {
        'frame': frame,
        'skel': '',
        'skelMean': '',
        'chain': chain_from_frame(frame, text),
        'blocks': blocks,
        'whole': zh or '',
        'src': 'rules',
    }


def main():
    toks = json.load(open(os.path.join(HERE, 'tokens.json'), encoding='utf-8'))
    out = []
    for i, x in enumerate(toks):
        t = x['t']
        a = analyze_sentence_cached(x)
        g = build_fallback(a, t, x.get('zh', ''))
        out.append(g)
    json.dump(out, open(os.path.join(HERE, 'fallback.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    n = sum(1 for r in out if r)
    print('fallback 帧:', n, '/', len(out))


def analyze_sentence_cached(x):
    from analyze import analyze_sentence
    return analyze_sentence(x)


if __name__ == '__main__':
    main()
