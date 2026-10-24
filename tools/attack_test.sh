#!/usr/bin/env bash
# =====================================================================
# attack_test.sh —— **补上 harness 漏掉的那个攻击**
#
# 【为什么要有这个脚本】
#   tools/pg_lock_harness.sh 的判据 2c 测的是：
#       x-student-id = S001（别人的学号） + x-row-token = S002 的令牌
#   ⇒ 令牌与学号**错配** ⇒ 删不掉 ⇒ 绿 ✅
#   🔴 但**真正的攻击**不是错配，而是：
#       ① 攻击者拿着**公开的 anon 密钥** ⇒ `action` 列**可读**
#       ② S001 自己的行 action 里写着 `... | #tk=aaaa1111...`
#       ③ 攻击者把**从 action 列读到的 S001 自己的令牌** + S001 的学号一起发
#       ⇒ 按 policy 字面意思：**这一行会被删掉**
#   ⇒ 「令牌」如果明文存在 action 列里，它**根本不是秘密**
#   ⇒ 这就是为什么必须存 **SHA-256(原始令牌)**，原始令牌只留本机
#
# 【本脚本断言两件事（缺一不可）】
#   A) 现行「明文令牌」政策 ⇒ 上面那个攻击**能删掉** ⇒ 洞是真的（⇒ 不能给老师用）
#   B) 「SHA-256 令牌」政策 ⇒ 同一个攻击**删不掉** ✅，
#      且**本人**（浏览器里留有原始令牌）仍然删得掉自己那一条 ✅
#
# 用法：bash tools/attack_test.sh
# ⚠️ 所有带引号的 SQL 一律写**临时文件**再用 psql -f 跑
#    （su -c "..." 会把双引号吃掉 ⇒ 上一版 harness 就是这么假红的）
# ⚠️ 中间目录放 /workspace/work 下，**不要用 /tmp**（沙箱会回收 ⇒ 还原失败）
# =====================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PGROOT="${PGROOT:-/workspace/work/pgtest_atk}"
PGDATA="$PGROOT/data"
PORT="${ATK_PGPORT:-55444}"
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"
TMPD="$PGROOT/t"
LOCK_SQL="$HERE/listen_lock_anon_delete.sql"
BASE_SQL="$HERE/fix_history_logs_rls_short.sql"

if [ ! -x "$PGBIN/initdb" ]; then echo "❌ 没装 postgres（apt 源要用 mirrors.aliyun.com）"; exit 1; fi
if [ ! -f "$LOCK_SQL" ]; then echo "❌ 找不到 $LOCK_SQL"; exit 1; fi

as_pg() { su postgres -c "$1"; }
run_sql() {
  local f="$TMPD/q.sql"
  printf '%s\n' "$1" > "$f"
  chown postgres:postgres "$f" 2>/dev/null || true
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -t -A -f $f" 2>&1
}
num() { run_sql "$1" | tr -d '[:space:]'; }

# 🔴 必须先**真的把上一轮的 postgres 弄死**再删目录
as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
if [ -f "$PGDATA/postmaster.pid" ]; then
  _pid=$(head -1 "$PGDATA/postmaster.pid" 2>/dev/null | tr -dc '0-9')
  [ -n "$_pid" ] && kill -9 "$_pid" >/dev/null 2>&1
  for _i in $(seq 1 40); do kill -0 "$_pid" >/dev/null 2>&1 || break; sleep 0.25; done
