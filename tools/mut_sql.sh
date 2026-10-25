#!/bin/bash
# 反向验证：把收尾 SQL 改坏 ⇒ 对应判据必须红
# 🔴 补丁用**正则**而不是字面量（SQL 的注释文案一改，字面量锚点就失效 ⇒ 上次就是这么静默失效的）
LISTEN_HOME="${LISTEN_HOME:-/workspace/listen-log}"
cd "$LISTEN_HOME"
# 🔴 备份**必须放工作区**：/tmp 会被沙箱回收 ⇒ 还原时备份没了 ⇒
#    「改坏的版本」留在磁盘上，还原失败还会**污染后面的用例**（我今天就这么把 SQL 带偏了）
MUTDIR="$LISTEN_HOME/build/.mut"
mkdir -p "$MUTDIR"
run_mut () {
  name="$1"; mode="$2"; expect="${3:-}"
  # 🔴 先**从基底重新生成**再改：还原不依赖备份文件（备份会丢、还原会失败）
  if ! python3 build/make_lock_sql.py >/dev/null; then echo "  [$name] ❌ 重新生成失败"; return 9; fi
  cp deploy/sql/05_lock_anon_delete.sql "$MUTDIR/orig.sql" || { echo "  [$name] ❌ 备份失败"; return 9; }
  python3 - "$mode" <<'PY'
import sys, re, os
mode = sys.argv[1]
LISTEN_HOME = os.environ.get('LISTEN_HOME', '/workspace/listen-log')
p = os.environ.get('LISTEN_LOCK_SQL', LISTEN_HOME + '/deploy/sql/05_lock_anon_delete.sql')
s = open(p, encoding='utf-8').read()
o = s

def swap_policy(new_using):
    global s
    s = re.sub(r"create policy p_anon_delete_logs.*?using \(.*?\n    \);",
               "create policy p_anon_delete_logs\n    on public.vocab_history_logs\n    for delete to anon\n    using (" + new_using + ");",
               s, flags=re.S)

# 🔴 定位「哈希比对」那段（不写死文本 ⇒ 文本漂移不会让补丁失配）
_HASH_RE = re.compile(r"encode\(sha256\(convert_to\(.*?, 'utf8'\)\), 'hex'\)")

if mode == 'wide':              # 退回旧的宽松 policy
    swap_policy("class_key ~ '^[A-Z][A-Z0-9_]*(\\|u[0-9]+)?$'")
elif mode == 'probeonly':       # 只剩探针行（=「砍掉学生删除功能」那版）
    swap_policy("student_id = '__rls_probe__'")
elif mode == 'notoken':         # 去掉令牌条件
    s = "\n".join(l for l in s.split("\n") if 'x-row-token' not in l)
elif mode == 'nostudent':       # 去掉学号条件
    s = "\n".join(l for l in s.split("\n") if "and student_id = nullif(" not in l)
elif mode == 'plainhash':       # 把 sha256 比对换成**明文**比对（= 明文令牌落库的老做法）
    # 🔴 用**正则**定位（文本形式会随加固改动漂移 ⇒ 补丁失配 ⇒ 误判成「验证无效」）
    s = _HASH_RE.sub(lambda _m: "nullif(nullif(current_setting('request.headers', true), '')::json->>'x-row-token', '')", s, count=1)
elif mode == 'loosetoken':      # 🔴 令牌**两处一起**放宽：正则放开 + 哈希比对换成通配符
    # ⚠ 只放宽正则**没有语义**了（sha256 比对仍然精确 ⇒ 攻击照样失败）⇒
    #   那样这条 mutation 就是摆设（会全绿）⇒ 必须改成「两处一起放宽」才是真实的攻击面
    s = s.replace("~ '^[A-Za-z0-9]{16,64}$'", "~ '^.*$'")
    s = _HASH_RE.sub(lambda _m: "'%'", s, count=1)
elif mode == 'nodrop':          # DO 块漏删旧的宽松 policy
    s = s.replace("and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )",
                  "and ( 'anon' = any(roles) and roles = ARRAY['public']::name[] )")
elif mode == 'noroles':         # DO 块少了 roles 过滤 ⇒ 误删老师的 FOR DELETE 形状
    s = s.replace("       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )", "")
elif mode == 'teachdrop':       # DO 块同时删 ALL **且** 没了 roles 过滤
    # ⚠ 只放宽 cmd 其实是**语义等价**的（roles 过滤已经挡住了老师的 policy）⇒ 判据会全绿
    #   ⇒ 这种 mutation 证明不了任何东西 ⇒ 必须连 roles 过滤一起去掉
    s = s.replace("       and cmd         = 'DELETE'", "       and cmd in ('DELETE','ALL')")
    s = s.replace("       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )", "")
else:
    raise SystemExit('unknown mode')

if s == o:
    raise SystemExit('PATCH_MISS')
open(p, 'w', encoding='utf-8').write(s)
PY
  if [ $? -ne 0 ]; then echo "  [$name] ❌ 补丁没打上 ⇒ 反向验证无效"; python3 build/make_lock_sql.py >/dev/null; return 9; fi
  if cmp -s "$MUTDIR/orig.sql" deploy/sql/05_lock_anon_delete.sql; then echo "  [$name] ❌ 文件没变化 ⇒ 无效"; return 9; fi
  timeout 300 bash build/pg_lock_harness.sh > /tmp/mut.log 2>&1; rc=$?
  red=$(grep -c "❌" /tmp/mut.log)
  python3 build/make_lock_sql.py >/dev/null || { echo "  [$name] ❌ 还原失败（重新生成失败）"; return 9; }
  # 🔴 guard 必须查**期望红的那条断言**：只查「有没有红」会把
  #    「环境失败（initdb 起不来）」误判成「反向验证成立」（今天就发生过）
  if [ -n "$expect" ]; then
    if ! grep -q "$expect" /tmp/mut.log; then
      echo "  [$name] ❌ 没看到期望红的断言「$expect」⇒ 这次红是别的原因（环境/别的用例）⇒ 反向验证**无效**"
      grep "❌" /tmp/mut.log | head -3 | sed 's/^/       /'
      return 9
    fi
  fi
  if [ "$rc" -ne 0 ] && [ "$red" -gt 0 ]; then
    echo "  [$name] exit=$rc 红=$red  ✅ 反向验证成立"
    grep "❌" /tmp/mut.log | head -3 | sed 's/^/       /'
  else
    echo "  [$name] exit=$rc 红=$red  ❌ 改坏后仍全绿 ⇒ 判据是摆设"
  fi
}
echo "== 反向验证（收尾 SQL · 令牌版）=="
# 收尾：无论中途怎样，退出前把文件恢复成「从基底生成的正式版」
trap 'python3 build/make_lock_sql.py >/dev/null 2>&1' EXIT
run_mut "A 退回旧的宽松 policy"        wide        "无头删 LISTEN"
run_mut "B DO 块漏删旧的宽松 policy"    nodrop      "无头删 LISTEN"
run_mut "C 只剩探针行（砍功能那版）"    probeonly   "带对令牌"
run_mut "D DO 块同时删 ALL"              teachdrop   "p_teach_"
run_mut "E DO 块少了 roles 过滤"        noroles     "p_teach_"
run_mut "F 去掉令牌条件"                notoken     "只带学号"
run_mut "G 去掉学号条件"                nostudent   "令牌对但学号错"
run_mut "H 令牌两处一起放宽（正则+哈希）"  loosetoken  "通配符"
run_mut "I sha256 比对改成明文比对"      plainhash   "带对令牌"
