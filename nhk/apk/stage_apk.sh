#!/usr/bin/env bash
# stage_apk.sh —— 把 APK 放进**待上线区** + 版本号核对 + 三份 md5 对账
#
# 🔴 **stage 必须在部署之前** —— 否则新 APK 根本没进这次部署。
# 🔴 stage 完还要清掉 site/app 里的版本化 APK 副本（site/app 会被整份部署，
#    里面留一份带版本号的 apk 是多余的，还会让人下错）。
set -uo pipefail

ROOT=/workspace/nhk
OUT=$ROOT/apk/out
DEPLOY=$ROOT/deploy_site
APK_NAME=NHK.apk

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

[ -f "$OUT/signed.apk" ] || die "没有 $OUT/signed.apk（先 bash apk/build.sh）"
mkdir -p "$DEPLOY"

say "① 放进待上线区"
cp -f "$OUT/signed.apk" "$DEPLOY/$APK_NAME"
ls -la "$DEPLOY/$APK_NAME"

say "② versionCode 核对（🔴 引导页的 apkVer 必须等于它）"
AAPT2=$(command -v aapt2 || echo "${ANDROID_HOME:-/opt/android-sdk}/build-tools/34.0.0/aapt2")
if [ -x "$AAPT2" ]; then
  VC=$("$AAPT2" dump badging "$DEPLOY/$APK_NAME" 2>/dev/null | sed -n "s/.*versionCode='\([0-9]*\)'.*/\1/p" | head -1)
else
  VC=$(python3 - <<'PY'
import re,zipfile,struct
# 没 aapt2 ⇒ 退化成读 manifest（AndroidManifest.xml 是二进制 XML，解析不了就空）
print("")
PY
)
fi
[ -n "$VC" ] || warn "读不到 versionCode（没有 aapt2）⇒ 请人工核对"
if [ -n "$VC" ]; then
  echo "  APK versionCode = $VC"
  echo "  👉 推引导页时 --apk-ver $VC"
fi

say "③ 三份 md5 对账（构建产物 / 待上线区 / 线上站）"
M1=$(md5sum "$OUT/signed.apk" | awk '{print $1}')
M2=$(md5sum "$DEPLOY/$APK_NAME" | awk '{print $1}')
echo "  构建产物   $M1"
echo "  待上线区   $M2"
[ "$M1" = "$M2" ] || die "md5 对不上 ⇒ 拷贝坏了"
CUR=$(curl -s -m 20 -H 'Cache-Control: no-cache' \
      https://tlv-creator.github.io/nhk-easy-news/version.json \
      | python3 -c "import json,sys; print((json.load(sys.stdin).get('site') or '').rstrip('/'))" 2>/dev/null)
if [ -n "$CUR" ]; then
  curl -s -m 300 -o "$ROOT/apk/out/online.apk" "$CUR/$APK_NAME" 2>/dev/null || true
  if [ -f "$ROOT/apk/out/online.apk" ]; then
    M3=$(md5sum "$ROOT/apk/out/online.apk" | awk '{print $1}')
    echo "  线上现役   $M3"
    [ "$M1" = "$M3" ] && echo "  ✅ 三份一致" || echo "  ℹ️ 与线上现役不同（说明这次确实换了 APK）"
  fi
fi

say "④ 清掉 site/app 里的版本化 APK 副本"
find "$ROOT/site/app" -maxdepth 2 -name "*.apk" -print -delete 2>/dev/null | sed 's/^/  删了 /'
ls "$ROOT/site/app"/*.apk 2>/dev/null || echo "  site/app 里没有 APK 残留 ✅"

say "✅ stage 完成。接着：bash $ROOT/work/deploy_nhk.sh prepare"
