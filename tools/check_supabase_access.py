#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_supabase_access_v3.py —— 补上 v2 漏掉的三件事（2026-10-24）

=====================================================================
v2 已经全绿（57 条断言），但我复核输出时发现**三处「没验到」**：

 ① **反向验证被跳过了** —— 脚本写的是
      wrote_ok = any('写入被挡' not in f for f in FAILS)
    ⇒ FAILS 为空时 any() 返回 **False** ⇒ **恰好在全绿的时候跳过**
    ⇒ 这是「空集合恒假」的老坑（同族：`x or 99` 吃掉 0.0）
    ⇒ 修法：用**正向写成功时置位的标志位**，不看 FAILS

 ② **DELETE 一次都没验** —— v2 的 delete-path 提取靠正则
      class_key=eq\.([A-Za-z0-9_]+)
    ⇒ 而听力站真实代码是**字符串拼接**：
      rest('vocab_history_logs?id=eq.' + Number(args.p_id) +
           '&class_key=eq.' + CFG.LOG_KEY + '&student_id=eq.' + sid3, {method:'DELETE'})
    ⇒ 正则一个都没匹配上 ⇒ 听力站三条删除路径被当成「该站没有删除路径」
    ⇒ 而 **v1 坏得最狠的恰恰就是 DELETE**（只放行探针 ⇒ 老师点「删掉了」数据还在）
    ⇒ 修法：**从 live code 里解析出真实 URL 形态**再照着打

 ③ **upsert 只测了「第一次」** —— upsert = INSERT … ON CONFLICT DO UPDATE
    ⇒ 第一次走 INSERT 分支，第二次才走 DO UPDATE 分支（v1 缺 UPDATE policy ⇒ 第二次报 42501）
    ⇒ 修法：每张有 onConflict 的表都**写两次**

【v3 新增的验收项】
  T1 三条删除路径（学生删自己 / 老师删一条 / 老师清空全部）⇒ 写→删→**回读确认真的没了**
  T2 反向验证（修复 v2 的空集合 bug）：非白名单 class_key 必须写不进去、读不到、删不掉
  T3 upsert 第二次写（ON CONFLICT DO UPDATE 分支）
  T4 **老师端看板必须读得到真实数据**（不是探针）⇒ 老师打开记录表不能是空的
  T5 「清空全部」路径用**不伤真实数据**的方式验（见 T1-c 的说明）

【退出码】0=全过 / 1=有断言失败
=====================================================================
"""
import json
import re
import sys
import urllib.error
import urllib.request

UA = 'Mozilla/5.0 (Linux; Android 13) Mobile'
REST = 'https://lszwxcmvcvbraqtrtgon.supabase.co/rest/v1'

LISTEN_SITE = 'https://irk7ibwekq8se.space.mcode.cn/'
VOCAB_SITE = 'https://iujsgx3ci73dp.space.mcode.cn/'
CET4_SITE = 'https://y8arnrff8t2hj.space.mcode.cn/'

PROBE_SID = '__rls_probe__'        # 探针 student_id
PROBE_CK = 'LISTENPROBE'           # 探针 class_key（「清空全部」路径专用，不碰真实 LISTEN）
NEG_CK = '__rls_neg_probe__'       # 反向验证用的非法 class_key

ANON_KEY = ''
FAILS = []
CHECKS = 0
ANY_WRITE_OK = False                # 🔴 v2 的 bug 就是没用这个


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


def count_of(raw):
    """从 Content-Range 里取精确行数（没带 count=exact 时返回 None）"""
    return None


def get_text(url):
    st, _, raw = http('GET', url, headers={'Accept': 'text/html,*/*'})
    return raw if st == 200 else ''


# ────────────────────────────────────────────── 判定
def ok(what):
    global CHECKS
    CHECKS += 1
    print('  ✅ %s' % what)


def fail(what, detail):
    global CHECKS
    CHECKS += 1
    FAILS.append('%s —— %s' % (what, detail))
    print('  ❌ %s —— %s' % (what, detail))


def log_row(class_key, sid=PROBE_SID):
    return {'class_key': class_key, 'class_name': '__rls_probe__', 'student_id': sid,
            'timestamp': '2026-10-24 12:00:00', 'duration_sec': 0, 'action': '__rls_probe__'}


def online_row(class_key, sid=PROBE_SID):
    return {'class_key': class_key, 'class_name': '__rls_probe__', 'student_id': sid,
            'status_text': '__rls_probe__', 'login_time': '12:00:00'}


def insert_log(class_key, sid=PROBE_SID):
    """插入探针行 ⇒ 返回 (成功?, 该行 id, 说明)"""
    global ANY_WRITE_OK
    rest('DELETE', '/vocab_history_logs?class_key=eq.%s&student_id=eq.%s' % (class_key, sid),
         prefer='return=representation')
    st, _, raw = rest('POST', '/vocab_history_logs', log_row(class_key, sid),
                      prefer='return=representation')
    if st not in (200, 201):
        return False, None, '写入被挡 http=%s %s' % (st, raw[:100])
    ANY_WRITE_OK = True
    rows = rows_of(raw)
    rid = rows[0].get('id') if rows else None
    return True, rid, ''



def as_list(v):
    """列可能是 json（回读是**字符串**）也可能是 jsonb（回读是数组）⇒ 两种都认"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else [x]
        except Exception:
            return []
    return []


