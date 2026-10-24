-- ═══════════════════════════════════════════════════════════════
-- tighten_regress 的**建库**部分（表 / GRANT / ENABLE ROW LEVEL SECURITY / 老师侧边界）
-- ⚠ ENABLE ROW LEVEL SECURITY **不能少** ⇒ 少了所有 policy 都是摆设 ⇒ 结论全是假的
-- ═══════════════════════════════════════════════════════════════
-- 建表 + 授权 + 开 RLS + 老师侧既有边界（与 pg_harness 一致，三件套缺一不可）
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end $$;

create table public.vocab_history_logs (
  id serial primary key, class_key text, class_name text, student_id text,
  timestamp text, duration_sec integer, action text);
create table public.vocab_submissions (
  id serial primary key, student_id text unique, submitted_exams jsonb);
create table public.vocab_online_status (
  id serial primary key, student_id text unique, class_key text, class_name text,
  status_text text, login_time text);
create table public.vocab_states (class_key text primary key, state_val text);

insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
select c,'班'||c,'S'||c,'2026-10-24 12:00:00',60,'练习'
  from (values ('JR1'),('CW3'),('WL_CW'),('XQ1'),('LISTEN'),('CET4')) v(c);
insert into public.vocab_states values ('__roster_v1','[{"id":"S1","name":"甲"}]'),('JR1','release_v1');

-- 🔴 少这一行 ⇒ 所有 policy 都是摆设 ⇒ 测出来的结论全是假的
alter table public.vocab_history_logs  enable row level security;
alter table public.vocab_submissions   enable row level security;
alter table public.vocab_online_status enable row level security;
alter table public.vocab_states        enable row level security;

-- 🔴 不补 GRANT ⇒ 报错是「permission denied for table」（权限）而不是 RLS 判定
grant usage on schema public to anon, authenticated;
grant all on all tables in schema public to anon, authenticated;
grant all on all sequences in schema public to anon, authenticated;

-- 老师侧既有边界（authenticated / service_role 能读能删任何行）—— **不许被收紧 SQL 动到**
-- ⚠ 上一版只建了 DELETE policy ⇒ authenticated **连 SELECT 都没有 policy**
--   ⇒ RLS 把所有行藏起来 ⇒ 老师侧从一开始什么都看不见 ⇒ **根本测不出「收紧把老师侧废了」这个回归**
--   ⇒ 教训：**要复现回归，必须先让「老师侧」在收紧前确实能干活**
create policy p_teach_select_logs   on public.vocab_history_logs   for select to authenticated, service_role using (true);
create policy p_teach_update_logs   on public.vocab_history_logs   for update to authenticated, service_role using (true) with check (true);
create policy p_teach_all_logs      on public.vocab_history_logs   for all to authenticated, service_role using (true) with check (true);
create policy p_teach_select_online on public.vocab_online_status for select to authenticated, service_role using (true);
create policy p_teach_all_online    on public.vocab_online_status for delete to authenticated, service_role using (true);
create policy p_teach_select_subs   on public.vocab_submissions    for select to authenticated, service_role using (true);
create policy p_teach_all_subs      on public.vocab_submissions    for delete to authenticated, service_role using (true);

-- 🔴 混合型 policy（anon + authenticated 同时覆盖）⇒ 用来验「拆分后 authenticated 侧原样保留」
create policy p_mixed_delete_logs on public.vocab_history_logs
    for delete to anon, authenticated
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

-- 🔴 补上 anon 的 INSERT / SELECT（对齐真实库形状）——
--   没有它们，④ 那段 `set role anon; insert ...` 必然 42501 ⇒ **LISTEN 三条路径其实从没被真正验过**
create policy p_anon_insert_logs on public.vocab_history_logs
    for insert to anon with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
create policy p_anon_select_logs on public.vocab_history_logs
    for select to anon using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
create policy p_anon_insert_online on public.vocab_online_status
    for insert to anon with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
create policy p_anon_select_online on public.vocab_online_status
    for select to anon using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
