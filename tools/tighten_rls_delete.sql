-- =====================================================================
-- 🔴 收紧 anon 的「删除」权限 —— **最终版**（2026-10-24）
-- Supabase SQL Editor → 新建 → 粘贴 → Run
-- 不改表结构、不动任何真实数据（只清探针行）
-- =====================================================================
--
-- 【本版是什么】
--   = 听力记录站同伴的 `tools/tighten_rls_delete_fixed.sql`（他用本地 PostgreSQL 实测过）
--     ＋ 我补的一处**自检修正**（见下面「⑤」——不改的话老师侧会「看起来消失」）
--
-- 【为什么要紧】老师 2026-10-24 装的那版给 anon 的 DELETE 是：
--      using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
--   ⇒ 任何持有公开 anon key 的人，只要 class_key 长得像班级名，就能删掉那一班的**全部**记录。
--   ⇒ 已发生：听力站「教师清空全部」按钮第一次真正生效，一按就删掉 34 条真实听力记录
--      （之前 RLS 挡着，它是「假成功」：界面说删了、库里没删；权限一放开就真删）。
--
-- 【收紧成什么样】
--   anon 能删的行只有两类：
--     ① student_id = '__rls_probe__'（探针行，清理用，任何班都行）
--     ② class_key = 'LISTEN'（**只给每日听力记录站**：老师的「删一条 / 清空全部」走 anon REST，
--        收掉它那两个按钮就成了「假成功」⇒ 功能直接废 ⇒ 这门课正在用）
--   ⇒ 词汇站四个班（JR1 / CW3 / WL_CW / XQ1，含 JR1|u1、JR1|u2）**anon 删不动了** ✅
--   ⇒ CET4 **也 anon 删不动** ✅（四级站明确要求：学生端不需要删除权，
--      「清空」是老师端的功能、老师端走 authenticated，本来就能删）
--   ⇒ 老师侧（authenticated / service_role）**边界一根不动** ✅（见下面「怎么保证」）
--
-- 【怎么保证「老师侧一根不动」】两层保险：
--   ① DO 块**只碰 roles 里含 anon 的 DELETE policy**
--      ⇒ roles={authenticated,service_role} 的**一根都不动**
--   ② 万一存在「roles 同时含 anon 和 authenticated」的 policy（混合型）
--      ⇒ **拆成两条**：anon 侧收紧 / authenticated 侧**判据原样重建**（一个字节不改）
--   ③ 新建的 policy **只写 `to anon`** ⇒ 老师侧连"被我的新规则沾到"的可能都没有
--      （铁律：修 anon 只写 to anon，教师侧一个字都不动）
--
--   📌 关于 pg_policies.cmd：老师侧的 `p_teach_all_*` 是 **FOR ALL** ⇒ 在 pg_policies 里 cmd='ALL'
--      ⇒ 本文件的 DO 块（cmd='DELETE'）**根本碰不到它们** ⇒ 老师侧天然安全。
--      ⇒ 也正因为如此，**自检 ② 必须同时查 cmd='ALL'**，否则老师侧会「看起来消失」——
--         那是查法的问题，不是权限被改坏了（这是最容易误读的假警报，我已修正）。
--
-- 【为什么用 DO 块先删再重建】
--   老师之前跑过不同版本的脚本，policy 名字不一样
--   （p_anon_delete_logs / p_anon_delete_subs / p_anon_delete_online / p_anon_delete_probe_* …）
--   ⇒ 逐个 drop 名字会漏 ⇒ 漏掉的那条宽松 policy 仍然生效（PERMISSIVE 之间是 OR）
--   ⇒ 改成：按 pg_policies 把该表上**含 anon 的** DELETE policy 全删光，只留一条收紧的。
-- =====================================================================

begin;

-- ── 1) vocab_history_logs ────────────────────────────────────────────
do $$
declare
  r record;
  saved_qual text;
  saved_name text;
begin
  for r in
    select policyname, qual, roles, tablename
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_history_logs'
       and cmd         = 'DELETE'
       and 'anon' = any(roles)          -- 🔴 **只碰含 anon 的**
  loop
    saved_name := r.policyname;
    saved_qual := r.qual;
    if 'authenticated' = any(r.roles) then
      -- 这条同时覆盖 anon 和 authenticated ⇒ 拆开：老师侧判据**原样**保留
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      execute format('create policy %I on public.%I for delete to authenticated, service_role using (%s)',
                     r.policyname || '_teacher', r.tablename, saved_qual);
      raise notice '已拆分 %（anon 收紧 / authenticated 原样保留）', saved_name;
    else
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      raise notice '已删除 anon 侧 DELETE policy: %', saved_name;
    end if;
  end loop;
