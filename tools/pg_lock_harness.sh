#!/usr/bin/env bash
# =====================================================================
# 本地 PostgreSQL 真跑台 —— 验证「收尾 SQL」的真实行为
#   用法：bash pg_lock_harness.sh
#   ⚠️ 所有带引号的 SQL 一律写成**临时文件**再用 psql -f 跑
#      （su -c "..." 会把双引号吃掉 ⇒ 上一版 harness 就是这么假红的）
#   ⚠️ 取数一律用 psql -t -A（只要一个数，别把表头/分隔线/行数一起吃进来）
# =====================================================================
set -uo pipefail

SQL_MAIN="${1:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$SQL_MAIN" ]; then
  for c in "$HERE/listen_lock_anon_delete.sql" \
           "$HERE/../deploy/sql/05_lock_anon_delete.sql" \
           "/workspace/listen-log/deploy/sql/05_lock_anon_delete.sql"; do
    [ -f "$c" ] && SQL_MAIN="$c" && break
  done
fi
SQL_BASE="$HERE/fix_history_logs_rls_short.sql"
[ -f "$SQL_BASE" ] || SQL_BASE=/workspace/listen-log/build/repo_scan/sql/fix_history_logs_rls_short.sql
PGROOT="${PGROOT:-/workspace/work/pgtest}"
PGDATA="$PGROOT/data"
PORT=55433
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"
TMPD="$PGROOT/t"

if [ ! -x "$PGBIN/initdb" ]; then echo "❌ 没装 postgres"; exit 1; fi
if [ ! -f "$SQL_MAIN" ]; then echo "❌ 找不到 $SQL_MAIN"; exit 1; fi

as_pg() { su postgres -c "$1"; }

