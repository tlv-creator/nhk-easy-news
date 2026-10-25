#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sp 轨（电视播报稿）ASR 字幕校对 —— 安全写回版

🔴🔴 写回安全（我踩过的坑）：
   subs/*.js 的结构是  window.__SUBS__=window.__SUBS__||{};window.__SUBS__["id"]={...};
   ⇒ 用 `=\s*\{.*\}\s*;?$` 这种贪婪正则去替换会**把文件头一起吃掉**（整个站当场崩）
   ⇒ 正确做法：**只定位并重写 "sp":[...] 这一段**，easy/web 段一个字节都不动
"""
import os, re, json, glob, difflib, time, urllib.request, sys, argparse

KEY = os.environ.get('MINIMAX_SUB_KEY', '')
URL = 'https://api.minimaxi.com/v1/text/chatcompletion_v2'
MODEL = 'MiniMax-M3'
SITE = '/workspace/nhk/site/app'
RAW = '/tmp/sp_raw.json'

SYSTEM = '你是日语电视播报字幕的校对员。你只修正识别错误，绝不改写措辞。'

# ── sp 段的精确读写 ─────────────────────────────────────────────
def read_sp(src):
    """返回 (sp 列表, (start,end) 在 src 中的位置)"""
    key = '"sp":['
    i = src.find(key)
    if i < 0: return None, None
    start = i + len(key) - 1
    arr, end = json.JSONDecoder().raw_decode(src, start)
    return arr, (start, end)

def write_sp(src, sp):
    """只替换 sp 段，easy/web/头部一律不动"""
    _, (start, end) = read_sp(src)
    new_text = json.dumps(sp, ensure_ascii=False, separators=(',', ':'))
    return src[:start] + new_text + src[end:]

# ── 闸门工具 ────────────────────────────────────────────────────
def has_bad_char(s):
    for ch in s:
        o = ord(ch)
        if (0x3000 <= o <= 0x303f or 0x3040 <= o <= 0x30ff or
                0x4e00 <= o <= 0x9fff or 0xff00 <= o <= 0xffef or o < 128):
            continue
        return True
    return False

def nums(s):
    return set(re.findall(r'\d+', s))

def chunk_stats(a, b):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ch = [(a[i1:i2], b[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal']
    if not ch: return 0, 0, 0
    return len(ch), sum(max(len(x), len(y)) for x, y in ch), max(max(len(x), len(y)) for x, y in ch)

def sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()

def passes(old, new, ref_nums):
    if not new or new == old: return False
    if abs(len(new) - len(old)) > 4: return False
    nseg, ntot, nlong = chunk_stats(old, new)
    if nseg > 3: return False
    if ntot > 12: return False
    if nlong > 9: return False
    if sim(old, new) < 0.82: return False
    if not nums(new).issubset(nums(old) | ref_nums): return False
    if has_bad_char(new): return False
    if re.search(r'[ 　]', new): return False
    ow = set(re.findall(r'[A-Za-z]{2,}', old))
    nw = set(re.findall(r'[A-Za-z]{2,}', new))
    if not nw.issubset(ow): return False
    return True

# ── LLM ─────────────────────────────────────────────────────────
def mm(prompt, retries=2):
    body = {"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"}, "temperature": 0.1, "max_tokens": 4000}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + KEY,
                                          'Content-Type': 'application/json'})
    for a in range(retries + 1):
        try:
            r = urllib.request.urlopen(req, timeout=180)
            d = json.loads(r.read().decode())
            return d['choices'][0]['message']['content'], d.get('usage', {})
        except Exception:
            if a >= retries: raise
            time.sleep(4)

def parse_json_array(txt):
    t = re.sub(r'^```(?:json)?', '', txt.strip()).strip()
    t = re.sub(r'```$', '', t).strip()
    i, j = t.find('['), t.rfind(']')
    if i < 0 or j < 0: raise ValueError('no array')
    return json.loads(t[i:j+1])

def build_prompt(ref, items):
    ref_lines = '\n'.join(f'  {i+1}. {t}' for i, t in enumerate(ref))
    sen_lines = '\n'.join(f'[{it["i"]}] {it["ja"]}' for it in items)
    return f"""下面给你三样东西：
【参照1】新闻标题
{ref[0]}

【参照2】同一新闻的「易懂版」正文（用词更简单，事实一致）
{ref_lines}

【参照3】待校对的「电视播报稿」ASR 字幕（可能有识别错误）

请你逐句输出**纠错后**的日文句子。

必须严格遵守的规则：
1. **只修正识别错误**（错字、错数字、错专有名词、错片假名）。禁止改写措辞、禁止润色、禁止调整语序、禁止加词删词。
2. 台风号数、年份、金额、百分比、速度、地名、人名、机构名，必须与【参照1】【参照2】一致。参照里出现过的数字优先采用。
3. 原文没有错的地方，原样返回。
4. 每句长度与原句基本相同（最多差 1 个字）。
5. 标点保持原样。
6. 数字连同单位要写完整（例：参照里是「台風30号」就写「台風30号」，不能只写「台風30」；「51.7メートル」不要漏「メートル」）。

【待校对句子】
{sen_lines}

严格输出 JSON 数组，不要任何解释、不要 markdown 代码块：
[{{"i": 序号, "ja": "纠错后的句子"}}]
序号照抄上面的 [数字]。"""

def collect_refs(eid, d, eps):
    ep = {e['id']: e for e in eps}.get(eid, {})
    easy = [c.get('ja', '') for c in d.get('easy', []) if c.get('ja')]
    web = [c.get('ja', '') for c in d.get('web', []) if c.get('ja')]
    return [ep.get('title', '') or ''] + easy[:14] + web[:4]

def run_llm():
    eps = json.loads(re.search(r'=\s*(\[.*\])\s*;?\s*$',
                   open(f'{SITE}/data/episodes.js', encoding='utf-8').read(), re.S).group(1))
    raw = {}
    usage = 0
    for f in sorted(glob.glob(f'{SITE}/subs/*.js')):
        eid = os.path.basename(f)[:-3]
        src = open(f, encoding='utf-8').read()
        d = json.loads(re.search(r'=\s*(\{.*\})\s*;?\s*$', src, re.S).group(1), strict=False)
        sp, _ = read_sp(src)
        if not sp: continue
        ref = collect_refs(eid, d, eps)
        items = [{'i': i, 'ja': c.get('ja', '')} for i, c in enumerate(sp)]
        got = {}
        for st in range(0, len(items), 24):
            batch = items[st:st+24]
            try:
                txt, u = mm(build_prompt(ref, batch))
                usage += u.get('total_tokens', 0)
                arr = parse_json_array(txt)
                for x in arr:
                    if 'i' in x: got[int(x['i'])] = (x.get('ja') or '').strip()
            except Exception as e:
                print(f'  ⚠️ {eid} 批次失败: {type(e).__name__} {str(e)[:60]}')
        raw[eid] = got
        print(f'  · {eid}  {len(sp)} 句 → LLM 返 {len(got)}')
    json.dump(raw, open(RAW, 'w'), ensure_ascii=False)
    print(f'  ✅ 原始结果存 {RAW}   token {usage}')

def apply_raw(reject=None, manual=None):
    """把 raw 结果写回（带闸门 + 剔除清单 + 人工补正）"""
    raw = json.load(open(RAW, encoding='utf-8'))
    reject = reject or {}
    manual = manual or {}
    eps = json.loads(re.search(r'=\s*(\[.*\])\s*;?\s*$',
                   open(f'{SITE}/data/episodes.js', encoding='utf-8').read(), re.S).group(1))
    total = 0
    for f in sorted(glob.glob(f'{SITE}/subs/*.js')):
        eid = os.path.basename(f)[:-3]
        if eid not in raw: continue
        src = open(f, encoding='utf-8').read()
        sp, _ = read_sp(src)
        if not sp: continue
        d = json.loads(re.search(r'=\s*(\{.*\})\s*;?\s*$', src, re.S).group(1), strict=False)
        ref = collect_refs(eid, d, eps)
        ref_nums = nums(' '.join(ref))
        got = raw[eid]
        n = 0
        for i, new in got.items():
            i = int(i)                      # json 的 object key 会变成 str
            if i >= len(sp): continue
            old = sp[i].get('ja', '')
            if i in manual.get(eid, []):
                sp[i]['ja'] = manual[eid][i]; n += 1; continue
            if i in reject.get(eid, []): continue
            if passes(old, new, ref_nums):
                sp[i]['ja'] = new; n += 1
        if n:
            # 🔴 2026-10-25：改成**原子写**（临时文件 + rename）
            #   ⇒ 并发时（sp_fix / build_sp / gen_blk 同时改 subs）不会读到**半截**文件
            #   ⇒ 之前我这边跑到一半 JSON 解析报「Invalid control character」（2026-10-25 踩到）
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(f), suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(write_sp(src, sp))
            os.replace(tmp, f)
            total += n
            print(f'  ✅ {eid}  写入 {n}/{len(sp)} 句')
    print(f'  合计写入 {total} 句')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true', help='只跑 LLM，存原始结果')
    ap.add_argument('--apply', action='store_true', help='只应用（从 RAW）')
    a = ap.parse_args()
    if a.run: run_llm()
    elif a.apply: apply_raw()
    else:
        run_llm(); apply_raw()