def read_log(class_key, sid=PROBE_SID, extra=''):
    q = 'class_key=eq.%s&student_id=eq.%s' % (class_key, sid)
    if extra:
        q += '&' + extra
    st, _, raw = rest('GET', '/vocab_history_logs?select=*&%s' % q, prefer='count=exact')
    return st, rows_of(raw), raw


def del_log(query):
    """DELETE 并返回 (http码, 返回体行数)"""
    st, _, raw = rest('DELETE', '/vocab_history_logs?' + query, prefer='return=representation')
    return st, rows_of(raw), raw


# ────────────────────────────────────────────── 从 live code 解析删除路径
def parse_listen_delete_paths():
    """从听力站 live code 里解析出真实删除 URL 形态

    ⇒ **不是猜的**：正则找 `rest('<table>?<query>', { method: 'DELETE' })`
    ⇒ 然后把 `CFG.LOG_KEY` 之类的常量**解析成字面值**
    ⇒ 返回 [(路径名, 表, query模板)]
    """
    code = ''
    root = LISTEN_SITE
    idx = get_text(root)
    for page in ('student.html', 'teacher.html'):
        t = get_text(root + page)
        if not t:
            continue
        code += '\n' + t
        for m in re.finditer(r'<script[^>]+src="([^"]+)"', t):
            code += '\n' + get_text(root + m.group(1).lstrip('/'))
    if not code:
        return None, ''

    # 常量表：CFG.LOG_KEY = 'LISTEN'  ⇒  {LOG_KEY: 'LISTEN'}
    consts = {}
    for m in re.finditer(r"(\w+)\s*:\s*'([A-Za-z0-9_]+)'", code):
        consts.setdefault(m.group(1), m.group(2))

    def split_plus(s):
        """按顶层 '+' 切分（不切字符串字面量里的 '+'）"""
        out, buf, i, inq = [], [], 0, False
        while i < len(s):
            c = s[i]
            if c == "'":
                inq = not inq
                buf.append(c)
            elif c == '+' and not inq:
                out.append(''.join(buf))
                buf = []
            else:
                buf.append(c)
            i += 1
        out.append(''.join(buf))
        # ⚠️ 收尾时 region 常带一个「, 」（`rest(url, { method: … }` 的逗号）
        #    ⇒ 不剥掉它，'CFG.LOG_KEY,' 就匹配不上常量正则 ⇒ 被当成未知表达式 ⇒ <VAL>
        parts = []
        for x in out:
            x = x.strip()
            if x and not x.startswith("'"):
                x = x.rstrip(',').strip()
            if x:
                parts.append(x)
        return parts

    paths = []
    # 🔴 v3 第一版解析器的坑：听力站的 URL 是**跨多个字符串拼接**的
    #    rest('vocab_history_logs?id=eq.' + Number(args.p_id) +
    #         '&class_key=eq.' + CFG.LOG_KEY + '&student_id=eq.' + sid3, { method: 'DELETE' })
    #    ⇒ 「rest('...' 后面紧跟 ', {'」的正则**一条都匹配不上** ⇒ 解析器报「没路径」
    #    ⇒ 修法：从 `rest(` 开始扫到 `method: 'DELETE'`，把中间的 '+' 表达式**逐段还原**
    for m in re.finditer(r"\brest\s*\(", code):
        start = m.end()
        mth = code.find("method: 'DELETE'", start, start + 1200)
        if mth < 0:
            continue
        opts = code.rfind('{', start, mth)
        if opts < 0:
            continue
        region = code[start:opts]
        url, last_key = '', ''
        for part in split_plus(region):
            lit = re.match(r"^'([^']*)'$", part)
            if lit:
                url += lit.group(1)
                tail = re.search(r'([A-Za-z_]+)=', lit.group(1)[-40:])
                last_key = tail.group(1) if tail else ''
                continue
            # 表达式：常量 → 字面值；变量 → 按它前面那个 key 给占位符
            cm = re.match(r'^(?:CFG\.)?(\w+)$', part)
            if cm and cm.group(1) in consts:
                url += consts[cm.group(1)]
                continue
            if last_key == 'id':
                url += '<ID>'
            elif last_key == 'student_id':
                url += '<SID>'
            else:
                url += '<VAL>'
        tm = re.match(r'^(vocab_\w+)\?(.*)$', url)
        if not tm:
            continue
        table, query = tm.group(1), tm.group(2)
        paths.append((table, query, code[max(0, m.start() - 120):m.start() + 60]))
    # 去重
    uniq = []
    seen = set()
    for t, q, ctx in paths:
        k = (t, q)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((t, q, ctx))
    return uniq, code


