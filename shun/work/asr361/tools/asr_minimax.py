#!/usr/bin/env python3
"""
Shun 全集 ASR —— MiniMax asr-1.0，**一次调用同时产出两路**：
  ① asr/<uid>.json  = {segments:[{start,end,text,words:[{s,e,w}]}]}   ← 词级时间轴
  ② mm/<uid>.json   = {chunks:[{start,end,text}]}                     ← 干净文本
⭐ ①②来自**同一次识别** ⇒ 不存在「whisper 词 ↔ MiniMax 文本」的跨系统对齐漂移
（peer 那套是两路独立识别再用 difflib 对齐，漂移风险在这里被消掉了）。

切段偏移：必须用**精确 -ss 起点**（不能用 segment muxer 的累计时长，会漂 200+ 秒）。
"""
import os, re, json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

ROOT = '/workspace/shun-web'
API = 'https://api.minimaxi.com/v1/speech_to_text'
KEY = os.environ.get('MINIMAX_SUB_KEY', '')
CHUNK = 460.0
WORKERS = 3
OUT_ASR = os.path.join(ROOT, 'asr')
OUT_MM = os.path.join(ROOT, 'mm')
TMP = os.path.join(ROOT, 'tmp')
for d in (OUT_ASR, OUT_MM, TMP):
    os.makedirs(d, exist_ok=True)


def dur_of(path):
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except Exception:
        return 0.0


def cut(src, start, dur, out):
    subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-ss', '%.3f' % start, '-t', '%.3f' % dur,
                    '-i', src, '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame',
                    '-b:a', '48k', out], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(out) and os.path.getsize(out) > 2000


def call_asr(path):
    cmd = ['curl', '-s', '--max-time', '280', '-X', 'POST', API,
           '-H', 'Authorization: Bearer %s' % KEY,
           '-F', 'file=@%s' % path, '-F', 'model=asr-1.0',
           '-F', 'response_format=verbose_json', '-F', 'timestamp_level=word']
    for _ in range(3):
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            d = json.loads(p.stdout)
        except Exception:
            time.sleep(4); continue
        if isinstance(d, dict) and d.get('segments') is not None:
            return d
        time.sleep(4)
    return None


def process(audio_path):
    uid = os.path.splitext(os.path.basename(audio_path))[0]
    fa, fm = os.path.join(OUT_ASR, uid + '.json'), os.path.join(OUT_MM, uid + '.json')
    if os.path.exists(fa) and os.path.exists(fm):
        try:
            if json.load(open(fa)).get('segments'):
                return (uid, 'cached', len(json.load(open(fa))['segments']))
        except Exception:
            pass
    total = dur_of(audio_path)
    segs, chunks, ok = [], [], True
    starts = [0.0]
    while starts[-1] + CHUNK < total:
        starts.append(starts[-1] + CHUNK)
    for i, st in enumerate(starts):
        dur = min(CHUNK, total - st)
        if dur < 1.0:
            break
        p = os.path.join(TMP, '%s_c%02d.mp3' % (uid, i))
        if not cut(audio_path, st, dur, p):
            ok = False; continue
        d = call_asr(p)
        if d is None:
            ok = False
            print('   [%s] chunk %d (start=%.0f) ASR 失败' % (uid, i, st), flush=True)
            os.remove(p); continue
        ss = d.get('segments', [])
        if ss:
            chunks.append({'start': round(st + float(ss[0]['start']), 3),
                           'end': round(st + float(ss[-1]['end']), 3),
                           'text': d.get('text') or ''})
        for s in ss:
            a = round(st + float(s.get('start', 0)), 3)
            b = round(st + float(s.get('end', 0)), 3)
            w = (s.get('text') or '').strip()
            segs.append({'start': a, 'end': b, 'text': w, 'words': [{'s': a, 'e': b, 'w': w}] if w else []})
        os.remove(p)
    if not segs:
        return (uid, 'empty', 0)
    segs.sort(key=lambda x: x['start'])
    json.dump({'audio': audio_path, 'duration': total, 'segments': segs,
               'n': len(segs), 'ok': ok, 'ts': time.strftime('%Y-%m-%d %H:%M:%S')},
              open(fa, 'w'), ensure_ascii=False)
    json.dump({'chunks': chunks, 'duration': total}, open(fm, 'w'), ensure_ascii=False)
    return (uid, 'ok' if ok else 'partial', len(segs))


def main():
    ids = sys.argv[1:] if len(sys.argv) > 1 else None
    if ids:
        files = [i for i in ids if os.path.exists(os.path.join(ROOT, 'audio', i + '.mp3'))]
    else:
        files = [f[:-4] for f in sorted(os.listdir(os.path.join(ROOT, 'audio')))
                 if f.endswith('.mp3') and 'probe' not in f]
    print('待处理 %d 集' % len(files), flush=True)
    t0 = time.time(); done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for uid, st, n in ex.map(process, [os.path.join(ROOT, 'audio', f + '.mp3') for f in files]):
            done += 1
            print('[%3d/%3d] %s %-8s segs=%-5d (%.0fs)' %
                  (done, len(files), uid, st, n, time.time() - t0), flush=True)
    print('=== 完成 %d/%d，%.0f 分钟 ===' % (done, len(files), (time.time() - t0) / 60))


if __name__ == '__main__':
    main()
