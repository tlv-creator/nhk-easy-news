#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量调用平台通道 audios_understand（0 订阅额度）→ platform_out/<tag>.json

流程：上传本地音频拿 temp_url → 分批调用 → 解析 JSON 数组落盘。
🔴 判据落在**计数**上：清单 N 集，跑完必须 N 个有效结果（记忆里的坑：
   「没被处理」和「处理了但跳过」在日志里长得一模一样）。
"""
import json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
PLAT_DIR = os.path.join(ROOT, 'platform_out')
os.makedirs(PLAT_DIR, exist_ok=True)

PROMPT = """请对这段日语教学播客做三件事：

1) 严格逐字转写：按听到的句子切分。日文保持原样（保留原有标点），英文保持原样，不要添加原文没有的词，不要翻译、不要改写、不要补充。
2) 为每一句给出简体中文翻译。
3) 为每一句标出语法成分块。块必须逐字来自该句原文，按出现顺序排列，互不重叠，相邻同类型块不要合并。用四种括号：
   [方] 连接词/副词/独立成分
   ［方］ 从句（角色可填：原因从句/条件从句/转折从句/让步从句/同时从句/并列从句/从句）
   〈方〉 句体成分（角色可填：主语/主题/宾语/状语/形式名词/名词短语）
   （方） 谓语
   每个块给 role（中文角色名）。

输出为 JSON 数组，每项形如：
{"ja":"日文原文","zh":"中文翻译","blocks":[{"s":"片段原文","b":"[]","role":"连接"}]}"""


def sh(args, timeout=1800):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def upload(path):
    rc, out, err = sh(['mcode-tools', 'upload_temp_url', path])
    if rc != 0:
        raise RuntimeError('upload 失败 %s: %s' % (path, err[:200]))
    return json.loads(out)['temp_url']


def call(batch):
    args = {'audio_info': [{'url': u, 'mime_type': 'audio/mpeg', 'prompt': PROMPT}
                           for u in batch.values()]}
    af = os.path.join(ROOT, 'platform_out', '_args.json')
    json.dump(args, open(af, 'w', encoding='utf-8'), ensure_ascii=False)
    rc, out, err = sh(['mcode-tools', 'connector', 'call',
                        'connector__matrix__audios_understand', '--args-file', af])
    if rc != 0:
        raise RuntimeError('调用失败 rc=%d: %s' % (rc, (err or out)[:300]))
    try:
        raw = json.loads(out)
    except Exception:
        raise RuntimeError('返回不是 JSON: %s' % out[:300])
    res = raw.get('results') or []
    if len(res) != len(batch):
        raise RuntimeError('返回条数 %d != 请求 %d' % (len(res), len(batch)))
    return res


def parse_desc(desc):
    m = re.search(r'```json\s*(.*?)\s*```', desc, re.S) or re.search(r'(\[.*\])', desc, re.S)
    if not m:
        raise ValueError('找不到 JSON 数组')
    return json.loads(m.group(1))


def main():
    rows = json.load(open(os.path.join(ROOT, 'new-episodes.json'), encoding='utf-8'))
    todo = [r for r in rows if not os.path.exists(os.path.join(PLAT_DIR, r['tag'] + '.json'))]
    if not todo:
        print('全部已跑完')
        return
    BATCH = 5
    print('待跑 %d 集，每批 %d 集' % (len(todo), BATCH), flush=True)
    # ① 先全部上传（temp_url 1 小时有效）
    urls = {}
    up = os.path.join(ROOT, 'platform_out', '_urls.json')
    if os.path.exists(up):
        urls = json.load(open(up, encoding='utf-8'))
    for r in todo:
        if r['tag'] in urls:
            continue
        try:
            urls[r['tag']] = upload(os.path.join(ROOT, 'audio', r['tag'] + '.mp3'))
            json.dump(urls, open(up, 'w', encoding='utf-8'), ensure_ascii=False)
            print('上传 %s ✓' % r['tag'], flush=True)
        except Exception as e:
            print('上传 %s ✗ %s' % (r['tag'], e), flush=True)
    # ② 分批调用
    ok = fail = 0
    for i in range(0, len(todo), BATCH):
        chunk = [r for r in todo[i:i + BATCH] if r['tag'] in urls]
        if not chunk:
            continue
        batch = {r['tag']: urls[r['tag']] for r in chunk}
        t0 = time.time()
        try:
            res = call(batch)
            for r in chunk:
                rr = res[chunk.index(r)]
                if not rr.get('success'):
                    print('  %s 平台失败: %s' % (r['tag'], str(rr.get('error'))[:160]), flush=True)
                    fail += 1
                    continue
                data = parse_desc(rr['description'])
                json.dump(data, open(os.path.join(PLAT_DIR, r['tag'] + '.json'), 'w',
                                    encoding='utf-8'), ensure_ascii=False)
                print('  %-8s %3d句 (%.0fs)' % (r['tag'], len(data), time.time() - t0), flush=True)
                ok += 1
        except Exception as e:
            print('  批次失败 %s: %s' % ([r['tag'] for r in chunk], e), flush=True)
            fail += len(chunk)
    # ③ 判据落在计数上
    have = len([r for r in rows if os.path.exists(os.path.join(PLAT_DIR, r['tag'] + '.json'))])
    print('=== 完成: 成功 %d ｜ 失败 %d ｜ 落盘 %d/%d 集 ===' % (ok, fail, have, len(rows)))
    if have != len(rows):
        print('🔴 数量对不上（还有 %d 集没结果）' % (len(rows) - have))
        sys.exit(1)


if __name__ == '__main__':
    main()
