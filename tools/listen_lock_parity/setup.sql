-- =====================================================================
-- 🔴🔴🔴 本文件**只能在你自己起的空的本地库**上跑！
--    第 3 行是：drop table if exists public.vocab_history_logs,
--                               public.vocab_online_status, public.vocab_states;
--    ⇒ **连到生产库执行 = 三个表直接没了**（所有学生的真实记录清零）
--    ⇒ 正确用法：initdb 起一个本地 PG → 建个空库（例：createdb rlstest）
--                → psql -d <本地空库> -f setup.sql
--    ⇒ 不确定当前连的是哪个库 ⇒ 先 \c 看清库名再跑
-- 🔴 夹具里的学号 90000001/90000002 是**合成占位**（不是真人学号）
--    ⇒ 夹具本身碰不到任何真学生的行 —— 但**前提仍是本地空库**
-- =====================================================================
-- 本地回归台 #3：真实库形状 + sha256 行令牌 + LISTEN 三条路径
-- ---------------------------------------------------------------------
-- 🔴 闸门：**只允许 unix socket 连接**（= 本地库）⇒ 任何 TCP 连接一律拒。
--    依据：unix socket 下 inet_server_addr() = NULL；走 TCP 时 = 该连接的本地地址。
--    ⚠️ 判据必须是 inet_server_addr()，**不能靠「库名白名单」** ——
--       每个人的本地库名都不一样（本台 rlstest / 别人的 vt / …）⇒ 白名单必误伤。
--    ⚠️ 下面这行 \set ON_ERROR_STOP on 是**闸门的一部分**，不能少：
--       没有它，psql 报错后会**继续往下跑** ⇒ 后面的 drop table 照样执行 ⇒ 闸门等于装饰。
--    ⚠️ 这个闸门**只加在夹具里**：老师要跑的收尾 SQL（tools/listen_lock_anon_delete.sql、
--       tools/tighten_rls_delete.sql）是**远程 TCP**跑的 ⇒ 那里加 socket 判据 = 老师直接跑不了。
\set ON_ERROR_STOP on
do $$ begin
  if inet_server_addr() is not null then
    raise exception 'setup.sql 只能在本地 unix socket 的库上跑（当前是 TCP 连接）—— 生产库跑这个文件会 drop 掉三张真表';
  end if;
end $$;
-- ---------------------------------------------------------------------
drop table if exists public.vocab_history_logs, public.vocab_online_status, public.vocab_states;

create table public.vocab_history_logs (
  id bigserial primary key, class_key text, class_name text, student_id text,
  timestamp timestamptz default now(), duration_sec integer, action text);
create table public.vocab_online_status (
  id bigserial primary key, class_key text, class_name text, student_id text, updated_at timestamptz default now());
create table public.vocab_states (class_key text primary key, payload jsonb);

grant all on public.vocab_history_logs to anon, authenticated, service_role;
grant all on public.vocab_online_status to anon, authenticated, service_role;
grant all on public.vocab_states to anon, authenticated, service_role;
alter table public.vocab_history_logs enable row level security;
alter table public.vocab_online_status enable row level security;
alter table public.vocab_states enable row level security;

-- 老师侧：真实库形状 = FOR ALL TO authenticated（cmd='ALL'）
create policy p_teach_all_logs on public.vocab_history_logs
  for all to authenticated using (true) with check (true);
create policy p_teach_all_online on public.vocab_online_status
  for all to authenticated using (true) with check (true);
create policy p_teach_all_states on public.vocab_states
  for all to authenticated using (true) with check (true);
-- 混合型（anon + authenticated）—— 收紧时必须被拆开且 authenticated 侧原样保留
create policy p_mixed_delete_logs on public.vocab_history_logs
  for delete to anon, authenticated using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

-- anon 读/写
create policy p_anon_read_logs on public.vocab_history_logs for select to anon using (true);
create policy p_anon_read_online on public.vocab_online_status for select to anon using (true);
create policy p_anon_read_states on public.vocab_states for select to anon using (true);
create policy p_anon_insert_logs on public.vocab_history_logs for insert to anon
  with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
create policy p_anon_insert_online on public.vocab_online_status for insert to anon with check (true);
create policy p_anon_insert_states on public.vocab_states for insert to anon with check (true);
create policy p_anon_update_states on public.vocab_states for update to anon using (true) with check (true);
-- 🔴 收紧前：anon 删整班（要修的洞）
create policy p_anon_delete_logs on public.vocab_history_logs for delete to anon
  using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
create policy p_anon_delete_online on public.vocab_online_status for delete to anon using (true);

-- 数据：词汇站四个班（基线）+ LISTEN 三条（带 sha256 令牌）+ 探针行
insert into public.vocab_history_logs (class_key, class_name, student_id, duration_sec, action)
select c.k, c.k || '班', '2660' || lpad((row_number() over (partition by c.k order by c.k))::text,4,'0'),
       60, '【平时练习】Unit 3 | Page 1 | 模式: 配对 | 时长: 60秒 | 配对: 5对 | 点击: 12次 | 在站: 90秒'
  from (values ('JR1',746),('CW3',591),('WL_CW',280),('XQ1',839),('CET4',6)) as c(k,n),
       generate_series(1, c.n) g;

-- LISTEN：A/B 两人各一条（🔴 学号 90000001/90000002 是**合成占位**，不是真人学号
--   ⇒ 夹具本身碰不到任何真学生的行；**但本文件开头的 drop table 会清库** ⇒ 仍只许本地空库），
--   令牌在库里只存 **sha256(原始令牌)**；原始令牌分别是
--   A = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'  B = 'b1c2d3e4f5061728394a5b6c7d8e9f001'
insert into public.vocab_history_logs (class_key, class_name, student_id, duration_sec, action) values
 ('LISTEN','听力','90000001',900,
  '【听力】精听 | 测试A | #tk=' || encode(sha256(convert_to('a1b2c3d4e5f60718293a4b5c6d7e8f90','utf8')),'hex')),
 ('LISTEN','听力','90000002',900,
  '【听力】精听 | 测试B | #tk=' || encode(sha256(convert_to('b1c2d3e4f5061728394a5b6c7d8e9f001','utf8')),'hex'));
-- 探针行
insert into public.vocab_history_logs (class_key, class_name, student_id, duration_sec, action)
values ('LISTEN','听力','__rls_probe__',10,'探针');
insert into public.vocab_online_status (class_key, class_name, student_id) values ('LISTEN','听力','__rls_probe__');
insert into public.vocab_states (class_key, payload) values ('__rls_probe__','{}'),('ZZPROBE','{}'),('PROBE_V1','{}');
insert into public.vocab_states (class_key, payload) values ('JR1','{}'),('CW3','{}'),('WL_CW','{}'),('XQ1','{}'),('__roster_v1','{}');
