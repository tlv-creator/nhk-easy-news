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

# 状语类 role 关键词：这些绝不能是 〈〉（宾语）
ADVERBIAL = re.compile(r'状语|副词|方式|手段|时间|地点|程度|频率|数量|范围|连接|并列|顺接|转折')


def retag(t):
    """ep001 -> s001"""
    m = re.match(r'^ep(\d+)$', t or '')
    return 's%03d' % int(m.group(1)) if m else t


def norm_bracket_semantics(frame):
    """口径归一：〈〉 + 状语类 role ⇒ 改 []。返回 (frame, 改动数)"""
    out, changed = [], 0
    for f in frame:
        g = dict(f)
        if g.get('b') == '〈〉' and ADVERBIAL.search(g.get('role') or ''):
            g['b'] = '[]'
            changed += 1
        out.append(g)
    return out, changed


def strip_english(ja, frame):
    """
    剥出英文对照 ⇒ 日文原文干净，英文单独走 en 字段。

    🔴 起因（2026-10-25 peer 复核）：平台通道产的原文末尾/词后带**英文对照**
       （例："このレストランは、色々な食べ物があります。There are various foods..."）
       而这些英文被标成了 `[]`/`［］` 块（role=英文翻译）⇒
        ① 成分层的「谓语块必须在末尾」判据失效（19% 的句子不合格）
        ② 用户看的日文精读页里混着英文，很怪
    ⇒ 判据：role 含「英文」的块 ⇒ 它是元信息，不是语法成分 ⇒ 从 t 里剥出
    ⇒ 剥完重算 p（删除会让后面所有字符位置移动 ⇒ 必须重算，不能沿用）
    返回 (日文, frame(去掉英文块), 英文串)
    """
    en_parts = []
    drop = []
    for i, f in enumerate(frame):
        ss = f.get('s') or ''
        ascii_ratio = (sum(1 for c in ss if c.isascii() and c.isalpha()) / max(1, len(ss)))
        if '英文' in (f.get('role') or '') or ascii_ratio > 0.5:
            en_parts.append(ss)
            drop.append(i)
    if not en_parts:
        return ja, frame, ''
    keep = [i for i in range(len(frame)) if i not in set(drop)]
    # 从 ja 里删掉英文片段（连同它前面的空白）
    out = ja
    for s in sorted(set(x for x in en_parts if x), key=len, reverse=True):
        if s in out:
            out = out.replace(s, '', 1)
    # 清掉残留的「词+空格」尾巴（如 "経験する to experience" → "経験する"）
    out = re.sub(r'[ \t　]+$', '', out)
    out = re.sub(r'^[ \t　]+', '', out)
    out = re.sub(r'([、。！？])[ \t　]+', r'\1', out)
    newframe = [frame[i] for i in keep]
    en = ' / '.join(x.strip() for x in en_parts if x.strip())
    return out, newframe, en


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
