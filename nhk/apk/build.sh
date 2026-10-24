#!/usr/bin/env bash
# apk/build.sh —— 打 APK（**只有原生层代码改了才跑**；内容更新不用重编）
#
# 🔴 SITE_URL 必须留空 ⇒ build.sh 取 sites 登记表里的**冻结兜底站**
#    （引导页读不到时 App 落它 ⇒ 这个域名不能变，变了 App 白屏）
# 🔴 **绝对不要传 PLACEHOLDER 或当前内容站** —— 闸门会 exit 1 拒绝（这是故意的）
# 🔴 签名口令**不在源码里**（外置到 /workspace/.secrets/nhk_ks_pass）
set -uo pipefail

ROOT=/workspace/nhk
APK_DIR=$ROOT/apk
OUT=$APK_DIR/out
KS_PASS_FILE=/workspace/.secrets/nhk_ks_pass
SITES_REG=$ROOT/data/sites.json

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

say "① 读冻结兜底站"
[ -f "$SITES_REG" ] || die "缺 $SITES_REG（先 cleanup_sites.py add 登记兜底站）"
FALLBACK=$(python3 - <<PY
import json
d=json.load(open("$SITES_REG"))
site=d.get("fallback_domain") or ""
for s in d.get("sites",[]):
    if s.get("frozen"): site=s["domain"]; break
print(site)
PY
)
[ -n "$FALLBACK" ] || die "登记表里没有冻结兜底站"
echo "  兜底站：$FALLBACK"

if [ -n "${SITE_URL:-}" ]; then
  case "$SITE_URL" in
    *"$FALLBACK"*) echo "  SITE_URL=$SITE_URL（=兜底站，允许）" ;;
    *) die "🔴 SITE_URL 必须是冻结兜底站（$FALLBACK），收到的是：$SITE_URL" ;;
  esac
fi

say "② 环境"
command -v gradle >/dev/null || die "没有 gradle"
[ -d "${ANDROID_HOME:-/opt/android-sdk}" ] || die "没有 Android SDK（ANDROID_HOME=${ANDROID_HOME:-未设}）"
[ -f "$KS_PASS_FILE" ] || die "缺签名口令 $KS_PASS_FILE（🔴 不许写进源码包）"
KS_PASS=$(cat "$KS_PASS_FILE")

say "③ 递增 versionCode"
python3 - <<'PY'
import json,os,re
p="/workspace/nhk/apk/app/build.gradle"
if not os.path.exists(p):
    raise SystemExit("找不到 %s" % p)
s=open(p,encoding="utf-8").read()
m=re.search(r"versionCode\s+(\d+)",s)
cur=int(m.group(1))
s=s.replace("versionCode %d"%cur, "versionCode %d"%(cur+1),1)
open(p,"w",encoding="utf-8").write(s)
print("  versionCode %d → %d"%(cur,cur+1))
PY

say "④ gradle assembleRelease"
cd "$APK_DIR" || die "没有 $APK_DIR"
gradle --no-daemon -q assembleRelease \
  -PsiteUrl="$FALLBACK" -PksPass="$KS_PASS" || die "gradle 失败"
[ -f "$OUT/signed.apk" ] || die "没出包：$OUT/signed.apk"
ls -la "$OUT/signed.apk"
echo "✅ 出包完成。接着跑：bash $ROOT/apk/stage_apk.sh"
