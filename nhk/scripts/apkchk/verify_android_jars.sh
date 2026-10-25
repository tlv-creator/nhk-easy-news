#!/bin/bash
# 官方 android.jar 的 sha256 校验（2026-10-22）
# ---------------------------------------------------------------------------
# 为什么必须有这个
# ---------------------------------------------------------------------------
# android.jar 是 **API 等级闸门的判据来源** —— 检查器靠它判断「这个方法是不是
# 引用了 minSdk 之外才有的 API」。
# ⇒ jar 坏了（下载不全 / 被 CDN 截断 / 放了个旧版），检查器照样 exit 0，
#   但**结论整个是错的**（会说「没有违规 API」）。
#   **这比没检查更坏** —— 没检查你知道没查，坏 jar 让你以为查过了。
#
# 所以：jar 的 sha256 必须**固化**下来（不是「拉的时候顺便算一下」——
#   那样第一次算出来的就是坏的，也照样放行）。
#
# 两道验证：
#   1) sha256 对上固化值          → 文件没被截断/替换
#   2) jar 结构自检（magic + 类可解析）→ 文件本身是合法 jar
set -e
cd "$(dirname "$0")"
JARS=jars
SHA_FILE="$JARS/jars.sha256"

if [ ! -f "$SHA_FILE" ]; then
  echo "[jars] ❌ 缺少固化值文件 $SHA_FILE —— 闸门无法判真伪，拒绝放行"
  echo "       （先跑一次 fetch_android_jars.sh 生成并人工确认）"
  exit 1
fi

fail=0
for api in 24 26 29; do
  jar="$JARS/android-$api.jar"
  if [ ! -f "$jar" ]; then
    echo "[jars] ❌ 缺 android-$api.jar —— 拒绝放行（不许「跳过 + 放行」）"
    fail=1
    continue
  fi
  # ---- 1) sha256 ----
  want=$(grep -E "android-$api\.jar$" "$SHA_FILE" | awk '{print $1}')
  got=$(sha256sum "$jar" | awk '{print $1}')
  if [ -z "$want" ]; then
    echo "[jars] ❌ $SHA_FILE 里没有 android-$api.jar 的固化 sha"
    fail=1
    continue
  fi
  if [ "$want" != "$got" ]; then
    echo "[jars] ❌ android-$api.jar sha256 不符"
    echo "        期望 $want"
    echo "        实际 $got"
    fail=1
    continue
  fi
  # ---- 2) 结构自检：jar 本身能被解析 ----
  if ! python3 "$JARS/../check_jar_valid.py" "$jar" >/dev/null 2>&1; then
    echo "[jars] ❌ android-$api.jar 结构自检失败（不是合法 jar / 类不可解析）"
    fail=1
    continue
  fi
  echo "[jars] ✓ android-$api.jar  sha256 + 结构自检 均通过  ($(stat -c%s "$jar") bytes)"
done

if [ "$fail" != "0" ]; then
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "❌ [API 闸门] android.jar 不可信 —— 拒绝出包"
  echo "   jar 是闸门的判据来源，坏 jar 会让检查器给出**错误结论**"
  echo "   修法：删掉 jars/ 重新跑 scripts/apkchk/fetch_android_jars.sh"
  echo "════════════════════════════════════════════════════════════"
  exit 1
fi
exit 0
