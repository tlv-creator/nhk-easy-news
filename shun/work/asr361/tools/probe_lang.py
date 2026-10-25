#!/usr/bin/env python3
"""抽样判定 Shun 各集是日语还是英语 —— 用 MiniMax ASR 取前 40 秒的文本，按假名比例判定。"""
import os, re, json, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ.get('MINIMAX_SUB_KEY', '')
API = 'https://api.minimaxi.com/v1/speech_to_text'
cat = json.load(open('/workspace/shun-web/cat.json'))
rows = cat['jp'] + cat['sp']


def probe(item):
    title, dur, url = item
    aid = re.search(r'episodes/([0-9a-f-]{36})', url).group(1)
    path = '/workspace/shun-web/tmp/probe_%s.mp3' % aid
    subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-ss', '60', '-t', '40', '-i', url,
                    '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '48k', path],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(path) or os.path.getsize(path) < 3000:
        return (title, 'dl-fail', 0, 0)
    cmd = ['curl', '-s', '--max-time', '120', '-X', 'POST', API,
           '-H', 'Authorization: Bearer %s' % KEY,
           '-F', 'file=@%s' % path, '-F', 'model=asr-1.0', '-F', 'response_format=json']
    p = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(path)
    try:
        d = json.loads(p.stdout)
    except Exception:
        return (title, 'api-fail', 0, 0)
    t = d.get('text') or ''
    kana = len(re.findall(r'[぀-ヿ]', t))
    kanji = len(re.findall(r'[一-鿿]', t))
    lat = len(re.findall(r'[A-Za-z]', t))
    jp = kana + kanji
    ratio = jp / max(1, jp + lat)
    lang = '日语' if ratio > 0.45 else ('英语' if ratio < 0.12 else '混合')
    return (title, lang, round(ratio, 2), jp)


if __name__ == '__main__':
    # 每隔 N 集抽一集，覆盖全部
    step = max(1, len(rows) // 28)
    sample = rows[::step]
    print('抽样 %d 集（总 %d）' % (len(sample), len(rows)))
    res = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(probe, sample):
            res.append(r)
            print('  %-6s jp_ratio=%-5s kana+kanji=%-4s  %s' % (r[1], r[2], r[3], r[0][:68]), flush=True)
    from collections import Counter
    print('\n=== 抽样汇总 ===')
    for k, v in Counter(r[1] for r in res).most_common():
        print('  %-8s %d' % (k, v))
    json.dump(res, open('/workspace/shun-web/lang_probe.json', 'w'), ensure_ascii=False, indent=1)
