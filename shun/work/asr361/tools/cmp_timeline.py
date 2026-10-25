#!/usr/bin/env python3
"""
对拍：audios_understand（平台通道）vs MiniMax ASR（用户额度通道）
判据：同一段音频，两边都出句级时间轴 ⇒ 文本对齐后比时间差。
"""
import json, os, re, subprocess, sys, difflib

AUDIO = sys.argv[1]
START = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
DUR = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
TMP = '/workspace/shun-web/tmp'
os.makedirs(TMP, exist_ok=True)
seg = os.path.join(TMP, 'cmp.mp3')
subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-ss', str(START), '-t', str(DUR),
                '-i', AUDIO, '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame',
                '-b:a', '64k', seg], check=True)

# ① MiniMax ASR（词级）
p = subprocess.run(['curl', '-s', '--max-time', '200', '-X', 'POST',
                    'https://api.minimaxi.com/v1/speech_to_text',
                    '-H', 'Authorization: Bearer ' + os.environ['MINIMAX_SUB_KEY'],
                    '-F', 'file=@%s' % seg, '-F', 'model=asr-1.0',
                    '-F', 'response_format=verbose_json', '-F', 'timestamp_level=word'],
                   capture_output=True, text=True)
try:
    mm = json.loads(p.stdout)
    mm_segs = mm.get('segments') or []
except Exception:
    mm_segs = []
print('MiniMax 词数: %d  (API %s)' % (len(mm_segs), 'ok' if mm_segs else '2056/失败'))

# ② audios_understand（句级 + 时间戳）
url = subprocess.run(['mcode-tools', 'upload_temp_url', seg], capture_output=True, text=True)
url = json.loads(url.stdout)['temp_url']
args = {'audio_info': [{'url': url, 'mime_type': 'audio/mpeg',
                        'prompt': '这段音频共%.0f秒。请逐句转写日语原文，并为每句标注起止时间（秒，精确到0.1）。输出 JSON 数组，每项 {"s":开始秒,"e":结束秒,"t":"日文"}，覆盖整段音频，按时间排序。不要多余说明。' % DUR}]}
open(os.path.join(TMP, 'cmp_args.json'), 'w').write(json.dumps(args))
p = subprocess.run(['mcode-tools', 'connector', 'call', 'connector__matrix__audios_understand',
                    '--args-file', os.path.join(TMP, 'cmp_args.json')], capture_output=True, text=True)
au = []
try:
    d = json.loads(p.stdout)
    txt = d['results'][0]['description']
    txt = re.sub(r'^```(?:json)?|```$', '', txt.strip(), flags=re.M)
    au = json.loads(txt)
except Exception as e:
    print('audios_understand 解析失败:', e, str(p.stdout)[:200])
print('audios_understand 句数: %d' % len(au))

if not mm_segs or not au:
    sys.exit(0)

# ③ MiniMax 词 → 句（按 >0.6s 停顿合并）
mm_sents, cur = [], []
for s in mm_segs:
    cur.append(s)
    if len(cur) > 1 and s['end'] - mm_segs[0]['start'] and (s['end'] - cur[0]['start']) > 0 and \
       (s['end'] - s['start'] >= 0 and len(cur) >= 1):
        pass
# 更稳：按相邻词间隔分组
mm_sents, cur = [], [mm_segs[0]]
for a, b in zip(mm_segs, mm_segs[1:]):
    if b['start'] - a['end'] > 0.6:
        mm_sents.append(cur); cur = [b]
    else:
        cur.append(b)
mm_sents.append(cur)
mm_sents = [{'s': g[0]['start'], 'e': g[-1]['end'], 't': ''.join(x['text'] for x in g)}
            for g in mm_sents]

print('\n=== MiniMax 句级（前 8）===')
for x in mm_sents[:8]:
    print('  [%6.2f-%6.2f] %s' % (x['s'], x['e'], x['t'][:56]))
print('=== audios_understand（前 8）===')
for x in au[:8]:
    print('  [%6.2f-%6.2f] %s' % (x['s'], x['e'], x['t'][:56]))

# ④ 文本对齐（用最长公共子序列匹配句子）
mm_txt = [re.sub(r'[\s\u3000]', '', x['t']) for x in mm_sents]
au_txt = [re.sub(r'[\s\u3000]', '', x['t']) for x in au]
sm = difflib.SequenceMatcher(None, mm_txt, au_txt)
pairs = [(mm_sents[i], au[j]) for i, j, n in sm.get_matching_blocks() for j in range(i + 1, i + 1 + n)]
print('\n=== 匹配 %d / %d 句 ===' % (len(pairs), len(au)))
diffs = []
for m, a in pairs:
    if abs(m['s'] - a['s']) < 60:      # 同一段内
        diffs.append(abs(m['s'] - a['s']))
        diffs.append(abs(m['e'] - a['e']))
if diffs:
    diffs.sort()
    n = len(diffs)
    print('  时间差(秒): 中位 %.3f | 均值 %.3f | P90 %.3f | 最大 %.3f'
          % (diffs[n // 2], sum(diffs) / n, diffs[int(n * .9)], diffs[-1]))
    print('  差值 <0.5s 占比: %.0f%%  |  <1.0s: %.0f%%'
          % (100 * sum(1 for d in diffs if d < .5) / n, 100 * sum(1 for d in diffs if d < 1.0) / n))
else:
    print('  没有可比对的同段句子')
