#!/bin/bash
# 🔴🔴 deploy_nhk.sh —— 发版的两阶段脚本（**闸门的唯一实现**）
#
# 为什么拆两阶段：
#   website_deploy 只能由**工具**调用（拿 URL），shell 里做不了
#   ⇒ 而「同步 deploy_site」必须发生在 website_deploy **之前**
#   ⇒ 而「推引导页」必须发生在 website_deploy **之后**（要新域名）
#   ⇒ 一个脚本里做不到 ⇒ 拆成 prepare / finish 两段。
#
# 🔴 2026-10-23 修的结构性问题：
#   cron prompt 第 5 步原来**内联**了整套部署流程，**没调用这个脚本**
#   ⇒ 这里的闸门对 cron 根本不生效 ⇒ 「加了防线但日常流程绕过去」= 没有防线
#   ⇒ 现在：手册 §7 的流程**只认这个脚本**，prompt 只留指针。
#
# 🔴 2026-10-25 加的三道闸门（今天被这三点卡死的）：
#   ⑤ 站点配额体检  → 配额吃光 ⇒ ENTITLEMENT_LIMIT_EXCEEDED（部署被硬拒）
#   ⑥ 缓存版本三件套 → APP_VER / CACHE / ver 不同号 ⇒ 用户手机永远拿旧字幕
#   ⑦ 线上注入核查  → 平台注入的统计脚本/悬浮球
#
# 用法：
#   bash work/deploy_nhk.sh prepare              # 闸门 + 同步 deploy_site（**部署前**）
#   bash work/deploy_nhk.sh finish <域名> <ver> "<note>" [旧域名] [新站node_id]
set -e
cd /workspace/nhk

PHASE="${1:?需要 prepare 或 finish}"
SITE="${2:-}"
VER="${3:-}"
NOTE="${4:-每日自動更新}"
OLD="${5:-}"
NEWNODE="${6:-}"

case "$PHASE" in
pages)
  # 🔴🔴🔴 2026-10-25 新增：**日更零部署路径**（Pages-only）
  #
  # 为什么要有这一档：
  #   `website_deploy` **每次都新建站点节点** ⇒ 发一版 = 占 1 个配额位（上限 50）
  #   ⇒ NHK 日更（周一~周五各 1 期）= 每周 +5 个位 ⇒ **10 周必然撞 ENTITLEMENT_LIMIT_EXCEEDED**
  #   ⇒ 2026-10-25 当天就撞过一次（v1.24→v1.29 连发 6 次，从不删旧的）
  #
  #   核心洞察：**NHK 每天变的是「内容」，不是「站」**
  #   ⇒ 内容（字幕/词库/语法/前端）放 **GitHub Pages**：同一个仓库 = 同一个站点
  #   ⇒ **地址永不变 + 零 mcode 配额占用**
  #   ⇒ 只有**音频**必须留 mcode（实测 github.io 拉 790KB 要 23~48 秒，mcode 只要 0.28 秒）
  #   ⇒ 音频**攒批发**（单站 200MB ⇒ 一个音频站能装约 148 期）
  #
  # 所以日更的正常路径是：
  #   ① 内容变了 ⇒ **只推 Pages**（零配额）——本档
  #   ② 音频攒够了 ⇒ 才发一次 mcode 站（发完立刻删旧站 ⇒ 净增 0）
  #
  # 🔴 判据在 scripts/daily_publish.py（**不许在这里重写配方**）
  echo "════ ⓪ Pages-only 发版（零 mcode 配额占用）════"
  python3 scripts/daily_publish.py --plan || exit 1
  echo ""
  echo "════ ① 同步 Pages（地址永不变）════"
  python3 scripts/daily_publish.py --sync-pages || exit 1
  echo ""
  echo "════ ② 问「要不要发 mcode 音频站」════"
  # exit 0 = 要发；exit 3 = 不要发（零占用）——**判据在 daily_publish.py 里**
  if python3 scripts/daily_publish.py --need-audio-deploy; then
    echo ""
    echo "🔴 音频攒到阈值了 ⇒ **需要发一次 mcode 站**（含音频）"
    echo "   ⇒ 请走：bash work/deploy_nhk.sh prepare  →  website_deploy  →  finish <域名> <ver> ..."
    echo "   ⇒ 🔴 发完**立刻删上一版**（净增 0）——见 finish 段第 ⑤ 步"
    exit 10   # ← 专用退出码：告诉调用方「Pages 好了，但还需要发音频站」
  else
    RC=$?
    if [ "$RC" != "3" ]; then
      echo "❌ 决策脚本异常退出（rc=$RC）⇒ 不许当作「不用发」"
      exit 1
    fi
    echo ""
    echo "✅ 音频还没到阈值 ⇒ **今天 0 配额占用**（Pages 已更新，音频继续用现役站）"
    exit 0
  fi
  ;;
