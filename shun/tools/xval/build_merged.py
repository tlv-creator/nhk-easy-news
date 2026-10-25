#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并版构建：13 集（用户旧文件那行 SHUN，逐字不动）+ 6 集（peer 推的 s001..s006）
  * 13 集：SHUN 行**逐字保留**（sha256 可验）→ 真实集号用 EPREAL 映射表
  * 6 集：走独立 script（window.SHUN2）
  * 排序：按**真实集号**（Ep1-6 → Ep266-278）⇒ 难度梯度
  * SHUNG / SHUNP：按**排序后的顺序**拼（app 的 BASE 也是按排序后的 EPS 算）
"""
import hashlib, json, os, re, sys

HERE = '/workspace/tools/shun'
OLD = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html'
BASE_HTML = '/workspace/attachments/2c146f4d301de83e/1.html'
PEER = '/workspace/tools/shun/repo3/nhk-easy-news-2ea927d3/shun-web/data'
OUT = os.path.join(HERE, 'shun-grammar-merged.html')
TAGS6 = ['s001', 's002', 's003', 's004', 's005', 's006']

# ---------- 1. 13 集：逐字取 SHUN 行 ----------
old = open(OLD, encoding='utf-8').read()
shun_line = next(l for l in old.split('\n')
                 if l.startswith('<script>window.SHUN=') and l.endswith(';</script>'))
payload = json.loads(re.match(r'^<script>window\.SHUN=(.*);</script>$', shun_line).group(1))
eps13 = payload['eps']
sha_line = hashlib.sha256(shun_line.encode('utf-8')).hexdigest()
print('13 集 SHUN 行：', len(shun_line), '字符 ｜ sha256', sha_line[:16])

# 真实集号：从 desc 里的 "EpNN" 解析（不手填 ⇒ 可复核）
EPREAL = {}
for e in eps13:
    m = re.search(r'Ep(\d+)', e.get('desc') or '')
    if not m:
        raise SystemExit('解析不到真实集号: ' + e['tag'])
    EPREAL[e['tag']] = int(m.group(1))
print('13 集真实集号:', json.dumps(EPREAL, ensure_ascii=False))

# ---------- 2. 6 集 ----------
eps6 = []
for tag in TAGS6:
    d = json.load(open(os.path.join(PEER, tag + '.json'), encoding='utf-8'))
    eps6.append(d)

# ---------- 2.1 时间轴单调化（peer 实测 s001 16 处 / s006 9 处交叠）----------
# 🔴 13 集 0 处交叠；6 集有 ⇒ 个别句子的 e 偏大，盖住了后面几句
#    ⇒ 修法（零额度、只动 s/e，不动 zh/gs/ru 一个字）：
#       e = min(e, 下一句.s)；若 e <= s 则 e = 下一句.s
def overlaps(sents):
    n = 0
    for i in range(len(sents) - 1):
        a, b = sents[i].get('e'), sents[i + 1].get('s')
        if a is not None and b is not None and a > b:
            n += 1
    return n

def monotonicize(sents):
    fixed = 0
    for i in range(len(sents) - 1):
        cur, nxt = sents[i], sents[i + 1]
        if cur.get('e') is None or nxt.get('s') is None:
            continue
        if cur['e'] > nxt['s']:
            old = cur['e']
            cur['e'] = nxt['s']
            if cur['e'] <= cur.get('s', 0):
                cur['e'] = nxt['s']
            fixed += 1
    return fixed

for d in eps6:
    before = overlaps(d['sents'])
    fixed = monotonicize(d['sents'])
    after = overlaps(d['sents'])
    if before:
        print(f"  {d['tag']}: 交叠 {before} → {after}（改了 {fixed} 句的 e；zh/gs/ru 未动）")
    assert after == 0, (d['tag'], after)
print('6 集:', len(eps6), '集', sum(len(e['sents']) for e in eps6), '句')

# ---------- 2.5 补 13 集的词级拆解（规则引擎产的块 parts 为空） ----------
from analyze import analyze_sentence
from frame import inner_of
sys.path.insert(0, HERE)
from gen_blocks import pos_note

TK13 = json.load(open(os.path.join(HERE, 'tokens.json'), encoding='utf-8'))
AN13_MAP = {i: a for i, a in enumerate(json.load(open(os.path.join(HERE, 'analysis.json'), encoding='utf-8')))}
stats = {'parts_filled': 0, 'blocks6_filled': 0}

def parts_for(toks, st, en):
    out = []; off = 0
    for t in toks:
        L = len(t['s'])
        if off >= st and off + L <= en:
            out.append({'s': t['s'], 'note': pos_note(t)})
        off += L
    return out

def fill_parts(o, gi):
    tk = TK13[gi]; a = AN13_MAP[gi]
    txt = tk['t']
    # 块的 p 区间：gs_final 里没有 p ⇒ 用「按出现顺序推进定位」重建
    cur = 0; n = 0
    for b in o['blocks']:
        s = b.get('s') or ''
        idx = txt.find(s, cur)
        if idx < 0:
            b['inner'] = b.get('inner') or ''
            continue
        cur = idx + len(s)
        st, en = idx, idx + len(s)
        if not b.get('inner'):
            try: b['inner'] = inner_of(a, txt, st, en)
            except Exception: b['inner'] = ''
        if not b.get('parts'):
            b['parts'] = parts_for(tk['toks'], st, en); n += 1
    return n

# ---------- 3. 排序（真实集号） ----------
def ep_real(e):
    if e.get('epReal') is not None:
        return e['epReal']
    return EPREAL[e['tag']]
order = sorted(eps13 + eps6, key=ep_real)
print('合并后集序（真实集号）:', [ep_real(e) for e in order])

# ---------- 4. SHUNG / SHUNP（按排序后的顺序） ----------
GS13 = json.load(open(os.path.join(HERE, 'gs_final.json'), encoding='utf-8'))
AN13 = json.load(open(os.path.join(HERE, 'analysis.json'), encoding='utf-8'))
assert len(GS13) == len(AN13) == sum(len(e['sents']) for e in eps13) == 1154

# 13 集在原顺序里的切片起点
starts13 = {}
off = 0
for e in eps13:
    starts13[e['tag']] = off
    off += len(e['sents'])

# 6 集的 tokens（给 gen_blocks 用）
TK6 = json.load(open(os.path.join(HERE, 'tokens_peer2.json'), encoding='utf-8'))
tmap6 = {(r['tag'], r['t']): r for r in TK6}
a6 = None

gs_out, pd_out = [], []
for e in order:
    if e['tag'] in starts13:
        st = starts13[e['tag']]
        n = len(e['sents'])
        for i in range(st, st + n):
            o = GS13[i]
            # 🔴 规则引擎产的块没有词级拆解（parts 为空）⇒ 用规则引擎补 inner/parts
            if o.get('blocks') and not any(b.get('parts') for b in o['blocks']):
                filled = fill_parts(o, i)
                stats['parts_filled'] += filled
            g = {}
            if o.get('frame'):
                g['frame'] = [{'s': f['s'], 'b': f['b'], 'role': f.get('role', '')} for f in o['frame']]
            for k in ('skel', 'skelMean', 'chain', 'blocks', 'whole'):
                if o.get(k):
                    g[k] = o[k]
            g['src'] = o.get('src', 'rules')
            gs_out.append(g)
            a = AN13[i]
            p = a['p']
            chain = [[p[k], p[k + 1], p[k + 2], p[k + 3]] for k in range(0, len(p), 4)]
            pd_out.append({'p': chain, 'mc': a.get('mc', []), 'k': a['k']})
    else:
        for s in e['sents']:
            gs = s.get('gs') or {}
            # 🔴 peer 的 s002..s006 的 gs.blocks 是空的（只有 s001 有）
            #    ⇒ 用规则引擎补 inner/parts（frame/skel/chain/whole 保持他们的）
            if not (gs.get('blocks') or []) and (gs.get('frame') or []):
                rec0 = tmap6.get((e['tag'], s.get('t') or ''))
                a6 = analyze_sentence(rec0) if rec0 else None
                blocks6 = []
                for b in gs['frame']:
                    ss = b.get('s') or ''
                    idx = (s.get('t') or '').find(ss)
                    if idx < 0:
                        blocks6.append({'s': ss, 'inner': '', 'parts': [], 'mean': ''}); continue
                    st, en = idx, idx + len(ss)
                    try: inner = inner_of(a6, s['t'], st, en)
                    except Exception: inner = ''
                    rec = tmap6.get((e['tag'], s.get('t') or ''))
                    pp = parts_for(rec['toks'], st, en) if rec else []
                    blocks6.append({'s': ss, 'inner': inner, 'parts': pp, 'mean': ''})
                gs['blocks'] = blocks6
                stats['blocks6_filled'] += len(blocks6)
            g = {}
            if gs.get('frame'):
                g['frame'] = [{'s': f['s'], 'b': f['b'], 'role': f.get('role', '')} for f in gs['frame']]
            for k in ('skel', 'skelMean', 'chain', 'blocks', 'whole'):
                if gs.get(k):
                    g[k] = gs[k]
            g['src'] = gs.get('src', 'peer')
            gs_out.append(g)
            # 谓语细分/形态链：peer 的 6 集没有 → 用我的规则引擎补
            pd_out.append({'p': [], 'mc': [], 'k': ''})
print('SHUNG', len(gs_out), '｜ SHUNP', len(pd_out))

# 6 集的谓语细分：用我的引擎补（零额度）
need = [(i, e) for i, e in enumerate(order) if e['tag'] not in starts13]
if need:
    import subprocess
    # 用 extract_generic + gen_blocks 的能力：这里直接调 analyze（需要 tokens）
    tk = json.load(open(os.path.join(HERE, 'tokens_peer2.json'), encoding='utf-8'))
    tmap = {(r['tag'], r['t']): r for r in tk}
    sys.path.insert(0, HERE)
    from analyze import analyze_sentence
    filled = 0
    for i, e in need:
        for j, s in enumerate(e['sents']):
            rec = tmap.get((e['tag'], s.get('t') or ''))
            if not rec:
                continue
            a = analyze_sentence(rec)
            p = a['p']
            chain = [[p[k], p[k + 1], p[k + 2], p[k + 3]] for k in range(0, len(p), 4)]
            pd_out[i * 0 + sum(len(order[k]['sents']) for k in range(i)) + j] = \
                {'p': chain, 'mc': a.get('mc', []), 'k': a['k']}
            filled += 1
    print('6 集谓语细分补齐:', filled)

# ---------- 5. 组装 ----------
base = open(BASE_HTML, encoding='utf-8').read()
ph = '<script>window.SHUN={"eps":[...]};</script>'
assert ph in base
out = base.replace(ph, shun_line)          # ← 13 集那行，逐字不动

inject = ('<script>window.SHUN2=' + json.dumps({'eps': eps6}, ensure_ascii=False, separators=(',', ':')) +
          ';window.EPREAL=' + json.dumps(EPREAL, ensure_ascii=False, separators=(',', ':')) +
          ';window.SHUNG=' + json.dumps(gs_out, ensure_ascii=False, separators=(',', ':')) +
          ';window.SHUNP=' + json.dumps(pd_out, ensure_ascii=False, separators=(',', ':')) + ';</script>\n')
out = out.replace('<script>\n(function(){', inject + '<script>\n(function(){', 1)

css = open(os.path.join(HERE, 'style.css'), encoding='utf-8').read()
a = out.index('<style>') + len('<style>'); b = out.index('</style>')
out = out[:a] + css + out[b:]

sheet_html = '''
<div id="sheet">
  <div id="shHead"></div>
  <div id="shBody"></div>
  <div style="padding:0 14px 12px;flex:0 0 auto">
    <button id="shClose">关闭</button>
  </div>
</div>

'''
assert '<div id="toast"></div>' in out
out = out.replace('<div id="toast"></div>', sheet_html + '<div id="toast"></div>', 1)

js = open(os.path.join(HERE, 'app_merged.js'), encoding='utf-8').read()
tag = -1
for _ in range(3):
    tag = out.index('<script>', tag + 8)
js_start = tag; js_end = out.index('</script>', js_start)
assert '(function(){' in out[js_start:js_start + 60]
out = out[:js_start] + '<script>\n' + js + out[js_end:]

open(OUT, 'w', encoding='utf-8').write(out)
print('补 parts 的块数:', stats['parts_filled'], '｜ 6 集补 blocks:', stats.get('blocks6_filled',0))
print('写出', OUT, os.path.getsize(OUT), 'bytes')

# 自检：13 集 SHUN 行在产物里仍然逐字一致
h = open(OUT, encoding='utf-8').read()
m = re.search(r'<script>window\.SHUN=.*?</script>', h, re.S)
got = m.group(0)
print('产物里 13 集 SHUN 行 sha256:', hashlib.sha256(got.encode('utf-8')).hexdigest()[:16],
      '｜ 与源一致:', got == shun_line)
