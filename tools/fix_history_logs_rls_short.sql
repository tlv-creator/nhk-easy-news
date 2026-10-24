-- =====================================================================
-- 【短版】tools/fix_history_logs_rls.sql 去掉全部注释行（语义完全一样）
--   用途：拼 ?query= 直达链接。完整版 URL 有 36766 字符 ⇒ **服务端返回 414 URI_TOO_LONG**
--        （2026-10-24 用户真撞上了这个错）⇒ 短版 URL 6362 字符 ⇒ 实测 200 ✅
--   已在本��� PostgreSQL 上真跑过：写/读/删/upsert/反向验证/开关测试 全部与完整版一致 ✅
--   ⚠️ 别手改：改完必须重跑 bash tools/pg_rls_harness.sh tools/fix_history_logs_rls_short.sql
-- =====================================================================
begin;

do $$
declare r record; has_col boolean := false;
begin
  select exists (select 1 from information_schema.columns
                  where table_schema = 'pg_catalog' and table_name = 'pg_policies'
                    and column_name = 'permissive') into has_col;
  if not has_col then
    raise notice 'pg_policies 没有 permissive 列（PG < 12）⇒ 跳过 restrictive 清理';
    return;
  end if;
  for r in
    select tablename, policyname from pg_policies
     where schemaname = 'public'
       and tablename in ('vocab_history_logs','vocab_submissions','vocab_online_status')
       and upper(permissive) = 'RESTRICTIVE'
  loop
    raise notice '已删除 restrictive policy: % . %', r.tablename, r.policyname;
    execute format('drop policy %I on public.%I', r.policyname, r.tablename);
  end loop;
exception when others then
  raise notice '⚠️ 清理 restrictive policy 失败（**不影响后面的语句**）：%', sqlerrm;
end $$;

drop policy if exists p_anon_insert_logs on public.vocab_history_logs;
create policy p_anon_insert_logs
    on public.vocab_history_logs
    for insert to anon
    with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_select_logs on public.vocab_history_logs;
create policy p_anon_select_logs
    on public.vocab_history_logs
    for select to anon
    using (class_key ~ '^(LISTEN|CET4|CET4TEST)$');

drop policy if exists p_anon_select_logs_vocab on public.vocab_history_logs;
create policy p_anon_select_logs_vocab
    on public.vocab_history_logs
    for select to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_delete_logs on public.vocab_history_logs;
create policy p_anon_delete_logs
    on public.vocab_history_logs
    for delete to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_insert_subs on public.vocab_submissions;
create policy p_anon_insert_subs
    on public.vocab_submissions
    for insert to anon
    with check (true);

drop policy if exists p_anon_update_subs on public.vocab_submissions;
create policy p_anon_update_subs
    on public.vocab_submissions
    for update to anon
    using (true)
    with check (true);

drop policy if exists p_anon_select_subs on public.vocab_submissions;
create policy p_anon_select_subs
    on public.vocab_submissions
    for select to anon
    using (true);

drop policy if exists p_anon_delete_subs on public.vocab_submissions;
create policy p_anon_delete_subs
    on public.vocab_submissions
    for delete to anon
    using (student_id = '__rls_probe__');

drop policy if exists p_anon_insert_online on public.vocab_online_status;
create policy p_anon_insert_online
    on public.vocab_online_status
    for insert to anon
    with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_update_online on public.vocab_online_status;
create policy p_anon_update_online
    on public.vocab_online_status
    for update to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
    with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_select_online on public.vocab_online_status;
create policy p_anon_select_online
    on public.vocab_online_status
    for select to anon
    using (class_key ~ '^(LISTEN|CET4|CET4TEST)$');

drop policy if exists p_anon_select_online_vocab on public.vocab_online_status;
create policy p_anon_select_online_vocab
    on public.vocab_online_status
    for select to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_delete_online on public.vocab_online_status;
create policy p_anon_delete_online
    on public.vocab_online_status
    for delete to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

delete from public.vocab_states
 where class_key in ('__probe__','__probe_states__','__rls_probe__','__cet4_probe__');
delete from public.vocab_history_logs where class_key = '__rls_probe__';
delete from public.vocab_submissions  where student_id = '__rls_probe__';
delete from public.vocab_online_status where class_key = '__rls_probe__';

commit;
