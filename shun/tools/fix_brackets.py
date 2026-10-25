#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据层收尾修正（直接改 data-new，不重跑对齐）

① **b 值半角化**：平台有时输出 `<>` / `()` ⇒ 渲染层按 `b.charAt(0)` 分类
   ⇒ 半角会落到错误的样式类 ⇒ 四类区分失效（实测 126 块）
   ⇒ 归一化到线上四类。
② **时间轴超出音频**：MiniMax 词级 ASR 的时间戳会超出音频末尾
   （实测 ep041 的广告句给到 632s，而音频只有 609.36s）⇒ 截断到真实音频时长。
   真实时长用 ffprobe 读本地 mp3（tagmap 的 dur 与 ffprobe 实测一致，取更准的）。
"""
import json, os, subprocess, sys, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
B_FIX = {'<>': '〈〉', '()': '（）', '<方>': '〈〉', '（方）': '（）',
         '[方]': '[]', '［方］': '［］', '〈方〉': '〈〉', '[]': '[]',
         '［］': '［］', '〈〉': '〈〉', '（）': '（）'}
# 🔴 平台还会输出带空格的括号（'[ ]' / '〈 〉'）⇒ 先去掉空格再匹配
PUNCT_TAIL = '、。，．.!?！？'


def real_dur(tag):
    p = os.path.join(ROOT, 'audio', tag + '.mp3')
    if not os.path.exists(p):
        return None
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', p], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception:
        return None


def main():
    rp = os.path.join(ROOT, 'new-episodes.json')
    rows = json.load(open(rp, encoding='utf-8'))
    st = collections.Counter()
    for r in rows:
        # 🔴 tagmap 的 dur 对某些集不准（实测 ep096 标 398 实际 428.46、ep211 标 399 实际 491.40）
        #    ⇒ 用 ffprobe 实测值覆盖（播放条总时长直接用它）
        rd = real_dur(r['tag'])
        if rd and abs(rd - float(r.get('dur') or 0)) > 1.0:
            st['dur_corrected'] += 1
            r['dur'] = round(rd, 2)
        p = os.path.join(ROOT, 'data-new', r['tag'] + '.json')
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        dur = real_dur(r['tag']) or float(r.get('dur') or 0)
        for s in d['sents']:
            gs = s.get('gs') or {}
            fr = gs.get('frame') or []
            for f in fr:
                b = f.get('b')
                bn = (b or '').replace(' ', '')
                if bn in B_FIX and B_FIX[bn] != b:
                    f['b'] = B_FIX[bn]
                    st['b_fixed'] += 1
            # chain 用的是 b 的首尾字符 ⇒ 跟着改
            if fr:
                gs['chain'] = [f['b'][0] + f['s'] + f['b'][-1] for f in fr]
            e = float(s.get('e') or 0)
            if dur and e > dur:
                s['e'] = round(dur, 2)
                st['e_clipped'] += 1
            if float(s.get('s') or 0) > dur:
                s['s'] = round(dur, 2)
                st['s_clipped'] += 1
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    json.dump(rows, open(rp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('修正:', dict(st))


if __name__ == '__main__':
    main()