end $$;

create policy p_anon_delete_logs on public.vocab_history_logs
    for delete to anon
    using (student_id = '__rls_probe__' or class_key = 'LISTEN');

-- ⚠ LISTEN 这条例外是**给每日听力记录站的**；等他们把教师侧搬到 service_role / authenticated，
--   把 `or class_key = 'LISTEN'` 去掉即可。CET4 **不开口子**（四级站明确要求）。

-- ── 2) vocab_online_status：同样只动 anon 侧 ──────────────────────────
do $$
declare
  r record;
  saved_qual text;
  saved_name text;
begin
  for r in
    select policyname, qual, roles, tablename
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_online_status'
       and cmd         = 'DELETE'
       and 'anon' = any(roles)
  loop
    saved_name := r.policyname;
    saved_qual := r.qual;
    if 'authenticated' = any(r.roles) then
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      execute format('create policy %I on public.%I for delete to authenticated, service_role using (%s)',
                     r.policyname || '_teacher', r.tablename, saved_qual);
      raise notice '已拆分 %（anon 收紧 / authenticated 原样保留）', saved_name;
    else
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      raise notice '已删除 anon 侧 DELETE policy: %', saved_name;
    end if;
  end loop;
end $$;

create policy p_anon_delete_online on public.vocab_online_status
    for delete to anon
    using (student_id = '__rls_probe__');
-- （每日听力记录站现在的代码里零引用这张表 ⇒ 收严不影响他们；
--   四级站也要求收紧 ⇒ 这张表 anon 只能删探针行）

-- ── 3) 清掉这轮排查可能留下的探针行（真数据不动）────────────────────
delete from public.vocab_history_logs where student_id = '__rls_probe__';
delete from public.vocab_online_status where student_id = '__rls_probe__';
-- vocab_states 里前三轮排查留下的 3 行探针（真实 key 只有 CW3/JR1/WL_CW/XQ1 + __roster_v1 + __cet4_*）
delete from public.vocab_states where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1');

commit;

-- =====================================================================
-- 自检：跑完把下面 ⑤ 段的结果发我（数字/结果对不上就别往下走，截图给我）
-- =====================================================================
-- ① anon 现在能删哪些 policy（应该只有一条收紧的，roles={anon}）
select '① ' || tablename || ' | ' || policyname || ' | roles=' || roles::text || ' | ' || left(qual, 60) as x
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
 order by tablename;
--
-- ②🔴 老师侧还在不在？**必须连 cmd='ALL' 一起查**
--   （老师侧的 p_teach_all_* 是 FOR ALL ⇒ cmd='ALL' ⇒ 只查 'DELETE' 会显示"老师侧不见了"，
--     那是查法的问题，不是权限被改坏 —— 这条我上一版写错过，这次改过来了）
select '② ' || tablename || ' | ' || policyname || ' | cmd=' || cmd || ' | roles=' || roles::text as x
  from pg_policies
 where schemaname = 'public'
   and cmd in ('ALL','DELETE')
   and tablename in ('vocab_history_logs','vocab_online_status')
   and ('authenticated' = any(roles) or 'service_role' = any(roles))
 order by tablename, cmd;
--
-- ③ 四个班的条数（基线：JR1 746 / CW3 582 / WL_CW 280 / XQ1 839）
select '③ ' || class_key || ' = ' || count(*) as x
  from public.vocab_history_logs
 where class_key in ('JR1','CW3','WL_CW','XQ1','JR1|u1','JR1|u2','LISTEN','CET4')
 group by class_key order by class_key;
--
-- ④ 总条数（基线：2454）
select '④ 总条数 = ' || count(*) as x from public.vocab_history_logs;
--
-- ⑤ 探针行清干净了没有（应该都是 0）
select '⑤ 残留探针 logs=' || (select count(*) from public.vocab_history_logs where student_id = '__rls_probe__')
     || ' online=' || (select count(*) from public.vocab_online_status where student_id = '__rls_probe__')
     || ' states=' || (select count(*) from public.vocab_states
                        where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1')) as x;
-- =====================================================================
