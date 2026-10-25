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
# 🔴 路径可移植：原来写死 /workspace/work/pgtest2 ⇒ 别人从仓库跑会写到不存在的目录
PGROOT="${TIGHTEN_PGROOT:-/var/tmp/pgtighten_regress}"
PGDATA="$PGROOT/data"
PORT="${LISTEN_PGPORT:-55433}"
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"

# ---- 在「仓库布局」和「工程布局」两种摆放方式里找文件 ------------------------
#   仓库里这些 .sh 放在 tools/ 下 ⇒ 原来写死的 "$HERE/tools/xxx" 会变成 tools/tools/xxx
#   ⇒ 从仓库跑必然断；而且 setup 那步是 `>/dev/null 2>&1` ⇒ **静默吞错** ⇒ 后面全是假结论
find_repo_file() {
  local name="$1" d
  for d in "$HERE" "$HERE/tools" "$HERE/../tools" "$HERE/.."; do
    if [ -f "$d/$name" ]; then echo "$d/$name"; return 0; fi
  done
  return 1
}
# ---- 夹具按名字候选找（仓库里出现过 _fixed / _variant 两个名字）----------------
SETUP=""
for name in tighten_regress_setup_fixed.sql tighten_regress_setup_variant.sql tighten_regress_setup.sql; do
  if SETUP="$(find_repo_file "$name")"; then break; fi
  SETUP=""
done
[ -n "$SETUP" ] || { echo "❌ 找不到夹具 SQL ⇒ 环境没搭起来 ⇒ 本次验证无效"; exit 9; }

as_pg() { su postgres -c "$1"; }
psql_as_pg() { as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -v ON_ERROR_STOP=1 -q $*" ; }

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
for i in 1 2 3 4 5; do rm -rf "$PGROOT" 2>/dev/null && break; sleep 1; done
mkdir -p "$PGROOT"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 \
  || { echo "❌ initdb 失败 ⇒ 环境无效"; exit 9; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败 ⇒ 环境无效"; tail -20 "$PGROOT/pg.log"; exit 9; }
# 🔴 建库不许静默吞错：原来用 -f "$HERE/t_sql_createdb.sql"，那个文件在共享仓库里根本不存在
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
  -c 'drop database if exists rlstest2' -c 'create database rlstest2'" >/dev/null 2>&1 \
  || { echo "❌ 建库失败 ⇒ 环境无效"; exit 9; }

echo "════ ① 建表 + 授权 + 开 RLS + 老师侧既有边界 ════"
if ! psql_as_pg -d rlstest2 -f "$SETUP" > "$PGROOT/setup.log" 2>&1; then
  echo "❌ 建表失败（$SETUP）⇒ 环境没搭起来 ⇒ 本次验证无效"; tail -20 "$PGROOT/setup.log"; exit 9
fi
echo "  ✅ 建好（表 / GRANT / ENABLE ROW LEVEL SECURITY / p_teach_all_*）← $SETUP"

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
# 🔴 不再依赖仓库里不存在的 t_sql_policies.sql ⇒ 内联（内容与它逐字相同）
cat > "$PGROOT/policies.sql" <<'POLSQL'
-- 列出收紧后剩下的 DELETE / ALL policy（看 roles 和判据）
select tablename, policyname, cmd, roles::text as roles, left(coalesce(qual,'(null)'),70) as qual
  from pg_policies
 where schemaname='public' and cmd in ('ALL','DELETE')
   and tablename in ('vocab_history_logs','vocab_online_status','vocab_submissions')
 order by tablename, cmd, policyname;
POLSQL
chown postgres:postgres "$PGROOT/policies.sql"
psql_as_pg -d rlstest2 -f "$PGROOT/policies.sql" 2>&1

echo

echo

echo

echo
echo "════ 全部断言跑完（② 老师侧能删 / ③ anon 删不掉 / ④ LISTEN 三条路径）════"
TESTS="$(find_repo_file tighten_regress_tests.sql)" || { echo "❌ 找不到断言脚本 ⇒ 环境无效"; exit 9; }
psql_as_pg -d rlstest2 -f "$TESTS" 2>&1 \
  | grep -E "^===|剩余|路径|影响行数|看得到|policy:|DELETE 权限" || true

as_pg "$PGBIN/pg_ctl -D $PGDATA -m fast stop" >/dev/null 2>&1
echo "（测试库已停）"
