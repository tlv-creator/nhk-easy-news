#!/usr/bin/env bash
# =====================================================================
# 第①步 tighten 的「读路径」独立验证（2026-10-25）
#
#   要验的问题：
#     收紧 SQL（tools/tighten_rls_delete_short.sql）只重建 **DELETE** policy
#     ⇒ 但 ops 页底部那行「实时现查」= **anon 读** vocab_states 的名册
#     ⇒ 万一收紧顺手碰了 anon 的 SELECT policy ⇒ 那行会当场空掉，而老师会以为站坏了
#     ⇒ 原 tighten_regress 只验「能不能删」，**一条 SELECT 断言都没有**
#
#   本脚本在「真实库形状」上跑，跑前 / 跑后各取一次快照并逐项比对：
#     ① anon 视角读 LISTEN 行数
#     ② anon 视角读名册：行数 + state_val 长度 + JSON 数组人数
#     ③ anon 的 SELECT policy 集合（policy 名 | roles | qual）的 md5
#
#   两条硬保证（没有它们，上面三条就是「跑绿了也说明不了什么」）：
#     · **基线非空守卫**：任一基线为 0 ⇒ 本次判定无效（exit 9），
#       不许拿「0 == 0」当「没变」（空语句假绿）
#     · **变异体反向验证**：最后把 anon 的 SELECT policy 全删掉再测一次
#       ⇒ ①② 必须变（读路径真的断了）⇒ 证明这两条断言有鉴别力，不是恒绿
#
# 🔴 只在本地 PG 上跑，**不碰生产库**
# 🔴 退出码三态，别混：
#     0 = 断言全过
#     1 = 断言红了（判据失败）
#     9 = **环境没搭起来**（找不到 PG / 找不到 SQL / 建库失败 / 基线为 0）
#          ⇒ 本次验证**无效**，既不是「通过」也不是「失败」
#
# 路径必须可移植（别人只拿共享仓库就能跑）：
#   TIGHTEN_SQL     短版 SQL 路径（不设就按脚本自身位置自动找）
#   TIGHTEN_SETUP   夹具 SQL 路径（不设就按名字候选自动找）
#   TIGHTEN_PGROOT  临时 PG 目录（默认 /var/tmp ⇒ NAS 上 initdb 会慢到 >45s）
#   LISTEN_PGPORT   端口（默认 55455）
# =====================================================================
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# ---- 找短版 SQL：参数 → 环境变量 → 相对脚本的候选 ---------------------------
SQL_MAIN=""
for cand in "$@" "${TIGHTEN_SQL:-}" \
            "$HERE/tighten_rls_delete_short.sql" \
            "$HERE/../tools/tighten_rls_delete_short.sql"; do
  if [ -n "$cand" ] && [ -f "$cand" ]; then SQL_MAIN="$cand"; break; fi
done
[ -n "$SQL_MAIN" ] || { echo "❌ 找不到短版 SQL（设 TIGHTEN_SQL）⇒ 环境无效"; exit 9; }

# ---- 找夹具（真实库形状）：_fixed → _variant → 原版 -------------------------
SETUP=""
for cand in "${TIGHTEN_SETUP:-}" \
            "$HERE/tighten_regress_setup_fixed.sql" \
            "$HERE/tighten_regress_setup_variant.sql" \
            "$HERE/tighten_regress_setup.sql"; do
  if [ -n "$cand" ] && [ -f "$cand" ]; then SETUP="$cand"; break; fi
done
[ -n "$SETUP" ] || { echo "❌ 找不到夹具 SQL ⇒ 环境无效"; exit 9; }

PGROOT="${TIGHTEN_PGROOT:-/var/tmp/pgtighten_read}"
PGDATA="$PGROOT/data"
PORT="${LISTEN_PGPORT:-55455}"
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)"
TMPD="$PGROOT/t"

if [ -z "$PGBIN" ] || [ ! -x "$PGBIN/initdb" ]; then
  echo "❌ 找不到 PostgreSQL（$PGBIN）⇒ 环境无效"; exit 9
fi
if ! id postgres >/dev/null 2>&1; then
  echo "❌ 没有 postgres 系统用户 ⇒ 环境无效"; exit 9
fi

as_pg() { su postgres -c "$1"; }
q() {  # 把 SQL 写文件再跑（引号地狱的根治办法）→ 只输出结果
  local f="$TMPD/q.sql"
  printf '%s\n' "$1" > "$f"
  chown postgres:postgres "$f" 2>/dev/null || true
  as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -t -A -f $f" 2>&1
}
num() { q "$1" | tr -d '[:space:]'; }
anon_num() { num "set role anon; $1"; }

