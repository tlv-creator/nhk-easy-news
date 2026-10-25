#!/usr/bin/env python3
"""
把已落盘的 prod/*.json（我的结构）转成 peer 的 sents/gs 结构（可直接塞进 shun-web/data/）。
顺带做三件生成侧的修正：
  ① **口径归一**：b=〈〉 且 role 是状语类 ⇒ 改成 b=[]（四类严格，〈〉 只留宾语）
  ② **逐字校验**（frame_guard）：s 必须是 ja 的连续子串，否则丢弃该块
  ③ **补 p 字段**：[start,end] 字符区间（游标顺序推进，避免重复子串错位）
  ④ **tag 前缀隔离**：ep001..ep361 → s001..s361（避免和 peer 的 ep01..ep13 字符串排序混在一起）

零 API 调用：只处理已落盘的数据。
"""
import json, os, re, sys, glob, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frame_guard

ROOT = '/workspace/shun-web'
PROD = os.path.join(ROOT, 'prod')
OUTD = os.path.join(ROOT, 'shun-web', 'data')
TAGMAP = os.path.join(ROOT, 'tagmap.json')

# 🔴 依据权威文档 shun/docs/新结构注释设计.md：
#    第 38 行「〈方〉 = 主句里的**宾语内容**（含形式名词化 〜こと + 后续助词）」
#    第 100 行「角色标签和括号必须配套：[]↔连接/副词，［］↔从句，〈〉↔宾语内容，（）↔谓语」
# ⇒ 〈〉 只放 role 含「宾语」的块；其余（表语/情感对象/小主语/名词短语/主题内容…）
#    一律归 []（独立成分）—— 用户的定义是「[] 连接/独立成分」
OBIN = re.compile(r'宾语|对象语')   # ⚠️ 不能只写「对象」——「对象**状语**」是状语不是宾语
# 旧规则（状语类）已被上面的规则覆盖，保留仅为兼容日志
ADVERBIAL = re.compile(r'状语|副词|方式|手段|时间|地点|程度|频率|数量|范围|连接|并列|顺接|转折')


def retag(t):
    """ep001 -> s001"""
    m = re.match(r'^ep(\d+)$', t or '')
    return 's%03d' % int(m.group(1)) if m else t


def norm_bracket_semantics(frame):
    """
    口径归一（依据权威文档）：
      〈〉 只放「宾语」类 role；其余 〈〉 一律改 []（独立成分）。
    返回 (frame, 改动数)
    """
    out, changed = [], 0
    for f in frame:
        g = dict(f)
        if g.get('b') == '〈〉' and not OBIN.search(g.get('role') or ''):
            g['b'] = '[]'
            changed += 1
        out.append(g)
    return out, changed


# 音效/纯标点行的识别（⇒ kind='sfx'）
SFX_RE = re.compile(r'^\s*(\[[^\]]*\]|♪[〜♪\s]*|[・.。,、\s]+)\s*$')
# 连续的 ASCII 词片段 ⇒ 英文
# 🔴 但**只剥「≥2 个 ASCII 词」的片段**：单个 ASCII 词多半是品牌名/缩写
#    （"Shunと日本語…"、"NHK協会"）⇒ 剥了会把日文行的开头切掉
EN_SEG = re.compile(r'[A-Za-z][A-Za-z\'\-\.]*(?:[ /]+[A-Za-z][A-Za-z\'\-\.]*)+')


def split_en(ja):
    """
    把 ja 拆成 (日文部分, 英文部分)。

    🔴 2026-10-25 修正一个真 bug：我原来用「整块 ASCII 字母占比 > 50%」判英文
       ⇒ 「Shunと日本語ポッドキャスト2初心者Beginners Genki 1 Level」
       ASCII 占比 55% ⇒ **整句被判成英文吞掉** ⇒ t 变空（10 句空 t 的真凶）
       ⇒ 正确做法：**逐片段剥离** —— 只删掉连续的 ASCII 词片段，日文一字不动。
    """
    en_parts = []

    def _take(m):
        seg = m.group(0).strip()
        if seg:
            en_parts.append(seg)
        return ' '

    jp = EN_SEG.sub(_take, ja)
    jp = re.sub(r'[ \t　]{2,}', ' ', jp).strip()
    # 🔴 剥完如果**一个日文字都没有** ⇒ 整句本来就是英文行 ⇒ 保留原文（绝不产空句）
    if not re.search(r'[぀-ヿ一-鿿]', jp):
        return ja, ''
    return jp, ' / '.join(en_parts)


def detect_kind(t, en, zh):
    """speech（台词）｜ sfx（音效/纯标点）｜ en（整句英文）"""
    if not (t or '').strip():
        return 'en' if en else 'sfx'
    if SFX_RE.match(t):
        return 'sfx'
    if not re.search(r'[぀-ヿ一-鿿]', t):
        return 'en'          # 整行是英文（不管 en 字段有没有内容）
    return 'speech'


