-- =====================================================================
-- 收尾 SQL：把 anon 的「整班删除」彻底关掉
-- 前提：教师侧特权端点（CloudBase /vocab-ops）已上线并验证过
-- Supabase SQL Editor → 新建 → 粘贴 → Run
-- =====================================================================
--
-- 【为什么是这一步】
--   之前 anon 的 DELETE policy 是：
--       using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
--   ⇒ 任何拿到**公开 anon key** 的人，只要 class_key 长得像班级名，
--     就能删掉那一班的**全部**记录。
--   ⇒ 已实际发生：听力站「教师清空全部」第一次真正生效 ⇒ 34 条真记录没了
--     （之前 RLS 挡着，它是「假成功」；权限一放开，同一个按钮第一次真删）。
--   ⇒ 中间那一步（tools/tighten_rls_delete.sql）先收成「只允许 LISTEN」，
--     因为老师的「删一条 / 清空全部」当时还走 anon REST。
--   ⇒ 现在老师侧已经搬到**服务端特权端点**（service_role + 服务端校验口令），
--     这一版把 LISTEN 这个最后的口子也收掉。
--
-- 【这一版收成什么样】
--   anon 能删的行只剩两类：
--     ① student_id = '__rls_probe__'
--        （验收脚本的探针行，任何班都行 —— 清理探针必须有这个口子）
--     ② class_key = 'LISTEN' 且 student_id = 请求头 x-student-id
--        ⇒ **学生只能删自己那一条**；老师的「删一条 / 清空全部」不走这条路
--
-- 【⚠ 为什么学生的删除要带 x-student-id 头】
--   PostgREST 会把请求头放进 current_setting('request.headers')（键名一律小写）
--   ⇒ RLS 里能读到「这次请求自称是谁」。
--   ⚠ 这个头是**自称**的，能被伪造 ⇒ 学生仍可能删掉同班同学的**单条**记录
--     （要逐条删、还要知道学号和行 id；比「一键清空全班」难得多，但不是零）
--     ⇒ 真正的隔离要靠「每人一个口令」（157 个学生要发口令，代价大），先记着这条路。
--     ⇒ 这一版比现在强的地方是：**一键清空全班彻底没了**。
--
-- 【不动 authenticated】
--   老师的 p_teach_all_* 是 `FOR ALL TO authenticated`
--   ⇒ 在 pg_policies 里 cmd = 'ALL'（不是 'DELETE'）⇒ 下面只删 cmd='DELETE' 的，
--     **不会碰到它们**。我们自己的 policy 也只写 `to anon`。
-- =====================================================================

begin;

-- ── 1) vocab_history_logs：把这张表上**所有 anon 能用的 DELETE policy** 删光，只留一条收紧的
--    （PERMISSIVE 之间是 OR ⇒ 漏删一条宽松的 = 白收紧）
--    🔴 **只删「对 anon 生效」的**：
--       · roles 里含 anon 的（`to anon`）
--       · 或 roles = {public} 的（没写 to 就是给所有角色）
--    ⇒ 老师侧那条（`FOR ALL TO authenticated` ⇒ roles={authenticated}）**一根都不动**
--    ⇒ 即使老师侧哪天变成 `for delete to authenticated`（cmd='DELETE' 但 roles 不含 anon）
--      也**不会被这个 DO 块误删** ← 这一层保险是同事提醒后补的
do $$
declare r record;
begin
  for r in
    select policyname
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_history_logs'
       and cmd         = 'DELETE'
       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )
  loop
    execute format('drop policy %I on public.vocab_history_logs', r.policyname);
  end loop;
end $$;

create policy p_anon_delete_logs
    on public.vocab_history_logs
    for delete to anon
    using (
      student_id = '__rls_probe__'
      or (
        class_key = 'LISTEN'
        and student_id = nullif(current_setting('request.headers', true)::json->>'x-student-id', '')
      )
    );

-- ── 2) vocab_online_status：只留探针行
--    （听力/四级站都不删这张表；词汇站也只 insert/upsert/select ⇒ 不影响任何人）
--    🔴 同样**只删对 anon 生效的**（理由同上）
do $$
declare r record;
begin
  for r in
    select policyname
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_online_status'
       and cmd         = 'DELETE'
       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )
  loop
    execute format('drop policy %I on public.vocab_online_status', r.policyname);
  end loop;
end $$;

create policy p_anon_delete_online
    on public.vocab_online_status
    for delete to anon
    using (student_id = '__rls_probe__');

-- ── 3) 清掉这几轮排查留在 vocab_states 的探针行（真数据不动）
delete from public.vocab_states
 where class_key in ('ZZPROBE', '__rls_probe__', 'PROBE_V1');

-- ── 4) 清掉历史探针行（vocab_history_logs / vocab_online_status）
delete from public.vocab_history_logs where student_id = '__rls_probe__';
delete from public.vocab_online_status where student_id = '__rls_probe__';

commit;

-- =====================================================================
-- 自检：跑完看这三条（数字对不上就别往下走，截图给我）
-- =====================================================================
-- ① 逐班条数 vs 基线（基线 = 2026-10-24 快照）
--   ⚠️ **数字会自己涨**（学生天天在练）⇒ 判据是「不许比基线少」，不是「必须等于基线」
--   （写死"总数必须等于 N"是错判据：学生一练习就变少 ⇒ 老师会误以为数据丢了）
with base(class_key, n) as (
  values ('JR1',746),('CW3',582),('WL_CW',280),('XQ1',839),('LISTEN',0),('CET4',6),('JR1|u1',0),('JR1|u2',0)
), cur as (
  select class_key, count(*) n from public.vocab_history_logs group by class_key
)
select '① ' || b.class_key || ' 现在=' || coalesce(c.n,0) || ' 基线=' || b.n
       || case when coalesce(c.n,0) < b.n then '  ⛔ 少了！' else '  ✅' end as x
  from base b left join cur c on c.class_key = b.class_key
 order by b.class_key;
--
-- ② 总条数：只判「没丢」，不判「等于」（基线 2453；学生练习会自然增长，看到比基线多是对的）
select '② 总数 ' || count(*) || ' / 基线 2453 → '
       || case when count(*) >= 2453 then '✅ 没丢数据' else '⛔ 少了数据！' end as x
  from public.vocab_history_logs;
--
-- ③ 现在每张表的 DELETE policy（应该每张表只剩一条收紧的）
select tablename, policyname, cmd, roles::text, qual
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
 order by tablename;
--
-- ④ 老师的 p_teach_all_* 还在吗（应该都在，cmd='ALL'）
select tablename, policyname, cmd, roles::text
  from pg_policies
 where schemaname = 'public' and policyname like 'p_teach_all%'
 order by tablename;
-- =====================================================================
