-- ═══════════════════════════════════════════════════════════════
-- tighten_regress 的全部 SQL（建表 / 授权 / 开 RLS / 老师侧边界 / 各角色行为断言）
-- ⚠ ENABLE ROW LEVEL SECURITY 这一行**不能少** ⇒ 少了的话所有 policy 都是摆设
--   ⇒ 测出来的「删得掉/删不掉」全是假的（我第一版就栽在这）
-- ═══════════════════════════════════════════════════════════════
\echo === ① 建表 + 授权 + 开 RLS + 老师侧既有边界 ===
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
create policy p_teach_all_logs      on public.vocab_history_logs   for delete to authenticated, service_role using (true);
create policy p_teach_select_online on public.vocab_online_status for select to authenticated, service_role using (true);
create policy p_teach_all_online    on public.vocab_online_status for delete to authenticated, service_role using (true);
create policy p_teach_select_subs   on public.vocab_submissions    for select to authenticated, service_role using (true);
create policy p_teach_all_subs      on public.vocab_submissions    for delete to authenticated, service_role using (true);

\echo === ② 老师侧（authenticated）删 JR1 ===
set role authenticated;
delete from public.vocab_history_logs where class_key = 'JR1';
reset role;
select '  → 老师侧删 JR1 后，JR1 剩余行数 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'JR1';
-- 供下一段测试用：把行插回来（reset role 之后是超级用户 ⇒ 不受 RLS 限制）
insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
  values ('JR1','班','SJR1','2026-10-24 12:00:00',60,'练习')
  on conflict do nothing;
select '  → 插回一行后，JR1 行数 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'JR1';

\echo === ③ 学生侧（anon）删 JR1 ===
set role anon;
delete from public.vocab_history_logs where class_key = 'JR1';
reset role;
select '  → anon 删 JR1 后，JR1 剩余行数 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'JR1';

\echo === ④ 听力站三条删除路径（anon）===
set role anon;
insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
  values ('LISTEN','探针','__rls_probe__','t',0,'p') on conflict do nothing;
delete from public.vocab_history_logs where class_key = 'LISTEN' and student_id = '__rls_probe__';
select '  → 路径1 学生删自己：LISTEN 里探针行剩余 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'LISTEN' and student_id = '__rls_probe__';
delete from public.vocab_history_logs where class_key = 'LISTEN';
select '  → 路径3 老师清空全部：LISTEN 剩余 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'LISTEN';
reset role;

\echo === ⑤ 剩下的 DELETE policy ===
select '  ' || tablename || ' | ' || policyname || ' | roles=' || roles::text || ' | ' || left(qual, 52) as p
  from pg_policies where schemaname = 'public' and cmd = 'DELETE' order by tablename;

\echo === ⑥ 调试：角色 / 权限 / 可见行数 ===
select 'current_user=' || current_user || ' session_user=' || session_user as whoami;
select 'authenticated 有 DELETE 权限 = ' || has_table_privilege('authenticated','public.vocab_history_logs','DELETE') as priv;
select '行数(切角色前) = ' || count(*) as n from public.vocab_history_logs;
select '  policy: '||policyname||' roles='||roles::text||' cmd='||cmd||' qual='||left(qual,40) as p
  from pg_policies where schemaname='public' and tablename='vocab_history_logs';
set role authenticated;
select '  切到 authenticated 后 current_user=' || current_user as cu;
select '  authenticated 看得到 JR1 行数 = ' || count(*) as n from public.vocab_history_logs where class_key='JR1';
do $$
declare n int;
begin
  delete from public.vocab_history_logs where class_key='JR1';
  get diagnostics n = row_count;
  raise notice '  DELETE 影响行数 = %', n;
end $$;
reset role;
select '行数(切回后) = ' || count(*) as n from public.vocab_history_logs;
