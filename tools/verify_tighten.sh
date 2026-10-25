#!/usr/bin/env bash
# =====================================================================
# 独立验证「第一步 tighten SQL（短版）」在**真实库形状**上：
#   ① 短版能跑通（无 SQL 错误）—— 老师点的就是这个短版
#   ② 老师侧 policy 一根都没坏（含 FOR ALL 的 p_teach_all_* 和混合型 policy）
#   ③ anon 的删除面从「任意 class_key」收成「只 LISTEN」
#   ④ 真数据一行没少
#
# 🔴 只在本地 PG 上跑，**不碰生产库**
# 🔴 退出码有三种，别混：
#     0  = 断言全过
#     1  = 断言红了（判据失败）
#     9  = **环境没搭起来**（initdb 失败 / 起库失败 / 找不到 PG）⇒ 本次验证**无效**，
#          不是「验证通过」也不是「验证失败」
#
# 路径必须可移植（别人只拿共享仓库就能跑）：
#   TIGHTEN_SQL      短版 SQL 路径（不设就按脚本自身位置自动找）
#   TIGHTEN_PGROOT   临时 PG 数据目录（默认 $HERE/.pgtighten）
#   LISTEN_PGPORT    端口（默认 55444）
# =====================================================================
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# ---- 找短版 SQL：参数 → 环境变量 → 相对脚本的候选 → 绝对候选 ------------------
SQL_MAIN=""
for cand in "$@"; do
  if [ -n "$cand" ] && [ -f "$cand" ]; then SQL_MAIN="$(cd "$(dirname "$cand")" && pwd)/$(basename "$cand")"; break; fi
done
if [ -z "$SQL_MAIN" ]; then
  for cand in \
    "${TIGHTEN_SQL:-}" \
    "$HERE/tighten_rls_delete_short.sql" \
    "$HERE/../tools/tighten_rls_delete_short.sql" \
    "$HERE/tighten_short.sql" \
    "/workspace/work/tighten_short.sql"; do
    if [ -n "$cand" ] && [ -f "$cand" ]; then SQL_MAIN="$cand"; break; fi
  done
fi
if [ -z "$SQL_MAIN" ]; then
  echo "❌ 找不到短版 SQL（设 TIGHTEN_SQL 指向它，或把它放在脚本同目录）"
  exit 9
fi

PGROOT="${TIGHTEN_PGROOT:-$HERE/.pgtighten}"
PGDATA="$PGROOT/data"
PORT="${LISTEN_PGPORT:-55444}"
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"
TMPD="$PGROOT/t"

if [ -z "$PGBIN" ] || [ ! -x "$PGBIN/initdb" ]; then
  echo "❌ 找不到 PostgreSQL 可执行文件（$PGBIN）⇒ 环境没搭起来 ⇒ 本次验证**无效**"
  exit 9
fi
if ! id postgres >/dev/null 2>&1; then
  echo "❌ 没有 postgres 系统用户 ⇒ 本机没法用 su postgres 起库 ⇒ 本次验证**无效**"
  exit 9
fi