def strip_english(ja, frame):
    """
    剥出英文对照 ⇒ 日文原文干净，英文单独走 en 字段。

    🔴 起因（2026-10-25 peer 复核）：平台通道产的原文末尾/词后带**英文对照**
       （例："このレストランは、色々な食べ物があります。There are various foods..."）
       而这些英文被标成了 `[]`/`［］` 块（role=英文翻译）⇒
        ① 成分层的「谓语块必须在末尾」判据失效（19% 句子不合格）
        ② 用户看的日文精读页里混着英文，很怪
    ⇒ 修法（第二版）：**逐片段剥离**（不是整块判英文）⇒ 日文原文干净、且**绝不把句子剥空**
    ⇒ 剥完为空 ⇒ 整句就是英文行 ⇒ t 保留原文、kind='en'（不留空句）
    返回 (日文, frame(去掉英文块), 英文串)
    """
    en_parts = []
    drop = []
    for i, f in enumerate(frame):
        ss = f.get('s') or ''
        if '英文' in (f.get('role') or ''):
            en_parts.append(ss)
            drop.append(i)
    keep = [i for i in range(len(frame)) if i not in set(drop)]
    jp, en_auto = split_en(ja)
    en = ' / '.join([x for x in en_parts if x.strip()] + ([en_auto] if en_auto else []))
    # 🔴 t 不得为空：剥完空了 ⇒ 说明整句是英文行 ⇒ 保留原文 + kind='en'
    if not jp.strip():
        return ja, [frame[i] for i in keep], en
    newframe = [frame[i] for i in keep]
    # frame 里那些「其实是英文」的块也去掉（逐片段判，不整块吞）
    kept = []
    for f in newframe:
        ss = f.get('s') or ''
        if ss and not re.search(r'[぀-ヿ一-鿿]', ss):
            continue          # 纯 ASCII 块 ⇒ 英文，不进成分层
        kept.append(f)
    return jp, kept, en


def add_positions(ja, frame):
    """
    给每个块算 p=[start,end]（游标顺序推进）。
    🔴 2026-10-25 peer 复核的衍生问题：frame 是**分层**语义 —— 从句整体块（［］）
       和它内部的成分块（〈〉/[]）**不能同时出现**（会重复覆盖同一段文字）。
       判据：块在 cur 之后找不到 ⇒ 被前一块（外层）覆盖 ⇒ 丢弃（外层优先，
       与设计文档样例一致：frame=[しあわせの神様は(［］), この家に(〈〉), …]）。
       被丢弃的块记进 sidecar ⇒ 信息不丢（以后做 blocks[].parts 时可用）。
    """
    out = []
    dropped = []
    cur = 0
    for f in frame:
        g = dict(f)
        s = g.get('s') or ''
        pos = ja.find(s, cur)
        if pos < 0:
            if ja.find(s) >= 0:
                dropped.append({'i': len(out), 's': s, 'b': g.get('b'), 'role': g.get('role'),
                                'covered_by_end': cur})
                continue          # 被外层块覆盖 ⇒ 丢弃（外层优先）
            continue              # 真不逐字（frame_guard 已拦，这里兜底）
        g['p'] = [pos, pos + len(s)]
        cur = pos + len(s)
        out.append(g)
    return out, dropped


