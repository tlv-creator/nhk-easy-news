#!/usr/bin/env python3
"""
Shun 361 集 · 一条龙生产（**零额度**，全走平台 connector 通道）：
   切块 → audios_understand（转写 + 翻译 + 四类括号成分标注 一次搞定）→ 合并 → 落盘

⭐ 为什么换通道：
  · MiniMax Token Plan 额度已枯竭（2056），ASR / 翻译 / gs 全被挡
  · connector__matrix__audios_understand 走平台通道，不烧用户额度
  · 实测对拍：时间轴与 MiniMax ASR 吻合到 ±1~2 秒（加块偏移后），且文本更干净
    （MiniMax 词级拼接会出现「自分自分」这种重复；本通道不会），句子粒度更自然

⭐ prompt 里直接写死「相邻同类也要分开，不要合并」⇒ 对应用户那句「要分开不要一起」
"""
import os, re, json, subprocess, sys, time, argparse
from concurrent.futures import ThreadPoolExecutor

ROOT = '/workspace/shun-web'
TMP = os.path.join(ROOT, 'tmp')
OUT = os.path.join(ROOT, 'prod')
for d in (TMP, OUT):
    os.makedirs(d, exist_ok=True)

BLOCK = 180.0          # 每块秒数（块内时间轴由模型给出，块间用精确偏移拼）
WORKERS = 5
EP_WORKERS = 5
TOOL = 'connector__matrix__audios_understand'

PROMPT = """这段音频共{dur:.0f}秒。请按句输出 JSON 数组，每项一句，不要任何多余说明：
{{"s":开始秒,"e":结束秒,"ja":"日文原文","zh":"整句中文翻译","frame":[{{"s":"片段","b":"括号","role":"角色"}}]}}

要求：
1. s / e：这句在音频里的起止时间（秒，精确到 0.1），必须覆盖整段音频、连续不乱序。
2. ja：**严格逐字转写**。绝对不要改写、翻译、替换或补充任何词：
   · 英文词保持原样（Beginners 就写 Beginners，不要写成「ビギナーズ」）
   · 不要添加原文里没有的词
   · 保留所有标点
   · 听不清的地方照最接近的发音写，不要意译。
3. zh：整句中文翻译。
3. frame：语法成分分层。b 只能用这四个字符串之一："[]"（连接词/副词）、"［］"（从句）、"〈〉"（宾语内容）、"（）"（谓语）。
   片段 s 必须逐字取自本句 ja，role 用中文短语（≤8字）。
   **每个片段单独列一项，相邻同类型的也要分开列，绝对不要合并成一个片段。**
   例：ja="しあわせの神様は、この家にしあわせを置きました"
   ⇒ frame=[{{"s":"しあわせの神様は","b":"［］","role":"主语从句"}},{{"s":"この家に","b":"〈〉","role":"宾语"}},{{"s":"しあわせを","b":"〈〉","role":"宾语"}},{{"s":"置きました","b":"（）","role":"谓语"}}]"""


def cut(src, start, dur, out):
    subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-ss', '%.3f' % start, '-t', '%.3f' % dur,
                    '-i', src, '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame',
                    '-b:a', '64k', out], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(out) and os.path.getsize(out) > 2000


