#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线上 50 集最终验收（直接打公网）

🔴 所有 URL 带 cache-buster（?v=随机数）—— GitHub Pages 的 CDN 会给旧 build，
   peer 上次就踩过（curl 线上 data/s002.json 拿到 s001 的内容）。
"""
import json, random, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = 'https://tlv-creator.github.io/nhk-easy-news'
V = random.randint(100000, 999999)
fails = []
NP = [0]


def ck(name, cond, extra=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (('  ' + str(extra)) if extra else ''))
    if cond:
        NP[0] += 1
    else:
        fails.append(name)


def get(path, tries=3):
    """用 curl 拉（实测 urllib 在这台机器上连接极慢，curl 0.9s vs urllib 卡死）"""
    url = '%s/%s?v=%d' % (BASE, path, V)
    last = None
    for i in range(tries):
        try:
            r = subprocess.run(['curl', '-sS', '--max-time', '240', '-A', 'mavis-verify',
                                '-H', 'Cache-Control: no-cache', url],
                               capture_output=True, timeout=300)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode('utf-8', 'replace')
            last = RuntimeError('rc=%d %s' % (r.returncode, r.stderr[:120]))
        except Exception as e:
            last = e
        time.sleep(2 + 2 * i)
    raise last


def main():
    print('【① 索引】')
    idx = json.loads(get('shun-web/data/index.json'))
    eps = idx['eps']
    ck('50 集', len(eps) == 50, '实际 %d' % len(eps))
    reals = [e['realEp'] for e in eps]
    ck('realEp 升序', reals == sorted(reals))
    ck('tag 唯一', len(set(e['tag'] for e in eps)) == 50)
    ck('第1集是 Ep1（Genki 基础在前）', reals[0] == 1, reals[0])
    ck('最后一集是 Ep278', reals[-1] == 278, reals[-1])
    print('     集序（realEp）:', reals)

    print('\n【② 逐集内容（50 集全拉，并发）')
    bad_zh = bad_ru = bad_frame = bad_tl = 0
    tot = 0
    t0 = time.time()

    def fetch_one(e):
        try:
            return e, json.loads(get('shun-web/data/%s.json' % e['tag'])), None
        except Exception as ex:
            return e, None, ex

    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(fetch_one, eps))
    print('     拉取完成，用时 %.0fs' % (time.time() - t0))
    for e, d, err in fetched:
        if d is None:
            ck('%s 可拉取' % e['tag'], False, str(err))
            continue
        ss = d.get('sents') or []
        tot += len(ss)
        prev = -1.0
        for s in ss:
            t = s.get('t') or ''
            if not (s.get('zh') or '').strip():
                bad_zh += 1
            ru = s.get('ru') or []
            if ''.join(a for a, _ in ru) != t:
                bad_ru += 1
            for f in ((s.get('gs') or {}).get('frame') or []):
                seg = f.get('s') or ''
                p = f.get('p')
                # 🔴 find 返回 0 是「在句首」，不是「不存在」⇒ 判据必须是 < 0
                if not seg or t.find(seg) < 0:
                    bad_frame += 1
                    break
                # p 两套格式都兼容：区间 [start,end] / 单值起点(int)
                if isinstance(p, list) and len(p) == 2:
                    st_, en_ = p[0], p[1]
                elif isinstance(p, int):
                    st_, en_ = p, p + len(seg)
                else:
                    bad_frame += 1
                    break
                if st_ < 0 or en_ > len(t) or en_ - st_ != len(seg):
                    bad_frame += 1
                    break
            st_, en_ = float(s.get('s') or 0), float(s.get('e') or 0)
            if en_ < st_ or st_ < prev - 0.01:
                bad_tl += 1
            prev = max(prev, en_)
    ck('句数 %d' % tot, tot > 3500, tot)
    ck('中文全有', bad_zh == 0, '空 %d' % bad_zh)
    ck('注音拼接 == 原文', bad_ru == 0, '不等 %d' % bad_ru)
    ck('frame.s 逐字 ∈ 原句 且 p 合法', bad_frame == 0, '违例 %d' % bad_frame)
    ck('时间轴不倒序', bad_tl == 0, '倒序 %d' % bad_tl)

    print('\n【③ 单文件离线版')
    try:
        s = get('shun-text.html')
        ck('单文件版可取', len(s) > 100000, '%.1f MB' % (len(s) / 1048576))
        ck('内联 50 集数据', s.count('"realEp"') >= 50, '%d 处' % s.count('"realEp"'))
        ck('含 window.SHUN', 'window.SHUN=' in s)
    except Exception as e:
        ck('单文件版可取', False, e)

    print('\n' + '=' * 56)
    print('通过 %d 项，失败 %d 项' % (NP[0], len(fails)))
    for f in fails:
        print('  ❌', f)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