# anon 视角的读快照（每个量一条 ⇒ 缺一条就是空值，不会被当成 0）
S_LISTEN="select count(*) from public.vocab_history_logs where class_key='LISTEN'"
S_ROSTER_ROWS="select count(*) from public.vocab_states where class_key='__roster_v1'"
S_ROSTER_LEN="select coalesce(max(length(state_val)),0) from public.vocab_states where class_key='__roster_v1'"
S_ROSTER_PPL="select coalesce(max(jsonb_array_length(state_val::jsonb)),0) from public.vocab_states where class_key='__roster_v1'"
# anon 的 SELECT policy 集合指纹（policy 名 | roles | 判据）⇒ 收紧前后必须逐字相同
S_SEL_MD5="select md5(coalesce(string_agg(policyname||'|'||roles::text||'|'||coalesce(qual,''), ';' order by policyname),''))
            from pg_policies where schemaname='public' and cmd='SELECT' and 'anon'=any(roles)"
S_SEL_N="select count(*) from pg_policies where schemaname='public' and cmd='SELECT' and 'anon'=any(roles)"

snapshot() {   # 跑前 / 跑后各调一次
  SNAP_LISTEN=$(anon_num "$S_LISTEN")
  SNAP_ROSTER_ROWS=$(anon_num "$S_ROSTER_ROWS")
  SNAP_ROSTER_LEN=$(anon_num "$S_ROSTER_LEN")
  SNAP_ROSTER_PPL=$(anon_num "$S_ROSTER_PPL")
  SNAP_SEL_MD5=$(num "$S_SEL_MD5")
  SNAP_SEL_N=$(num "$S_SEL_N")
}

pass=0; fail=0
ok()  { echo "  ✅ $1"; pass=$((pass+1)); }
bad() { echo "  ❌ $1"; fail=$((fail+1)); }

echo "== 搭「真实库形状」=="
as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
rm -rf "$PGROOT"; mkdir -p "$PGROOT" "$TMPD"; chown -R postgres:postgres "$PGROOT"
as_pg "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 \
  || { echo "❌ initdb 失败 ⇒ 环境无效"; exit 9; }
as_pg "$PGBIN/pg_ctl -D $PGDATA -o \"-p $PORT -k $PGROOT -c listen_addresses=''\" -l $PGROOT/pg.log -w start" >/dev/null 2>&1 \
  || { echo "❌ 起库失败 ⇒ 环境无效"; tail -20 "$PGROOT/pg.log"; exit 9; }
as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d postgres -q -c 'drop database if exists rlstest' -c 'create database rlstest'" >/dev/null 2>&1 \
  || { echo "❌ 建库失败 ⇒ 环境无效"; exit 9; }

# 建库这一步**不许静默吞错**：之前有一版是 `>/dev/null 2>&1` ⇒ 文件名写错也不报错，
#   后面「断言」跑在一个根本没建起来的库上 ⇒ 结论全是假的
cp "$SETUP" "$TMPD/setup.sql"; chown postgres:postgres "$TMPD/setup.sql"
if ! as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $TMPD/setup.sql" > "$TMPD/setup.log" 2>&1; then
  echo "❌ 建表失败（$SETUP）⇒ 环境无效"; tail -20 "$TMPD/setup.log"; exit 9
fi
echo "  ✅ 夹具建好（$SETUP）"

echo "== 跑前基线（anon 视角）=="
snapshot
echo "  LISTEN行数=$SNAP_LISTEN 名册行数=$SNAP_ROSTER_ROWS 名册长度=$SNAP_ROSTER_LEN 人数=$SNAP_ROSTER_PPL anonSELECTpolicy数=$SNAP_SEL_N"
# 🔴 基线必须非空 ⇒ 否则「跑前 == 跑后」是 0 == 0 的**空语句假绿**
if [ "${SNAP_LISTEN:-0}" -lt 1 ] || [ "${SNAP_ROSTER_ROWS:-0}" -lt 1 ] \
   || [ "${SNAP_ROSTER_LEN:-0}" -lt 1 ] || [ "${SNAP_ROSTER_PPL:-0}" -lt 1 ] \
   || [ "${SNAP_SEL_N:-0}" -lt 1 ] || [ -z "${SNAP_SEL_MD5:-}" ]; then
  echo "❌ 基线为空/不对 ⇒ 本次验证**无效**（不产出结论）"
  as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
  exit 9
fi
echo "  ✅ 基线非空 ⇒ 下面的断言有意义"

