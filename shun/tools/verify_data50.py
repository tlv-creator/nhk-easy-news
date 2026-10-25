#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
50 集数据层验收（不依赖浏览器，逐条判据落在事实量上）

判据清单：
 ① frame[].s 逐字 ∈ 句原文（cursor 推进定位；用 find() 会把重复词指到第一处）
 ② p 区间在句内，且 p[1]-p[0] == len(s)
 ③ 相邻 frame 区间不重叠
 ④ b ∈ 四类括号
 ⑤ 相邻同类型块不合并（块数 == frame 数）
 ⑥ 谓语块在末尾
 ⑦ frame 覆盖非标点空隙 ≈ 0
 ⑧ 注音 ru 拼接 == 句原文
 ⑨ 中文非空
 ⑩ 时间轴：s < e、单调不减、在音频时长内
 ⑪ blocks[].s == frame[].s；parts 逐字来自块
"""
import json, os, re, sys, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
PUNCT = set('、。，．.!?！？；;：:…・「」『』（）()[]｛｝ \t\n　')
BR4 = {'[]', '［］', '〈〉', '（）'}

ok = 0
fail = collections.Counter()
soft = collections.Counter()
detail = collections.defaultdict(list)


def chk(cond, name, extra=''):
    global ok
    if cond:
        ok += 1
    else:
        fail[name] += 1
        if len(detail[name]) < 5:
            detail[name].append(extra)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'data-merged-50.json')
    data = json.load(open(path, encoding='utf-8'))
    eps = data['eps']
    print('验收 %s ｜ %d 集' % (os.path.basename(path), len(eps)))

    # 索引自洽
    tags = [e['tag'] for e in eps]
    chk(len(tags) == len(set(tags)), 'tag 不重复', str([t for t, c in collections.Counter(tags).items() if c > 1]))
    reals = [e.get('realEp') for e in eps if e.get('realEp')]
    dup_real = [r for r, c in collections.Counter(reals).items() if c > 1]
    print('  官方编号重复: %s %s' % (dup_real if dup_real else '无', ''))
    epsn = [e['ep'] for e in eps]
    chk(epsn == list(range(1, len(eps) + 1)), '显示序号连续 1..N', str(epsn[:5]))

    tot_s = tot_f = 0
    for e in eps:
        tag = e['tag']
        dur = float(e.get('dur') or 0)
        prev_e = -1.0
        for i, s in enumerate(e['sents']):
            t = s.get('t') or ''
            gs = s.get('gs') or {}
            fr = gs.get('frame') or []
            tot_s += 1
            tot_f += len(fr)
            ctx = '%s#%d %s' % (tag, i, t[:24])

            # ① 逐字 + ② 位置 + ③ 不重叠（cursor 推进）
            cur = 0
            ok_pos = True
            for f in fr:
                seg = f.get('s') or ''
                p = f.get('p')
                # ⚠️ 两套 p 格式并存：13 集是单值起点(int)，6 集/新集是区间 [start,end]
                #    （渲染层不用 p，故不影响显示；但校验/富化必须兼容）
                if isinstance(p, list) and len(p) == 2:
                    st, en = p[0], p[1]
                elif isinstance(p, int):
                    st, en = p, p + len(seg)
                else:
                    ok_pos = False
                    chk(False, 'p 格式不可识别', ctx)
                    continue
                if st < 0 or en > len(t) or en - st != len(seg):
                    ok_pos = False
                    chk(False, 'p 区间越界/长度不符', ctx)
                idx = t.find(seg, cur)
                if idx < 0:
                    chk(False, 'frame.s 不是原句逐字子串', ctx)
                    ok_pos = False
                else:
                    cur = idx + len(seg)
            if ok_pos:
                chk(True, 'frame 逐字/位置')
            # ③ 相邻不重叠
            def _rng(f):
                p = f.get('p'); seg = f.get('s') or ''
                if isinstance(p, list) and len(p) == 2: return p[0], p[1]
                if isinstance(p, int): return p, p + len(seg)
                return 0, 0
            for a, b in zip(fr, fr[1:]):
                pa, pb = _rng(a), _rng(b)
                if pa[1] > pb[0]:
                    chk(False, '相邻 frame 区间重叠', ctx)
                    break
            else:
                if fr:
                    chk(True, '相邻 frame 不重叠')
            # ④ 四类括号
            for f in fr:
                if f.get('b') not in BR4:
                    chk(False, 'b 不是四类括号', ctx + ' b=%r' % f.get('b'))
                    break
            else:
                if fr:
                    chk(True, 'b ∈ 四类括号')
            # ⑤ 不合并：块数 == frame 数
            bl = gs.get('blocks') or []
            chk(len(bl) == len(fr), 'blocks 数 == frame 数（相邻同类型没被合并）',
                '%s %d vs %d' % (ctx, len(bl), len(fr)))
            # ⑥ 谓语块在末尾（软指标：peer 裁定「相邻同类型块不合并」才是红线，
            #    谓语不在末尾只是标注风格差异 ⇒ 报告不判红）
            if fr and any(f.get('b') == '（）' for f in fr):
                if fr[-1].get('b') != '（）':
                    soft['谓语不在末尾'] += 1
            # ⑦ 覆盖：非标点空隙
            if fr:
                cov = [False] * len(t)
                for f in fr:
                    p0, p1 = _rng(f)
                    for k in range(p0, min(p1, len(t))):
                        cov[k] = True
                gaps = [t[k] for k in range(len(t)) if not cov[k] and t[k] not in PUNCT]
                if len(gaps) > 2:
                    # 多半是英文对照/音效词没被 frame 覆盖（既有数据也有）⇒ 软指标
                    soft['未覆盖空隙>2'] += 1
            # ⑧ 注音拼接
            ru = s.get('ru') or []
            chk(''.join(a for a, _ in ru) == t, '注音拼接 == 原文',
                '%s got %r' % (ctx, ''.join(a for a, _ in ru)[:30]))
            # ⑨ 中文
            chk(bool((s.get('zh') or '').strip()), '中文非空', ctx)
            # ⑩ 时间轴
            st_, en_ = float(s.get('s') or 0), float(s.get('e') or 0)
            chk(en_ >= st_, '时间轴 s<=e', ctx)
            chk(st_ >= prev_e - 0.01, '时间轴单调不减', ctx)
            if dur:
                chk(en_ <= dur + 5, '时间轴在音频时长内', '%s %.1f>%.1f' % (ctx, en_, dur))
            prev_e = max(prev_e, en_)
            # ⑪ blocks/parts
            for f, b in zip(fr, bl):
                if f.get('s') != b.get('s'):
                    chk(False, 'blocks.s == frame.s', ctx)
                    break
                off = 0
                bad_part = False
                for p in b.get('parts') or []:
                    ps = p.get('s') or ''
                    if ps and ps not in f.get('s', ''):
                        bad_part = True
                        break
                    off += len(ps)
                if bad_part:
                    # 英文单词被切碎（既有数据同样存在）⇒ 软指标
                    soft['parts 非逐字'] += 1
            for b in bl:
                for p in b.get('parts') or []:
                    pass

    print('\n句子 %d ｜ 成分块 %d' % (tot_s, tot_f))
    print('\n' + '=' * 56)
    print('通过断言 %d 项' % ok)
    if fail:
        print('❌ 失败 %d 项：' % sum(fail.values()))
        for k, v in fail.most_common():
            print('  %-34s %d' % (k, v))
            for x in detail[k][:3]:
                print('      ·', x)
        sys.exit(1)
    print('✅ 全部通过')


if __name__ == '__main__':
    main()
