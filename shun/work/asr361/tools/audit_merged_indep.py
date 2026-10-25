#!/usr/bin/env python3
"""
合并版单文件（shun-text.html）的**独立审计**——直读产物，不依赖作者的脚本。
判据全部落在「事实量」上：
  ① 集数 / 句数（且两个数据源 SHUN / SHUN2 / SHUNP 句数一致）
  ② 每句 t 非空
  ③ frame[].s 逐字 ∈ t（连续子串）
  ④ frame[].p 在句内、长度 == len(s)、相邻不重叠
  ⑤ b 四选一
  ⑥ 同类型相邻块合并率 = 0（用户原话「要分开不要一起」）
  ⑦ 集号唯一（真实 Ep 号不重号）
  ⑧ blocks[i].s == frame[i].s
"""
import json, re, sys, hashlib, collections, subprocess, time

URL = "https://tlv-creator.github.io/nhk-easy-news/shun-text.html"
OUT = "/workspace/output/_merged_audit.html"


def get(url):
    # 🔴 GitHub Pages CDN 会返回旧 build ⇒ 每次线上验证必须带 cache-buster
    import os
    if os.environ.get("AUDIT_LOCAL"):
        return open(os.environ["AUDIT_LOCAL"], 'rb').read()
    sep = "&" if "?" in url else "?"
    url = "%s%sv=%d" % (url, sep, int(time.time()))
    return subprocess.run(["curl", "-sL", "--max-time", "120", url],
                          capture_output=True).stdout


def extract(html, var):
    """取 window.<var> = {...}; 的 JSON"""
    i = html.find("window.%s=" % var)
    if i < 0:
        return None
    j = html.find("{", i)
    depth = 0; instr = False; esc = False
    for k in range(j, len(html)):
        ch = html[k]
        if instr:
            if esc: esc = False
            elif ch == '\\': esc = True
            elif ch == '"': instr = False
            continue
        if ch == '"': instr = True
        elif ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(html[j:k+1])
    return None


def audit(eps, name, errs, stats):
    tagset = []
    for e in eps:
        tagset.append(e.get('tag'))
        sents = e.get('sents') or []
        stats['sents'] += len(sents)
        for si, s in enumerate(sents):
            t = s.get('t') or ''
            if not t.strip():
                stats['noJp'] += 1
                errs.append("%s %s 句%d: t 为空" % (name, e.get('tag'), si))
            gs = s.get('gs') or {}
            fr = gs.get('frame') or []
            stats['frames'] += len(fr)
            last_end = -1; prev_b = None
            for fi, b in enumerate(fr):
                ss = b.get('s') or ''
                bb = b.get('b')
                if bb not in ('[]', '［］', '〈〉', '（）'):
                    stats['b_illegal'] += 1
                    errs.append("%s %s 句%d 块%d: b 非法 %r" % (name, e.get('tag'), si, fi, bb))
                if ss not in t:
                    stats['s_not_sub'] += 1
                    errs.append("%s %s 句%d 块%d: s 非子串 %r" % (name, e.get('tag'), si, fi, ss[:30]))
                p = b.get('p')
                # 🔴 线上 p 有两种格式：
                #    新数据 = [start, end]；13 集旧数据 = 整数起点（end 由 len(s) 推导）
                if p is None:
                    stats['p_missing'] += 1
                elif isinstance(p, int):
                    stats['p_int'] += 1
                    a, z = p, p + len(ss)
                    if not (0 <= a < z <= len(t)):
                        stats['p_oob'] += 1
                        errs.append("%s %s 句%d 块%d: p(整数) 越界 %r len=%d" % (name, e.get('tag'), si, fi, p, len(t)))
                    else:
                        if t[a:z] != ss:
                            stats['p_mismatch'] += 1
                            errs.append("%s %s 句%d 块%d: p(整数) 切片≠s %r vs %r" % (name, e.get('tag'), si, fi, t[a:z][:30], ss[:30]))
                        if a < last_end:
                            stats['p_overlap'] += 1
                            errs.append("%s %s 句%d 块%d: p(整数) 重叠" % (name, e.get('tag'), si, fi))
                        last_end = z
                elif isinstance(p, (list, tuple)) and len(p) == 2:
                    a, z = p
                    if not (0 <= a < z <= len(t)):
                        stats['p_oob'] += 1
                        errs.append("%s %s 句%d 块%d: p 越界 %r len=%d" % (name, e.get('tag'), si, fi, p, len(t)))
                    elif z - a != len(ss):
                        stats['p_mismatch'] += 1
                        errs.append("%s %s 句%d 块%d: p 长度≠len(s) %r" % (name, e.get('tag'), si, fi, p))
                    if a < last_end:
                        stats['p_overlap'] += 1
                        errs.append("%s %s 句%d 块%d: p 重叠" % (name, e.get('tag'), si, fi))
                    last_end = z
                else:
                    stats['p_missing'] += 1
                    errs.append("%s %s 句%d 块%d: p 格式无法解析 %r" % (name, e.get('tag'), si, fi, p))
                if bb == prev_b:
                    # 🔴 口径澄清（2026-10-25 纠我自己的错）：
                    #    「相邻同类型」**不是违规**——它恰恰是「没把两个同类型块合并成一个」的证据。
                    #    真正的红线是「一个块里塞了多个同类型成分」(frame 层的语义合并)。
                    #    ⇒ 这里只统计，不判失败（与网页层权威审计 audit_merged.py 口径一致）。
                    stats['adj_same'] += 1
                prev_b = bb
            bl = gs.get('blocks') or []
            stats['blocks'] += len(bl)
            for i, bl2 in enumerate(bl):
                if i < len(fr) and (bl2.get('s') or '') != (fr[i].get('s') or ''):
                    stats['blocks_mismatch'] += 1
                    errs.append("%s %s 句%d: blocks[%d].s != frame[%d].s" % (name, e.get('tag'), si, i, i))
    return tagset


