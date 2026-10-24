#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_submissions_shape.py —— 验收 `vocab_submissions.submitted_exams` 的**列形状**

=====================================================================
【为什么单独一个脚本】
RLS 验收（check_supabase_access.py）管的是「能不能读写」，
这个脚本管的是「读回来的**形状**对不对」——
⇒ **写进去是数组 ≠ 读出来是数组**（2026-10-24 实测踩过）

【背景：词汇站的真 bug】
`vocab_submissions.submitted_exams` 这列原来是 `json`（不是 jsonb）
⇒ PostgREST 回读给的是**字符串** `'["Unit1Exam"]'`，不是数组
⇒ 词汇站 markExamAsSubmitted() 里：
       let exams = data && data.submitted_exams ? data.submitted_exams : [];
       if (!exams.includes(examName)) { exams.push(examName); ... }
   ⇒ `exams` 是字符串 ⇒ **`.push` 不存在 ⇒ TypeError**
   ⇒ 外面套着 `catch(e){}` ⇒ **静默吞掉**
   ⇒ 症状：**每个学生只有第一次交卷被记上**，第二次起永远记不下来
   ⇒ 而且 hasSubmittedExam() 的 `exams.includes(name)` 变成**子串匹配**
     （'Unit1' 会命中 'Unit1Final'）

【根治】把列改成 jsonb ⇒ PostgREST 回读直接给数组 ⇒ **前端一个字都不用改**
   alter table public.vocab_submissions
     alter column submitted_exams type jsonb using submitted_exams::jsonb;

【本脚本验什么】
 ① 写一个数组进去（on_conflict=student_id，和真实代码一致）
 ② 读回来 ⇒ 必须是 **list**（是 str ⇒ 还没改 ⇒ 报红并给出该跑的 SQL）
 ③ **照 app 的真实 JS 逻辑跑一遍**：
      exams.includes('Unit1Exam')  必须为 True（不是子串匹配）
      exams.push('Unit2Exam')      必须不抛错
 ④ upsert 第二次写仍只有 1 行（顺带覆盖 ON CONFLICT DO UPDATE）
 ⑤ 探针行清理 + 回读确认清干净

【退出码】0=形状正确（jsonb 已生效）/ 1=还没改（列仍是字符串）/ 2=脚本自身出错
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
VOCAB_SITE = 'https://iujsgx3ci73dp.space.mcode.cn/'

# ⚠️ 指纹必须是 DELETE policy 放行的那**一个字面值**
#    （vocab_submissions 的 delete policy = using (student_id = '__rls_probe__')）
#    ⇒ 用别的指纹 ⇒ 自己插的行自己删不掉 ⇒ 留在老师的真表里
PROBE_SID = '__rls_probe__'
EXAM_A = 'Unit1Exam'
EXAM_B = 'Unit2Exam'


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


def rows_of(raw):
    try:
        v = json.loads(raw or '[]')
        return v if isinstance(v, list) else [v]
    except Exception:
        return []


def get_anon_key():
    st, _, raw = http('GET', VOCAB_SITE, headers={'Accept': 'text/html,*/*'})
    if st != 200:
        return ''
    m = re.search(r'sb_publishable_[A-Za-z0-9_]+', raw)
    return m.group(0) if m else ''


