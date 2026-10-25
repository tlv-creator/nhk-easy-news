#!/usr/bin/env bash
# 🔴 v6 的 test_apk_check_api.py 零参数会 exit 2（源码里 `if not jars: return 2`）
#    → 所谓"零参数可跑"其实不成立。peer 不想为此重发工具站（会出现两个内容不同的站），
#    所以我本地包一层：自动去 jars/ 找 jar，把参数补上。
#
# 用法：
#   bash selftest.sh                     # 用内置 guard_sample.apk + jars/ 下的 jar
#   bash selftest.sh <某个.apk>          # 用我自己的真实 APK 当样本（推荐）
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JARS=""
for lvl in 24 26 29 34; do
  j="$HERE/jars/android-$lvl.jar"
  [ -f "$j" ] && JARS="$JARS --jars $lvl=$j"
done
if [ -z "$JARS" ]; then
  echo "[FAIL] jars/ 下没有 android-*.jar → 先跑：python3 fetch_android_jars.py --out $HERE/jars --verify" >&2
  exit 1
fi
APK="${1:-}"
python3 "$HERE/test_apk_check_api.py" $APK $JARS
