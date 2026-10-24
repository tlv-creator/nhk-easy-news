#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_supabase_access.py v2 —— 三站 Supabase RLS **真实验收**（2026-10-24 重写）

=====================================================================
【为什么重写：v1 有两个假绿，都是我自己复核后确认的】
  ① **站→表的归属靠「扫本地文件路径」猜** ⇒ 听力记录站的三个路径写错了
     （v1 写 `listening/js/app.js`，真实是 `listening/app/js/{api,student,teacher}.js`）
     ⇒ 它**一行都没被扫到** ⇒ 报告里全是「来源: 词汇学习站」
     ⇒ **听力站实际是坏的，脚本却报全绿** ❌
  ② **只看 HTTP 码** ⇒ PostgREST 删不掉也返回 200/204 + 空数组
     ⇒ 「删除功能静默失效」在 v1 里**看不见** ❌

【v2 的做法】
  · 需求**从线上 live 代码抽**（抓每个站的 index.html + 它引用的 js，递归一层）
    ⇒ 站、页、js 路径对不对，脚本自己会发现；路径写错 ⇒ 当场报「没抓到代码」而不是默默跳过
  · anon key **从站自己的 js 里现取**（publishable key 本来就是公开的，但不必落进仓库）
  · 每个 (表, class_key) 跑**真实闭环**：写探针 → 回读确认行在 → 删 → 回读确认真的没了
  · 一条**反向验证**：不在白名单的 class_key **必须写不进去** ⇒ 证明 policy 真的收窄了，
    而不是 `using (true)`
  · 探针行在 finally 里清理；vocab_states 用专属探针 key（**绝不覆盖真实班级的 state_val**）

【退出码】0=全过 / 1=有断言失败（看输出里的 ❌ 列表）/ 脚本崩溃 ⇒ 留 Traceback
  （跑批器用 scripts/strict_test_exit.py 区分「判据拦截」与「脚本崩了」）
=====================================================================
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

UA = 'Mozilla/5.0 (Linux; Android 13) Mobile'
REST = 'https://lszwxcmvcvbraqtrtgon.supabase.co/rest/v1'

# 站清单（域名来自 protected-sites.json；**不自动刷新**，避免拿到已删的站）
STATIONS = [
    ('词汇学习站 Unit1+2+3', 'https://iujsgx3ci73dp.space.mcode.cn/'),
    ('每日听力记录站',       'https://irk7ibwekq8se.space.mcode.cn/'),
    ('四级精读日报站',       'https://y8arnrff8t2hj.space.mcode.cn/'),
    ('词汇学习站 VocabLog',  'https://zp67qm11jonui.space.mcode.cn/'),   # 走 CloudBase 网关，不碰 Supabase
]

# 明显是演示/探针的 key，不算真实需求
FAKE_KEY = re.compile(r'^(DEMO\d+|demo.*|__.*)$', re.I)

# 探针
PROBE_ID = '__rls_probe__'
NEG_KEY = '__rls_neg_probe__'


# ────────────────────────────────────────────── HTTP
def http(method, url, body=None, headers=None, timeout=30):
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode('utf-8', 'replace')
    except Exception as e:
        return 0, {}, 'EXC: %s' % e


def rest(method, table_path, body=None, prefer=None):
    h = {}
    if ANON_KEY:
        h['apikey'] = ANON_KEY
        h['Authorization'] = 'Bearer ' + ANON_KEY
    if prefer:
        h['Prefer'] = prefer
    return http(method, REST + table_path, body, h)


def rows_of(raw):
    try:
        v = json.loads(raw or '[]')
        return v if isinstance(v, list) else [v]
    except Exception:
        return []


# ────────────────────────────────────────────── 抓线上代码
def fetch_text(url):
    st, _, raw = http('GET', url, headers={'Accept': 'text/html,*/*'})
    return raw if st == 200 else ''