as_pg() { su postgres -c "$1"; }
run_sql() {
  local f="$TMPD/q.sql"
  printf '%s\n' "$1" > "$f"
  chown postgres:postgres "$f" 2>/dev/null || true
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -t -A -f $f" 2>&1
}
num() { run_sql "$1" | tr -d '[:space:]'; }

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
rm -rf "$PGROOT"; mkdir -p "$PGROOT" "$TMPD"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 || { echo "❌ initdb 失败 ⇒ 环境没搭起来 ⇒ 本次验证**无效**"; exit 9; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败 ⇒ 环境没搭起来 ⇒ 本次验证**无效**"; tail -20 "$PGROOT/pg.log"; exit 9; }
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -q -c 'drop database if exists rlstest' -c 'create database rlstest'" >/dev/null 2>&1

pass=0; fail=0
ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
bad()  { echo "  ❌ $1"; fail=$((fail+1)); }

echo "== 搭「真实库形状」=="
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q" >/dev/null 2>&1 <<'SQL'
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin; end if;
end $$;
create table public.vocab_history_logs (
  id bigserial primary key, class_key text, class_name text, student_id text,
  timestamp text, duration_sec int, action text);
create table public.vocab_online_status (
  id bigserial primary key, class_key text, student_id text, state text);
create table public.vocab_states (
  id bigserial primary key, class_key text, payload jsonb);
alter table public.vocab_history_logs enable row level security;
alter table public.vocab_online_status enable row level security;
alter table public.vocab_states enable row level security;
-- 跑之前的形状：anon 的删除面是「任意像班级名的 class_key」⇒ 能删光任何一班
create policy p_anon_delete_logs on public.vocab_history_logs
  for delete to anon using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');
create policy p_anon_delete_online on public.vocab_online_status
  for delete to anon using (student_id = '__rls_probe__');
-- 老师侧：FOR ALL（cmd='ALL'）⇒ 只查 cmd='DELETE' 会误判成「老师侧不见了」
create policy p_teach_all_logs on public.vocab_history_logs
  for all to authenticated using (true) with check (true);
create policy p_teach_all_online on public.vocab_online_status
  for all to authenticated using (true) with check (true);
-- 混合型：同时含 anon + authenticated 的 DELETE policy
create policy p_mixed_logs on public.vocab_history_logs
  for delete to anon, authenticated using (class_key = 'MIXED');
insert into public.vocab_history_logs (class_key, class_name, student_id, timestamp, duration_sec, action)
values ('JR1','26互联网金融1班','90000011','2026/10/25 20:00:00',600,'x'),
       ('LISTEN','26财务管理3班','90000021','2026/10/25 20:46:00',900,'y'),
       ('LISTEN','26财务管理3班','90000022','2026/10/25 21:00:00',300,'z'),
       ('CET4','四级','999','2026/10/25 21:10:00',120,'w');
insert into public.vocab_history_logs (class_key, student_id) values ('LISTEN','__rls_probe__');
insert into public.vocab_online_status (class_key, student_id, state) values ('LISTEN','__rls_probe__','x');
insert into public.vocab_states (class_key, payload) values ('ZZPROBE','{}'),('__roster_v1','{}');
SQL

echo "== 跑前基线 =="
b_logs=$(num "select count(*) from public.vocab_history_logs")
b_states=$(num "select count(*) from public.vocab_states")
b_teach=$(num "select count(*) from pg_policies where schemaname='public' and cmd='ALL'")
b_mixed=$(num "select count(*) from pg_policies where policyname='p_mixed_logs'")
b_real=$(num "select count(*) from public.vocab_history_logs where student_id <> '__rls_probe__'")
echo "  logs=$b_logs states=$b_states 老师ALL=$b_teach 混合型=$b_mixed"
# 🔴🔴 **基线必须 > 0**（真踩过：搭库失败 ⇒ logs=0 ⇒ 「跑前==跑后」是 0==0 的**空语句假绿**）
if [ "$b_logs" -lt 4 ] || [ "$b_teach" -lt 2 ] || [ "$b_mixed" -lt 1 ] || [ "$b_states" -lt 2 ]; then
  echo "❌ 环境没搭起来（基线为 0 或不对）⇒ 本次验证**无效**，不产出结论"
  as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
  exit 9
fi
echo "  ✅ 基线非空 ⇒ 下面的断言有意义"

echo "== 跑短版 tighten（$SQL_MAIN）=="
cp "$SQL_MAIN" "$TMPD/tighten.sql"; chown postgres:postgres "$TMPD/tighten.sql"
OUT=$(as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $TMPD/tighten.sql" 2>&1); rc=$?
if [ $rc -eq 0 ] && ! printf '%s' "$OUT" | grep -qi "error"; then
  ok "短版跑通（exit 0，无 SQL 错误）"
else
  bad "短版有错："; printf '%s\n' "$OUT" | head -8 | sed 's/^/       /'
fi

echo "== 断言 =="
a_logs=$(num "select count(*) from public.vocab_history_logs where student_id <> '__rls_probe__'")
# ⚠️ 跑前的基线含探针行；这条 SQL 的**设计意图**就是清掉探针 ⇒ 必须拿「非探针行数」比
[ "$a_logs" = "$b_real" ] && ok "真数据一行没少（非探针 $a_logs 条）" || bad "真数据变了：$b_real → $a_logs"
a_teach=$(num "select count(*) from pg_policies where schemaname='public' and cmd='ALL'")
[ "$a_teach" = "$b_teach" ] && ok "老师侧 FOR ALL policy 一根没少（$a_teach）" || bad "老师侧 policy 少了：$b_teach → $a_teach"
a_mixed=$(num "select count(*) from pg_policies where policyname like 'p_mixed_logs%'")
[ "$a_mixed" -ge 1 ] && ok "混合型 policy 还在（$a_mixed 条，含 _teacher 副本）" || bad "混合型 policy 不见了"
a_qual=$(num "select coalesce(string_agg(qual,' / '),'') from pg_policies where policyname='p_mixed_logs_teacher'")
if [ -n "$a_qual" ] && printf '%s' "$a_qual" | grep -q "MIXED"; then
  ok "混合型的老师半边**按原判据**重建（qual=$a_qual）"
else
  bad "混合型老师半边没按原判据重建（qual=$a_qual）"
fi
a_anon=$(num "select count(*) from pg_policies where schemaname='public' and cmd='DELETE' and 'anon'=any(roles)")
[ "$a_anon" = "2" ] && ok "anon 的 DELETE policy 每表 1 条（共 $a_anon）" || bad "anon DELETE policy 数不对：$a_anon"
a_l=$(num "select coalesce(qual,'') from pg_policies where policyname='p_anon_delete_logs'")
printf '%s' "$a_l" | grep -q "LISTEN" && ok "anon 的日志删除面已收成 LISTEN（$a_l）" || bad "anon 删除面没收紧：$a_l"
a_probe=$(num "select (select count(*) from public.vocab_history_logs where student_id='__rls_probe__') + (select count(*) from public.vocab_online_status where student_id='__rls_probe__') + (select count(*) from public.vocab_states where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1'))")
[ "$a_probe" = "0" ] && ok "探针行清干净了（0）" || bad "还有探针残留：$a_probe"
a_roster=$(num "select count(*) from public.vocab_states where class_key='__roster_v1'")
a_states=$(num "select count(*) from public.vocab_states")
[ "$a_roster" = "1" ] && ok "__roster_v1 没被误删（states 总数 $a_states，探针 ZZPROBE 已清）" || bad "__roster_v1 被误删了（states=$a_states）"

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
echo
echo "== 结果：通过 $pass / 失败 $fail =="
[ "$fail" -eq 0 ] || exit 1
exit 0