def main():
    os.makedirs(OUTD, exist_ok=True)
    tm = json.load(open(TAGMAP))
    by_tag = {e['tag']: e for e in tm}
    by_new = {retag(e['tag']): e for e in tm}

    n_ep = n_sent = n_drop = n_fix = n_adv = n_cov = n_en = 0
    sidecar = {}
    index = []
    for src in sorted(glob.glob(os.path.join(PROD, '*.json'))):
        base = os.path.basename(src)
        if not re.match(r'^(ep|s)\d+\.json$', base):
            continue
        d = json.load(open(src))
        old_tag = d.get('tag')
        new_tag = retag(old_tag)
        meta = by_new.get(new_tag, {})
        sents = []
        for si, it in enumerate(d.get('sentences', [])):
            ja = it.get('ja') or ''
            zh = (it.get('zh') or '').strip()
            fr = it.get('frame') or []
            fr2, probs, reps = frame_guard.check_frame(ja, fr)
            n_drop += len(probs)
            n_fix += len(reps)
            fr3, nadv = norm_bracket_semantics(fr2)
            n_adv += nadv
            # 🔴 剥英文对照（英文不是语法成分）⇒ 日文干净 + 谓语块才能在末尾
            ja2, fr3b, en = strip_english(ja, fr3)
            if en:
                n_en += 1
                ja, fr3 = ja2, fr3b
            fr4, dropped = add_positions(ja, fr3)
            for dp in dropped:
                sidecar.setdefault(new_tag, []).append(
                    {'sent': si, 'i': dp['i'], 's': dp['s'], 'b': dp['b'],
                     'role': dp['role'], 'why': 'covered-by-outer-block'})
                n_cov += 1
            sents.append({
                't': ja,
                'en': en,            # 英文对照（原文里带的，剥出来）
                'kind': detect_kind(ja, en, zh),   # speech | sfx | en
                'ru': [],            # 注音：peer 用本地 ruby.py 算（零额度）⇒ 留空
                'w': [],             # 词：同上
                'zh': zh,
                'gs': {'frame': fr4, 'src': 'llm'},
                's': it.get('s'),
                'e': it.get('e'),
            })
        n_sent += len(sents)
        n_ep += 1
        title = meta.get('title') or old_tag
        desc = '【N5-N4】%s / Japanese Podcast for Beginners' % title
        out = {
            'tag': new_tag,
            'ep': meta.get('ep') or int(re.sub(r'\D', '', new_tag) or 0),
            'title': title,
            'desc': desc,
            'dur': meta.get('dur') or d.get('duration') or 0,
            'audio': meta.get('url') or d.get('audio') or '',
            'sents': sents,
        }
        with open(os.path.join(OUTD, new_tag + '.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
        index.append({'tag': new_tag, 'ep': out['ep'], 'title': title, 'desc': desc,
                      'dur': out['dur'], 'audio': out['audio'], 'n': len(sents)})

    # 🔴 index.json 是 peer 的共享文件 ⇒ **不在这里写**（会覆盖人家的 13 集）
    #    由 tools/merge_index.py 负责：拉线上当基底 → 只加我的条目 → compare-and-swap 推

    # tagmap 改 tag 前缀
    for e in tm:
        e['tag'] = retag(e['tag'])
    with open(TAGMAP, 'w', encoding='utf-8') as f:
        json.dump(tm, f, ensure_ascii=False, separators=(',', ':'))

    # prod / cues 文件改名 + 内部 tag 改
    for sub in ('prod', os.path.join('work', 'cues')):
        dd = os.path.join(ROOT, sub)
        if not os.path.isdir(dd):
            continue
        for p in glob.glob(os.path.join(dd, 'ep*.json')):
            b = os.path.basename(p)
            m = re.match(r'^(?:ep|s)(\d+)(_cues)?\.json$', b)
            if not m:
                continue
            suffix = m.group(2) or ''
            np = os.path.join(dd, 's%03d%s.json' % (int(m.group(1)), suffix))
            d = json.load(open(p))
            if isinstance(d, dict) and 'tag' in d:
                d['tag'] = 's%03d' % int(m.group(1))
                with open(np, 'w', encoding='utf-8') as f:
                    json.dump(d, f, ensure_ascii=False)
            else:
                shutil.copy(p, np)
            os.remove(p)

    print('=== 转换完成（零 API 调用）===')
    print('  集数: %d   句子: %d' % (n_ep, n_sent))
    # 🔴 断言：归一后 〈〉 的 role 必须都含「宾语/对象语」（权威文档第 100 行）
    bad = []
    for fn2 in sorted(os.listdir(OUTD)):
        if not re.match(r'^s\d+\.json$', fn2):
            continue
        d2 = json.load(open(os.path.join(OUTD, fn2), encoding='utf-8'))
        for si2, s2 in enumerate(d2['sents']):
            for b2 in s2['gs']['frame']:
                if b2['b'] == '〈〉' and not OBIN.search(b2.get('role') or ''):
                    bad.append((fn2, si2, b2['role'], b2['s']))
    print('  断言 〈〉role 非宾语: %d 处 %s' % (len(bad), '✅' if not bad else '❌ ' + str(bad[:5])))
    if bad:
        raise SystemExit(1)
    print('  丢弃块(不逐字): %d   修复块(剥括号): %d   口径归一(〈〉→[]): %d   丢弃块(被外层覆盖): %d'
          % (n_drop, n_fix, n_adv, n_cov))
    print('  剥英文对照: %d 句（英文进 en 字段，日文干净）' % n_en)
    sp = os.path.join(OUTD, '..', 'frame_overlap.json')
    with open(os.path.normpath(sp), 'w', encoding='utf-8') as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=1)
    print('  sidecar(被覆盖的内部成分块): %s' % os.path.normpath(sp))
    print('  产出: %s/s*.json（%d 集）' % (OUTD, n_ep))
    print('  索引: 交给 tools/merge_index.py（共享文件，不在这里写）')


if __name__ == '__main__':
    main()