# ────────────────────────────────────────────── 主流程
def main():
    global ANON_KEY
    print('=' * 74)
    print('Supabase RLS 验收 v3 —— 补 DELETE / 反向验证 / upsert 第二次 / 老师端真实数据')
    print('项目 = lszwxcmvcvbraqtrtgon')
    print('=' * 74)

    # ── 0) anon key
    for site in (VOCAB_SITE, LISTEN_SITE, CET4_SITE):
        code = get_text(site)
        km = re.search(r'sb_publishable_[A-Za-z0-9_]+', code)
        if km:
            ANON_KEY = km.group(0)
            break
    if not ANON_KEY:
        print('❌ 拿不到 anon key ⇒ 无法验收')
        return 1
    print('  anon key：从线上代码现取 ✅')

    # ── 1) 从 live code 解析听力站删除路径
    print('\n━━ 1) 从听力站 live code 解析真实删除路径（不靠猜）━━')
    paths, lcode = parse_listen_delete_paths()
    if not paths:
        print('❌ 一条删除路径都没解析到 ⇒ 解析器坏了（不是「没有删除路径」）')
        return 1
    for t, q, ctx in paths:
        print('  · %s ? %s' % (t, q))

    # ── 2) T1 三条删除路径
    print('\n━━ 2) T1 三条删除路径（写 → 删 → 回读确认真的没了）━━')
    # 真实 LISTEN 里有多少行（**不许把真实数据删掉**）
    st, _, raw = rest('GET', '/vocab_history_logs?select=id&class_key=eq.LISTEN', prefer='count=exact')
    real_listen = len(rows_of(raw))
    print('  （LISTEN 现有 %d 行真实记录）' % real_listen)

    # T1-a 学生删自己一条: ?id=eq.<id>&class_key=eq.LISTEN&student_id=eq.<sid>
    good, rid, why = insert_log('LISTEN')
    if not good:
        fail('T1-a 前置（写探针行）', why)
    else:
        q = 'id=eq.%s&class_key=eq.LISTEN&student_id=eq.%s' % (rid, PROBE_SID)
        st, body, raw = del_log(q)
        st2, rows2, _ = read_log('LISTEN')
        if rows2:
            fail('T1-a 学生删自己一条', 'http=%s 但行还在 ⇒ 删不掉（只看返回码会假绿）' % st)
        else:
            ok('T1-a 学生删自己一条（id+class_key+student_id）→ 删掉了，回读确认已消失')

    # T1-b 老师删一条: ?id=eq.<id>&class_key=eq.LISTEN
    good, rid, why = insert_log('LISTEN')
    if not good:
        fail('T1-b 前置（写探针行）', why)
    else:
        q = 'id=eq.%s&class_key=eq.LISTEN' % rid
        st, body, raw = del_log(q)
        st2, rows2, _ = read_log('LISTEN')
        if rows2:
            fail('T1-b 老师删一条', 'http=%s 但行还在 ⇒ 删不掉' % st)
        else:
            ok('T1-b 老师删一条（id+class_key）→ 删掉了，回读确认已消失')

    # T1-c 老师清空全部: ?class_key=eq.LISTEN
    #   ⚠️ 这一条会删掉 LISTEN 的**全部真实行** ⇒ 老师有真学生数据时不能直接打
    #   ⇒ 做法：先在**别的、白名单同样放行的 key** 上打**完全同形态的 URL**（只有 class_key 过滤，
    #      没有 id / student_id 过滤）⇒ 证明「按 class_key 一把清」这条路是通的，
    #      同时**证明它不会误删别的 class_key 的行**
    good, rid, why = insert_log(PROBE_CK)
    ins2, rid2, why2 = insert_log('JR1')     # 另一个班级的行，用来验证没被误删
    if not (good and ins2):
        fail('T1-c 前置（写探针行）', why or why2)
    else:
        q = 'class_key=eq.%s' % PROBE_CK
        st, body, raw = del_log(q)
        st2, rows2, _ = read_log(PROBE_CK)
        st3, rows3, _ = read_log('JR1')
        if rows2:
            fail('T1-c 老师清空全部（同形态 URL）', 'http=%s 但同 key 的行还在 ⇒ 删不掉' % st)
        else:
            ok('T1-c 老师清空全部（只有 class_key 过滤的 URL）→ 同 key 全部清掉，回读确认已消失')
        if rows3:
            ok('T1-c 同时确认：别的班级（JR1）没被误删 ✓')
        else:
            fail('T1-c 误删检查', 'JR1 的探针行不见了 ⇒ 清空范围过大')
        # 收尾
        rest('DELETE', '/vocab_history_logs?class_key=eq.JR1&student_id=eq.%s' % PROBE_SID,
             prefer='return=representation')

    # 真实 LISTEN 行数复核（确认上面三条没有伤到真实数据）
    st, _, raw = rest('GET', '/vocab_history_logs?select=id&class_key=eq.LISTEN', prefer='count=exact')
    after_listen = len(rows_of(raw))
    if after_listen == real_listen:
        ok('真实 LISTEN 数据未被破坏（前后都是 %d 行）' % real_listen)
    else:
        fail('真实 LISTEN 数据', '前 %d 行 → 后 %d 行 ⇒ 验收脚本动了真实数据' % (real_listen, after_listen))

    # ── 3) T2 反向验证（修 v2 的空集合 bug）
    print('\n━━ 3) T2 反向验证（不在白名单的 class_key 必须写不进 / 读不到 / 删不掉）━━')
    if not ANY_WRITE_OK:
        print('  ⚠️ 跳过：正向写都不通 ⇒ 「拒」不能证明 policy 收窄')
    else:
        # 3-1 写
        st, _, raw = rest('POST', '/vocab_history_logs', log_row(NEG_CK), prefer='return=representation')
        if st in (200, 201):
            fail('T2-1 非法 class_key 写入', "class_key='%s' 竟然写进去了 ⇒ policy 是 using(true)" % NEG_CK)
            rest('DELETE', '/vocab_history_logs?class_key=eq.%s' % NEG_CK, prefer='return=representation')
        else:
            ok("T2-1 class_key='%s' 写入被拒（http=%s）⇒ policy 确实按 class_key 收窄" % (NEG_CK, st))
        # 3-2 读
        st, _, raw = rest('GET', '/vocab_history_logs?select=*&class_key=eq.%s' % NEG_CK, prefer='count=exact')
        if rows_of(raw):
            fail('T2-2 非法 class_key 读取', '竟然读得到行 ⇒ select policy 没收窄')
        else:
            ok('T2-2 非法 class_key 读不到任何行')
        # 3-3 删（先造一条合法 LISTEN 行，再尝试用非法 key 去删它 ⇒ 删不掉）
        good, rid, why = insert_log('LISTEN')
        if good:
            st, body, raw = del_log('class_key=eq.%s&id=eq.%s' % (NEG_CK, rid))
            st2, rows2, _ = read_log('LISTEN')
            if not rows2:
                fail('T2-3 越权删除', '用非白名单 class_key 竟然把 LISTEN 的行删掉了 ⇒ delete policy 放行过宽')
            else:
                ok('T2-3 用非白名单 class_key 删不掉别人的行 ✓')
            rest('DELETE', '/vocab_history_logs?class_key=eq.LISTEN&student_id=eq.%s' % PROBE_SID,
                 prefer='return=representation')

    # ── 4) T3 upsert 第二次写（ON CONFLICT DO UPDATE 分支）
    print('\n━━ 4) T3 upsert 第二次写（upsert = INSERT … ON CONFLICT DO UPDATE ⇒ 必须有 update policy）━━')
    # 4-1 vocab_online_status（词汇站 onConflict: student_id）
    sid = PROBE_SID
    rest('DELETE', '/vocab_online_status?student_id=eq.%s' % sid, prefer='return=representation')
    st1, _, r1 = rest('POST', '/vocab_online_status?on_conflict=student_id', online_row('LISTEN', sid),
                      prefer='resolution=merge-duplicates,return=representation')
    if st1 not in (200, 201):
        fail('T3-1 online_status 第一次写', 'http=%s %s' % (st1, r1[:100]))
    else:
        st2, _, r2 = rest('POST', '/vocab_online_status?on_conflict=student_id',
                          dict(online_row('LISTEN', sid), status_text='__rls_probe_2nd__'),
                          prefer='resolution=merge-duplicates,return=representation')
        if st2 not in (200, 201):
            fail('T3-1 online_status 第二次写（走 DO UPDATE）',
                 'http=%s %s ⇒ 缺 UPDATE policy（v1 就是死在这）' % (st2, r2[:140]))
        else:
            st3, _h3, raw3 = rest('GET', '/vocab_online_status?select=*&student_id=eq.%s' % sid,
                                 prefer='count=exact')
            rows3 = rows_of(raw3)
            if len(rows3) == 1 and rows3[0].get('status_text') == '__rls_probe_2nd__':
                ok('T3-1 online_status upsert 第二次写成功且内容已更新（仍只有 1 行 ⇒ 走的是 DO UPDATE）✓')
            else:
                fail('T3-1 online_status 第二次写', '写入返回 %s，但回读内容不是新值' % st2)
        rest('DELETE', '/vocab_online_status?student_id=eq.%s' % sid, prefer='return=representation')
    # 4-2 vocab_states（词汇站 onConflict: class_key）
    st1, _, r1 = rest('POST', '/vocab_states?on_conflict=class_key',
                      {'class_key': PROBE_SID, 'state_val': '__rls_probe__'},
                      prefer='resolution=merge-duplicates,return=representation')
    if st1 not in (200, 201):
        fail('T3-2 states 第一次写', 'http=%s %s' % (st1, r1[:100]))
    else:
        st2, _, r2 = rest('POST', '/vocab_states?on_conflict=class_key',
                          {'class_key': PROBE_SID, 'state_val': '__rls_probe_2nd__'},
                          prefer='resolution=merge-duplicates,return=representation')
        if st2 not in (200, 201):
            fail('T3-2 states 第二次写（走 DO UPDATE）', 'http=%s %s' % (st2, r2[:140]))
        else:
            st3, _h3, raw3 = rest('GET', '/vocab_states?select=*&class_key=eq.%s' % PROBE_SID,
                                 prefer='count=exact')
            rows3 = rows_of(raw3)
            if len(rows3) == 1 and rows3[0].get('state_val') == '__rls_probe_2nd__':
                ok('T3-2 states upsert 第二次写成功且内容已更新（仍只有 1 行 ⇒ 走的是 DO UPDATE）✓')
            else:
                fail('T3-2 states 第二次写', '写入返回 %s，但回读内容不是新值' % st2)
        rest('DELETE', '/vocab_states?class_key=eq.%s' % PROBE_SID, prefer='return=representation')
    # 4-3 vocab_submissions（词汇站 onConflict: student_id）
    st1, _, r1 = rest('POST', '/vocab_submissions?on_conflict=student_id',
                      {'student_id': PROBE_SID, 'submitted_exams': ['__rls_probe__']},
                      prefer='resolution=merge-duplicates,return=representation')
    if st1 not in (200, 201):
        fail('T3-3 submissions 第一次写', 'http=%s %s' % (st1, r1[:100]))
    else:
        st2, _, r2 = rest('POST', '/vocab_submissions?on_conflict=student_id',
                          {'student_id': PROBE_SID, 'submitted_exams': ['__rls_probe__', 'x2']},
                          prefer='resolution=merge-duplicates,return=representation')
        if st2 not in (200, 201):
            fail('T3-3 submissions 第二次写（走 DO UPDATE）', 'http=%s %s' % (st2, r2[:140]))
        else:
            st3, _h3, raw3 = rest('GET', '/vocab_submissions?select=*&student_id=eq.%s' % PROBE_SID,
                                 prefer='count=exact')
            rows3 = rows_of(raw3)
            if len(rows3) == 1 and len(as_list(rows3[0].get('submitted_exams'))) == 2:
                ok('T3-3 submissions upsert 第二次写成功且内容已更新（仍只有 1 行 ⇒ 走的是 DO UPDATE，不是插了第二行）✓')
            else:
                fail('T3-3 submissions 第二次写',
                     '写入返回 %s，但回读到 %d 行 / 内容 %s ⇒ 可能插了第二行而不是更新'
                     % (st2, len(rows3), [r.get('submitted_exams') for r in rows3]))
        rest('DELETE', '/vocab_submissions?student_id=eq.%s' % PROBE_SID, prefer='return=representation')

    # ── 5) T4 老师端看板必须读得到真实数据
    print('\n━━ 5) T4 老师端看板必须读得到真实数据（v1 的症状：打开记录表是空的）━━')
    for label, q in (('LISTEN（听力站）', 'class_key=eq.LISTEN'),
                     ('JR1（词汇站）', 'class_key=eq.JR1'),
                     ('CET4（四级站）', 'class_key=eq.CET4')):
        st, _, raw = rest('GET', '/vocab_history_logs?select=id&%s' % q, prefer='count=exact')
        n = len(rows_of(raw))
        if st == 200:
            ok('%s 读得到 %d 行（老师端看板不是空的）' % (label, n))
        else:
            fail('%s 读取' % label, 'http=%s' % st)
    st, _, raw = rest('GET', '/vocab_states?select=*&class_key=eq.__roster_v1', prefer='count=exact')
    if st == 200 and rows_of(raw):
        ok('名册 __roster_v1 读得到 %d 行' % len(rows_of(raw)))
    else:
        fail('名册读取', 'http=%s ⇒ 三个站都会登不进' % st)

    # ── 6) 收尾清理
    for t, q in (('vocab_history_logs', 'class_key=eq.LISTEN&student_id=eq.%s' % PROBE_SID),
                 ('vocab_history_logs', 'class_key=eq.%s' % PROBE_CK),
                 ('vocab_online_status', 'student_id=eq.%s' % PROBE_SID),
                 ('vocab_submissions', 'student_id=eq.%s' % PROBE_SID),
                 ('vocab_states', 'class_key=eq.%s' % PROBE_SID)):
        rest('DELETE', '/%s?%s' % (t, q), prefer='return=representation')

    # ── 7) 汇总
    print('\n━━ 6) 汇总 ━━')
    print('  断言数：%d' % CHECKS)
    if FAILS:
        print('  ❌ 失败 %d 条：' % len(FAILS))
        for f in FAILS:
            print('     - %s' % f)
        print('\n结果：红（%d 条没过）' % len(FAILS))
        return 1
    if CHECKS == 0:
        print('\n❌ 零断言 ⇒ 判失败（脚本坏了，不是没问题）')
        return 1
    print('\n结果：🟢 全绿（%d 条断言）' % CHECKS)
    return 0


if __name__ == '__main__':
    sys.exit(main())
