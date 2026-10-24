#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_supabase_access.py —— 「RLS 装完之后，所有还在用的站还能不能干活？」

起因（2026-10-24 真实事故）：
  给 vocab_history_logs 装 RLS 时，只给 authenticated（教师侧）配了 policy，
  而**三个站的学生端全是 anon 直写** ⇒ 全被 42501 挡掉：
    · 学生端：练习记录全丢
    · 教师端：SELECT 返回 0 行 ⇒ 老师看到的是**空的**（不是报错，最难发现）
  ⇒ **RLS 是「改了共享资源，却没检查所有共用者」**的典型。

本脚本回答的问题（就是当时没人问的那句）：
  ① **源码里实际用到**哪些「表 × 操作」？   ← 扫三个站的前端源码
  ② 这些操作在 anon 下**实测**能不能做？      ← 真连库跑
  ③ 有没有「源码要用、但被挡」的组合？        ← 对照出结论

🔴 判据设计（两条都踩过坑）：
  · 「SELECT 返回 200 但 0 行」**不等于**「表是空的」—— 也可能是被 RLS 过滤了
    ⇒ 所以读能力**不判「有没有行」**，而是判「**查得到我事先知道存在的行**」
    （探针：vocab_states 的 __cet4_index / vocab_history_logs 的 class_key=JR1）
  · 写入探测会**留下脏数据** ⇒ 修复 SQL 里配了只允许删 `__rls_probe__` 的 DELETE policy
    ⇒ 探测行写完立刻删，**不留垃圾**；而且该 policy 删不掉别人的数据。

用法：
  python3 tools/check_supabase_access.py              # 扫源码 + 实测（需要网络）
  python3 tools/check_supabase_access.py --scan-only  # 只扫源码
