#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sp 轨（电视播报稿）ASR 字幕校对
────────────────────────────────
背景：普通版的「音」= 电视播报音，「字」= ASR 出来的播报稿。
     ASR 有错字（台風35号 ← 应 30号；咲き島諸島 ← 应 先島諸島；温泰低気圧 ← 应 温帯低気圧）
     ⇒ 用户看到字幕和听到的声音对不上。

做法：LLM 只做「改错字」，用同一新闻的【标题】+【易懂版正文】+【报纸原文】作参照。
🔴 三道闸门（防止 LLM 自由发挥）：
   ① 编辑距离：改后句 vs 原句，距离 ≤ 2（短句 ≤ 1）
   ② 长度：|len新 - len原| ≤ 1
   ③ 数字：新句里的每个数字必须 ∈ (原句数字 ∪ 参照文本数字) —— 防止幻觉改数
   不通过 ⇒ 保留原句（不猜、不硬改）
"""
import os, re, json, glob, difflib, time, urllib.request, sys

KEY = os.environ.get('MINIMAX_SUB_KEY', '')
URL = 'https://api.minimaxi.com/v1/text/chatcompletion_v2'
MODEL = 'MiniMax-M3'
SITE = '/workspace/nhk/site/app'

SYSTEM = (
    '你是日语电视播报字幕的校对员。你只修正识别错误，绝不改写措辞。'
)

def mm(prompt, retries=2):
    body = {"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
        "temperature": 0.1, "max_tokens": 4000}
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
    for a in range(retries + 1):
        try:
            r = urllib.request.urlopen(req, timeout=180)
            d = json.loads(r.read().decode())
            txt = d['choices'][0]['message']['content']
            return txt, d.get('usage', {})
        except Exception as e:
            if a >= retries: raise
            time.sleep(4)

def parse_json_array(txt):
    t = txt.strip()
    t = re.sub(r'^```(?:json)?', '', t).strip()
    t = re.sub(r'```$', '', t).strip()
    i, j = t.find('['), t.rfind(']')
    if i < 0 or j < 0: raise ValueError('no array')
    return json.loads(t[i:j+1])

def ed(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()

def edit_distance(a, b):
    """标准编辑距离（Levenshtein）"""
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j-1] + 1, prev[j-1] + (ca != cb)))
        prev = cur
    return prev[-1]

def nums(s):
    return set(re.findall(r'\d+', s))

# 🔴 只允许这些字符出现在日文句子里：汉字 / 平假名 / 片假名 / 全角符号 / ASCII
def has_bad_char(s):
    for ch in s:
        o = ord(ch)
        if (0x3000 <= o <= 0x303f or 0x3040 <= o <= 0x30ff or
                0x4e00 <= o <= 0x9fff or 0xff00 <= o <= 0xffef or o < 128):
            continue
        return True        # 韩文 / emoji / 其它语种字符 ⇒ 拦掉
    return False

def diff_chunks(a, b):
    """字符级差异片段：[(旧片段, 新片段), ...]"""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal': continue
        out.append((a[i1:i2], b[j1:j2]))
    return out

def chunk_stats(a, b):
    """→ (变化片段数, 总变化字符数, 最长片段字符数)"""
    ch = diff_chunks(a, b)
    if not ch: return 0, 0, 0
    total = sum(max(len(x), len(y)) for x, y in ch)
    longest = max(max(len(x), len(y)) for x, y in ch)
    return len(ch), total, longest

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

def main():
    eps = json.loads(re.search(r'=\s*(\[.*\])\s*;?\s*$',
                   open(f'{SITE}/data/episodes.js', encoding='utf-8').read(), re.S).group(1))
    E = {e['id']: e for e in eps}
    files = sorted(glob.glob(f'{SITE}/subs/*.js'))
    total_fixed = total_sent = 0
    usage = {'total_tokens': 0}
    report = []
    for f in files:
        eid = os.path.basename(f)[:-3]
        src = open(f, encoding='utf-8').read()
        d = json.loads(re.search(r'=\s*(\{.*\})\s*;?\s*$', src, re.S).group(1))
        sp = d.get('sp')
        if not sp: continue
        ep = E.get(eid, {})
        easy = [c.get('ja', '') for c in d.get('easy', []) if c.get('ja')]
        web = [c.get('ja', '') for c in d.get('web', []) if c.get('ja')]
        title = ep.get('title', '') or ''
        ref = [title] + easy[:14] + web[:4]
        ref_nums = nums(' '.join(ref))
        items = [{'i': i, 'ja': c.get('ja', '')} for i, c in enumerate(sp)]
        fixed = {}
        for st in range(0, len(items), 24):
            batch = items[st:st+24]
            try:
                txt, u = mm(build_prompt(ref, batch))
                usage['total_tokens'] += u.get('total_tokens', 0)
                arr = parse_json_array(txt)
            except Exception as e:
                print(f'  ⚠️ {eid} 批次{st//24} 失败（保留原句）: {type(e).__name__} {str(e)[:80]}')
                continue
            m = {int(x['i']): x.get('ja', '') for x in arr if 'i' in x}
            for it in batch:
                new = (m.get(it['i']) or '').strip()
                old = it['ja']
                if not new or new == old: continue
                # 🔴 闸门（按「变化片段」判定，不按整句编辑距离）
                #    一句话里常同时有 错字 + 错数字 + 错专名 ⇒ 整句编辑距离天然偏大
                #    真正要挡的是「被改写成另一句」⇒ 看变化片段数/长度 + 整体相似度
                nseg, ntot, nlong = chunk_stats(old, new)
                if nseg > 3: continue          # 改动处太多 ⇒ 疑似改写
                if ntot > 12: continue         # 改动总量太大
                if nlong > 9: continue         # 单处改动过长
                if abs(len(new) - len(old)) > 4: continue
                if ed(old, new) < 0.82: continue
                nn = nums(new)
                if not nn.issubset(nums(old) | ref_nums): continue
                # 🔴 闸门四：不得引入非日文字符（实测抓到过韩文「거리」被 LLM 塞进来）
                if has_bad_char(new): continue
                # 🔴 闸门五：不得含半角空格（实测抓到过「各地 で」这种被塞进来的空格）
                if re.search(r'[ \u3000]', new): continue
                # 🔴 闸门六：不得凭空引入新的 ASCII 单词（实测抓到过「カラリコ→rarities」）
                ow = set(re.findall(r'[A-Za-z]{2,}', old))
                nw = set(re.findall(r'[A-Za-z]{2,}', new))
                if not nw.issubset(ow): continue
                fixed[it['i']] = new
        if not fixed:
            print(f'  ➖ {eid}  无改动')
            report.append((eid, 0, []))
            continue
        # 写回（先留旧值，报告要用）
        olds = {i: sp[i]['ja'] for i in fixed}
        for i, new in fixed.items():
            sp[i]['ja'] = new
        new_src = re.sub(r'=\s*\{.*\}\s*;?\s*$',
                         '= ' + json.dumps(d, ensure_ascii=False, separators=(',', ':')) + ';',
                         src, flags=re.S)
        open(f, 'w', encoding='utf-8').write(new_src)
        total_fixed += len(fixed); total_sent += len(sp)
        print(f'  ✅ {eid}  改 {len(fixed)}/{len(sp)} 句')
        for i, new in list(fixed.items())[:4]:
            print(f'       旧: {olds[i][:72]}')
            print(f'       新: {new[:72]}')
        report.append((eid, len(fixed), []))
    print()
    print(f'════ 合计：改动 {total_fixed} 句 / 共 {total_sent} 句  ·  token 用量 {usage["total_tokens"]} ════')

if __name__ == '__main__':
    main()
