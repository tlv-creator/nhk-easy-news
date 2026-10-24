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
--   anon 只能删一种行：**student_id = '__rls_probe__'**（探针行，清理用）
--   ⇒ 四个班（JR1 / CW3 / WL_CW / XQ1，含 JR1|u1、JR1|u2）**anon 删不动了**
--   ⇒ LISTEN / CET4 / CET4TEST 也**一并收紧**（四级站明确要求：
--     学生端不需要删除权，「清空」是老师端的功能、老师端走 authenticated，本来就能删）
--   ⇒ 教师侧（authenticated，p_teach_all_*）不受影响，照常能删任何行。
--
-- ⚠ 已知副作用（**不是 bug，是取舍**，听力站那边已同步）：
--   听力站的「老师删一条 / 清空全部」用的是 **anon** 调 REST
--   （api.js: rest('vocab_history_logs?class_key=eq.' + LOG_KEY, {method:'DELETE'})）
--   ⇒ 收紧后那两个按钮会**变成假成功**（RLS 挡 DELETE 时 PostgREST 返回 200 + 空数组）
--   ⇒ 他们的正解是教师侧挪到 authenticated / service_role；真要「先保住功能」，
--      把下面 〔可选〕那段解开（**替换**上面那条，不是追加 —— PERMISSIVE 之间是 OR）。
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
    using (student_id = '__rls_probe__');

-- 〔可选·仅当听力站明确说「先保住功能」时解开〕
-- ⚠ 下面是**替换**上面那条 policy，不是追加 —— PERMISSIVE 之间是 OR，追加等于没收紧。
-- create policy p_anon_delete_logs on public.vocab_history_logs
--     for delete to anon, authenticated
--     using (student_id = '__rls_probe__' or class_key = 'LISTEN');

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
