#!/usr/bin/env bash
# 打包 50 集网络版 APK 交付物：**只含 APK**，文件名全 ASCII
#
#   output/shun.apk.zip  →  内含 shun.apk
#
# 🔴 纪律（上一版踩过的坑）：
#   打包完**必须**把 ZIP 里的 APK 解出来跟源 APK 逐字 cmp
#   —— 第一版就是「zip 里装的是旧 APK」而日志一切正常，靠 cmp 才抓住
set -euo pipefail

WS="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$WS/pkg/shun-50ep-net-v1.3.0.apk"
OUT="$WS/output"
TMPD="$WS/pkg/.pack"

[ -f "$SRC" ] || { echo "❌ 没有源 APK: $SRC"; exit 1; }

mkdir -p "$OUT" "$TMPD"
rm -f "$OUT/shun.apk" "$OUT/shun.apk.zip"

# ---- 1) 复制（不是软链！软链会跟着改）
cp "$SRC" "$OUT/shun.apk"
cp "$SRC" "$TMPD/shun.apk"

# ---- 2) 打包（-j 压平目录，文件名 ASCII）
( cd "$TMPD" && zip -q -j "$OUT/shun.apk.zip" shun.apk )

# ---- 3) 逐字对账：ZIP 里的 APK == 源 APK
rm -rf "$TMPD/x"; mkdir -p "$TMPD/x"
( cd "$TMPD/x" && unzip -q -o "$OUT/shun.apk.zip" )
cmp "$TMPD/x/shun.apk" "$SRC" || { echo "❌ ZIP 里的 APK 与源 APK 不一致（装错文件了）"; exit 1; }
cmp "$TMPD/x/shun.apk" "$OUT/shun.apk" || { echo "❌ ZIP 里的 APK 与 output/shun.apk 不一致"; exit 1; }
echo "✅ 逐字对账通过：ZIP 内 APK == 源 APK"

# ---- 4) 体检
unzip -t "$OUT/shun.apk.zip" > /dev/null && echo "✅ ZIP 完整"
if unzip -l "$OUT/shun.apk.zip" | grep -qiE "\.jks|keystore|\.py$|\.js$|\.java$|scripts/|assets/"; then
  echo "❌ 交付包里混进了不该有的东西（keystore / 源码 / assets）"; exit 1
fi
echo "✅ 没有 keystore / 源码 / 内置资源"

# ---- 5) 文件名必须 ASCII
if unzip -l "$OUT/shun.apk.zip" | grep -P '[^\x00-\x7F]'; then
  echo "❌ ZIP 内有非 ASCII 文件名"; exit 1
fi
echo "✅ ZIP 内文件名全 ASCII"

# ---- 6) 报告
echo
echo "── 交付物 ──"
ls -l "$OUT/shun.apk.zip" "$OUT/shun.apk" | awk '{print $5, $9}'
md5sum "$OUT/shun.apk.zip" "$OUT/shun.apk" "$SRC"
unzip -l "$OUT/shun.apk.zip"

rm -rf "$TMPD"
echo
echo "✅ 打包完成：$OUT/shun.apk.zip"