prepare)
  echo "════ ⓪ 发版闸门（**全部必须绿**）════"
  # ① 凭据绝不能进交付包/源码包（🔴 2026-10-23 差点把签名密钥拷进源码包）
  #    🔴 2026-10-25 晚修：这里**只扫 deploy_site**，不要连带扫 work/src_slim
  #       原因：work/src_slim 是**上一轮** prepare 留下的副本（③ 才刷新它）
  #       ⇒ 在 ③ 之前扫它 = 扫上一轮的内容 ⇒ 本轮新引入的问题要等下一轮才被抓
  #       ⇒ 而且会把「上一轮有、这轮已修」的东西误报成当前问题（今天真踩到：
  #          pack_html.py 的 PEM 字面量已拆开构造，但 src_slim 里还是旧副本 ⇒ 误报）
  #       ⇒ src_slim 由 **③ 之后**那次检查负责（那才是当轮的产物）
  python3 scripts/check_no_secret_in_package.py deploy_site || exit 1
  # ② 配方只写一遍（同一个配方写在 N 个地方 ⇒ 改了一处 = 修了 1/N）
  python3 scripts/check_audio_recipe.py || exit 1
  # ③ 瘦源码包必须当次刷新（否则发出去的是旧配方的副本）
  bash work/make_src_slim.sh
  python3 scripts/check_no_secret_in_package.py work/src_slim || exit 1
  # ④ 🔴 媒体库闸门：跨期 md5 重复 = 抓到了**别的新闻**的音轨
  #    （NHK 新闻页底部挂着最新一期的播放器 ⇒ 抓页面 m3u8 极易张冠李戴，详见手册 §6.9）
  python3 scripts/check_fast_audio.py --dir site/app/media/fast || exit 1

  # ⑤ 🔴🔴 2026-10-25 新增：**站点配额体检**
  #    为什么放在部署**前**：配额是 50 个站，今天从 v1.24 连发 6 次从不删旧的
  #    ⇒ 配额吃光 ⇒ 部署被 ENTITLEMENT_LIMIT_EXCEEDED **硬拒**（当天的活全卡在这）
  #    ⇒ 部署前先看：① NHK 自己是不是只留了「现役 + 兜底」② 全账号离配额还有多远
  #    ⇒ 不满足 ⇒ **当场停**（别部署到一半才被拒）
  python3 scripts/site_quota_guard.py check || exit 1

  # ⑥ 🔴🔴 2026-10-25 新增：**缓存版本三件套**（APP_VER / sw.js CACHE / version.json ver）
  #    为什么：app.js 用 `?v=APP_VER` 抓字幕，sw.js 用 CACHE 当预缓存桶
  #    ⇒ 不同号 ⇒ Service Worker 先返回旧字幕 ⇒「用户说没更新」的真因
  #    ⇒ 升版只在 scripts/bump_content_ver.py 一处（**配方不许写两遍**）
  echo "════ ⓑ 版本号升版 + 缓存闸门 ════"
  # 🔴 升版规则（**唯一的实现在 scripts/bump_content_ver.py**）：
  #    · 内容指纹（data/*.js + subs/*.js）变了 ⇒ 发布版本号 +1
  #      （APP_VER 是字幕 URL 的破缓存号 ⇒ 字幕变了必须升，否则用户拿旧字幕）
  #    · 内容没变 ⇒ 不升（用户不必白下 200KB）
  #    · 站内 ver 落后于引导页 ⇒ 强制跟上去（异常兜底）
  GUIDEVER=$(python3 - <<'PY'
import sys
sys.path.insert(0, '/workspace/nhk/scripts')
from site_quota_guard import boot_info
b = boot_info()
print(b.get("ver") or 0)
PY
)
  SITEVER0=$(python3 -c "import json;print(json.load(open('site/app/version.json'))['ver'])")
  echo "   引导页已发布 ver=$GUIDEVER · 站内 ver=$SITEVER0"
  if [ "$SITEVER0" -lt "$GUIDEVER" ]; then
    echo "   ⚠️ 站内 ver 落后于引导页 ⇒ 强制升到 $((GUIDEVER+1))"
    python3 scripts/bump_content_ver.py --release-ver $((GUIDEVER+1)) || exit 1
  else
    python3 scripts/bump_content_ver.py || exit 1
  fi
  python3 scripts/check_cache_ver.py --dir site/app || exit 1
  RELVER=$(python3 -c "import json;print(json.load(open('site/app/version.json'))['ver'])")
  echo "   本次发布版本号 = $RELVER（引导页要写这个数）"
  echo "$RELVER" > work/release_ver.txt

  echo "════ ⓑb APK 进待上线区（如果这轮 build 过）════"
  # 🔴 顺序坑（2026-10-23 修）：原来 cron 是「先部署、后打包」⇒ 打包出来的新 APK
  #    **根本没进这次部署** ⇒ 引导页指向新站的 NHK.apk，拿到的却是上一版的。
  #    ⇒ 现在在**同步 deploy_site 之前**先把产物放进 site/app。
  if [ -f /workspace/nhk/apk/out/signed.apk ]; then
    bash /workspace/nhk/apk/stage_apk.sh || exit 1
  else
    echo "   （没有新构建产物 ⇒ 沿用 site/app 里现有的 APK）"
  fi

  echo "════ ① 同步 deploy_site（先删后拷，避免旧文件赖着被发出去）════"
  rm -rf deploy_site
  cp -a site/app deploy_site
  du -sh deploy_site

  echo "════ ①b 剥掉平台注入（悬浮球 / PV 统计脚本）════"
  # 🔴 mcode **每次部署都会**往 index.html 里注入：
  #   ① <script data-minimax-analytics=...>（PV 统计 → POST 到 agent.minimax.cn）
  #   ② <div id="minimax-floating-ball"> + 它的 CSS + 初始化 JS
  #      ⇒ 点它会 window.open 到外部 ⇒ **在 App 里把用户整个甩出 App**
  #      ⇒ 而且它盖住底栏工具条（單句循環 / A＋ / ⬇下載）
  # ⇒ 源码里剥一次没用（下次部署又被注入）⇒ **每次部署都剥**（这步进流程）
  # ⇒ 另外 index.html 里还有**运行时兜底**（platformInjectGuard）：
  #    万一平台在上传那一刻又注入，兜底脚本会把球摘掉、内联 CSS 也会藏它。
  # 🔴🔴 2026-10-25 查清：平台是**在上传那一刻**把注入写进 OSS 上的文件
  #    ⇒ 「剥完再重新部署」**不会**让 token 消失（只会换一个 token + 多一个站）
  #    ⇒ 真正能去掉 token 的只有：平台开关 / 覆盖 OSS 对象 / 换托管（见手册 §14.3）
  python3 scripts/strip_inject.py site/app/index.html
  python3 scripts/strip_inject.py deploy_site/index.html

  echo "════ ①b2 部署产物自检（缓存三件套 + 无平台注入）════"
  python3 scripts/check_cache_ver.py --dir deploy_site || exit 1
  if grep -q 'data-minimax-analytics="minimax-website-analytics-v1"' deploy_site/index.html; then
    echo "❌ deploy_site/index.html 里还有平台统计脚本 ⇒ 停"; exit 1
  fi
  if ! grep -q "var BLOCK = 'agent.minimax.cn'" deploy_site/index.html; then
    echo "❌ 拦截器不见了 ⇒ 停（它才是挡住上报的那一层）"; exit 1
  fi
  echo "   ✅ 源文件里没有平台注入 + 拦截器在"

  echo "════ ①c 部署产物自检 ════"
  python3 - <<'PY'
