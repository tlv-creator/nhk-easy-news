#!/usr/bin/env bash
# 🔴 Shun 回归跑批器
#
# 为什么要它（不是「方便」）：
#   ① **恒绿检测** —— 一个测试崩在前面 / 早退 / 什么都不做就 exit 0 → 跑批器必须判失败。
#      （同 hub 那边踩过的：「exit 0 但一条断言都没输出」= 假绿）
#   ② **断言条数对账** —— 每个套件必须打印「N 通过 / M 失败」，N=0 一律判失败。
#   ③ **不看退出码看汇总行** —— 崩溃退出也是 1，会造成假绿。
#   ④ **node 与 python 套件走同一套判定** —— 别让 python 套件享受豁免。
set -uo pipefail
cd "$(dirname "$0")/.."

JS_SUITES="test_shun_shell test_shun_ai test_ai_clean test_boot test_bgplay test_site_gone test_audio_retry test_gs_render test_gs_copy"
PY_SUITES="test_audio_gate test_gs_gate"
pass=0; fail=0; skip=0; failed=""; skipped=""

# $1=名字 $2=跑法(node|python) $3=文件
run_one() {
  local s="$1" how="$2" f="$3"
  if [ ! -f "$f" ]; then
    echo "  ❌ $s —— 脚本不存在"; fail=$((fail+1)); failed="$failed\n  $s（不存在）"; return
  fi
  local out rc line n_pass n_fail outfile
  outfile=$(mktemp /tmp/shun_suite_XXXXXX.txt)
  if [ "$how" = "python" ]; then
    timeout 900 python3 "$f" > "$outfile" 2>&1; rc=$?
  else
    timeout 900 node "$f" > "$outfile" 2>&1; rc=$?
  fi
  out=$(cat "$outfile")
  # 🔴🔴 **区分「断言红了」与「脚本崩了」**（2026-10-23 NHK 提，我采纳）：
  #   脚本崩了时**退出码也是 1** ⇒ 只看退出码会把「脚本自己炸了」当成「测试抓到了问题」
  #   ⇒ **方向搞反**：该修的是脚本，却当成产品有问题去查 ⇒ 白折腾
  if ! python3 scripts/strict_test_exit.py "$outfile" >/dev/null 2>&1; then
    echo "  ❌ $s —— **脚本崩了**（不是断言红了）"
    fail=$((fail+1)); failed="$failed\n  $s（脚本崩溃）"
    grep -nE "Traceback|Error:|Exception|node:internal" "$outfile" | head -4 | sed 's/^/       /'
    rm -f "$outfile"; return
  fi
  rm -f "$outfile"
  # 汇总行形如「101 通过 / 0 失败」或「═══ 闸门自检：6 通过 / 0 失败 ═══」
  line=$(printf '%s' "$out" | grep -oE '[0-9]+ 通过 / [0-9]+ 失败' | tail -1)
  n_pass=$(printf '%s' "$line" | grep -oE '^[0-9]+' | head -1)
  n_fail=$(printf '%s' "$line" | grep -oE '[0-9]+ 失败' | grep -oE '^[0-9]+' | head -1)
  if [ -z "$line" ]; then
    # 🔴 **超时 ≠ 崩了 ≠ 断言红了** —— 三者的处置完全不同：
    #   ① 超时（TimeoutError）：2 核机器刚跑完 38MB 构建时会发生 ⇒ **负载性假红**
    #      ⇒ **重试一次**（同「异步断言要有界预算 + 必要时一次重试」那条）
    #   ② 脚本崩了：strict_test_exit 认得出（上面已经判过了）
    #   ③ 早退/恒绿：没有任何输出
    if printf '%s' "$out" | grep -q 'TimeoutError'; then
      echo "  ⏳ $s —— 超时（负载性假红）⇒ 重试一次"
      sleep 5
      outfile=$(mktemp /tmp/shun_suite_XXXXXX.txt)
      if [ "$how" = "python" ]; then
        timeout 900 python3 "$f" > "$outfile" 2>&1; rc=$?
      else
        timeout 900 node "$f" > "$outfile" 2>&1; rc=$?
      fi
      out=$(cat "$outfile")
      if ! python3 scripts/strict_test_exit.py "$outfile" >/dev/null 2>&1; then
        echo "  ❌ $s —— **脚本崩了**（重试后）"
        fail=$((fail+1)); failed="$failed\n  $s（脚本崩溃）"
        grep -nE "Traceback|Error:|Exception|node:internal" "$outfile" | head -4 | sed 's/^/       /'
        rm -f "$outfile"; return
      fi
      rm -f "$outfile"
      line=$(printf '%s' "$out" | grep -oE '[0-9]+ 通过 / [0-9]+ 失败' | tail -1)
      n_pass=$(printf '%s' "$line" | grep -oE '^[0-9]+' | head -1)
      n_fail=$(printf '%s' "$line" | grep -oE '[0-9]+ 失败' | grep -oE '^[0-9]+' | head -1)
      if [ -z "$line" ]; then
        echo "  ❌ $s —— 重试后仍无汇总行（超时/早退）"; fail=$((fail+1)); failed="$failed\n  $s（无汇总行 rc=$rc）"
        printf '%s\n' "$out" | tail -3 | sed 's/^/       /'
        return
      fi
      if [ "${n_pass:-0}" -eq 0 ]; then
        echo "  ❌ $s —— 重试后仍是 0 条断言（恒绿）"; fail=$((fail+1)); failed="$failed\n  $s（0 断言）"; return
      fi
      if [ "$rc" -ne 0 ] || [ "${n_fail:-0}" -ne 0 ]; then
        echo "  ❌ $s —— $line（重试后 rc=$rc）"; fail=$((fail+1)); failed="$failed\n  $s（$line）"
        printf '%s\n' "$out" | grep -E "❌" | head -4 | sed 's/^/       /'
        return
      fi
      echo "  ✅ $s —— $line（重试后通过）"; pass=$((pass+1))
      sleep 3
      return
    fi
    echo "  ❌ $s —— 没有汇总行（崩了/早退/恒绿）"; fail=$((fail+1)); failed="$failed\n  $s（无汇总行 rc=$rc）"
    printf '%s\n' "$out" | tail -3 | sed 's/^/       /'
    return
  fi
  if [ "${n_pass:-0}" -eq 0 ]; then
    echo "  ❌ $s —— 0 条断言（恒绿）"; fail=$((fail+1)); failed="$failed\n  $s（0 断言）"; return
  fi
  if [ "$rc" -ne 0 ] || [ "${n_fail:-0}" -ne 0 ]; then
    echo "  ❌ $s —— $line（rc=$rc）"; fail=$((fail+1)); failed="$failed\n  $s（$line）"
    printf '%s\n' "$out" | grep -E "❌" | head -4 | sed 's/^/       /'
    return
  fi
  echo "  ✅ $s —— $line"; pass=$((pass+1))
  # 🔴 套件之间留 3 秒：上一套的 Chrome 进程可能还没退干净（2 核机器上会拖累下一套的音频解码 → 假红）
  sleep 3
}

