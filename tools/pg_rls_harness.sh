#!/usr/bin/env bash
# =====================================================================
# 本地 PostgreSQL 测试台 —— 验证 RLS policy 的真实行为
#   用法：bash pg_harness.sh <要测的 sql 文件>
#   目标：① SQL 能跑通（无语法错）② 逐个 class_key 验 写/读/删/upsert 是否符合预期
# =====================================================================
set -uo pipefail
SQL_FILE="${1:?用法: pg_harness.sh <sql 文件>}"
PGROOT=/workspace/work/pgtest
PGDATA="$PGROOT/data"
PORT=55432
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"
if [ ! -x "$PGBIN/initdb" ]; then echo "❌ 没装 postgres（$PGBIN）"; exit 1; fi

# 🔴 initdb / postgres 拒绝以 root 跑 ⇒ 全部用 postgres 用户执行
as_pg() { su postgres -c "$1"; }
# psql 包装：参数用空格分隔（本脚本的路径都不含空格）
psql_as_pg() { as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -v ON_ERROR_STOP=1 -q $*" ; }

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
rm -rf "$PGROOT"; mkdir -p "$PGROOT"; chown -R postgres:postgres "$PGROOT"

as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 || { echo "❌ initdb 失败"; exit 1; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败"; tail -20 "$PGROOT/pg.log"; exit 1; }

psql_as_pg -d postgres >/dev/null 2>&1 <<'SQL'
drop database if exists rlstest;
create database rlstest;
SQL

psql_as_pg -d rlstest >/dev/null 2>&1 <<'SQL'
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
end $$;

-- 表结构（列名/类型全部来自三个站线上代码的实际载荷）
create table public.vocab_history_logs (
  id serial primary key,
  class_key text, class_name text, student_id text,
  timestamp text, duration_sec integer, action text);
create table public.vocab_submissions (
  id serial primary key,
  student_id text unique, submitted_exams jsonb);
create table public.vocab_online_status (
  id serial primary key,
  student_id text unique, class_key text, class_name text,
  status_text text, login_time text);
create table public.vocab_states (
  class_key text primary key, state_val text);

-- 预置数据：模拟真实库（2418 行里各班都有）
insert into public.vocab_history_logs (class_key, class_name, student_id, timestamp, duration_sec, action)
select c, '班'||c, 'S'||c, '2026-10-24 12:00:00', 60, '练习'
from (values ('JR1'),('CW3'),('WL_CW'),('XQ1'),('LISTEN'),('CET4')) v(c);
insert into public.vocab_states values ('__roster_v1','[{"id":"S1","name":"甲"}]'), ('JR1','release_v1');
-- 🔴 预置「已存在」的行 ⇒ 之后 anon 的 upsert 才会走 ON CONFLICT DO UPDATE 分支
--    （第一次标记是纯 INSERT，第二次才是 UPDATE ⇒ 只测第一次会漏掉「缺 update policy」这个洞）
insert into public.vocab_submissions (student_id, submitted_exams) values ('__probe__', '["旧"]'::jsonb);
insert into public.vocab_online_status (student_id,class_key,class_name,status_text,login_time)
  values ('__probe__','JR1','班','旧','11:00');

alter table public.vocab_history_logs  enable row level security;
alter table public.vocab_submissions   enable row level security;
alter table public.vocab_online_status enable row level security;
alter table public.vocab_states        enable row level security;

-- 🔴 Supabase 默认给 anon / authenticated 授了 public schema 的表权限
--    不补这条 ⇒ 报错是「permission denied for table」（权限）而不是 RLS 判定
--    ⇒ 两种错误 SQLSTATE 都是 42501 ⇒ 不加这句就会把「权限错」当成「RLS 收窄」测出来
grant usage on schema public to anon, authenticated;
grant all on all tables in schema public to anon, authenticated;
grant all on all sequences in schema public to anon, authenticated;
SQL

echo "════ 施加 policy 文件：$SQL_FILE ════"
if ! psql_as_pg -d rlstest -f "$SQL_FILE" > "$PGROOT/apply.log" 2>&1; then
  echo "❌ SQL 执行失败（语法错 / 列不存在 / 事务回滚）："; cat "$PGROOT/apply.log"; exit 1
fi
if grep -qiE "error|rollback" "$PGROOT/apply.log"; then
  echo "⚠️ apply.log 里有报错痕迹："; grep -iE "error|rollback" "$PGROOT/apply.log" | head -5
fi
echo "✅ SQL 执行通过（无语法错）"

psql_as_pg -d rlstest 2>&1 <<'SQL'
set role anon;
\echo '--- ① 写（按 class_key 白名单）---'
do $$
declare c text; ok_cnt int := 0; bad text := '';
begin
  foreach c in array array['JR1','CW3','WL_CW','XQ1','LISTEN','CET4',
                           'JR1|u1','WL_CW|u2','XQ1|u3',      -- 🔴 词汇站第一/二课的真实形态
                           'ZZ9'] loop                          -- 🔴 老师以后新增的班级
    begin
      insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
      values (c,'探针','__probe__','t',0,'probe');
      ok_cnt := ok_cnt + 1;
    exception when others then bad := bad || c || '[' || sqlerrm || '] '; end;
  end loop;
  raise notice '写入成功 %/6 个；被拒：%', ok_cnt, coalesce(nullif(bad,''),'无');
end $$;

\echo '--- ② 读 ---'
select '  anon 读 LISTEN 行数 = ' || count(*) from public.vocab_history_logs where class_key='LISTEN';
select '  anon 读 JR1 行数    = ' || count(*) from public.vocab_history_logs where class_key='JR1';

\echo '--- ③ 删（听力站三条真实路径）---'
do $$
declare n int; msg text := '';
begin
  delete from public.vocab_history_logs where class_key='LISTEN' and student_id='__probe__';
  get diagnostics n = row_count; msg := msg || '路径1(学生删自己) 删掉' || n || '行; ';
  delete from public.vocab_history_logs where id=(select min(id) from public.vocab_history_logs where class_key='LISTEN') and class_key='LISTEN';
  get diagnostics n = row_count; msg := msg || '路径2(老师删一条) 删掉' || n || '行; ';
  delete from public.vocab_history_logs where class_key='LISTEN';
  get diagnostics n = row_count; msg := msg || '路径3(老师清空全部) 删掉' || n || '行; ';
  raise notice '%', msg;
end $$;

\echo '--- ④ 删完回读（PostgREST 删不掉也返回 200 ⇒ 必须回读）---'
select '  LISTEN 剩余行数 = ' || count(*) from public.vocab_history_logs where class_key='LISTEN';

\echo '--- ⑤ upsert（词汇站：标记已交卷 / 写在线状态）---'
do $$
declare msg text := '';
begin
  begin
    insert into public.vocab_submissions (student_id, submitted_exams)
    values ('__probe__', '["x"]'::jsonb)
    on conflict (student_id) do update set submitted_exams = excluded.submitted_exams;
    msg := msg || 'submissions.upsert ✅; ';
  exception when others then msg := msg || 'submissions.upsert ❌('||sqlerrm||') '; end;
  begin
    insert into public.vocab_online_status (student_id,class_key,class_name,status_text,login_time)
    values ('__probe__','JR1','班','ok','12:00')
    on conflict (student_id) do update set status_text = excluded.status_text;
    msg := msg || 'online_status.upsert ✅; ';
  exception when others then msg := msg || 'online_status.upsert ❌('||sqlerrm||'); '; end;
  raise notice '%', msg;
end $$;

\echo '--- ⑥ 反向验证：白名单外的 class_key 必须写不进去 ---'
do $$
declare msg text := '';
begin
  begin
    insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
    values ('__rls_neg__','x','__probe__','t',0,'probe');
    msg := msg || '❌ 下划线开头的竟然写进去了（policy 没收窄）; ';
  exception when others then msg := msg || '✅ 下划线开头被拒; '; end;
  begin
    insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
    values ('lowercase','x','__probe__','t',0,'probe');
    msg := msg || '❌ 小写 key 竟然写进去了; ';
  exception when others then msg := msg || '✅ 小写 key 被拒; '; end;
  raise notice '%', msg;
end $$;
reset role;
SQL

psql_as_pg -d rlstest -c "delete from public.vocab_history_logs where student_id='__probe__';" >/dev/null 2>&1
as_pg "$PGBIN/pg_ctl -D $PGDATA -m fast stop" >/dev/null 2>&1
echo "（测试库已停）"
