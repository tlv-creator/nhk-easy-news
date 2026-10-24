-- =====================================================================
-- 🔴 收紧 anon 的「删除」权限 —— **修正版**（2026-10-24）
-- Supabase SQL Editor → 新建 → 粘贴 → Run
--
-- 【为什么要紧】老师 2026-10-24 装的那版给 anon 的 DELETE 是：
--      using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
--   ⇒ 任何持有公开 anon key 的人，只要 class_key 长得像班级名，
--      就能删掉那一班的**全部**记录。
--   ⇒ 已发生：听力站「教师清空全部」第一次真正生效，一按就删掉 34 条真实听力记录
--      （之前 RLS 挡着，它是「假成功」；权限一放开就真删）。
--
-- 【这版和同伴那版的区别（我用本地 PostgreSQL 实测出来的）】
--   同伴版 `tools/tighten_rls_delete.sql` 的 DO 块是：
--        where cmd = 'DELETE'            ← **不分角色**
--   ⇒ 它把**老师侧 `to authenticated` 的 p_teach_all_* 也一起删了**
--   ⇒ 再重建一条 `to anon, authenticated` 但判据收窄成
--        student_id = '__rls_probe__' or class_key = 'LISTEN'
--   ⇒ 实测结果（本地 PG，RLS 真的开着）：
--        · 老师侧(authenticated) 删 JR1 → **删不掉**（原来能删任意行）
--        · 学生侧(anon) 删 JR1 → 删不掉 ✅（收紧的目的达到了）
--        · 听力站 LISTEN 三条删除路径 → 照常 ✅
--   ⇒ 今天三个站都没有「老师端用 authenticated 删非 LISTEN 行」的代码路径
--      ⇒ **暂时不会坏**，但等于**悄悄收走了老师侧的能力**
--      ⇒ 以后老师端要做「删某个学生的某条记录」（在词汇站/四级站）就变成静默失败
--
-- 【这版怎么做】
--   ① **只删 roles 里含 anon 的 DELETE policy**
--      （roles 是 {authenticated,service_role} 的老师侧 policy **一根都不动**）
--   ② 如果存在「roles 同时含 anon 和 authenticated」的 policy
--      （= 同伴那版建出来的形状）⇒ **拆成两条**：
--        · anon 侧 → 收紧成 (__rls_probe__ 或 LISTEN)
--        · authenticated 侧 → **判据原样重建**（一个字节都不改）
--   ③ 删光 anon 侧后重建一条收紧的
--
-- 【收紧成什么样】
--   anon 能删的行只有两类：
--     ① student_id = '__rls_probe__'（探针行，清理用，任何班都行）
--     ② class_key = 'LISTEN'（只给每日听力记录站：老师的「删一条/清空全部」走 anon REST）
--   ⇒ 词汇站四个班（JR1/CW3/WL_CW/XQ1，含 JR1|u1、JR1|u2）**anon 删不动** ✅
--   ⇒ CET4 **也 anon 删不动** ✅（四级站明确要求）
--   ⇒ 老师侧（authenticated / service_role）**边界完全不变** ✅
--
-- ⚠ 唯一的取舍（**不是 bug，是取舍**）：
--   LISTEN 这一班 anon 仍可整班删 ⇒ 听力站已加护栏（显示条数 + 要求手打「清空」）
--   ⇒ 等他们把教师侧搬到 service_role / authenticated，把 `or class_key = 'LISTEN'` 去掉即可。
--
-- 【为什么用 DO 块先删再重建】
--   老师之前跑过不同版本，policy 名字不一样
--   ⇒ 逐个 drop 名字会漏 ⇒ 漏掉的那条宽松 policy 仍然生效（PERMISSIVE 之间是 OR）
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

-- ⚠ LISTEN 这条例外是**给每日听力记录站的**
--   （老师的「删一条 / 清空全部」走 anon REST，收掉它那两个按钮就成了「假成功」⇒ 功能直接废）
--   ⇒ 等他们把教师侧搬到 service_role / authenticated 之后，把 `or class_key = 'LISTEN'` 去掉即可。
--   ⇒ CET4 **不开口子**（四级站明确要求：学生端不需要删除权）。

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

commit;

-- =====================================================================
-- 自检：跑完看下面这几条，数字/结果对不上就别往下走，把输出截图给我
-- =====================================================================
-- ① 现在 anon 能删哪些 policy（应该只有一条收紧的，roles={anon}）
select '① ' || tablename || ' | ' || policyname || ' | roles=' || roles::text || ' | ' || left(qual, 60) as x
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
 order by tablename;

-- ② 老师侧那条还在不在（roles 里应该同时有 authenticated 和 service_role）
select '② ' || tablename || ' | ' || policyname || ' | roles=' || roles::text as x
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
   and tablename in ('vocab_history_logs','vocab_online_status')
   and 'authenticated' = any(roles);

-- ③ 各班条数
--   🔴 **不要拿「基线数字」当判据** —— 学生正在用词汇站，记录数**每天都在涨**
--   （实测：2454 → 2453 → 2462；CW3 582 → 591）
--   ⇒ 正确做法：**跑之前先跑一次 ③④ 记下数字，跑完再跑一次，对比你自己的前后值**
--   ⇒ 这次改动**只动 policy、不动数据** ⇒ 词汇站四个班的条数应当**一模一样**
select '③ ' || class_key || ' = ' || count(*) as x
  from public.vocab_history_logs
 where class_key in ('JR1','CW3','WL_CW','XQ1','JR1|u1','JR1|u2','LISTEN','CET4')
 group by class_key order by class_key;

-- ④ 总条数（同样：**只跟自己的前后值比**，不要跟任何硬编码数字比）
select '④ 总条数 = ' || count(*) as x from public.vocab_history_logs;
-- =====================================================================
