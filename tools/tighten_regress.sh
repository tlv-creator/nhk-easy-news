#!/usr/bin/env bash
# =====================================================================
# 收紧版 SQL 的回归测试（2026-10-24）
#
#   要验的问题：
#     tools/tighten_rls_delete.sql 的 DO 块把两张表上 **所有** cmd='DELETE' 的
#     policy 全删了 —— **包括老师侧 to authenticated 的 p_teach_all_*** ——
#     然后只重建一条 to anon, authenticated 但判据收窄
#     ⇒ 老师侧的既有边界被改了（违反「修 anon 不碰 authenticated」铁律）
#
#   为什么自己写而不用 pg_harness.sh：
#     harness 的建表三件套（表 / GRANT / **enable row level security**）缺一不可
#     ⇒ 我第一版手写时漏了 ENABLE ROW LEVEL SECURITY ⇒ 所有 policy 都是摆设
#     ⇒ 测出来的「删不掉/删得掉」全是假的（工具的 bug 直接变成我的结论）
# =====================================================================
set -uo pipefail
[ $# -ge 1 ] || { echo "用法: tighten_regress.sh <sql1> [sql2 ...]"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
PGROOT=/workspace/work/pgtest2
PGDATA="$PGROOT/data"
PORT=55433
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"

as_pg() { su postgres -c "$1"; }
psql_as_pg() { as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -v ON_ERROR_STOP=1 -q $*" ; }

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
for i in 1 2 3 4 5; do rm -rf "$PGROOT" 2>/dev/null && break; sleep 1; done
mkdir -p "$PGROOT"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1
psql_as_pg -d postgres >/dev/null 2>&1 -f "$HERE/t_sql_createdb.sql"

echo "════ ① 建表 + 授权 + 开 RLS + 老师侧既有边界 ════"
psql_as_pg -d rlstest2 -f "$HERE/tools/tighten_regress_sql.sql" >/dev/null 2>&1
echo "  ✅ 建好（表 / GRANT / ENABLE ROW LEVEL SECURITY / p_teach_all_*）"

n=2
for f in "$@"; do
  echo
  echo "════ 施加 SQL #$n: $f ════"
  if psql_as_pg -d rlstest2 -f "$f" > "$PGROOT/apply$n.log" 2>&1; then
    echo "  ✅ 执行通过"
  else
    echo "  ❌ 失败："; tail -20 "$PGROOT/apply$n.log"; exit 1
  fi
  n=$((n+1))
done

echo
echo "════ ④ 收紧后剩下的 DELETE policy ════"
psql_as_pg -d rlstest2 -f "$HERE/t_sql_policies.sql" 2>&1

echo

echo

echo

echo
echo "════ 全部断言跑完（② 老师侧能删 / ③ anon 删不掉 / ④ LISTEN 三条路径）════"
for f in t_sql_teacher.sql t_sql_anon.sql t_sql_paths.sql t_sql_policies.sql t_sql_debug.sql; do
  psql_as_pg -d rlstest2 -f "$HERE/$f" 2>&1 | grep -E "剩余|路径|影响行数|看得到|policy:|DELETE 权限" || true
done

as_pg "$PGBIN/pg_ctl -D $PGDATA -m fast stop" >/dev/null 2>&1
echo "（测试库已停）"