# 🔴 **缺依赖的套件要「跳过」而不是「失败」**：
#   周更 agent 的沙箱里**没有** audio/（音频在音频站，仓库里不放音频）
#   ⇒ test_audio_retry 需要的本地音频样本不存在 ⇒ 跑必然红 ⇒ 那是环境问题不是产品问题。
#   ⇒ 探测到就跳过并**打印原因**（不许静默跳过：跳过必须有理由）。
need_audio() {
  [ -f "audio/ep01.m4a" ] || return 0
  return 1
}
for s in $JS_SUITES; do
  if [ "$s" = "test_audio_retry" ] && need_audio; then
    echo "  ⏭  $s —— **跳过**（缺 audio/ep01.m4a：周更沙箱里没有音频样本）"
    skip=$((skip+1)); skipped="$skipped\n  $s（缺 audio/ep01.m4a）"
    continue
  fi
  run_one "$s" node "scripts/$s.js"
done
for s in $PY_SUITES; do run_one "$s" python "scripts/$s.py"; done

echo
echo "═══ 套件 $pass 通过 / $fail 失败 / $skip 跳过 ═══"
if [ "$skip" -gt 0 ]; then echo -e "跳过项：$skipped"; fi
if [ "$fail" -ne 0 ]; then echo -e "失败项：$failed"; exit 1; fi
exit 0