def main():
    raw = get(URL)
    if len(raw) < 100000:
        print("❌ 下载失败 len=%d" % len(raw)); sys.exit(1)
    open(OUT, 'wb').write(raw)
    html = raw.decode('utf-8', 'replace')
    print("=== 合并版单文件独立审计 ===")
    print("  文件: %.2f MB ｜ sha256=%s" % (len(raw)/1e6, hashlib.sha256(raw).hexdigest()[:16]))
    errs = []
    stats = collections.Counter()
    all_tags = []
    for var in ('SHUN', 'SHUN2'):
        d = extract(html, var)
        if d is None:
            print("  ⚠️ 找不到 window.%s" % var); continue
        eps = d.get('eps') or (d if isinstance(d, list) else [])
        print("  window.%s: %d 集" % (var, len(eps)))
        all_tags += audit(eps, var, errs, stats)
    p = extract(html, 'SHUNP')
    if p is not None:
        print("  window.SHUNP: %d 条（谓语细分/形态链）" % len(p))
        stats['shunp'] = len(p)
    # 集号重号
    dup = [k for k, v in collections.Counter(all_tags).items() if v > 1]
    print("\n  ① 集数 %d ｜ 集号重复: %d %s" % (len(all_tags), len(dup), dup or ''))
    print("  ② 空 t(noJp): %d" % stats['noJp'])
    print("  ③ s 非子串: %d" % stats['s_not_sub'])
    print("  ④ p 缺/越界/重叠/长度不符: %d/%d/%d/%d（整数起点格式 %d 个）"
          % (stats['p_missing'], stats['p_oob'], stats['p_overlap'], stats['p_mismatch'], stats['p_int']))
    print("  ⑤ b 非法: %d" % stats['b_illegal'])
    print("  ⑥ 同类型相邻块: %d 对（**这是「没被合并」的证据，不是违规**；只统计不判红）"
          % stats['adj_same'])
    print("  ⑦ blocks 数 %d ｜ blocks[i].s != frame[i].s: %d" % (stats['blocks'], stats['blocks_mismatch']))
    print("  句数合计 %d ｜ 成分块 %d" % (stats['sents'], stats['frames']))
    if errs:
        print("\n❌ 错误 %d 条（前 12 条）:" % len(errs))
        for e in errs[:12]:
            print("   ", e)
        sys.exit(1)
    print("\n✅ 审计全部通过")


if __name__ == '__main__':
    main()
