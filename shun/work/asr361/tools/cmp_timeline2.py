#!/usr/bin/env python3
"""
对拍（用**已落盘**的 MiniMax ASR，不调 API）：
  audios_understand（平台通道，零额度） vs  MiniMax ASR（存档）
判据：同一段音频，两边句级时间轴 → 文本匹配后比时间差。
"""
import json, os, re, subprocess, sys, difflib, glob

AUDIO = sys.argv[1]
ASR = sys.argv[2]
START = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
DUR = float(sys.argv[4]) if len(sys.argv) > 4 else 120.0
TMP = '/workspace/shun-web/tmp'
os.makedirs(TMP, exist_ok=True)
seg = os.path.join(TMP, 'cmp.mp3')
subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-ss', str(START), '-t', str(DUR),
                '-i', AUDIO, '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame',
                '-b:a', '64k', seg], check=True)

# ① 存档 MiniMax 词级 → 只取落在 [START, START+DUR] 的词 → 句
asr = json.load(open(ASR))
words = []
for s in asr['segments']:
    for w in (s.get('words') or []):
        if w.get('w'):
            words.append(w)
words = [w for w in words if w['e'] >= START and w['s'] <= START + DUR]
print('存档 MiniMax 该段词数: %d' % len(words))
mm_sents, cur = [], []
for a, b in zip(words, words[1:]):
    cur.append(a)
    if b['s'] - a['e'] > 0.6:
        mm_sents.append(cur); cur = [b]
if words:
    cur.append(words[-1]); mm_sents.append(cur)
mm_sents = [{'s': g[0]['s'], 'e': g[-1]['e'], 't': ''.join(x['w'] for x in g)} for g in mm_sents]

# ② audios_understand
u = subprocess.run(['mcode-tools', 'upload_temp_url', seg], capture_output=True, text=True)
url = json.loads(u.stdout)['temp_url']
args = {'audio_info': [{'url': url, 'mime_type': 'audio/mpeg',
                        'prompt': '这段音频共%.0f秒。请逐句转写日语原文，并为每句标注起止时间（秒，精确到0.1）。输出 JSON 数组，每项 {"s":开始秒,"e":结束秒,"t":"日文"}，覆盖整段音频，按时间排序。不要多余说明。' % DUR}]}
open(os.path.join(TMP, 'cmp_args.json'), 'w').write(json.dumps(args))
p = subprocess.run(['mcode-tools', 'connector', 'call', 'connector__matrix__audios_understand',
                    '--args-file', os.path.join(TMP, 'cmp_args.json')], capture_output=True, text=True)
au = []
try:
    d = json.loads(p.stdout)
    t = re.sub(r'^```(?:json)?|```$', '', d['results'][0]['description'].strip(), flags=re.M)
    au = json.loads(t)
except Exception as e:
    print('解析失败:', e, str(p.stdout)[:200])
print('audios_understand 句数: %d' % len(au))
if not mm_sents or not au:
    sys.exit(0)

print('\n=== 存档 MiniMax 句级（前 6）===')
for x in mm_sents[:6]:
    print('  [%6.2f-%6.2f] %s' % (x['s'], x['e'], x['t'][:58]))
print('=== audios_understand（前 6）===')
for x in au[:6]:
    print('  [%6.2f-%6.2f] %s' % (x['s'], x['e'], x['t'][:58]))

# ③ 文本相似匹配
def norm(s): return re.sub(r'[\s\u3000]', '', s)
mm_txt = [norm(x['t']) for x in mm_sents]
au_txt = [norm(x['t']) for x in au]
sm = difflib.SequenceMatcher(None, mm_txt, au_txt)
pairs = []
for i, j, n in sm.get_matching_blocks():
    for k in range(n):
        pairs.append((mm_sents[i + k], au[j + k]))
# 再补一层：文本相似度配对（匹配块漏掉的）
unmatched_m = [x for x in mm_sents if not any(x is p[0] for p in pairs)]
unmatched_a = [x for x in au if not any(x is p[1] for p in pairs)]
for m in unmatched_m:
    best, bi = 0, -1
    for a in unmatched_a:
        r = difflib.SequenceMatcher(None, norm(m['t']), norm(a['t'])).ratio()
        if r > best:
            best, bi = r, unmatched_a.index(a)
    if best > 0.55 and bi >= 0:
        pairs.append((m, unmatched_a[bi]))

print('\n=== 配对 %d 句（MiniMax %d 句 / AU %d 句）===' % (len(pairs), len(mm_sents), len(au)))
ds, de = [], []
for m, a in pairs:
    ds.append(abs(m['s'] - a['s'])); de.append(abs(m['e'] - a['e']))
allд = ds + de
if allд:
    s = sorted(allд); n = len(s)
    print('  时间差(秒): 中位 %.3f | 均值 %.3f | P90 %.3f | 最大 %.3f' %
          (s[n // 2], sum(s) / n, s[int(n * .9)], s[-1]))
    print('  <0.3s: %.0f%%  |  <0.6s: %.0f%%  |  <1.0s: %.0f%%' %
          (100 * sum(1 for x in s if x < .3) / n, 100 * sum(1 for x in s if x < .6) / n,
           100 * sum(1 for x in s if x < 1.0) / n))
print('\n=== 逐句对照（前 12 对）===')
for m, a in pairs[:12]:
    print('  MM[%6.2f-%6.2f] AU[%6.2f-%6.2f] d=%+.2f/%+.2f | %s' %
          (m['s'], m['e'], a['s'], a['e'], a['s'] - m['s'], a['e'] - m['e'], m['t'][:42]))
