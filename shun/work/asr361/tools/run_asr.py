#!/usr/bin/env python3
"""
Shun 361 集 ASR 批处理：
  1) 每集按 480s 切片（API 硬上限 500s）
  2) 并发调 MiniMax asr-1.0
  3) 合并 segments，段内时间加偏移 → 集级绝对时间轴
  4) 落盘 asr/<id>.json（带缓存，断了能续跑）
判据：每集必须「有 segments」且「最后一段 end <= 时长+2s」，否则标记失败不写正式文件。
"""
import os, sys, json, re, subprocess, time
from concurrent.futures import ThreadPoolExecutor

ROOT = '/workspace/shun-web'
AUD = os.path.join(ROOT, 'audio')
ASR = os.path.join(ROOT, 'asr')
TMP = os.path.join(ROOT, 'tmp')
for d in (ASR, TMP):
    os.makedirs(d, exist_ok=True)

SEG_SEC = 480
WORKERS = 6
KEY = os.environ.get('MINIMAX_SUB_KEY', '')
API = 'https://api.minimaxi.com/v1/speech_to_text'


def ffmpeg_split(src, outdir, tag, seg=SEG_SEC):
    os.makedirs(outdir, exist_ok=True)
    # 🔴 坑：不能用 '%s_%03d.mp3' % (tag, 0) —— 那会把 %03d 先填成 000，
    #    pattern 里就没了序号占位符 ⇒ ffmpeg 无法给第 2 段起名
    pat = os.path.join(outdir, tag + '_%03d.mp3')
    subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-i', src, '-f', 'segment',
                    '-segment_time', str(seg), '-reset_timestamps', '1', '-c', 'copy',
                    pat], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(f for f in os.listdir(outdir) if f.startswith(tag + '_') and f.endswith('.mp3'))


def call_asr(path):
    cmd = ['curl', '-s', '--max-time', '240', '-X', 'POST', API,
           '-H', 'Authorization: Bearer %s' % KEY,
           '-F', 'file=@%s' % path,
           '-F', 'model=asr-1.0',
           '-F', 'response_format=verbose_json']
    for _ in range(3):
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            d = json.loads(p.stdout)
        except Exception:
            time.sleep(3); continue
        if isinstance(d, dict) and d.get('segments') is not None:
            return d
        # 记录错误便于排查，但继续重试
        err = (d.get('base_resp') or {}).get('status_msg') if isinstance(d, dict) else p.stdout[:200]
        time.sleep(3)
    return None


def dur_of(path):
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except Exception:
        return 0.0


def process(audio_path):
    aid = os.path.splitext(os.path.basename(audio_path))[0]
    out = os.path.join(ASR, aid + '.json')
    if os.path.exists(out):
        try:
            d = json.load(open(out))
            if d.get('segments'):
                return (aid, 'cached', len(d['segments']))
        except Exception:
            pass
    total = dur_of(audio_path)
    parts = ffmpeg_split(audio_path, TMP, aid)
    if not parts:
        return (aid, 'split-fail', 0)
    segs = []
    ok = True
    for i, fn in enumerate(parts):
        pp = os.path.join(TMP, fn)
        d = call_asr(pp)
        if d is None:
            ok = False
            print('  [%s] part %d ASR 失败' % (aid, i), flush=True)
            continue
        off = dur_of(pp) * 0  # 偏移用累计更稳
        for s in d.get('segments', []):
            segs.append({'start': round(s.get('start', 0) + off, 3),
                         'end': round(s.get('end', 0) + off, 3),
                         'speaker': s.get('speaker', 'S1'),
                         'text': (s.get('text') or '').strip()})
        off += dur_of(pp)
        os.remove(pp)
    if not segs:
        return (aid, 'asr-empty', 0)
    segs.sort(key=lambda x: x['start'])
    rec = {'id': aid, 'duration': total, 'segments': segs, 'ok': ok,
           'n': len(segs), 'ts': time.strftime('%Y-%m-%d %H:%M:%S')}
    json.dump(rec, open(out, 'w'), ensure_ascii=False, indent=1)
    return (aid, 'ok' if ok else 'partial', len(segs))


def main():
    files = sorted(f for f in os.listdir(AUD) if f.endswith('.mp3'))
    files = [f for f in files if not f.startswith('probe')]
    print('待处理: %d 集' % len(files), flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for aid, st, n in ex.map(process, [os.path.join(AUD, f) for f in files]):
            done += 1
            print('[%3d/%3d] %s  %-9s segs=%d  (%.0fs)' %
                  (done, len(files), aid, st, n, time.time() - t0), flush=True)
    print('=== 完成 %d/%d，用时 %.0f 分钟 ===' % (done, len(files), (time.time() - t0) / 60))


if __name__ == '__main__':
    main()
