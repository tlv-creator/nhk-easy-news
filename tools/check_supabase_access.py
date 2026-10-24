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

# 🔴🔴 护栏：**「整班删」这个形状只能打在探针 key 上**
#   同伴 2026-10-24 拦过一次：
#   `DELETE ?class_key=eq.LISTEN`（不带 student_id 过滤）一旦指向**真实 class_key**
#   ⇒ 会把全班真记录一次删光 ⇒ **正是 34 条听力记录消失的那个形状**
#   ⇒ 所以「拿 LISTEN 自己的 key 验」这种改法是危险的 ⇒ 只能拿探针 key
#   ⇒ 这里的断言让这件事**由代码保证**，而不是靠「我记得别写错」
REAL_CLASS_KEYS = {'LISTEN', 'CET4', 'CET4TEST', 'JR1', 'CW3', 'WL_CW', 'XQ1',
                   'JR1|u1', 'JR1|u2', 'WL_CW|u1', 'WL_CW|u2', 'XQ1|u1', 'XQ1|u2'}

# 🔴 行令牌（听力站 2026-10-24 的双凭证方案：学号 + 行令牌）
#   前端把令牌藏在 action 末尾：'... | #tk=<32位十六进制>'
#   删除时两个头都要带：x-student-id + x-row-token
#   ⇒ **探针行必须也带令牌**，否则收紧后的 policy 会把探针行挡住 ⇒ 验收脚本假红
#   （前端的 randomToken()：crypto.getRandomValues 16 字节 → 32 位十六进制）
import os as _os
TK = _os.urandom(16).hex()          # 32 位十六进制（原始令牌）
import hashlib as _hl
TK_HASH = _hl.sha256(TK.encode('utf-8')).hexdigest()   # 64 位十六进制（SHA-256 版令牌）
TK_HEADERS = {'x-student-id': PROBE_SID, 'x-row-token': TK}

# 🔴🔴 探针行要能扛住**两种**令牌制（同伴 2026-10-24 正在从「原文」切到「SHA-256」）
#   变体①：action 里存**原文** ⇒ policy 比较 action like '%#tk=' || 请求头 || '%'
#   变体②：action 里存 **sha256(原文)** ⇒ policy 比较 action like '%#tk=' || sha256(请求头) || '%'
#   变体③：请求头直接送 sha256 ⇒ policy 比较 action like '%#tk=' || 请求头 || '%'
#   ⇒ **一行里同时带两个后缀** ⇒ 上面三种变体下这一行都能被删掉
#   ⇒ 换句话说：清理不会漏垃圾 ⇒ 我的验收脚本不会在老师真表里留残渣
TK_ACTION = '__rls_probe__ | #tk=' + TK + ' | #tk=' + TK_HASH
assert PROBE_CK not in REAL_CLASS_KEYS, '整班删形状的探针 key 不能是真实班级'
assert PROBE_SID not in REAL_CLASS_KEYS

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


def rest(method, table_path, body=None, prefer=None, headers=None):
    h = {}
    if ANON_KEY:
        h['apikey'] = ANON_KEY
        h['Authorization'] = 'Bearer ' + ANON_KEY
    if prefer:
        h['Prefer'] = prefer
    if headers:
        h.update(headers)
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


PHASE_FAILS = []
POLICY_VARIANT = 'UNKNOWN'

def phase_fail(what, detail):
    """阶段未完成（不是脚本坏了）⇒ 单独记，汇总时分开说"""
    global CHECKS
    PHASE_FAILS.append('%s —— %s' % (what, detail))
    print('  ⚠️ %s' % what)
    print('     %s' % detail)
    CHECKS += 1


def fail(what, detail):
    global CHECKS
    CHECKS += 1
    FAILS.append('%s —— %s' % (what, detail))
    print('  ❌ %s —— %s' % (what, detail))


def log_row(class_key, sid=PROBE_SID, tk=None, action=None):
    # 🔴 action 末尾必须带 `#tk=<令牌>`（与听力站前端同形）⇒ 收紧后的 policy 才认这条探针行
    tk = tk or TK
    act = action if action is not None else ('__rls_probe__ | #tk=' + tk)
    return {'class_key': class_key, 'class_name': '__rls_probe__', 'student_id': sid,
            'timestamp': '2026-10-24 12:00:00', 'duration_sec': 0, 'action': act}


