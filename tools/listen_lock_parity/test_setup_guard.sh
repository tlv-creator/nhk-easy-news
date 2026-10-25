#!/usr/bin/env bash
# =====================================================================
# setup.sql 的**闸门**自检 —— 不是靠注释，是靠真的会拒绝执行
#
#   背景：tools/listen_lock_parity/setup.sql 第一句 SQL 是
#         drop table if exists public.vocab_history_logs,
#                                public.vocab_online_status, public.vocab_states;
#         ⇒ 这三个表是**词汇站 + 四级站 + 听力站共用的**
#         ⇒ 谁要是把它连到生产库跑一次，学生记录直接清零
#
#   闸门原理（⚠️ 不要用「库名白名单」！各家本地库名不一样 ⇒ 白名单必误伤）：
#     只允许 **unix socket** 连接（= 本地库）。
#     判据：unix socket 下 inet_server_addr() = NULL；走 TCP 时 = 该连接的服务端地址。
#     ⇒ 任何远程 TCP 连接（= 生产库）一律拒绝。
#
#   本脚本验四件事：
#     ① 闸门与 ON_ERROR_STOP 必须在文件里（缺任一个 ⇒ 闸门等于装饰）
#     ② unix socket 连接 ⇒ 放行，且**完整跑完**（表建好 + policy 建好 + 零报错）
#     ③ TCP 连接 ⇒ **拒绝执行**，且**三张表和里面的数据一个都不能少**
#     ④ 角色必须先建（否则 grant 报错 ⇒ 脚本中途死掉 ⇒ policy 没建全）
#
#   用法：bash test_setup_guard.sh [setup.sql 路径]
#   ⚠️ 需要本地 postgres（apt install postgresql）
#   ⚠️ 用 /var/tmp（overlay 盘）⇒ NAS 上 initdb 慢到 >45s，会被误报成环境失败
# =====================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SETUP="${1:-$HERE/setup.sql}"
PGROOT="${PGROOT:-/var/tmp/pg_setup_guard}"
PGDATA="$PGROOT/data"
PORT="${PGPORT_TEST:-55499}"
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"
TMPD="$PGROOT/t"
PASS=0; FAIL=0

ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; [ -n "${2:-}" ] && echo "     $2"; }

as_pg() { su postgres -c "$1"; }

# 跑 SQL：$1=连接方式(socket|tcp) $2=库名 $3=sql  ⇒ 只取纯数字行
q() {
  local how="$1" db="$2" sql="$3" f="$TMPD/q.sql" host
  case "$how" in
    socket) host="-h $PGROOT" ;;
    tcp)    host="-h 127.0.0.1" ;;
    *)      host="-h $PGROOT" ;;
  esac
  printf '%s\n' "$sql" > "$f"
  chown postgres:postgres "$f" 2>/dev/null || true
  as_pg "$PGBIN/psql $host -p $PORT -U postgres -d $db -v ON_ERROR_STOP=1 -q -t -A -f $f" 2>&1
}
# 只要一个数字（🔴 psql 报错文本里也有数字 ⇒ 必须严格取整行，别 tr -dc '0-9'）
qnum() { q "$1" "$2" "$3" | grep -E '^-?[0-9]+$' | head -1; }

# 摆一个「像生产库」的库：三张表 + 一行标记数据
make_fake_prod() {  # $1=db
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -q -c 'drop database if exists $1' -c 'create database $1'" >/dev/null
  q socket "$1" "create table public.vocab_history_logs (id serial primary key, class_key text, marker text);
                 create table public.vocab_online_status (id serial primary key, class_key text);
                 create table public.vocab_states (class_key text primary key, marker text);
                 insert into public.vocab_history_logs (class_key, marker) values ('LISTEN','REAL_DATA_别删我');" >/dev/null
}
# 打印「三张表还在几张 : vocab_history_logs 还剩几行」
tables_left() {  # $1=how $2=db
  local t m
  t=$(qnum "$1" "$2" "select count(*) from information_schema.tables where table_schema='public' and table_name in ('vocab_history_logs','vocab_online_status','vocab_states');")
  m=$(qnum "$1" "$2" "select case when to_regclass('public.vocab_history_logs') is null then -1 else (select count(*) from public.vocab_history_logs) end;")
  echo "${t:-?}:${m:-?}"
}