def call_understand(path, dur):
    up = subprocess.run(['mcode-tools', 'upload_temp_url', path], capture_output=True, text=True)
    try:
        url = json.loads(up.stdout)['temp_url']
    except Exception:
        return None
    args = {'audio_info': [{'url': url, 'mime_type': 'audio/mpeg',
                            'prompt': PROMPT.format(dur=dur)}]}
    af = os.path.join(TMP, 'args_%d.json' % os.getpid())
    with open(af, 'w') as f:
        json.dump(args, f, ensure_ascii=False)
    p = subprocess.run(['mcode-tools', 'connector', 'call', TOOL, '--args-file', af],
                       capture_output=True, text=True)
    try:
        os.remove(af)
    except OSError:
        pass
    try:
        d = json.loads(p.stdout)
        t = re.sub(r'^```(?:json)?|```$', '', d['results'][0]['description'].strip(), flags=re.M)
        arr = json.loads(t)
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def do_block(audio, start, dur, tag):
    seg = os.path.join(TMP, '%s_b%05d.mp3' % (tag, int(start)))
    if not cut(audio, start, dur, seg):
        return None
    try:
        arr = None
        for attempt in range(3):          # 🔴 失败重试（平台通道偶发失败）
            arr = call_understand(seg, dur)
            if arr:
                break
            time.sleep(3 * (attempt + 1))
    finally:
        try:
            os.remove(seg)
        except OSError:
            pass
    if not arr:
        return None
    out = []
    for it in arr:
        if not isinstance(it, dict) or not it.get('ja'):
            continue
        fr = it.get('frame') or []
        norm = []
        for f in fr:
            if not isinstance(f, dict) or not f.get('s'):
                continue
            b = (f.get('b') or '').replace(' ', '').replace('　', '')
            bmap = {'[]': '[]', '［］': '［］', '〈〉': '〈〉', '（）': '（）',
                    '[': '[]', '［': '［］', '〈': '〈〉', '（': '（）'}
            b = bmap.get(b, b)
            if b not in ('[]', '［］', '〈〉', '（）'):
                continue
            norm.append({'s': f['s'], 'b': b, 'role': (f.get('role') or '').strip()})
        out.append({'s': round(start + float(it.get('s', 0)), 2),
                    'e': round(start + float(it.get('e', 0)), 2),
                    'ja': it['ja'], 'zh': (it.get('zh') or '').strip(),
                    'frame': norm})
    return out


def process_ep(audio, tag, dur_total):
    out = os.path.join(OUT, tag + '.json')
    if os.path.exists(out):
        try:
            if json.load(open(out)).get('sentences'):
                return (tag, 'cached', len(json.load(open(out))['sentences']))
        except Exception:
            pass
    blocks = []
    st = 0.0
    while st < dur_total:
        blocks.append((st, min(BLOCK, dur_total - st)))
        st += BLOCK
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda b: do_block(audio, b[0], b[1], tag), blocks))
    sents = []
    missing = 0
    for (st, du), arr in zip(blocks, res):
        if arr is None:
            missing += 1
            continue
        sents.extend(arr)
    if not sents:
        return (tag, 'empty', 0)
    sents.sort(key=lambda x: x['s'])
    json.dump({'tag': tag, 'audio': audio, 'duration': dur_total, 'sentences': sents,
               'n': len(sents), 'missing_blocks': missing,
               'ts': time.strftime('%Y-%m-%d %H:%M:%S')},
              open(out, 'w'), ensure_ascii=False)
    return (tag, 'ok' if not missing else 'partial(%d)' % missing, len(sents))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--only', default='')
    args = ap.parse_args()
    tm = json.load(open(os.path.join(ROOT, 'tagmap.json')))
    if args.only:
        want = set(args.only.split(','))
        tm = [r for r in tm if r['tag'] in want or r['uid'] in want]
    if args.limit:
        tm = tm[:args.limit]
    tasks = []
    for r in tm:
        a = os.path.join(ROOT, 'audio', r['uid'] + '.mp3')
        if os.path.exists(a):
            tasks.append((a, r['tag'], r['dur']))
    print('待处理 %d 集（每集切 %.0fs 块，集级并发 %d）' % (len(tasks), BLOCK, EP_WORKERS), flush=True)
    t0 = time.time(); done = 0
    from concurrent.futures import ThreadPoolExecutor as _TPE
    with _TPE(max_workers=EP_WORKERS) as ex:
        futs = {ex.submit(process_ep, a, tag, du): (tag, du) for a, tag, du in tasks}
        for fut in futs:
            pass
        for fut, (tag, du) in futs.items():
            st, n = fut.result()[1:]
            done += 1
            print('[%3d/%3d] %s %-12s sents=%-4d (%.0fs)' %
                  (done, len(tasks), tag, st, n, time.time() - t0), flush=True)


if __name__ == '__main__':
    main()
