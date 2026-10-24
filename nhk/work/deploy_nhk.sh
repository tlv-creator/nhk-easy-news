#!/usr/bin/env bash
# deploy_nhk.sh —— 发版闸门（**部署只调它**，别手写部署步骤）
#
#   prepare                                  ① 自检 + 打包待上线目录
#   finish <新域名> <ver> "<note>" <旧域名>   ② 推引导页 + 等 Pages built + 线上验收 + 旧站扫引用
#
# 🔴 为什么要有闸门：mcode 每次部署都换随机域名，引导页是**唯一永久入口**。
#    手写部署步骤 = 绕过闸门 = 引导页没推 / 旧站没清 ⇒ 用户停在旧站。
set -uo pipefail

ROOT=/workspace/nhk
PROJ=$ROOT
DEPLOY=$PROJ/deploy_site
GUIDE_REPO=tlv-creator/nhk-easy-news
SITES_REG=$PROJ/data/sites.json

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

# 引导页当前站（唯一权威 = version.json）
current_site() {
  curl -s -m 20 -H 'Cache-Control: no-cache' \
    https://tlv-creator.github.io/nhk-easy-news/version.json \
    | python3 -c "import json,sys; print((json.load(sys.stdin).get('site') or '').rstrip('/'))" 2>/dev/null
}
current_apkver() {
  curl -s -m 20 -H 'Cache-Control: no-cache' \
    https://tlv-creator.github.io/nhk-easy-news/version.json \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('apkVer') or '')" 2>/dev/null
}

cmd_prepare() {
  say "① 自检"
  [ -f "$PROJ/site/app/index.html" ] || die "site/app 不在（先 setup_env.sh + fetch_nhk.py --restore-site）"
  [ -f "$PROJ/site/app/app.js" ]     || die "site/app/app.js 不在"
  [ -f "$PROJ/site/app/data/episodes.js" ] || die "site/app/data/episodes.js 不在"

  say "② 本地测试"
  (cd "$PROJ" && python3 test_local.py) || die "本地测试没过 ⇒ 不许发版"

  say "③ 打包待上线目录"
  rm -rf "$DEPLOY"
  mkdir -p "$DEPLOY"
  cp -r "$PROJ/site/app/." "$DEPLOY/"
  # APK：内容更新**不用重编**（原生层没改）⇒ 沿用现役 APK
  if [ ! -f "$DEPLOY/NHK.apk" ]; then
    cur=$(current_site)
    [ -n "$cur" ] || die "读不到引导页当前站（拿不到现役 APK）"
    say "   下载现役 APK：$cur/NHK.apk"
    curl -sL -m 300 -o "$DEPLOY/NHK.apk" "$cur/NHK.apk" || die "APK 下载失败"
  fi
  ls -la "$DEPLOY/NHK.apk"

  say "④ 凭据自查（🔴 部署产物里不能有凭据）"
  if grep -rlE '781108|nhknews2024|ghp_|sb_publishable|MINIMAX_SUB_KEY|nhk_ks_pass' "$DEPLOY" 2>/dev/null | head -5; then
    die "部署产物里扫到凭据 ⇒ 停"
  fi
  echo "   干净 ✅"

  say "⑤ 体积"
  du -sh "$DEPLOY"
  say "⑥ 部署"
  cat <<EOF

现在用 **website_deploy 工具**部署这个目录：
    path        = $DEPLOY
    source_path = $PROJ
    project_name = NHKやさしいことばニュース

🔴 拿到新域名后，**立刻**执行：
    bash $PROJ/work/deploy_nhk.sh finish <新域名> <ver> "<note>" <旧域名>
EOF
}

cmd_finish() {
  NEW=$1; VER=$2; NOTE=$3; OLD=$4
  [ -n "$NEW" ] && [ -n "$VER" ] || die "用法：finish <新域名> <ver> \"<note>\" <旧域名>"
  [ -n "${GH_TOKEN:-}" ] || die "没有 GH_TOKEN（推引导页要用）"
  NEW=${NEW%/}
  APKVER=$(current_apkver)
  say "① 推引导页（新站 $NEW，ver=$VER，apkVer=${APKVER:-?}）"
  python3 "$PROJ/scripts/update_nhk_guide.py" --site "$NEW" --ver "$VER" \
      --apk-ver "$APKVER" --note "$NOTE" --date "$(date +%F)" --push \
    || die "推引导页失败"

  say "② 等 Pages built（最多 5 分钟）"
  ok=0
  for i in $(seq 1 30); do
    sleep 10
    got=$(curl -s -m 20 -H 'Cache-Control: no-cache' \
          https://tlv-creator.github.io/nhk-easy-news/version.json \
          | python3 -c "import json,sys; print((json.load(sys.stdin).get('site') or '').rstrip('/'))" 2>/dev/null)
    if [ "$got" = "$NEW" ]; then ok=1; echo "   ✅ 引导页已指向 $NEW（第 $i 次）"; break; fi
    echo "   …等待中（第 $i 次，当前 $got）"
  done
  [ "$ok" = 1 ] || die "引导页 5 分钟内没指向新站 ⇒ 别急着清旧站"

  say "③ 线上验收"
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 30 "$NEW/")
  [ "$code" = "200" ] || die "新站 HTTP $code"
  n=$(curl -s -m 60 "$NEW/data/episodes.js" | grep -c '"id"' || true)
  echo "   新站 200 ✅，期数 $n"
  curl -s -m 30 "$NEW/NHK.apk" -o /dev/null -w '   APK %{http_code} %{size_download}B\n'

  say "④ 旧站扫引用（🔴 旧站可以删，但先确认没人在用）"
  if [ -n "$OLD" ]; then
    OD=${OLD#https://}; OD=${OD%%.*}
    echo "   旧站：$OD"
    for f in "$PROJ/deploy_site/app.js" "$PROJ/site/app/app.js" "$PROJ/site/app/data/cdn.js"; do
      [ -f "$f" ] && grep -l "$OD" "$f" 2>/dev/null | sed "s/^/   ⚠️ 还被引用：/"
    done
    echo "   （引导页已切走 ⇒ 旧站可以删）"
  fi

  say "⑤ 清理旧站（每天必做）"
  python3 "$PROJ/scripts/cleanup_sites.py" plan || warn "cleanup plan 有问题"

  cat <<EOF

✅ 发版完成：
   新站   $NEW
   引导页 https://tlv-creator.github.io/nhk-easy-news/  （已指向新站）
   旧站   ${OLD:-（无）}  ⇒ 按上面 plan 打印的命令逐条删
EOF
}

case "${1:-}" in
  prepare) cmd_prepare ;;
  finish)  shift; cmd_finish "$@" ;;
  *) echo "用法：$0 prepare | finish <新域名> <ver> \"<note>\" <旧域名>"; exit 2 ;;
esac
