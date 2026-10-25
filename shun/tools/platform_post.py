#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平台通道结果 → 站点句子（零额度）· v3

三层链条（每层都有实测依据）：
  ① 平台句 ↔ cue 句：用**字符覆盖率**做相似度（不是整句 ratio）
     —— 平台把 cues 的一句并进更长的句时，整句 ratio 被稀释（实测 2 句丢失），
        覆盖率不受稀释影响（"びっくりしますよ。" 被长句 100% 覆盖 ⇒ 1.0）。
  ② cue 句 → ASR 字符区间：cursor 推进定位（实测 52/52 命中，100% 字符覆盖）
  ③ ASR 字符区间 → 词级时间（asr/<uid>.json 的 words[]，精确到 0.16 秒）

⇒ 最终句子以**平台**为轴（切句更准、标点全、平假名规范），时间轴来自词级 ASR。
"""
import json, os, re, sys, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
PUNCT = '、。，．.！!？?；;：:…・「」『』（）()［］[]｛｝<>《》〜~ー-－　 \t\n'
_TRANS = str.maketrans({c: '' for c in PUNCT})
BR = re.compile(r'^[［\[〈（(](.*)[］\]〉）)]$', re.S)


def norm(s):
    return (s or '').translate(_TRANS).lower()


# 括号对：开 → (线上四类, 闭合符)
BR_MAP = {'[': ('[]', ']'), '［': ('［］', '］'), '〈': ('〈〉', '〉'),
          '（': ('（）', '）'), '(': ('（）', ')'), '【': ('【】', '】'), '〔': ('〔〕', '〕')}
BR_CLOSE = {v[1]: v[0] for v in BR_MAP.values()}


def strip_brackets(s):
    """返回 (内容, 括号类或 None)

    🔴 平台输出的括号有三种怪形态，实测都踩过：
      ① 完整一对：「〈しゅんと日本語ポッドキャスト〉」
      ② **半开口**：「［旅行を」+「始める］」（一个括号跨两块）
      ③ **嵌套**：「〈［罪がない人が殺される］のをみるのは〉」（双层）
    ⇒ 递归剥到没有括号为止；括号类取**最外层**（与平台给的 b 一致）。

    ⚠️ 剥完仍不是原句逐字子串的块 ⇒ 丢（内容与原文不符，硬定位会错位）。
       例：平台把「のを見る」写成「のをみる」⇒ 同义替换，本质是标注错误。
    """
    s = s or ''
    outer = None
    for _ in range(6):                       # 最多剥 6 层，防御异常嵌套
        if not s:
            break
        m = BR.match(s)
        if m:
            if outer is None:
                outer = BR_MAP.get(m.group(0)[0], (None, None))[0]
            s = m.group(1)
            continue
        if s[0] in BR_MAP:
            if outer is None:
                outer = BR_MAP[s[0]][0]
            s = s[1:]
            continue
        if s[-1] in BR_CLOSE:
            if outer is None:
                outer = BR_CLOSE[s[-1]]
            s = s[:-1]
            continue
        break
    # 🔴 剥完必须 strip：平台会写成「［ めっちゃ面白いと ］」⇒ 剥完是「 めっちゃ面白いと 」
    #    带前后空格 ⇒ 不是原句子串 ⇒ 整块被丢（实测 ep304 一集丢 50 块）
    return s.strip(), outer


# 🔴 平台有时把日文按字/词加空格（实测 ep304 整集：「私 も フリーランス に なった 時 は」）
#    ⇒ 删掉「日文/假名/标点之间」的空格，保留 ASCII 词之间的空格（英文要分词）
def _jp(ch):
    return ('\u3040' <= ch <= '\u30ff') or ('\u4e00' <= ch <= '\u9fff') or ch in '、。，．.!?？：；…・「」『』'


def despace(s):
    if not s:
        return s
    out = []
    for i, ch in enumerate(s):
        if ch == ' ':
            prev = s[i - 1] if i > 0 else ''
            nxt = s[i + 1] if i + 1 < len(s) else ''
            if _jp(prev) and _jp(nxt):
                continue
        out.append(ch)
    return ''.join(out)


# 🔴 平台有时写 '〈方〉' / '［方］' / '（方）' ⇒ 归一化到线上四类
B_NORM = {'〈方〉': '〈〉', '［方］': '［］', '（方）': '（）', '[方]': '[]',
          '〈〉': '〈〉', '［］': '［］', '（）': '（）', '[]': '[]'}


def load_words(asr_path):
    d = json.load(open(asr_path, encoding='utf-8'))
    words = []
    pos = 0
    for seg in d.get('segments') or []:
        for w in (seg.get('words') or []):
            t = w.get('w') or ''
            if not t:
                continue
            words.append({'w': t, 's': w['s'], 'e': w['e'], 'c0': pos, 'c1': pos + len(t)})
            pos += len(t)
    return words


def cov(a, b):
    """a 被 b 覆盖的字符比例（多重集交集）"""
    if not a:
        return 0.0
    ca = collections.Counter(a)
    cb = collections.Counter(b)
    inter = sum(min(v, cb.get(k, 0)) for k, v in ca.items())
    return inter / len(a)


def score(cue, plat):
    """双向覆盖率取大者"""
    if not cue or not plat:
        return 0.0
    return max(cov(cue, plat), cov(plat, cue))


def align(cue_texts, plat_texts):
    """单调 DP：cue 句为主轴，平台句可多归一句(1:n)，cue 句也可多归一句(n:1)"""
    n, m = len(cue_texts), len(plat_texts)
    C = [norm(t) for t in cue_texts]
    P = [norm(t) for t in plat_texts]
    S = [[score(C[i], P[j]) for j in range(m)] for i in range(n)]
    NEG = -1e9
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i][j]
            if cur <= NEG / 2:
                continue
            if i < n and j < m:                                   # 1:1
                v = cur + S[i][j]
                if v > dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = v
                    back[i + 1][j + 1] = (i, j, '11')
                # 1:n  platform j..j2 全归 cue[i]
                for j2 in range(j + 1, m):
                    v2 = cur + cov(C[i], ''.join(P[j:j2 + 1]))
                    if v2 > dp[i + 1][j2 + 1]:
                        dp[i + 1][j2 + 1] = v2
                        back[i + 1][j2 + 1] = (i, j, '1n')
            if i < n and j < m:                                   # n:1  cue i..i2 全归 platform[j]
                for i2 in range(i + 1, n):
                    v = cur + cov(P[j], ''.join(C[i:i2 + 1]))
                    if v > dp[i2 + 1][j + 1]:
                        dp[i2 + 1][j + 1] = v
                        back[i2 + 1][j + 1] = (i, j, 'n1')
            if j < m:                                             # 平台句丢弃
                v = cur
                if v > dp[i][j + 1]:
                    dp[i][j + 1] = v
                    back[i][j + 1] = (i, j, 'skipP')
            if i < n:                                             # cue 句丢弃
                v = cur
                if v > dp[i + 1][j]:
                    dp[i + 1][j] = v
                    back[i + 1][j] = (i, j, 'skipC')
    pairs = collections.defaultdict(list)
    i, j = n, m
    while i > 0 or j > 0:
        b = back[i][j]
        if b is None:
            break
        pi, pj, kind = b
        if kind == '11':
            pairs[pi].append(pj)
        elif kind == '1n':
            pairs[pi].extend(range(pj, j))
        elif kind == 'n1':
            for k in range(pi, i):
                pairs[k].append(pj)
        i, j = pi, pj
    return pairs


def loc_blocks(ja, blocks):
    """⚠️ 顺序不能反：先 despace 句正文，再定位块 —— p 是字符位置，改长度就全错"""
    ja = despace(ja)
    out = []
    cur = 0
    for b in blocks:
        raw = despace(b.get('s', ''))
        s, bfrom_s = strip_brackets(raw)
        if not s:
            continue
        idx = ja.find(s, cur)
        if idx < 0:
            idx = ja.find(s)
            if idx < 0:
                continue
        # 🔴 s 里带的括号比 b 字段权威（b 与 s 矛盾时以 s 为准）
        bb = bfrom_s or B_NORM.get(b.get('b') or '', b.get('b') or '[]')
        out.append({'s': s, 'b': bb, 'role': b.get('role') or '',
                    'p': [idx, idx + len(s)]})
        cur = idx + len(s)
    return out


def load_platform(path):
    raw = json.load(open(path, encoding='utf-8'))
    if isinstance(raw, list):
        return raw
    desc = raw['results'][0]['description']
    m = re.search(r'```json\s*(.*?)\s*```', desc, re.S) or re.search(r'(\[.*\])', desc, re.S)
    return json.loads(m.group(1))


def build(r):
    plat = load_platform(os.path.join(ROOT, 'platform_out', r['tag'] + '.json'))
    words = load_words(os.path.join(ROOT, r['asr']))
    cues = json.load(open(os.path.join(ROOT, r['cues']), encoding='utf-8'))['cues']

    # ② cue 句 → ASR 字符区间（cursor 推进）
    asr_full = ''.join(w['w'] for w in words)
    cue_asr = []
    cur = 0
    miss = 0
    for c in cues:
        idx = asr_full.find(c['t'], cur)
        if idx < 0:
            idx = asr_full.find(c['t'])
            if idx < 0:
                miss += 1
                idx = cur
        cue_asr.append((idx, idx + len(c['t'])))
        cur = idx + len(c['t'])

    # ① 平台句 ↔ cue 句
    pairs = align([c['t'] for c in cues], [p.get('ja', '') for p in plat])
    # 反向：每个平台句归属哪些 cue 句
    plat2cue = collections.defaultdict(list)
    for ci, js in pairs.items():
        for j in js:
            plat2cue[j].append(ci)

    def span_of(cue_ids):
        los = [cue_asr[i][0] for i in cue_ids]
        his = [cue_asr[i][1] for i in cue_ids]
        return min(los), max(his)

    def time_of(a_lo, a_hi):
        lo = hi = None
        for w in words:
            if w['c1'] <= a_lo:
                continue
            if w['c0'] >= a_hi:
                break
            if lo is None:
                lo = w['s']
            hi = w['e']
        return (lo, hi) if lo is not None else (None, None)

    sents = []
    st = collections.Counter()
    prev_e = 0.0
    for j, p in enumerate(plat):
        ja = despace(p.get('ja', '') or '')
        zh = (p.get('zh') or '').strip()
        cids = sorted(plat2cue.get(j) or [])
        if cids:
            a_lo, a_hi = span_of(cids)
            s, e = time_of(a_lo, a_hi)
            st['timed'] += 1
        else:
            s = e = None
            st['no_time'] += 1
        if s is None:
            s, e = prev_e, prev_e
        prev_e = max(prev_e, e or 0.0)
        blocks = loc_blocks(ja, p.get('blocks') or [])
        st['blocks'] += len(blocks)
        st['blocks_dropped'] += len(p.get('blocks') or []) - len(blocks)
        chain = [f['b'][0] + f['s'] + f['b'][-1] for f in blocks]
        sents.append({
            't': ja, 'zh': zh, 'en': '', 'kind': 'speech',
            's': round(s, 2), 'e': round(e, 2),
            'gs': {
                'frame': blocks,
                'blocks': [{'s': f['s'], 'inner': '', 'parts': [], 'mean': ''} for f in blocks],
                'chain': chain, 'skel': '', 'skelMean': '', 'whole': zh, 'src': 'llm',
            },
        })
    # 🔴 时间轴单调化 + 按时间排序
    #    （对齐后可能出现区间交叠/倒序 ⇒ 渲染层按数组下标找「当前句」会跳错位置）
    sents.sort(key=lambda x: (x['s'], x['e']))
    for i in range(len(sents) - 1):
        cur, nxt = sents[i], sents[i + 1]
        if cur['e'] > nxt['s']:
            cur['e'] = nxt['s']
            st['e_clamped'] += 1
        if cur['e'] < cur['s']:
            cur['e'] = nxt['s']
            st['e_fixed'] += 1
    st['cue_locate_miss'] = miss
    st['cue_unmatched'] = sum(1 for i in range(len(cues)) if not pairs.get(i))
    return sents, st


def main():
    rows = json.load(open(os.path.join(ROOT, 'new-episodes.json'), encoding='utf-8'))
    outdir = os.path.join(ROOT, 'data-new')
    os.makedirs(outdir, exist_ok=True)
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    allst = collections.Counter()
    for r in rows:
        if only and r['tag'] not in only:
            continue
        pp = os.path.join(ROOT, 'platform_out', r['tag'] + '.json')
        if not os.path.exists(pp):
            continue
        sents, st = build(r)
        allst.update(st)
        json.dump({'tag': r['tag'], 'sents': sents},
                  open(os.path.join(outdir, r['tag'] + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
        print('%-8s %3d句  %s' % (r['tag'], len(sents), dict(st)))
    print('== 合计 ==', dict(allst))


if __name__ == '__main__':
    main()