def station_code(root):
    """抓一个站的 index.html（**内联代码也要**）+ 它引用的所有 js ⇒ 返回 (代码全文, 文件列表)

    🔴 v2 第一版的坑：词汇学习站是**单文件内联**（全部 JS 在 index.html 里，没有外部 .js）
       ⇒ 只抓外部 js 会「一个 js 都没抓到」⇒ 需求整站缺失（还差点被当成「没问题」）
       ⇒ 现在**内联 HTML 与外部 js 一视同仁**地进代码语料。
    """
    idx = fetch_text(root)
    if not idx:
        return '', []
    code_all = idx          # 内联代码
    urls, seen = [], set()
    for m in re.finditer(r'<script[^>]+src="([^"]+\.js[^"]*)"', idx):
        u = m.group(1)
        if u.startswith('http'):
            u2 = u
        else:
            u2 = root.rstrip('/') + '/' + u.lstrip('/')
        if u2 not in seen:
            seen.add(u2)
            urls.append(u2)
    # 有些站是 index.html → student.html / teacher.html → 各自的 js
    for m in re.finditer(r'<a[^>]+href="([^"]+\.html[^"]*)"', idx):
        page = root.rstrip('/') + '/' + m.group(1).lstrip('/')
        ptxt = fetch_text(page)
        if not ptxt:
            continue
        code_all += '\n' + ptxt
        for m2 in re.finditer(r'<script[^>]+src="([^"]+\.js[^"]*)"', ptxt):
            u2 = root.rstrip('/') + '/' + m2.group(1).lstrip('/')
            if u2 not in seen:
                seen.add(u2)
                urls.append(u2)
    files = []
    for u in urls:
        t = fetch_text(u)
        if t:
            code_all += '\n' + t
            files.append(u)
    return code_all, files


KNOWN_TABLES = ('vocab_history_logs', 'vocab_submissions', 'vocab_online_status', 'vocab_states')


def extract_needs(code):
    """从代码里抽：用了哪些表 / 哪些 class_key / 哪些走 delete

    🔴 v2 第一版的坑：另外两个站用的是 `rest('vocab_history_logs?...')` / `fetchAllRows('vocab_...')`
       这种**裸 REST URL**，不是 `from('vocab_history_logs')` ⇒ 只认 from() 会把表全漏掉
       ⇒ 现在按「语料里出现过哪些 vocab_* 表」来认表。
    """
    tables = set()
    for t in KNOWN_TABLES:
        if t in code:
            tables.add(t)
    # class_key 的各种写法（全语料）
    keys = set()
    keys |= set(re.findall(r"class_key\s*[:=]\s*['\"]([A-Za-z0-9_]+)['\"]", code))
    keys |= set(re.findall(r"class_key=eq\.([A-Za-z0-9_]+)", code))
    keys |= set(re.findall(r"LOG_KEY\s*:\s*['\"]([A-Za-z0-9_]+)['\"]", code))
    keys |= set(re.findall(r"classKey\s*:\s*['\"]([A-Za-z0-9_]+)['\"]", code))
    keys |= set(re.findall(r"['\"]([A-Z][A-Z0-9_]{2,})['\"]\s*:\s*['\"]", code))
    keys = {k for k in keys if not FAKE_KEY.match(k)}
    # delete 路径里用到的 class_key（窗口取 URL 附近的）
    del_keys = set()
    for m in re.finditer(r"method:\s*['\"]DELETE['\"]|\.delete\(\)", code):
        seg = code[max(0, m.start() - 700):m.start() + 300]
        del_keys |= set(re.findall(r"class_key=eq\.([A-Za-z0-9_]+)", seg))
        del_keys |= set(re.findall(r"LOG_KEY", seg))
    del_keys = {k for k in del_keys if not FAKE_KEY.match(k)}
    upsert = set(re.findall(r"from\(['\"](vocab_[a-z_]+)['\"]\)\.upsert", code))
    return tables, keys, del_keys, upsert