echo "== 跑短版 tighten（$SQL_MAIN）=="
cp "$SQL_MAIN" "$TMPD/tighten.sql"; chown postgres:postgres "$TMPD/tighten.sql"
OUT=$(as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $TMPD/tighten.sql" 2>&1); rc=$?
if [ $rc -eq 0 ] && ! printf '%s' "$OUT" | grep -qi "^ERROR"; then
  ok "短版跑通（exit 0，无 SQL 错误）"
else
  bad "短版有错："; printf '%s\n' "$OUT" | grep -iE "^ERROR|error:" | head -5 | sed 's/^/       /'
fi

echo "== 断言（跑后 vs 跑前）=="
B_LISTEN="$SNAP_LISTEN"; B_ROWS="$SNAP_ROSTER_ROWS"; B_LEN="$SNAP_ROSTER_LEN"
B_PPL="$SNAP_ROSTER_PPL"; B_MD5="$SNAP_SEL_MD5"
snapshot
[ "$SNAP_LISTEN" = "$B_LISTEN" ] \
  && ok "① anon 读 LISTEN 行数不变（$SNAP_LISTEN）" \
  || bad "① anon 读 LISTEN 行数变了：$B_LISTEN → $SNAP_LISTEN"
if [ "$SNAP_ROSTER_ROWS" = "$B_ROWS" ] && [ "$SNAP_ROSTER_LEN" = "$B_LEN" ] && [ "$SNAP_ROSTER_PPL" = "$B_PPL" ]; then
  ok "② anon 读名册不变（行数=$SNAP_ROSTER_ROWS 长度=$SNAP_ROSTER_LEN 人数=$SNAP_ROSTER_PPL）"
else
  bad "② anon 读名册变了：($B_ROWS/$B_LEN/$B_PPL) → ($SNAP_ROSTER_ROWS/$SNAP_ROSTER_LEN/$SNAP_ROSTER_PPL)"
fi
[ -n "$SNAP_SEL_MD5" ] && [ "$SNAP_SEL_MD5" = "$B_MD5" ] \
  && ok "③ anon 的 SELECT policy 集合逐字未变（md5=$SNAP_SEL_MD5）" \
  || bad "③ anon 的 SELECT policy 集合变了：$B_MD5 → $SNAP_SEL_MD5"

echo "== 变异体反向验证（把 anon 的 SELECT policy 全删掉）=="
# 目的：证明上面两条断言**真的在测读路径**，不是恒绿
#   ⇒ 删完之后 ①② 必须变（读路径真的断了）⇒ 断言有鉴别力
# 🔴 SQL 走**文件**、错误**不吞**：上一版把 DO 块塞进 psql -c，
#    而 `su postgres -c "…"` 里的 $$ 会被内层 shell 展开成 PID ⇒ DO 块语法错
#    ⇒ 变异体根本没执行；而输出又被 >/dev/null 2>&1 吞掉
#    ⇒ 结果误报成「断言是恒绿」⇒ **造出来的对照组自己没生效，还敢拿来判别人**
cat > "$TMPD/mutate.sql" <<'MUTSQL'
do $$
declare r record;
begin
  for r in select policyname, tablename from pg_policies
           where schemaname='public' and cmd='SELECT' and 'anon'=any(roles)
  loop
    execute format('drop policy %I on public.%I', r.policyname, r.tablename);
  end loop;
end $$;
MUTSQL
chown postgres:postgres "$TMPD/mutate.sql"
MUT_OUT=$(as_pg "$PGBIN/psql -h $PGROOT -p $PORT -U postgres -d rlstest -v ON_ERROR_STOP=1 -q -f $TMPD/mutate.sql" 2>&1)
mut_rc=$?
if [ $mut_rc -ne 0 ]; then
  bad "变异体自己没执行成功（rc=$mut_rc）⇒ 本次反向验证**无效**，不是「断言没鉴别力」"
  printf '%s\n' "$MUT_OUT" | head -5 | sed 's/^/       /'
else
  # 🔴 先证明对照组自己造对了：anon 的 SELECT policy 必须真的变成 0 条
  mut_sel_n=$(num "$S_SEL_N")
  if [ "$mut_sel_n" != "0" ]; then
    bad "变异体没删掉 policy（还剩 $mut_sel_n 条）⇒ 反向验证无效"
  else
    snapshot
    if [ "$SNAP_LISTEN" != "$B_LISTEN" ] || [ "$SNAP_ROSTER_ROWS" != "$B_ROWS" ] \
       || [ "$SNAP_ROSTER_PPL" != "$B_PPL" ] || [ "$SNAP_SEL_MD5" != "$B_MD5" ]; then
      ok "变异体被抓住（anon 读路径确实断了：LISTEN $B_LISTEN→$SNAP_LISTEN 名册 $B_ROWS→$SNAP_ROSTER_ROWS）⇒ 断言有鉴别力"
    else
      bad "变异体被抓住前态没问题，但断言对「读路径已断」毫无反应 ⇒ 断言是恒绿"
    fi
  fi
fi

as_pg "$PGBIN/pg_ctl -D $PGDATA -m immediate stop" >/dev/null 2>&1 || true
echo
echo "== 结果：通过 $pass / 失败 $fail =="
[ "$fail" -eq 0 ] || exit 1
exit 0