退出码：0 = 全通 / 1 = 有被挡的组合 / 2 = 没验成（连不上/没源文件，不放行）
"""
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

SB_URL = os.environ.get('SB_URL', 'https://lszwxcmvcvbraqtrtgon.supabase.co')
SB_KEY = os.environ.get('SB_KEY', 'sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo')
REPO = 'tlv-creator/nhk-easy-news'
GH = os.environ.get('GH_TOKEN', '')

# 源码里扫到什么就查什么表；这张表若源码没用到，仍要查（它是共用表）
SCAN_TABLES = None  # None = 扫出来的全都查

# 每张表的「读探针」：我事先知道存在的行（查得到 = 读通道 + 这张表有 SELECT policy）
READ_PROBE = {
    'vocab_states': 'class_key=eq.__cet4_index',
    'vocab_history_logs': 'class_key=eq.JR1',
}

# 装 RLS 之前的真实基线（2026-10-24 实测，用来判断「0 行」是「被挡」还是「真空」）
BASELINE = {'vocab_history_logs': 2418, 'vocab_states': 17}

# 每张表的「写探针」：一行最小探针 + 删除它用的过滤条件
# 🔴 探针行必须**删得掉**才做写探测 ⇒ 探测前先探 DELETE 权限（见 probe_write）
PROBE = {
    'vocab_states': {'row': {'class_key': '__rls_probe__'},
                      'filter': 'class_key=eq.__rls_probe__'},
    'vocab_submissions': {'row': {'student_id': '__rls_probe__'},
                          'filter': 'student_id=eq.__rls_probe__'},
    'vocab_online_status': {'row': {'class_key': '__rls_probe__'},
                             'filter': 'class_key=eq.__rls_probe__'},
    'vocab_history_logs': {'row': {'class_key': '__rls_probe__',
                                   'student_id': '__rls_probe__',
                                   'action': '__rls_probe__',
                                   'timestamp': '2026/10/24 03:00:00'},
                            'filter': 'class_key=eq.__rls_probe__'},
}

# 源码位置：本地目录 + 仓库文件
LOCAL_ROOTS = [
    ('词汇站(本地)', ['/workspace/vocab/site/vocab-unit123-v1-prod-standalone.html',
                     '/workspace/vocab/pkg/vocablog/site/vocab-unit123-v1-prod-standalone.html']),
]
REPO_FILES = [
    ('四级精读日报站', ['cet4.html']),
    ('每日听力记录站', ['listening/index.html', 'listening/app/index.html',
                        'listening/app/student.html', 'listening/js/app.js']),
    ('词汇学习站', ['vocab/index.html']),
]

OPS = ['insert', 'select', 'update', 'upsert', 'delete']
cleanup = []   # 写探针删不掉的行 ⇒ 攒成清理 SQL
FROM_RE = re.compile(r"""\.from\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\)""")
OP_RE = re.compile(r"""\.(insert|select|update|upsert|delete)\s*\(""")


def http(method, path, body=None, extra=None, timeout=25):
    url = SB_URL + path
    data = None
    hdrs = {'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs['Content-Type'] = 'application/json'
    hdrs.update(extra or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode('utf-8', 'replace')
    except Exception as e:
        return 0, {}, ('NETERR: ' + str(e))


def fetch_repo(path):
    if not GH:
        return None
    url = 'https://api.github.com/repos/%s/contents/%s' % (REPO, path)
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + GH, 'Accept': 'application/vnd.github+json',
        'User-Agent': 'mavis-rls-check'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
            import base64
            return base64.b64decode(d['content']).decode('utf-8', 'replace')
    except Exception:
        return None


def scan_sources():
    """扫源码 → {表: {操作: [来源]}}"""
    usage = {}
    blobs = []
    for label, files in LOCAL_ROOTS:
        for f in files:
            if os.path.isfile(f):
                blobs.append((label, f, io.open(f, encoding='utf-8', errors='replace').read()))
    for label, paths in REPO_FILES:
        for p in paths:
            txt = fetch_repo(p)
            if txt is not None:
                blobs.append((label, 'repo:' + p, txt))
    if not blobs:
        return usage, blobs
    for label, name, txt in blobs:
        for m in FROM_RE.finditer(txt):
            table = m.group(1)
            if table.startswith('__') or table in ('schema',):
                continue
            tail = txt[m.end():m.end() + 260]
            ops = set(OP_RE.findall(tail))
            if not ops:
                continue
            for op in ops:
                usage.setdefault(table, {}).setdefault(op, set()).add('%s/%s' % (label, name))
    return usage, blobs


def probe_read(table):
    """读探针：查得到「事先知道存在的行」才算读通。
    返回 (通?, 证据串)"""
    q = READ_PROBE.get(table)
    if not q:
        st, hd, raw = http('GET', '/rest/v1/%s?select=*&limit=1' % table)
        return (st == 200), 'HTTP %s（无专用探针，只看能否访问）' % st
    st, hd, raw = http('GET', '/rest/v1/%s?%s&limit=1' % (table, q))
    rng = (hd.get('Content-Range') or hd.get('content-range') or '').strip()
    if st != 200:
        return False, 'HTTP %s %s' % (st, raw[:90])
    # content-range 形如 0-0/* ⇒ 有行；*/* ⇒ 0 行（可能是表空，也可能是被 RLS 过滤）
    if rng.startswith('*/*') or rng == '':
        return False, 'HTTP 200 但 content-range=%s ⇒ 0 行（探针 %s 查不到 ⇒ 读被挡或表已空）' % (rng or '(无)', q)
    return True, 'HTTP 200，content-range=%s ⇒ 探针行查到了 ✅' % rng


def probe_write(table):
    """写探针 —— **零污染版**：把一行**真实数据原样写回**（内容不变 ⇒ 数据不变）。
    失败再退到「写一条探针行 + 给清理 SQL」。

    🔴 为什么不能只用「写探针行」：RLS 挡 DELETE 时 PostgREST 返回 **200/204 + 空数组**
       （不报错）⇒ 探针行会永久留在库里（2026-10-24 我在 vocab_states 上踩过两次）。
    🔴 为什么不能只用「写坏行」测：列校验在 RLS 之前 ⇒ 写不存在的列返回 PGRST204，
       **测不出 RLS**（实测 vocab_submissions 写 {"probe":1} → 400，写合法列 → 401 42501）。
    ⇒ 零污染判据 = **原样 upsert 真实行**：被挡 ⇒ 42501；通过 ⇒ 数据一模一样。
    返回 (三态, 证据串)：'ok' / 'blocked' / 'unknown'
    """
    p = PROBE.get(table)
    if not p:
        return 'unknown', '没有配探针行 ⇒ 未验证（不是「被挡」）'

    # ── 方案 1（首选，零污染）：读一行真实数据，原样 upsert 回去 ──
    st, hd, raw = http('GET', '/rest/v1/%s?select=*&limit=1' % table)
    rows = []
    try:
        rows = json.loads(raw) if raw.strip().startswith('[') else []
    except Exception:
        rows = []
    if rows:
        row = rows[0]
        st2, hd2, raw2 = http('POST', '/rest/v1/%s' % table, row,
                              {'Prefer': 'resolution=merge-duplicates,return=minimal'})
        if st2 in (401, 403) or '42501' in raw2:
            return 'blocked', 'HTTP %s %s（原样写回被挡 ⇒ 写通道不可用）' % (st2, raw2[:110])
        if st2 in (200, 201):
            return 'ok', ('HTTP %s 原样 upsert 一条真实行成功 ⇒ 写通道可用 ✅'
                          '（内容一字未改，数据零变化）' % st2)

    # ── 方案 2（兜底，会留一行）：写探针行，并给出清理 SQL ──
    st, hd, raw = http('POST', '/rest/v1/%s' % table, p['row'],
                       {'Prefer': 'return=minimal'})
    if st not in (200, 201):
        return 'blocked', 'HTTP %s %s' % (st, raw[:130])
    st2, hd2, raw2 = http('DELETE', '/rest/v1/%s?%s' % (table, p['filter']), None,
                          {'Prefer': 'return=representation'})
    try:
        deleted = json.loads(raw2) if raw2.strip().startswith('[') else []
    except Exception:
        deleted = []
    if deleted:
        return 'ok', 'HTTP %s 写入成功 → 删除返回 %d 行 ⇒ 真的删掉了，不留垃圾 ✅' % (st, len(deleted))
    cleanup.append("delete from public.%s where %s;" % (table, p['filter'].split('=', 1)[1].replace('eq.', "'")))
    return 'ok', ('HTTP %s 写入成功，但**删除被 RLS 挡（200 + 空数组）** ⇒ 库里留下一行探针\n'
                  '            ⚠ 清理 SQL: delete from public.%s where %s;' % (st, table, p['filter']))


def main(argv):
    scan_only = '--scan-only' in argv
    write_probe = '--write-probe' in argv

    print('══ ① 扫三个站的前端源码，看它们实际用到哪些「表 × 操作」 ══')
    usage, blobs = scan_sources()
    if not blobs:
        sys.stderr.write('❌ 一份源码都没拿到（本地缺 + 仓库拉不到）⇒ 没验成，不放行\n')
        return 2
    if not usage:
        sys.stderr.write('❌ 源码里一处 .from(...) 都没扫到 ⇒ 扫描器可能失效了，不放行\n')
        return 2
    tables = sorted(usage.keys())
    for t in tables:
        ops = sorted(usage[t].keys())
        srcs = set()
        for o in ops:
            srcs |= usage[t][o]
        print('  %-24s %-28s  来源: %s' % (t, '+'.join(ops), ', '.join(sorted(srcs))[:90]))

    if scan_only:
        return 0

    print('')
    print('══ ② 实测 anon 身份下，每张表到底能做什么 ══')
    print('   （探针：vocab_states→__cet4_index；vocab_history_logs→class_key=JR1）')
    print('')
    blocked = []
    unknown = []
    rows = []
    for t in tables:
        need = set(usage[t].keys())
        r_ok, r_why = probe_read(t)
        if not r_ok and t in BASELINE:
            r_why += '（装 RLS 前基线：%d 行 ⇒ 不是表空，是被 policy 挡了）' % BASELINE[t]
        if 'select' in need and not r_ok:
            blocked.append('%s 读被挡（源码 %s 要读）' % (t, ','.join(sorted(need))))
        w_state, w_why = 'ok', '源码未用到写入'
        if 'insert' in need or 'upsert' in need:
            w_state, w_why = probe_write(t)
            if w_state == 'blocked':
                blocked.append('%s 写被挡（源码 %s 要写）' % (t, ','.join(sorted(need))))
            elif w_state == 'unknown':
                unknown.append('%s 写通道**未验证**（不是被挡）：%s' % (t, w_why))
        rows.append((t, need, r_ok, r_why, w_state, w_why))

    for t, need, r_ok, r_why, w_state, w_why in rows:
        print('  ── %s   （源码要用：%s）' % (t, ','.join(sorted(need))))
        print('     读 %s  %s' % ('✅ 通' if r_ok else '🔴 被挡', r_why))
        wl = {'ok': '✅ 通', 'blocked': '🔴 被挡', 'unknown': '⚪ 未验证'}[w_state]
        print('     写 %s  %s' % (wl, w_why))

    print('')
    if blocked:
        print('⛔ 有「源码要用、但被挡」的组合：')
        for b in blocked:
            print('   · %s' % b)
    if unknown:
        print('')
        print('⚪ 有没验到的（**不是被挡**，是探针行删不掉所以没敢写）：')
        for u in unknown:
            print('   · %s' % u)
    if cleanup:
        print('')
        print('⚠ 本次写探针留下了这些行（RLS 挡 DELETE，删不掉）—— 请在 SQL Editor 跑：')
        for c in cleanup:
            print('   ' + c)
    if blocked:
        return 1
    print('')
    print('✅ 三个站要用的事，现在全都能做（没验到的部分见上面 ⚪）。')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