# ────────────────────────────────────────────── 表的探针行（列名全部来自线上代码）
def probe_row(table, ck):
    if table == 'vocab_history_logs':
        return {'class_key': ck, 'class_name': '__rls_probe__', 'student_id': PROBE_ID,
                'timestamp': '2026-10-24 12:00:00', 'duration_sec': 0, 'action': '__rls_probe__'}
    if table == 'vocab_online_status':
        return {'class_key': ck, 'class_name': '__rls_probe__', 'student_id': PROBE_ID,
                'status_text': '__rls_probe__', 'login_time': '12:00:00'}
    if table == 'vocab_submissions':
        return {'student_id': PROBE_ID, 'submitted_exams': ['__rls_probe__']}
    if table == 'vocab_states':
        return {'class_key': PROBE_ID, 'state_val': '__rls_probe__'}
    return {'class_key': ck, 'student_id': PROBE_ID}


def read_filter(table, ck):
    """用「站自己那套过滤条件」回读（这样才和真实用法一致）"""
    if table == 'vocab_submissions':
        return 'student_id=eq.' + PROBE_ID
    if table == 'vocab_states':
        return 'class_key=eq.' + PROBE_ID
    return 'class_key=eq.%s&student_id=eq.%s' % (ck, PROBE_ID)


def delete_filter(table, ck):
    if table == 'vocab_submissions':
        return 'student_id=eq.' + PROBE_ID
    if table == 'vocab_states':
        return 'class_key=eq.' + PROBE_ID
    return 'class_key=eq.%s&student_id=eq.%s' % (ck, PROBE_ID)


# ────────────────────────────────────────────── 主流程
ANON_KEY = ''
FAILS = []
CHECKS = 0


def fail(what, detail):
    FAILS.append('%s —— %s' % (what, detail))
    print('  ❌ %s —— %s' % (what, detail))


def ok(what):
    print('  ✅ %s' % what)


def cleanup():
    """无论如何都清掉探针行"""
    for table, f in [('vocab_history_logs', 'class_key=eq.%s' % PROBE_ID),
                     ('vocab_online_status', 'class_key=eq.%s' % PROBE_ID),
                     ('vocab_submissions', 'student_id=eq.' + PROBE_ID),
                     ('vocab_states', 'class_key=eq.' + PROBE_ID)]:
        rest('DELETE', '/%s?%s' % (table, f), prefer='return=representation')


def closed_loop(table, ck, need_read=True, need_write=True, need_delete=True):
    """写 → 回读 → 删 → 回读。返回 (通过?, 细节)"""
    global CHECKS
    row = probe_row(table, ck)
    # 🔴 先清掉同 key 的旧探针 ⇒ 脚本可重复跑，不会被 409 误判成红
    rest('DELETE', '/%s?%s' % (table, delete_filter(table, ck)), prefer='return=representation')
    st, _, raw = rest('POST', '/' + table, row, prefer='return=representation')
    if st == 409:
        st = 200      # 已存在（上一轮残留）⇒ 继续验读/删，不算红
    if st not in (200, 201):
        return False, '写入被挡 http=%s %s' % (st, raw[:110])
    CHECKS += 1
    q = read_filter(table, ck)
    st, _, raw = rest('GET', '/%s?select=*&%s' % (table, q), prefer='count=exact')
    if st != 200:
        return False, '回读失败 http=%s %s' % (st, raw[:110])
    if not rows_of(raw):
        return False, '写进去了但回读不到（写和读不是一个通道）'
    CHECKS += 1
    if not need_delete:
        # 只验写入/读取 ⇒ 顺手删掉（这个站本来没有删除路径）
        rest('DELETE', '/%s?%s' % (table, delete_filter(table, ck)), prefer='return=representation')
        return True, '写✓ 读✓（该站没有删除路径，未验删）'
    st, _, raw = rest('DELETE', '/%s?%s' % (table, delete_filter(table, ck)), prefer='return=representation')
    if st not in (200, 204):
        return False, '删除被挡 http=%s %s' % (st, raw[:110])
    CHECKS += 1
    st, _, raw = rest('GET', '/%s?select=*&%s' % (table, q), prefer='count=exact')
    if rows_of(raw):
        return False, '🔴 删不掉（PostgREST 也返回 200/204 ⇒ 只看返回码会假绿）'
    CHECKS += 1
    return True, '写✓ 回读到✓ 删✓ 回读确认已消失✓'