fi
rm -rf "$PGROOT"; mkdir -p "$TMPD"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 || { echo "❌ initdb 失败"; exit 1; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败"; tail -20 "$PGROOT/pg.log"; exit 1; }
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -q -c 'drop database if exists rlstest' -c 'create database rlstest'" >/dev/null 2>&1

echo "== 建测试台（表 + GRANT + ENABLE RLS + 老师两种形状）=="
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q" <<'SQL'
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
end $$;
create table public.vocab_history_logs (
  id serial primary key, class_key text, class_name text, student_id text,
  timestamp text, duration_sec integer, action text);
create table public.vocab_online_status (
  id serial primary key, student_id text unique, class_key text, class_name text,
  status_text text, login_time text);
create table public.vocab_states (class_key text primary key, state_val text);
-- 🔴🔴 基座 SQL 还会碰这张表（upsert / on_conflict 都要它）⇒ **缺了它，基座 SQL 会执行到一半报错**
--   ⇒ 报错被我 >/dev/null 吞掉 ⇒ SELECT policy 没建出来 ⇒ **anon 看不见行**
--   ⇒ 后果：「攻击删不掉」是**假绿**、「本人删得掉」是**假红** ⇒ 两条结论都不可信
--   ⇒ 这就是「以为测到了，其实没测到」的一族
create table public.vocab_submissions (
  id serial primary key, student_id text unique, submitted_exams jsonb);
grant all on all tables in schema public to anon;
grant all on all tables in schema public to authenticated;
alter table public.vocab_history_logs enable row level security;
alter table public.vocab_online_status enable row level security;
alter table public.vocab_states enable row level security;
alter table public.vocab_submissions enable row level security;
create policy p_anon_delete_logs_old on public.vocab_history_logs for delete to anon using (class_key ~ '^[A-Z][A-Z0-9_]*$');
create policy p_teach_all_logs on public.vocab_history_logs for all to authenticated using (true) with check (true);
create policy p_teach_all_online on public.vocab_online_status for all to authenticated using (true) with check (true);
SQL

# ── 造数据 ──────────────────────────────────────────────────────────
#   S001 自己的那行，明文令牌 = 'aaaa1111bbbb2222cccc3333'
#   ⇒ 攻击者只要能读 action 列，就能拿到它
#   ⇒ SHA-256 版里，action 列存的是 sha256(原始令牌)，原始令牌**不在库里**
RAW1='aaaa1111bbbb2222cccc3333'
RAW2='dddd1111eeee2222ffff3333'
HASH1=$(printf '%s' "$RAW1" | sha256sum | cut -d' ' -f1)
HASH2=$(printf '%s' "$RAW2" | sha256sum | cut -d' ' -f1)

seed_plain() {
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q" <<SQL
insert into public.vocab_history_logs (class_key, class_name, student_id, timestamp, duration_sec, action) values
 ('LISTEN','26互联网金融1班','S001','2026/10/24 12:00:00',600,'A | #tk=$RAW1'),
 ('LISTEN','26互联网金融1班','S001','2026/10/24 13:00:00',300,'B | #tk=$RAW2'),
 ('LISTEN','26互联网金融2班','S002','2026/10/24 14:00:00',200,'C | #tk=9999aaaa8888bbbb7777cccc'),
 ('JR1','JR1班','S003','2026/10/24 10:00:00',100,'X');
insert into public.vocab_states values ('__roster_v1','[{"id":"S001"}]');
SQL
}
seed_hash() {
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q" <<SQL
insert into public.vocab_history_logs (class_key, class_name, student_id, timestamp, duration_sec, action) values
 ('LISTEN','26互联网金融1班','S001','2026/10/24 12:00:00',600,'A | #tk=$HASH1'),
 ('LISTEN','26互联网金融1班','S001','2026/10/24 13:00:00',300,'B | #tk=$HASH2'),
 ('LISTEN','26互联网金融2班','S002','2026/10/24 14:00:00',200,'C | #tk=9999aaaa8888bbbb7777cccc'),
 ('JR1','JR1班','S003','2026/10/24 10:00:00',100,'X');
insert into public.vocab_states values ('__roster_v1','[{"id":"S001"}]');
SQL
}
C_S001="select count(*) from public.vocab_history_logs where student_id='S001'"

FAILS=0; PASSES=0
ck() { if [ "$2" = "1" ]; then PASSES=$((PASSES+1)); echo "  ✅ $1"; else FAILS=$((FAILS+1)); echo "  ❌ $1  ← 实到: $3"; fi; }

# ═══════════════════════════════════════════════════════════════════
echo
echo "════ 阶段 A：现行「明文令牌」政策（tools/listen_lock_anon_delete.sql）════"
# ═══════════════════════════════════════════════════════════════════
if [ -f "$BASE_SQL" ]; then
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $BASE_SQL" >/dev/null 2>&1 \
    || { echo "  ❌ 基座 SQL 装失败 ⇒ **中止**（测试台不对，不是 policy 的问题）"; exit 1; }
fi
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $LOCK_SQL" >/dev/null 2>&1 \
  || { echo "  ❌ 收尾 SQL 没装上"; exit 1; }
seed_plain
echo "  装好了：明文令牌版 policy"

# 🔴🔴 前置断言：**anon 必须能看见行**，否则下面所有「删不掉」都是假绿
v=$(run_sql "set role anon; select count(*) from public.vocab_history_logs where class_key='LISTEN';" | tr -d '[:space:]')
if [ "$v" != "3" ]; then
  echo "  ❌ **测试台没搭对**（anon 能看见的行数 = $v，应为 3）"
  echo "     ⇒ anon 没有 SELECT policy ⇒ 后面所有「删不掉」都是**假绿** ⇒ 中止"
  exit 1
fi
echo "  ✅ 前置断言：anon 能看见 3 行（测试台成立）"

# 🔴 真正的攻击：攻击者从 action 列**读到** S001 自己的令牌，再配上 S001 的学号
run_sql "set role anon;
set request.headers = '{\"x-student-id\":\"S001\",\"x-row-token\":\"$RAW1\"}';
delete from public.vocab_history_logs where class_key='LISTEN';" >/dev/null 2>&1
v=$(num "$C_S001")
if [ "$v" = "1" ]; then
  ck "🔴 攻击（读 action 列拿令牌 + 伪造学号）**真的删掉了 S001 的一行**" 1 "S001 剩 $v 行（应为 1）"
  echo "     ⇒ ⚠️ **明文令牌不是秘密** ⇒ 这一版**不能给老师用**（同伴已决定改 SHA-256 版）"
else
  ck "🔴 攻击（读 action 列拿令牌 + 伪造学号）被挡住了" 0 "S001 剩 $v 行（竟没删掉 ⇒ 明文版比想的更严？）"
fi

# ═══════════════════════════════════════════════════════════════════
echo
echo "════ 阶段 B：「SHA-256 令牌」政策（候选修法）════"
# ═══════════════════════════════════════════════════════════════════
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q" <<'SQL'
drop policy if exists p_anon_delete_logs on public.vocab_history_logs;
create policy p_anon_delete_logs
    on public.vocab_history_logs
    for delete to anon
    using (
      student_id = '__rls_probe__'
      or (
        class_key = 'LISTEN'
        and student_id = nullif(current_setting('request.headers', true)::json->>'x-student-id', '')
        and nullif((current_setting('request.headers', true)::json->>'x-row-token'), '') ~ '^[A-Za-z0-9]{16,64}$'
        and action like ('%#tk=' || encode(sha256(convert_to((current_setting('request.headers', true)::json->>'x-row-token'), 'utf8')), 'hex') || '%')
      )
    );
SQL
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -q -c 'delete from public.vocab_history_logs' -c 'delete from public.vocab_states'" >/dev/null 2>&1
seed_hash
echo "  装好了：SHA-256 令牌版 policy（action 列存 sha256(原始令牌)）"

# 攻击者从 action 列读到的**只有哈希** ⇒ 拿它当令牌发 ⇒ 必须删不掉
run_sql "set role anon;
set request.headers = '{\"x-student-id\":\"S001\",\"x-row-token\":\"$HASH1\"}';
delete from public.vocab_history_logs where class_key='LISTEN';" >/dev/null 2>&1
v=$(num "$C_S001")
[ "$v" = "2" ] && ck "🔴 攻击（拿 action 列里的**哈希**当令牌）⇒ 删不掉" 1 "S001 剩 $v 行" \
             || ck "🔴 攻击（拿 action 列里的哈希当令牌）⇒ 删不掉" 0 "S001 剩 $v 行（应为 2）"

# 本人（浏览器里留有原始令牌）⇒ 必须删得掉自己那一条
run_sql "set role anon;
set request.headers = '{\"x-student-id\":\"S001\",\"x-row-token\":\"$RAW1\"}';
delete from public.vocab_history_logs where class_key='LISTEN' and id in (select id from public.vocab_history_logs where student_id='S001' limit 1);" >/dev/null 2>&1
v=$(num "$C_S001")
[ "$v" = "1" ] && ck "✅ 本人带原始令牌 ⇒ 删得掉自己那一条（功能没被误伤）" 1 "S001 剩 $v 行" \
             || ck "✅ 本人带原始令牌 ⇒ 删得掉自己那一条（功能没被误伤）" 0 "S001 剩 $v 行（应为 1）"

# 探针行的口子必须还在（验收脚本靠它）
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -q -c \"insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action) values ('LISTEN','探针','__rls_probe__','2026/10/24 15:00:00',0,'probe')\"" >/dev/null 2>&1
run_sql "set role anon; delete from public.vocab_history_logs where student_id='__rls_probe__';" >/dev/null 2>&1
v=$(num "select count(*) from public.vocab_history_logs where student_id='__rls_probe__'")
[ "$v" = "0" ] && ck "探针行的清理口子还在（验收脚本靠它）" 1 "剩 $v 行" \
                || ck "探针行的清理口子还在（验收脚本靠它）" 0 "剩 $v 行"

# 老师侧（authenticated）必须没被削
run_sql "set role authenticated; delete from public.vocab_history_logs where class_key='JR1';" >/dev/null 2>&1
v=$(num "select count(*) from public.vocab_history_logs where class_key='JR1'")
[ "$v" = "0" ] && ck "老师侧（authenticated）删 JR1 仍然能删 ⇒ 老师权限没被削" 1 "JR1 剩 $v 行" \
                || ck "老师侧（authenticated）删 JR1 仍然能删 ⇒ 老师权限没被削" 0 "JR1 剩 $v 行"

echo
echo "================ 结果 ================"
echo "通过 $PASSES 条，失败 $FAILS 条"
[ "$FAILS" = "0" ] && echo "🟢 全绿" || echo "🔴 有失败"
exit $FAILS
