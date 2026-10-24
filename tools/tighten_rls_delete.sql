-- =====================================================================
-- 🔴 收紧 anon 的「删除」权限（2026-10-24 事故后）
-- Supabase SQL Editor → 新建 → 粘贴 → Run
-- 不改表结构、不动任何真实数据（除了清掉探针行）
-- =====================================================================
--
-- 【为什么要紧】老师装的那版短 SQL 给 anon 的 DELETE 是：
--      using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
--   ⇒ 任何持有公开 anon key 的人，只要 class_key 长得像班级名，就能删那一班的**全部**记录。
--   ⇒ 实际已发生：听力站「教师清空全部」按钮第一次真正生效，
--      一按就把 34 条真实听力记录删掉了（之前 RLS 挡着，它是「假成功」）。
--   ⇒ 我自己也被同一扇门绊了一跤（误删 10 条真实记录，已逐字恢复）。
--
-- 【收紧成什么样】
--   anon 能删的只有两类行：
--     ① student_id = '__rls_probe__'（探针行，清理用，任何班都行）
--     ② class_key = 'LISTEN'（**只给每日听力记录站**：老师的「删一条 / 清空全部」走 anon REST）
--   ⇒ 词汇站四个班（JR1 / CW3 / WL_CW / XQ1，含 JR1|u1、JR1|u2）**anon 删不动了** ✅
--   ⇒ CET4 **也 anon 删不动** ✅（四级站明确要求：学生端不需要删除权，
--      「清空」是老师端的功能、老师端走 authenticated，本来就能删）
--   ⇒ 教师侧（authenticated，p_teach_all_*）不受影响，照常能删任何行。
--
-- ⚠ 唯一的取舍（**不是 bug，是取舍**）：
--   LISTEN 这一班 anon 仍可整班删 ⇒ 听力站那边已加护栏（显示条数 + 要求手打「清空」）
--   ⇒ 等他们把教师侧搬到 service_role / authenticated，把 `or class_key = 'LISTEN'` 去掉即可。
--
-- 【为什么用 DO 块先删光再重建】
--   老师之前可能跑过不同版本的脚本，policy 名字不一样
--   （p_anon_delete_logs / p_anon_delete_probe_logs / …）
--   ⇒ 逐个 drop 名字会漏 ⇒ 漏掉的那条宽松 policy 仍然生效（PERMISSIVE 之间是 OR）
--   ⇒ 改成：按 pg_policies 把这张表上 **cmd='DELETE' 的 policy 全部删掉**，只留一条收紧的。
-- =====================================================================

begin;

-- ── 1) vocab_history_logs：收紧 DELETE ────────────────────────────
do $$
declare
  r record;
begin
  for r in
    select policyname
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_history_logs'
       and cmd         = 'DELETE'
  loop
    execute format('drop policy %I on public.vocab_history_logs', r.policyname);
  end loop;
end $$;

create policy p_anon_delete_logs on public.vocab_history_logs
    for delete to anon, authenticated
    using (student_id = '__rls_probe__' or class_key = 'LISTEN');

-- ⚠ LISTEN 这条例外是**给每日听力记录站的**（老师的「删一条 / 清空全部」走 anon REST，
--   收掉它那两个按钮就成了「假成功」⇒ 功能直接废）。
--   ⇒ 等他们把教师侧搬到 service_role / authenticated 之后，把 `or class_key = 'LISTEN'` 去掉即可。
--   ⇒ CET4 **不开口子**（四级站明确要求：学生端不需要删除权，老师端走 authenticated）。

-- ── 2) vocab_online_status：同样收紧 DELETE ───────────────────────
--    （词汇站的在线状态行同样不该被 anon 批量删；听力/四级的照旧可删）
do $$
declare
  r record;
begin
  for r in
    select policyname
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_online_status'
       and cmd         = 'DELETE'
  loop
    execute format('drop policy %I on public.vocab_online_status', r.policyname);
  end loop;
end $$;

create policy p_anon_delete_online on public.vocab_online_status
    for delete to anon, authenticated
    using (student_id = '__rls_probe__');
-- （每日听力记录站现在的代码里零引用这张表 ⇒ 收严不影响他们；
--   四级站也要求收紧 ⇒ 这张表 anon 只能删探针行）

-- ── 3) 清掉这轮排查可能留下的探针行（真数据不动）────────────────
delete from public.vocab_history_logs where student_id = '__rls_probe__';
delete from public.vocab_online_status where student_id = '__rls_probe__';

commit;

-- =====================================================================
-- 自检：跑完看这三条（数字对不上就别往下走，截图给我）
-- =====================================================================
-- ① 四个班的条数（删前基线：JR1 746 / CW3 582 / WL_CW 280 / XQ1 839）
select class_key, count(*) as 条数
  from public.vocab_history_logs
 where class_key in ('JR1','CW3','WL_CW','XQ1','JR1|u1','JR1|u2')
 group by class_key order by class_key;
--
-- ② 总条数（删前基线：2454）
select count(*) as 总条数 from public.vocab_history_logs;
--
-- ③ 现在 anon 能删哪些 policy（应该只剩一条收紧的）
select tablename, policyname, cmd, qual
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
 order by tablename;
-- =====================================================================