echo "═══ setup.sql 闸门自检 ═══"
echo "· 被测文件：$SETUP"
[ -f "$SETUP" ] || { echo "❌ 找不到 $SETUP"; exit 9; }
[ -x "$PGBIN/initdb" ] || { echo "❌ 没装 postgres（apt install postgresql）"; exit 9; }

# ---- ① 闸门本体必须在（两个都要：判据 + ON_ERROR_STOP）
if grep -q "inet_server_addr()" "$SETUP"; then ok "① 有基于 inet_server_addr() 的连接闸门";
else bad "① 没找到连接闸门" "文件里搜不到 inet_server_addr()"; fi
if grep -qE '^\\set +ON_ERROR_STOP +on' "$SETUP"; then ok "① 有 \\set ON_ERROR_STOP on（否则报错后会继续往下跑 ⇒ 闸门等于装饰）";
else bad "① 缺 \\set ON_ERROR_STOP on" "没有它，raise exception 之后 psql 会继续执行后面的 drop table"; fi
if grep -q "create role anon" "$SETUP"; then ok "① 会先建 anon/authenticated/service_role 角色（否则 grant 报错 ⇒ 脚本中途死掉）";
else bad "① 没建角色" "全新本地库里没有这三个角色 ⇒ grant 那句会报错"; fi

# ---- 起本地库（socket + TCP 都要能连）
as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
if [ -f "$PGDATA/postmaster.pid" ]; then
  _pid=$(head -1 "$PGDATA/postmaster.pid" 2>/dev/null | tr -dc '0-9')
  [ -n "$_pid" ] && kill -9 "$_pid" >/dev/null 2>&1
fi
rm -rf "$PGROOT"; mkdir -p "$PGROOT" "$TMPD"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 || { echo "❌ initdb 失败（环境无效）"; exit 9; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses='127.0.0.1'\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败（环境无效）"; tail -20 "$PGROOT/pg.log"; exit 9; }
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -q -c 'drop database if exists rlstest' -c 'create database rlstest'" >/dev/null

# ---- ② unix socket（= 本地库）⇒ 放行，且完整跑完
out=$(q socket rlstest "$(cat "$SETUP")")
n=$(qnum socket rlstest "select count(*) from information_schema.tables where table_schema='public' and table_name in ('vocab_history_logs','vocab_online_status','vocab_states');")
pol=$(qnum socket rlstest "select count(*) from pg_policies where schemaname='public';")
if [ "${n:-0}" = "3" ]; then ok "② socket 连接 ⇒ 放行，三张表都建出来了（3/3）";
else bad "② socket 连接 ⇒ 放行但表不对" "表数=${n:-0}/3；输出：$(echo "$out" | head -3)"; fi
if [ "${pol:-0}" -ge 3 ] 2>/dev/null; then ok "② 脚本**完整跑完**（policy 建了 ${pol} 条 ⇒ 不是中途死的）";
else bad "② 脚本没跑完" "policy 只有 ${pol:-0} 条 ⇒ 中途报错停了"; fi
if echo "$out" | grep -qi "error"; then bad "② 放行时有别的报错" "$(echo "$out" | grep -i error | head -2)";
else ok "② 放行时零报错"; fi

# ---- ③ TCP 连接（= 远程/生产）⇒ 必须拒绝，且表和数据一个都不能少
make_fake_prod prodsim
out=$(q tcp prodsim "$(cat "$SETUP")")
if echo "$out" | grep -qE "只能在本地|拒绝|inet_server_addr|unix socket"; then ok "③ TCP 连接 ⇒ 被拒（闸门生效）";
else bad "③ TCP 连接没被拦住！" "$(echo "$out" | head -3)"; fi
left=$(tables_left tcp prodsim)
if [ "$left" = "3:1" ]; then ok "③ 拒绝后：三张表还在 + 那行数据还在（3 表 / 1 行）";
else bad "③ 拒绝后数据被动了！" "现在剩 $left（期望 3:1）"; fi

# ---- 收尾
as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
echo
if [ "$FAIL" -eq 0 ]; then echo "🟢 全绿 $PASS 条"; exit 0; else echo "🔴 红 $FAIL 条（通过 $PASS）"; exit 1; fi