def online_row(class_key, sid=PROBE_SID):
    return {'class_key': class_key, 'class_name': '__rls_probe__', 'student_id': sid,
            'status_text': '__rls_probe__', 'login_time': '12:00:00'}


def insert_log(class_key, sid=PROBE_SID, action=None):
    """插入探针行 ⇒ 返回 (成功?, 该行 id, 说明)"""
    global ANY_WRITE_OK
    rest('DELETE', '/vocab_history_logs?class_key=eq.%s&student_id=eq.%s' % (class_key, sid),
         prefer='return=representation')
    st, _, raw = rest('POST', '/vocab_history_logs', log_row(class_key, sid, action=action),
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


def del_log(query, headers=None):
    """DELETE 并返回 (http码, 返回体行数)"""
    st, _, raw = rest('DELETE', '/vocab_history_logs?' + query, prefer='return=representation',
                      headers=headers)
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
    # 真实 LISTEN 里有哪些行（**不许把真实数据删掉**）
    # 🔴🔴 记的是 **id 集合**，不是条数：
    #   157 个学生正在实时交作业 ⇒ 条数**只增不减** ⇒ 「前后条数一样」这个判据会**随机假红**
    #   ⇒ 正确判据：**跑之前就存在的 id，跑完之后必须还在**（我一行真记录都没删）
    #   ⇒ 新增的行（学生新交的）**不算违规**
    st, _, raw = rest('GET', '/vocab_history_logs?select=id,student_id&class_key=eq.LISTEN')
    _before_rows = rows_of(raw)
    real_ids = set()
    for r in _before_rows:
        sid = str(r.get('student_id', ''))
        if sid.startswith('__rls'):        # 探针行不算「真实数据」
            continue
        real_ids.add(r.get('id'))
    real_listen = len(_before_rows)
    print('  （LISTEN 现有 %d 行；其中真实记录 %d 条，已记下 id 集合）' % (real_listen, len(real_ids)))

    # 🔴 双凭证：学号 + 行令牌（听力站 2026-10-24 方案）
    #   ⇒ 删除请求必须带 x-student-id + x-row-token 两个头
    #   ⇒ 探针行的 action 里也带同一个令牌（与前端同形）
    #   ⇒ 这一段**在收紧前后都成立**（收紧前只用学号，收紧后两个都要）
    def del_headers(sid=PROBE_SID, tk=None):
        return {'x-student-id': sid, 'x-row-token': tk or TK}

    # T1-a 学生删自己一条: ?id=eq.<id>&class_key=eq.LISTEN&student_id=eq.<sid>
    good, rid, why = insert_log('LISTEN')
    if not good:
        fail('T1-a 前置（写探针行）', why)
    else:
        q = 'id=eq.%s&class_key=eq.LISTEN&student_id=eq.%s' % (rid, PROBE_SID)
        st, body, raw = del_log(q, headers=del_headers())
        st2, rows2, _ = read_log('LISTEN')
        if rows2:
            fail('T1-a 学生删自己一条', 'http=%s 但行还在 ⇒ 删不掉（只看返回码会假绿）' % st)
        else:
            ok('T1-a 学生删自己一条（id+class_key+student_id + 学号/令牌双凭证）→ 删掉了，回读确认已消失')

    # T1-b 老师删一条: ?id=eq.<id>&class_key=eq.LISTEN
    good, rid, why = insert_log('LISTEN')
    if not good:
        fail('T1-b 前置（写探针行）', why)
    else:
        q = 'id=eq.%s&class_key=eq.LISTEN' % rid
        st, body, raw = del_log(q, headers=del_headers())
        st2, rows2, _ = read_log('LISTEN')
        if rows2:
            fail('T1-b 老师删一条', 'http=%s 但行还在 ⇒ 删不掉' % st)
        else:
            ok('T1-b 老师删一条（id+class_key + 学号/令牌双凭证）→ 删掉了，回读确认已消失')

    # T1-c 老师清空全部: ?class_key=eq.LISTEN
    #   ⚠️ 这一条会删掉 LISTEN 的**全部真实行** ⇒ 老师有真学生数据时不能直接打
    #   ⇒ 做法：先在**别的、白名单同样放行的 key** 上打**完全同形态的 URL**（只有 class_key 过滤，
    #      没有 id / student_id 过滤）⇒ 证明「按 class_key 一把清」这条路是通的，
    #      同时**证明它不会误删别的 class_key 的行**
    if PROBE_CK in REAL_CLASS_KEYS:
        # 🔴 绝不能在这里给 PROBE_CK 赋值（函数内赋值 ⇒ Python 把它当局部变量
        #    ⇒ 后面读它就是 UnboundLocalError）⇒ 只中止，不改值
        fail('T1-c 护栏', '整班删形状的 key 指向了真实班级 ⇒ 会清掉全班真记录 ⇒ 中止')
        raise SystemExit(2)
    good, rid, why = insert_log(PROBE_CK)
    ins2, rid2, why2 = insert_log('JR1')     # 另一个班级的行，用来验证没被误删
    if not (good and ins2):
        fail('T1-c 前置（写探针行）', why or why2)
    else:
        # 🔴🔴 **两种结果都可能是对的**（取决于当前处于收紧前的哪一阶段）：
        #   阶段①（未收紧 LISTEN）：anon 能整班删 ⇒ 探针行应当被清掉
        #   阶段②（已收紧 LISTEN）：anon **不能**整班删 ⇒ 探针行应当**留着**（被 policy 挡住）
        #   ⇒ 所以判据落「**真实数据一根不少**」，而不是「探针行有没有被删」
        #   ⇒ 顺带把「当前处在哪个阶段」打印出来 ⇒ 老师一看就知道收紧跑到哪一步了
        q = 'class_key=eq.%s' % PROBE_CK
        st, body, raw = del_log(q, headers=del_headers())
        st2, rows2, _ = read_log(PROBE_CK)
        st3, rows3, _ = read_log('JR1')
        if rows2:
            ok('T1-c 整班删形状（打在探针 key 上）：**被 policy 挡住** ⇒ 当前是「LISTEN 已收紧」阶段 ✅'
               '（这条路径现在归服务端特权端点管，anon 不该能整班删）')
            rest('DELETE', '/vocab_history_logs?class_key=eq.%s&student_id=eq.%s' % (PROBE_CK, PROBE_SID),
                 prefer='return=representation', headers=del_headers())
        else:
            ok('T1-c 整班删形状（打在探针 key 上）：删得掉 ⇒ 当前是「LISTEN 尚未收紧」阶段 ✅'
               '（收尾 SQL 上线后这条会变成「被挡住」）')
        if rows3:
            ok('T1-c 同时确认：别的班级（JR1）没被误删 ✓')
        else:
            fail('T1-c 误删检查', 'JR1 的探针行不见了 ⇒ 清空范围过大')
        # 收尾
        rest('DELETE', '/vocab_history_logs?class_key=eq.JR1&student_id=eq.%s' % PROBE_SID,
             prefer='return=representation')

    # ── T1-d / T1-e：LISTEN 的 anon 口子**开没开** + 令牌路径通不通 ──────────
    # 🔴🔴 为什么判别行不能用探针 student_id：
    #   探针例外（student_id='__rls_probe__'）是**无条件可删**的
    #   ⇒ 拿它去测「LISTEN 口子开没开」会被这个例外掩盖 ⇒ **结论会说谎**
    #     （收紧前后整班删都能成功 ⇒ 旧脚本会一直报「尚未收紧」）
    #   ⇒ 判别行用 student_id='__rls_probe2__' ⇒ 探针例外碰不到它
    PHASE_SID = '__rls_probe2__'
    PHASE = 'UNKNOWN'
    good, rid, why = insert_log('LISTEN', PHASE_SID, action=TK_ACTION)
    if not good:
        fail('T1-d 前置（写判别行）', why)
    else:
        # T1-d 阶段判定：带学号、**不带令牌**
        #   旧政策（LISTEN 开着）⇒ 删得掉；新政策（锁死）⇒ 删不掉
        st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid,
                                headers={'x-student-id': PHASE_SID})
        st2, rows2, _ = read_log('LISTEN', PHASE_SID)
        if rows2:
            PHASE = 'LOCKED'
            ok('T1-d LISTEN 的 anon 删除口子**已锁死** ✅（带学号但不带令牌 ⇒ 删不掉）'
               '⇒ 公开 anon 密钥再也删不了听力数据')
        else:
            PHASE = 'OPEN'
            phase_fail('T1-d LISTEN 的 anon 删除口子**还开着** ⚠️',
                 'http=%s 但行没了 ⇒ 任何拿到 anon key 的人都能删 LISTEN 的行（整班也能）'
                 '⇒ **收尾 SQL 还没跑 / 没生效**（这是「阶段未完成」，不是脚本坏了）' % st)
        # 判别行可能还在（LOCKED 阶段）⇒ 用令牌路径清掉它（顺带就是 T1-e 的断言）
        if rows2:
            st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid,
                                    headers={'x-student-id': PHASE_SID, 'x-row-token': TK})
            st2, rows2, _ = read_log('LISTEN', PHASE_SID)
            if rows2:
                st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid,
                                        headers={'x-student-id': PHASE_SID, 'x-row-token': TK_HASH})
                st2, rows2, _ = read_log('LISTEN', PHASE_SID)
            if rows2:
                fail('T1-e 令牌路径不通 + 判别行清不掉',
                     '学号 + 令牌（原文/SHA-256 两种都试过）仍删不掉 ⇒ '
                     '① 学生「删自己那一条」是废的；② 这一行会留在老师真表里 ⇒ '
                     '请老师在 SQL Editor 跑：delete from public.vocab_history_logs '
                     "where student_id = '%s';" % PHASE_SID)
            else:
                ok('T1-e 令牌路径通（学号 + x-row-token）⇒ 学生「删自己那一条」能用 ✅'
                   '（判别行已顺带清干净）')

    # T1-e' 令牌路径独立再验一次（两种政策下都该成立）
    good2, rid2, why2 = insert_log('LISTEN', PHASE_SID, action=TK_ACTION)
    if not good2:
        fail("T1-e' 前置（写判别行）", why2)
    else:
        st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid2,
                                headers={'x-student-id': PHASE_SID, 'x-row-token': TK})
        st2, rows2, _ = read_log('LISTEN', PHASE_SID)
        if rows2:
            st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid2,
                                    headers={'x-student-id': PHASE_SID, 'x-row-token': TK_HASH})
            st2, rows2, _ = read_log('LISTEN', PHASE_SID)
        if rows2:
            fail("T1-e' 令牌路径不通", '带学号 + 令牌（原文/SHA-256 两种）仍删不掉')
        else:
            ok("T1-e' 令牌路径独立复验通过 ✅")
        # 清理：把可能残留的判别行按 student_id 删干净（不留垃圾）
        rest('DELETE', '/vocab_history_logs?student_id=eq.%s' % PHASE_SID,
             prefer='return=representation',
             headers={'x-student-id': PHASE_SID, 'x-row-token': TK})
        st3, rows3, _ = read_log('LISTEN', PHASE_SID)
        if rows3:
            fail('清理', '判别行还剩 %d 行 ⇒ 留在老师真表里了' % len(rows3))
        else:
            ok('清理：判别行一根不剩（老师真表零污染）')

    # ── T1-f：🔴「现役前端形状」的真实学生路径 ────────────────────────────
    #   现役前端（2026-10-24 起）：action 里写 **sha256(原始令牌)**，删除时发**原始令牌**
    #   ⇒ 这一行 = 学生真的点「删掉这条」时走的那条路
    #   ⇒ 它一次回答两个问题：
    #     ① 学生现在到底能不能删掉自己那条
    #     ② 生产 policy 是「明文版」还是「sha256 版」
    #   ⚠️ 用**非探针**学号（否则探针例外会让结论说谎）
    #   ⚠️ 两个分支都能自己清干净：
    #     · 发原始令牌删得掉 ⇒ sha256 版 ⇒ 行没了 ✅
    #     · 发原始令牌删不掉 ⇒ 明文版 ⇒ 再发「摘要」当令牌 ⇒ 明文版能删掉 ✅
    #       （而「发摘要能删掉」这件事本身就是「明文版」的证据）
    RAW2 = _os.urandom(16).hex()
    HASH2 = _hl.sha256(RAW2.encode('utf-8')).hexdigest()
    good, rid, why = insert_log('LISTEN', PHASE_SID, action='__rls_probe__ | #tk=' + HASH2)
    if not good:
        fail('T1-f 前置（写现役形状的行）', why)
    else:
        st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid,
                                headers={'x-student-id': PHASE_SID, 'x-row-token': RAW2})
        st2, rows2, _ = read_log('LISTEN', PHASE_SID)
        if not rows2:
            # 🔴 关键前提：**必须先确认 policy 已经收紧**（T1-d 说 LOCKED）
            #   否则「删得掉」是因为**权限太松**（class_key='LISTEN' 一把放行），
            #   **不能**据此判断「是 sha256 版」⇒ 那会把「还没收紧」误报成「已收紧」
            if PHASE == 'LOCKED':
                ok('T1-f 现役前端形状（action 存摘要 + 请求头发原文）⇒ **学生删自己那条是通的** ✅'
                   '（且 policy 已收紧 ⇒ 这说明它确实是 **sha256 版**）')
                POLICY_VARIANT = 'SHA256'
            else:
                ok('T1-f 现役前端形状：删得掉，但 **policy 还没收紧** ⇒ 现在删得掉是因为**权限太松**，'
                   '**不能**据此判断令牌口径 ⇒ 令牌路径要等收尾 SQL 生效才能验')
                POLICY_VARIANT = 'LOOSE'
        else:
            st, body, raw = del_log('id=eq.%s&class_key=eq.LISTEN' % rid,
                                    headers={'x-student-id': PHASE_SID, 'x-row-token': HASH2})
            st2, rows2, _ = read_log('LISTEN', PHASE_SID)
            if rows2:
                fail('T1-f 清理失败', '判别行还剩 %d 行 ⇒ 请老师跑：delete from public.vocab_history_logs '
                     "where student_id = '%s'" % (len(rows2), PHASE_SID))
            else:
                fail('T1-f 令牌路径不通 🔴',
                     '现役前端把 **sha256 摘要**写进 action、删除时发**原始令牌**，'
                     '但 policy 判不过去 ⇒ 学生「删自己那一条」是废的 '
                     '⇒ **必须跑收尾 SQL（sha256 版）**')
                POLICY_VARIANT = 'PLAIN'

    # 真实 LISTEN 复核（确认上面那些删除没有伤到真实数据）
    # 🔴 判据落「**跑之前的 id 还在不在**」，不落「条数一样吗」
    #   ⇒ 学生实时交作业 ⇒ 条数会涨 ⇒ 那不是「动了真实数据」
    #   ⇒ 真正要抓的是「**我删掉了原本就在的真记录**」
    st, _, raw = rest('GET', '/vocab_history_logs?select=id,student_id&class_key=eq.LISTEN')
    _after_rows = rows_of(raw)
    after_ids = set()
    for r in _after_rows:
        if str(r.get('student_id', '')).startswith('__rls'):
            continue
        after_ids.add(r.get('id'))
    lost = real_ids - after_ids
    if lost:
        fail('真实 LISTEN 数据', '**跑之前的 %d 条真记录里有 %d 条不见了**（id=%s）⇒ 验收脚本动了真实数据'
             % (len(real_ids), len(lost), sorted(lost)[:5]))
    else:
        ok('真实 LISTEN 数据未被破坏（跑之前的 %d 条真记录一条不少；期间学生新交了 %d 条，不算违规）'
           % (len(real_ids), len(after_ids) - len(real_ids)))

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
    # 🔴🔴 v3.1 的改法：**不再插探针行** ⇒ 零污染
    #   实测：vocab_states 的 DELETE policy 对**所有** key 形状都挡着
    #     （ZZPROBE / PROBE_V1 / __rls_probe__ 全部「返 200 + 空数组，但行还在」）
    #   ⇒ **anon 永远删不掉这张表的行** ⇒ 任何探针都会在老师的真表里留垃圾
    #     （我因此在 vocab_states 里留了 3 行：ZZPROBE / PROBE_V1 / __rls_probe__）
    #   ⇒ 改用「**把一行真实数据原样 upsert 回去**」：内容一字未改 ⇒ 库里零变化
    #   ⇒ 顺带证明 conflict 目标 class_key 真有唯一索引（否则会多出一行 ⇒ 行数判据会红）
    st, _, raw = rest('GET', '/vocab_states?select=class_key,state_val&class_key=eq.__roster_v1',
                      prefer='count=exact')
    roster = rows_of(raw)
    if not roster:
        fail('T3-2 states 写通道', '名册行读不到 ⇒ 无法做零污染写入验证')
    else:
        _, _, raw_n = rest('GET', '/vocab_states?select=class_key', prefer='count=exact')
        n_before = len(rows_of(raw_n))
        sv = roster[0].get('state_val')
        st1, _, r1 = rest('POST', '/vocab_states?on_conflict=class_key',
                          {'class_key': roster[0]['class_key'], 'state_val': sv},
                          prefer='resolution=merge-duplicates,return=representation')
        if st1 not in (200, 201):
            fail('T3-2 states 原样回写', 'http=%s %s' % (st1, r1[:100]))
        else:
            st3, _h3, raw3 = rest('GET', '/vocab_states?select=state_val&class_key=eq.__roster_v1',
                                  prefer='count=exact')
            back = rows_of(raw3)
            if back and back[0].get('state_val') == sv:
                ok('T3-2 states 写✓ 原样回写后回读内容一字未改 ✓（零污染写法）')
            else:
                fail('T3-2 states 回读', '原样回写后内容变了')
            _, _, raw_n2 = rest('GET', '/vocab_states?select=class_key', prefer='count=exact')
            n_after = len(rows_of(raw_n2))
            if n_after == n_before:
                ok('T3-2 states 行数未变（%d 行）⇒ 没插出第二行 ⇒ on_conflict=class_key 有效' % n_after)
            else:
                fail('T3-2 states 行数', '%d → %d ⇒ 插了第二行（on_conflict 没生效）' % (n_before, n_after))
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
            # 🔴 提示必须反映**真实状态**（0 行也要说 0 行，别写「不是空的」）
            ok('%s 读得到 %d 行%s' % (label, n,
               '' if n else '（该班当前没有记录 ⇒ 老师端看板会显示空列表，但**不是被 RLS 挡住**）'))
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
                 ('vocab_submissions', 'student_id=eq.%s' % PROBE_SID)):
        rest('DELETE', '/%s?%s' % (t, q), prefer='return=representation')
    # ⚠️ vocab_states **故意不删**：它的 DELETE 对 anon 全挡 ⇒ 删了也只是「200 + 空数组」
    #    ⇒ v3 现在**不在这张表插任何探针**（改成原样回写名册行）⇒ 没有需要清理的东西

    # ── 7) 汇总
    print('\n━━ 6) 汇总 ━━')
    print('  当前 LISTEN 删除口径：%s' % {
        'SHA256': 'sha256 版（前端写摘要、policy 比摘要）✅ 与现役前端一致',
        'PLAIN': '🔴 令牌路径不通 ⇒ 学生「删自己那一条」是废的 ⇒ 必须跑收尾 SQL',
        'LOOSE': '🔴 **权限还没收紧**（class_key=LISTEN 一把放行）⇒ 现在删得掉是因为太松，'
                 '**不是**因为令牌对 ⇒ 必须跑收尾 SQL',
        'UNKNOWN': '未测到'}.get(POLICY_VARIANT, '未测到'))
    print('  断言数：%d' % CHECKS)
    if PHASE_FAILS:
        print('  ⚠️ 阶段未完成 %d 条（**不是脚本坏了** ⇒ 收尾还没做完）：' % len(PHASE_FAILS))
        for f in PHASE_FAILS:
            print('     - %s' % f)
    if FAILS:
        print('  ❌ 真失败 %d 条：' % len(FAILS))
        for f in FAILS:
            print('     - %s' % f)
        print('\n结果：红（真失败 %d 条 + 阶段未完成 %d 条）' % (len(FAILS), len(PHASE_FAILS)))
        return 1
    if PHASE_FAILS:
        print('\n结果：🟡 断言全过，但**收尾还没做完**（阶段未完成 %d 条）' % len(PHASE_FAILS))
        return 1
    if CHECKS == 0:
        print('\n❌ 零断言 ⇒ 判失败（脚本坏了，不是没问题）')
        return 1
    print('\n结果：🟢 全绿（%d 条断言）' % CHECKS)
    return 0


if __name__ == '__main__':
    sys.exit(main())