import json, sys, os
ok = True
js = open("/workspace/nhk/deploy_site/app.js", encoding="utf-8").read()
if "this.pos = ((+st.pos) || 0) / 1000;" not in js:
    print("❌ deploy_site/app.js 没有毫秒→秒换算（字幕同步会差 1000 倍）"); ok = False
else:
    print("✅ app.js 的毫秒→秒换算在")
apk = "/workspace/nhk/deploy_site/NHK.apk"
if not os.path.exists(apk):
    print("❌ 没有 APK（引导页的 apk 字段会指向 404）"); ok = False
else:
    print("✅ APK 在（%d B）" % os.path.getsize(apk))
v = json.load(open("/workspace/nhk/deploy_site/version.json", encoding="utf-8"))
print("✅ version.json ver=%s" % v.get("ver"))
sys.exit(0 if ok else 1)
PY
  echo ""
  echo "✅ prepare 完成 ⇒ 现在可以 website_deploy deploy_site"
  echo "   本次发布版本号 ver=$RELVER ⇒ 部署完跑："
  echo "   bash work/deploy_nhk.sh finish <新域名> $RELVER \"<note>\" <旧域名>"
  ;;

finish)
  [ -n "$SITE" ] || { echo "❌ finish 需要新站域名" >&2; exit 1; }
  S="https://${SITE}.space.mcode.cn"

  # 🔴 版本号/版本名以**站内 version.json** 为准（唯一权威）
  #    传进来的 VER 只用来对账；不一致就大声报出来（不是静默改）
  SITEVER=$(python3 -c "import json;print(json.load(open('site/app/version.json'))['ver'])")
  SITEVERNAME=$(python3 -c "import json;print(json.load(open('site/app/version.json')).get('verName','1.4'))")
  if [ -n "$VER" ] && [ "$VER" != "$SITEVER" ]; then
    echo "🔴 传入的 ver($VER) 与站内 version.json($SITEVER) 不一致 ⇒ 以站内为准"
    VER="$SITEVER"
  else
    VER="$SITEVER"
  fi

  echo "════ ③ 推永久引导页 ════"
  export GH_TOKEN="$(cat /workspace/.secrets/gh_token)"
  python3 push_boot.py --site "${S}" \
      --apk "${S}/NHK.apk" \
      --ver "$VER" --vername "$SITEVERNAME" --note "$NOTE" 2>&1 | tail -3

  echo "════ ③b 等 Pages 渲染端追上（源端先到，渲染端慢一拍）════"
  for i in $(seq 1 12); do
    v=$(curl -s -m 15 https://tlv-creator.github.io/nhk-easy-news/version.json \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('ver'))" 2>/dev/null)
    echo "   渲染端 ver=$v（期望 $VER）"
    [ "$v" = "$VER" ] && break
    sleep 8
  done

  echo "════ ④ 线上验收 ════"
  python3 test_online.py "$S" 2>&1 | tail -3
  python3 test_audio_sync.py "$S" 2>&1 | tail -3
  python3 test_native_tick.py "$S" 2>&1 | tail -3

  echo "════ ④b 线上缓存三件套（APP_VER / CACHE / ver 同号？）════"
  # 🔴 这是「用户说没更新」的**最后一公里**判据：
  #    站上三者不同号 ⇒ 用户的 Service Worker 会一直返回旧字幕
  python3 scripts/check_cache_ver.py --url "$S" "v${VER}" || exit 1

  echo "════ ④c 线上注入核查（拦截器 / 兜底 / 0 外发）════"
  python3 scripts/check_live_inject.py "$S" || exit 1

  echo "════ ⑤ 站点登记 + 清理旧站（**配额**）════"
  # 🔴🔴 2026-10-25：每次部署都新建站点节点 ⇒ 不删旧的 ⇒ 50 个配额 50 天后必然炸
  #    ⇒ 规矩：**部署完立刻删上一版**（只留「现役 1 个 + 永久兜底 1 个」）
  #    ⇒ 放在 Pages 同步**之前**：配额是硬约束（满了 = 发不了版），同步失败不该拖住它
  if [ -n "$NEWNODE" ]; then
    python3 scripts/site_quota_guard.py register --node "$NEWNODE" --domain "$SITE" --ver "$VER" || exit 1
  else
    python3 scripts/site_quota_guard.py register --node auto --domain "$SITE" --ver "$VER" || exit 1
  fi
  # 四道闸门：保护清单 status≠ACTIVE / frozen≠true / APK 里没这个域名 / 引导页+源码零引用
  python3 scripts/site_quota_guard.py plan || exit 1
  # apply：能删就删（删完验 404）；本会话没有 drive 权限时，把精确命令落盘 + 打印
  python3 scripts/site_quota_guard.py apply || RC=$?
  if [ -n "${RC:-}" ] && [ "$RC" != "0" ]; then
    echo "🔴🔴 有站没删掉（配额在涨）⇒ 命令已落盘 work/pending_site_deletes.txt"
    echo "   ⇒ 请用**有 drive 权限的会话**执行那些 drive files delete 命令"
  fi
  if [ -n "$OLD" ]; then
    echo "════ 旧站（$OLD）删除前：四层扫引用 ════"
    python3 work/repo_scripts/check_dead_refs.py --dead "$OLD" --apk site/app/NHK.apk --protected 2>&1 | tail -8
    echo "   （上面 exit=0 ⇒ 零活引用 ⇒ 才允许删）"
  fi
  python3 scripts/site_quota_guard.py verify || exit 1

  echo "════ ⑥ 🔴 同步 GitHub Pages 永久网页版 ════"
  # 永久地址：https://tlv-creator.github.io/nhk-easy-news/nhk-web/
  # ⇒ 每天发版后必须同步一次，否则它会停在旧期数（用户收藏的是这个地址）
  # 🔴 顺序：**必须在推引导页之后**（同步脚本从仓库的 version.json 读现役站 ⇒ 音频才指对地方）
  # 🔴 源目录 = **本次部署的那份**（deploy_site），不是 site/app（后者可能已被别的进程改过）
  python3 scripts/check_pages_sync.py --before --src /workspace/nhk/deploy_site || exit 1
  python3 scripts/sync_pages.py --src /workspace/nhk/deploy_site || exit 1
  python3 scripts/check_pages_sync.py --after --src /workspace/nhk/deploy_site || exit 1
  echo "✅ Pages 永久网页版已同步（音频源 = 现役站 $SITE）"

  echo "✅ finish 完成：引导页已推 + 线上已验 + 旧站已清 + Pages 永久版已同步"
  ;;

*)
  echo "用法： bash work/deploy_nhk.sh pages                              # Pages-only（零配额，推荐日更用）" >&2
  echo "       bash work/deploy_nhk.sh prepare                            # 闸门 + 同步 deploy_site（**部署前**）" >&2
  echo "       bash work/deploy_nhk.sh finish <域名> <ver> \"<note>\" [旧域名] [新站node_id]" >&2
  exit 1
  ;;
esac
