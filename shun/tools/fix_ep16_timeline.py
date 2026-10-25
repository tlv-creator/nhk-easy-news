#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用平台通道（0 订阅额度）拿 Ep1 / Ep6 的**精确时间轴**，修掉 s001/s006 被截断的时长。

为什么要修：fix_timeline.py 只把 e 截断到下一句的 s ⇒ 消除了交叠，
但被截断的那几句**时长仍然偏长**（s001 #11 截断后 23.4 秒，实际可能只有 2 秒）。
⇒ 用平台通道重跑要一份带时间戳的转写，把时间轴补准。

流程：上传音频 → 平台转写（要求时间戳）→ 按文本对齐到现有句子 → 替换 s/e
🔴 验收：时间跨度必须接近音频时长；不接近就**不采用**（宁可保留截断值）。
"""
import json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_post as PP

ROOT = os.path.dirname(os.path.abspath(__file__))

PROMPT = """请对这段日语播客做两件事：
1) 严格逐字转写：按听到的句子切分。日文保持原样（保留原有标点），英文保持原样，不要添加原文没有的词，不要翻译、不要改写、不要补充。
2) 为每一句给出它在音频中的**起止秒数**（start / end，单位秒，精确到 0.1 秒，start 小于 end）。

输出为 JSON 数组，每项形如：
{"ja":"日文原文","start":12.3,"end":15.8}"""


def sh(args, timeout=1200):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_one(tag):
    mp3 = os.path.join(ROOT, 'audio', tag + '.mp3')
    rc, out, err = sh(['mcode-tools', 'upload_temp_url', mp3])
    url = json.loads(out)['temp_url']
    af = os.path.join(ROOT, 'platform_out', '_args_tl.json')
    json.dump({'audio_info': [{'url': url, 'mime_type': 'audio/mpeg', 'prompt': PROMPT}]},
              open(af, 'w', encoding='utf-8'), ensure_ascii=False)
    rc, out, err = sh(['mcode-tools', 'connector', 'call',
                        'connector__matrix__audios_understand', '--args-file', af])
    raw = json.loads(out)
    desc = raw['results'][0]['description']
    m = re.search(r'```json\s*(.*?)\s*```', desc, re.S) or re.search(r'(\[.*\])', desc, re.S)
    return json.loads(m.group(1))


def main():
    data = json.load(open(os.path.join(ROOT, 'data-merged.json'), encoding='utf-8'))
    bytag = {e['tag']: e for e in data['eps']}
    pairs_map = {'ep001': 's001', 'ep006': 's006'}     # 交接包 tag → 站点 tag
    for src_tag, dst_tag in pairs_map.items():
        if dst_tag not in bytag:
            print('  跳过 %s（站点里没有）' % dst_tag)
            continue
        ep = bytag[dst_tag]
        dur = float(ep.get('dur') or 0)
        try:
            plat = run_one(src_tag)
        except Exception as e:
            print('  %s 平台失败: %s' % (dst_tag, e))
            continue
        items = []
        for p in plat:
            try:
                st, en = float(p['start']), float(p['end'])
            except Exception:
                continue
            items.append({'ja': p.get('ja', ''), 's': st, 'e': en})
        if not items:
            print('  %s 平台没给时间戳' % dst_tag)
            continue
        # 对齐：站点句 ↔ 平台句
        pairs = PP.align([s['t'] for s in ep['sents']], [it['ja'] for it in items])
        newt = []
        miss = 0
        for i, s in enumerate(ep['sents']):
            js = pairs.get(i) or []
            if not js:
                miss += 1
                newt.append((float(s.get('s') or 0), float(s.get('e') or 0)))
                continue
            lo = min(items[j]['s'] for j in js)
            hi = max(items[j]['e'] for j in js)
            newt.append((lo, hi))
        # 🔴 验收：跨度必须接近音频时长，且不许倒退
        span_lo = min(t[0] for t in newt)
        span_hi = max(t[1] for t in newt)
        mono = all(newt[i][0] >= newt[i - 1][1] - 0.01 for i in range(1, len(newt)))
        cover = span_hi / dur if dur else 0
        print('  %s: %d句 未匹配%d ｜ 跨度 %.1f-%.1f ｜ 覆盖音频 %.0f%% ｜ 单调 %s' % (
            dst_tag, len(ep['sents']), miss, span_lo, span_hi, cover * 100, mono))
        if not mono or cover < 0.85:
            print('     🔴 平台时间轴不可靠 ⇒ 不采用（保留截断值）')
            continue
        for s, (lo, hi) in zip(ep['sents'], newt):
            s['s'] = round(lo, 2)
            s['e'] = round(hi, 2)
        print('     ✅ 已替换时间轴')
    json.dump(data, open(os.path.join(ROOT, 'data-merged.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main()