def main():
    key = get_anon_key() or os.environ.get('SUPABASE_ANON_KEY', '')
    if not key:
        print('❌ 拿不到 anon key ⇒ 无法验收')
        return 2
    H = {'apikey': key, 'Authorization': 'Bearer ' + key, 'User-Agent': UA}

    def rest(method, path, body=None, prefer=None):
        h = dict(H)
        if prefer:
            h['Prefer'] = prefer
        # ⚠️ body 传**对象**，编码交给 http() ⇒ 自己再 dumps 一次会变成「bytes 不是 JSON」
        return http(method, REST + path, body, h)

    print('=' * 72)
    print('vocab_submissions.submitted_exams 列形状验收')
    print('=' * 72)

    # 清掉上一轮探针
    rest('DELETE', '/vocab_submissions?student_id=eq.%s' % PROBE_SID, prefer='return=representation')

    fails = []
    checks = 0
    try:
        # ① 写一个数组进去（和真实代码一致：onConflict student_id）
        st, _, raw = rest('POST', '/vocab_submissions?on_conflict=student_id',
                          {'student_id': PROBE_SID, 'submitted_exams': [EXAM_A]},
                          prefer='resolution=merge-duplicates,return=representation')
        if st not in (200, 201):
            print('❌ 写入被挡 http=%s %s' % (st, raw[:160]))
            print('   ⇒ 先确认 RLS 修好了（跑 tools/check_supabase_access.py）')
            return 1
        print('✅ 写入成功（写进去的是数组）')

        # ② 读回来 —— 判据落在**形状**上
        st, _, raw = rest('GET', '/vocab_submissions?select=submitted_exams&student_id=eq.%s&limit=1' % PROBE_SID,
                          prefer='count=exact')
        rows = rows_of(raw)
        if not rows:
            print('❌ 回读不到探针行 ⇒ 读通道有问题')
            return 1
        val = rows[0].get('submitted_exams')
        print('   回读到的值 = %r（类型 %s）' % (val, type(val).__name__))

        if isinstance(val, str):
            checks += 1
            print('\n🔴 还是**字符串** ⇒ 列还是 json/text，没改成 jsonb')
            print('   ⇒ 词汇站里 `exams.push(examName)` 会抛 TypeError ⇒ 第二次交卷记不下来')
            print('\n请在 Supabase SQL Editor 跑这段（一次 Run 即可，前端不用改）：')
            print('''
begin;
update public.vocab_submissions
   set submitted_exams = '[]'
 where submitted_exams is not null
   and btrim(submitted_exams::text) !~ '^\\s*\\[';

alter table public.vocab_submissions
  alter column submitted_exams type jsonb using submitted_exams::jsonb;
commit;
''')
            return 1
        if not isinstance(val, list):
            print('❌ 形状不认识：%s' % type(val).__name__)
            return 2

        checks += 1
        print('✅ 读回来是**数组** ⇒ 列已是 jsonb ⇒ 前端不用改')

        # ③ 照 app 的真实 JS 逻辑跑一遍
        exams = val if val else []
        if not isinstance(exams, list):
            exams = []
        # JS: exams.includes(examName)
        inc = EXAM_A in exams
        if not inc:
            checks += 1
            print('❌ `exams.includes("%s")` 为 False ⇒ 数组内容不对：%r' % (EXAM_A, exams))
            fails.append('includes 判不到已交卷的考试')
        else:
            checks += 1
            print('✅ `exams.includes("%s")` = True（真数组包含，不是子串匹配）' % EXAM_A)

        # JS: exams.push(examName) —— 字符串会在这里炸
        try:
            exams.append(EXAM_B)
            checks += 1
            print('✅ `exams.push("%s")` 不抛错 ⇒ 第二次交卷能记上' % EXAM_B)
        except AttributeError as e:
            checks += 1
            print('🔴 `exams.push` 抛错：%s ⇒ 第二次交卷永远记不下来' % e)
            fails.append('push 抛错（列还是字符串形状）')

        # ④ upsert 第二次写仍只有 1 行
        st, _, raw = rest('POST', '/vocab_submissions?on_conflict=student_id',
                          {'student_id': PROBE_SID, 'submitted_exams': exams},
                          prefer='resolution=merge-duplicates,return=representation')
        st2, _, raw2 = rest('GET', '/vocab_submissions?select=submitted_exams&student_id=eq.%s&limit=2' % PROBE_SID,
                            prefer='count=exact')
        rows2 = rows_of(raw2)
        if len(rows2) == 1:
            checks += 1
            print('✅ upsert 第二次写后仍只有 1 行 ⇒ 走的是 DO UPDATE，不是插了第二行')
        else:
            checks += 1
            print('❌ upsert 第二次写后有 %d 行 ⇒ 可能插了第二行' % len(rows2))
            fails.append('upsert 产生了重复行')

        # ⑤ 再验一次 app 的第二段逻辑（读回来还是数组）
        val2 = rows2[0].get('submitted_exams') if rows2 else None
        if isinstance(val2, list) and EXAM_B in val2:
            checks += 1
            print('✅ 第二次交卷的内容已入库并能读回（数组）')
        else:
            checks += 1
            print('❌ 第二次交卷内容没读回来：%r' % val2)
            fails.append('第二次写入的内容没读回')
    finally:
        rest('DELETE', '/vocab_submissions?student_id=eq.%s' % PROBE_SID, prefer='return=representation')
        # 清理必须回读确认
        st, _, raw = rest('GET', '/vocab_submissions?select=student_id&student_id=eq.%s&limit=1' % PROBE_SID,
                          prefer='count=exact')
        if rows_of(raw):
            print('⚠️ 探针行没清干净 ⇒ 手动跑：delete from vocab_submissions where student_id = %r' % PROBE_SID)

    print('\n━━ 汇总 ━━')
    print('  断言数：%d' % checks)
    if fails:
        for f in fails:
            print('  ❌ %s' % f)
        print('\n结果：红（%d 条没过）' % len(fails))
        return 1
    print('\n结果：🟢 全绿（%d 条断言）⇒ 列形状已正确，词汇站不用改代码' % checks)
    return 0


if __name__ == '__main__':
    sys.exit(main())