# 跑一段 SQL（内容写文件，避开引号地狱）；输出走 stdout
run_sql() {  # $1 = sql 文本；打印输出
  local f="$TMPD/q.sql"
  printf '%s\n' "$1" > "$f"
  chown postgres:postgres "$f" 2>/dev/null || true
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -t -A -f $f" 2>&1
}
# 只要一个数字
num() { run_sql "$1" | tr -d '[:space:]'; }

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
rm -rf "$PGROOT"; mkdir -p "$PGROOT" "$TMPD"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 || { echo "❌ initdb 失败"; exit 1; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败"; tail -20 "$PGROOT/pg.log"; exit 1; }
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -q -c 'drop database if exists rlstest' -c 'create database rlstest'" >/dev/null 2>&1

as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q" <<'SQL'
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
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
grant all on all tables in schema public to anon;
grant all on all tables in schema public to authenticated;
alter table public.vocab_history_logs enable row level security;
alter table public.vocab_submissions enable row level security;
alter table public.vocab_online_status enable row level security;
-- 老师的 authenticated 侧：**两种形状都建**（真实库是 FOR ALL；另一种是万一哪天变成 FOR DELETE）
--   ⇒ 收尾 SQL 必须**两种都不动**（只删对 anon 生效的）
create policy p_teach_all_logs on public.vocab_history_logs for all to authenticated using (true) with check (true);
create policy p_teach_all_online on public.vocab_online_status for all to authenticated using (true) with check (true);
create policy p_teach_del_logs on public.vocab_history_logs for delete to authenticated using (true);
create policy p_teach_del_online on public.vocab_online_status for delete to authenticated using (true);
insert into public.vocab_history_logs (class_key, class_name, student_id, timestamp, duration_sec, action) values
 ('LISTEN','26互联网金融1班','S001','2026/10/24 12:00:00',600,'A'),
 ('LISTEN','26互联网金融1班','S001','2026/10/24 13:00:00',300,'B'),
 ('LISTEN','26互联网金融2班','S002','2026/10/24 14:00:00',200,'C'),
 ('JR1','JR1班','S003','2026/10/24 10:00:00',100,'X'),
 ('JR1','JR1班','S004','2026/10/24 11:00:00',100,'Y'),
 ('CET4','四级','S005','2026/10/24 12:00:00',100,'Z'),
 ('LISTEN','探针','__rls_probe__','2026/10/24 15:00:00',0,'probe');
insert into public.vocab_states values ('__roster_v1','[{"id":"S001"}]'), ('ZZPROBE','x'), ('__rls_probe__','x'), ('PROBE_V1','x');
SQL

echo "== 先装「老师当时跑的那版」（短版）＝模拟线上现状 =="
if [ -f "$SQL_BASE" ]; then
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $SQL_BASE" >/dev/null 2>&1 \
    && echo "  ✅ 短版装上了" || echo "  ⚠️ 短版没装上（不影响本轮判据）"
fi

echo "== 再装收尾 SQL =="
if ! as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $SQL_MAIN" >/dev/null 2>&1; then
  echo "  ❌ 收尾 SQL 跑失败 ⇒ 立刻停（语法错就别给老师）"
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -f $SQL_MAIN" 2>&1 | tail -20
  exit 1
fi
echo "  ✅ 收尾 SQL 跑通了"

FAILS=0; PASSES=0
ck() { if [ "$2" = "1" ]; then PASSES=$((PASSES+1)); echo "  ✅ $1"; else FAILS=$((FAILS+1)); echo "  ❌ $1  ← 实到: $3"; fi; }

C_LISTEN="select count(*) from public.vocab_history_logs where class_key='LISTEN'"
C_S001="select count(*) from public.vocab_history_logs where student_id='S001'"
C_JR1="select count(*) from public.vocab_history_logs where class_key='JR1'"
C_CET4="select count(*) from public.vocab_history_logs where class_key='CET4'"
C_PROBE="select count(*) from public.vocab_history_logs where student_id='__rls_probe__'"

echo
echo "【判据 1】anon **不带** x-student-id 头 ⇒ 整班删不掉（这是这次要堵的洞）"
run_sql "set role anon;
set request.headers = '{}';
delete from public.vocab_history_logs where class_key='LISTEN';" > /tmp/o1 2>&1
v=$(num "$C_LISTEN"); [ "$v" = "3" ] && ck "无头删 LISTEN：3 条都还在" 1 || ck "无头删 LISTEN：3 条都还在" 0 "$v"
grep -qi "error" /tmp/o1 && echo "     （psql 报错：$(head -2 /tmp/o1 | tr '\n' ' ')）"

echo
echo "【判据 2】anon 带 x-student-id 头 ⇒ 只能删**自称是那个人**的行"
run_sql "set role anon;
set request.headers = '{\"x-student-id\":\"S002\"}';
delete from public.vocab_history_logs where class_key='LISTEN';" > /tmp/o2 2>&1
v=$(num "$C_LISTEN"); [ "$v" = "2" ] && ck "带 S002 的头删 LISTEN：只掉了 S002 那 1 条（剩 2）" 1 || ck "带 S002 的头删 LISTEN：只掉 S002 那条" 0 "$v"
v=$(num "$C_S001"); [ "$v" = "2" ] && ck "S001 的 2 条一条没动" 1 || ck "S001 的 2 条一条没动" 0 "$v"

echo
echo "【判据 3】anon 删别的班（JR1）⇒ 删不动"
run_sql "set role anon;
set request.headers = '{\"x-student-id\":\"S003\"}';
delete from public.vocab_history_logs where class_key='JR1';" > /tmp/o3 2>&1
v=$(num "$C_JR1"); [ "$v" = "2" ] && ck "删 JR1：2 条都还在" 1 || ck "删 JR1：2 条都还在" 0 "$v"

echo
echo "【判据 4】anon 删 CET4 ⇒ 删不动"
run_sql "set role anon;
set request.headers = '{\"x-student-id\":\"S005\"}';
delete from public.vocab_history_logs where class_key='CET4';" > /tmp/o4 2>&1
v=$(num "$C_CET4"); [ "$v" = "1" ] && ck "删 CET4：还在" 1 || ck "删 CET4：还在" 0 "$v"

echo
echo "【判据 5】探针行清理的口子还在（验收脚本靠它）"
run_sql "set role anon;
set request.headers = '{}';
delete from public.vocab_history_logs where student_id='__rls_probe__';" > /tmp/o5 2>&1
v=$(num "$C_PROBE"); [ "$v" = "0" ] && ck "探针行：能删（已删掉）" 1 || ck "探针行：能删（已删掉）" 0 "$v"

echo
echo "【判据 6】authenticated 老师的 p_teach_* 没被削掉（**两种形状都要在**）"
run_sql "set role authenticated;
delete from public.vocab_history_logs where class_key='JR1';" > /tmp/o6 2>&1
v=$(num "$C_JR1"); [ "$v" = "0" ] && ck "authenticated 删 JR1：能删（老师权限没被削）" 1 || ck "authenticated 删 JR1：能删" 0 "$v"
n_teach=$(num "select count(*) from pg_policies where schemaname='public' and policyname like 'p_teach_%'")
[ "$n_teach" -ge 4 ] && ck "p_teach_* 四条都在（FOR ALL ×2 + FOR DELETE ×2）" 1 || ck "p_teach_* 四条都在" 0 "实到 $n_teach"
# 🔴 关键：FOR DELETE 形状的老师 policy（roles={authenticated}）**不能被**收尾 SQL 的 DO 块删掉
n_teach_del=$(num "select count(*) from pg_policies where schemaname='public' and cmd='DELETE' and policyname like 'p_teach_%'")
[ "$n_teach_del" -ge 2 ] && ck "老师的 FOR DELETE 形状 policy 也没被误删" 1 || ck "老师的 FOR DELETE 形状 policy 也没被误删" 0 "实到 $n_teach_del"

echo
echo "【判据 7】vocab_states 的探针行清了、名册行没动"
v=$(num "select count(*) from public.vocab_states where class_key in ('ZZPROBE','__rls_probe__','PROBE_V1')")
[ "$v" = "0" ] && ck "vocab_states 探针行：已清" 1 || ck "vocab_states 探针行：已清" 0 "$v"
v=$(num "select count(*) from public.vocab_states where class_key='__roster_v1'")
[ "$v" = "1" ] && ck "vocab_states 名册行：还在" 1 || ck "vocab_states 名册行：还在" 0 "$v"

echo
echo "【判据 8】policy 现状"
run_sql "select tablename||' | cmd='||cmd||' | '||policyname||' | roles='||roles::text from pg_policies where schemaname='public' and (cmd='DELETE' or policyname like 'p_teach_all%') order by 1;" | sed 's/^/     /'
n_del=$(run_sql "select count(*) from pg_policies where schemaname='public' and cmd='DELETE' and tablename in ('vocab_history_logs','vocab_online_status') and 'anon' = any(roles)" | tr -d '[:space:]')
n_teach=$(run_sql "select count(*) from pg_policies where schemaname='public' and policyname like 'p_teach_all%'" | tr -d '[:space:]')
[ "$n_del" = "2" ] && ck "两张表各只剩一条 **anon** DELETE policy" 1 || ck "两张表各只剩一条 **anon** DELETE policy" 0 "$n_del"
[ "$n_teach" -ge 2 ] && ck "p_teach_all_* 还在（>=2）" 1 || ck "p_teach_all_* 还在" 0 "$n_teach"

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true

echo
echo "================ 结果 ================"
echo "通过 $PASSES 条，失败 $FAILS 条"
if [ "$FAILS" -gt 0 ]; then exit 1; fi
echo "🟢 全绿"
exit 0