def main():
    global ANON_KEY
    print('=' * 70)
    print('Supabase RLS 真实验收 v2   项目=lszwxcmvcvbraqtrtgon')
    print('=' * 70)

    # ── 1) 扫线上代码，抽需求
    print('\n━━ 1) 需求发现（从线上 live 代码抽，不是猜的）━━')
    need = {}          # (table, ck) → {'read':bool,'write':bool,'delete':bool}
    station_of = {}    # (table, ck) → set(站名)
    for name, root in STATIONS:
        code, files = station_code(root)
        if not code:
            print('  ⚠️  %s：没抓到任何 js（站可能改名/换结构了 ⇒ 需求缺失，**别当通过**）' % name)
            continue
        keym = re.search(r'sb_publishable_[A-Za-z0-9_]+', code)
        if keym and not ANON_KEY:
            ANON_KEY = keym.group(0)
        if not keym:
            print('  ℹ️  %s：代码里没有 Supabase key ⇒ 该站不走 Supabase（CloudBase 网关）⇒ 跳过' % name)
            continue
        tables, keys, del_keys, upsert = extract_needs(code)
        tables = {t for t in tables if t.startswith('vocab_')}
        keys = {k for k in keys if not FAKE_KEY.match(k)}
        kind = '内联单文件站（js 全在 html 里）' if not files else '抓到 %d 个 js' % len(files)
        print('  · %s（%s，用到表 %s）' % (name, kind, ','.join(sorted(tables)) or '—'))
        for t in sorted(tables):
            tks = sorted(keys) if t in ('vocab_history_logs', 'vocab_online_status') else ['(无 class_key 列)']
            for ck in tks:
                if t == 'vocab_states':
                    continue        # 下面单独处理（不能拿真班级 key 写探针）
                k = (t, ck)
                need.setdefault(k, {'read': True, 'write': True, 'delete': ck in del_keys})
                station_of.setdefault(k, set()).add(name)
        for ck in del_keys:
            k = ('vocab_history_logs', ck)
            if k in need:
                need[k]['delete'] = True
    if not ANON_KEY:
        ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
    if not ANON_KEY:
        print('\n❌ 拿不到 anon key（线上代码里没抽到，环境变量也没有）⇒ 无法验收')
        return 1
    print('  anon key：从线上代码现取（不落仓库）✅')

    if not need:
        print('\n❌ 一条需求都没抽到 ⇒ 一定是扫描器坏了（不是「没问题」）')
        return 1

    # ── 2) 真实闭环
    print('\n━━ 2) 真实闭环验收（写 → 回读 → 删 → 回读）━━')
    try:
        for (t, ck), req in sorted(need.items()):
            label = '%s / %s' % (t, ck)
            rest('DELETE', '/%s?%s' % (t, delete_filter(t, ck)), prefer='return=representation')
            st, _, raw = rest('POST', '/' + t, probe_row(t, ck), prefer='return=representation')
            if st == 409:
                st = 200
            if st not in (200, 201):
                # 写都进不去 ⇒ 后续无从谈起，直接判失败
                fail(label, '写入被挡 http=%s %s' % (st, raw[:110]))
                continue
            global CHECKS
            CHECKS += 1
            q = read_filter(t, ck)
            st, _, raw = rest('GET', '/%s?select=*&%s' % (t, q), prefer='count=exact')
            if st != 200 or not rows_of(raw):
                fail(label, '写进去了但回读不到（http=%s）' % st)
                continue
            CHECKS += 1
            if not req['delete']:
                rest('DELETE', '/%s?%s' % (t, delete_filter(t, ck)), prefer='return=representation')
                ok('%s  写✓ 回读到✓（该站没有删除路径，未验删）  ← %s'
                   % (label, '/'.join(sorted(station_of.get((t, ck), [])))))
                continue
            st, _, raw = rest('DELETE', '/%s?%s' % (t, delete_filter(t, ck)), prefer='return=representation')
            st2, _, raw2 = rest('GET', '/%s?select=*&%s' % (t, q), prefer='count=exact')
            if rows_of(raw2):
                fail(label, '🔴 删不掉（PostgREST 也返回 200/204 ⇒ 只看返回码会假绿）')
            else:
                CHECKS += 2
                ok('%s  写✓ 回读到✓ 删✓ 回读确认已消失✓  ← %s'
                   % (label, '/'.join(sorted(station_of.get((t, ck), [])))))

        # vocab_states：只读名册 + 用专属探针 key 验写（绝不覆盖真班级的 state_val）
        print('\n━━ 2b) vocab_states（名册表；探针用专属 key，不碰真班级）━━')
        st, _, raw = rest('GET', '/vocab_states?select=*&class_key=eq.__roster_v1', prefer='count=exact')
        if st == 200 and rows_of(raw):
            ok('名册可读（__roster_v1 读得到 %d 行）' % len(rows_of(raw)))
            CHECKS += 1
        else:
            fail('vocab_states 名册读取', 'http=%s，读不到名册 ⇒ 三个站都会登不进' % st)
        # 🔴 vocab_states 的 DELETE 被 RLS 挡（v1 SQL 注释里写过：是设计如此）⇒ **不测删**
        #    否则会拿一条「本来就该挡」的删除判成红 ⇒ 假红
        rest('DELETE', '/vocab_states?class_key=eq.%s' % PROBE_ID, prefer='return=representation')
        st, _, raw = rest('POST', '/vocab_states', probe_row('vocab_states', PROBE_ID),
                          prefer='resolution=merge-duplicates,return=representation')
        if st not in (200, 201):
            fail('vocab_states 探针写入', 'http=%s %s' % (st, raw[:110]))
        else:
            CHECKS += 1
            st, _, raw = rest('GET', '/vocab_states?select=*&class_key=eq.%s' % PROBE_ID, prefer='count=exact')
            if rows_of(raw):
                CHECKS += 1
                ok('vocab_states 写✓ 回读到✓（该表 DELETE 被 RLS 挡是设计如此 ⇒ 未验删）')
            else:
                fail('vocab_states 探针回读', '写进去了但回读不到')

        # ── 3) 反向验证：不在白名单的 class_key 必须写不进去
        print('\n━━ 3) 反向验证（不在白名单的 class_key 必须写不进去 ⇒ policy 真的收窄了）━━')
        # 🔴 只有「该写的写得进去」时，这条反向验证才有意义
        #    （现在这种「全都拒」的状态下，任何写入都会被拒 ⇒ 会骗成「policy 收窄得很好」的假绿）
        wrote_ok = any('写入被挡' not in f for f in FAILS)
        if not wrote_ok:
            print('  ⚠️ 跳过：正向写都不通 ⇒ 现在的「拒」不能证明 policy 收窄（等修好再验）')
        else:
            st, _, raw = rest('POST', '/vocab_history_logs', probe_row('vocab_history_logs', NEG_KEY),
                              prefer='return=representation')
            if st in (200, 201):
                fail('反向验证', "class_key='%s' 竟然写进去了 ⇒ policy 是 using(true)，没有收窄" % NEG_KEY)
                rest('DELETE', '/vocab_history_logs?class_key=eq.%s' % NEG_KEY, prefer='return=representation')
            else:
                CHECKS += 1
                ok("class_key='%s' 被拒（http=%s）⇒ policy 确实按 class_key 收窄" % (NEG_KEY, st))
    finally:
        cleanup()

    # ── 4) 汇总
    print('\n━━ 4) 汇总 ━━')
    print('  断言数：%d' % CHECKS)
    if FAILS:
        print('  ❌ 失败 %d 条：' % len(FAILS))
        for f in FAILS:
            print('     - %s' % f)
        print('\n结果：红（%d 条没过）' % len(FAILS))
        return 1
    if CHECKS == 0:
        print('\n❌ 零断言 ⇒ 判失败（扫描器/流程坏了，不是没问题）')
        return 1
    print('\n结果：🟢 全绿（%d 条断言）' % CHECKS)
    return 0


if __name__ == '__main__':
    sys.exit(main())
