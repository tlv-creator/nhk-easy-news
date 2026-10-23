#!/usr/bin/env bash
# =====================================================================
# 词汇学习站 → GitHub Pages 永久入口 一键发布
#
# 为什么要有这个：
#   · 腾讯云 CloudBase 默认域名会插「风险提醒」页，而且是用 HTTP 404 状态码发的
#     → 学生从手机打开看到的就是 404，必须点一次「确定访问」
#   · mcode 域每次 redeploy 都换域名 → 学生链接当场死
#   · GitHub Pages：推新版本 URL 不变 + 没有中间页 + 不会被清站删 + 零成本
#
# 用法：
#   GH_TOKEN=xxx bash tools/publish_vocab_pages.sh
#   GH_TOKEN=xxx bash tools/publish_vocab_pages.sh --dry-run   # 只推文件不验
# =====================================================================
set -euo pipefail

ROOT="${ROOT:-/workspace/vocab}"
REPO="${REPO:-tlv-creator/nhk-easy-news}"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-vocab/index.html}"
SRC="${SRC:-$ROOT/site/vocab-unit123-v1-prod-standalone.html}"
PAGES_URL="${PAGES_URL:-https://tlv-creator.github.io/nhk-easy-news/vocab/}"
API="https://api.github.com/repos/$REPO/contents/$DEST"

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. 前置检查 ──
command -v python3 >/dev/null || die "缺 python3"
[ -n "${GH_TOKEN:-}" ] || die "没有 GH_TOKEN"
[ -f "$SRC" ] || die "源文件不存在: $SRC"

# ── 1. 凭据闸门：正式版绝不能带凭据 ──
say "凭据自检（明文密码 / 教师摘要 必须都是 0）"
for k in 781108 4862b0735ade; do
  n=$(grep -c "$k" "$SRC" || true)
  [ "${n:-0}" = "0" ] || die "源文件含凭据材料 '$k'（$n 处）—— 拒绝发布"
  printf '    %-18s %s ✅\n' "$k" "0"
done
grep -q "TEACHER_EXPECT_HASH = ''" "$SRC" || die "不是正式版（TEACHER_EXPECT_HASH 非空）"

# ── 2. 自包含检查：不能有相对路径依赖 ──
rel=$(grep -oE '(src|href)="[^"h][^"]*"' "$SRC" | grep -v '^href="javascript:' || true)
[ -z "$rel" ] || die "有相对路径依赖，放到子目录会坏：$rel"

say "推送到 $REPO:$BRANCH → $DEST"
if [ "$DRY" = "1" ]; then
  say "dry-run：跳过推送，源文件 $(du -h "$SRC" | cut -f1)"
  exit 0
fi

# ── 3. 推（用 sha 提交，防覆盖别人的并发改动）──
# 现取当前 sha（文件已存在时必须带，否则 GitHub 报 422 "sha wasn't supplied"）
SHA=""
curl -sS -m 30 "$API" -H "Authorization: Bearer $GH_TOKEN" -o /tmp/_cur.json 2>/dev/null || true
SHA=$(python3 -c "
import json,io
try:
    d=json.load(io.open('/tmp/_cur.json',encoding='utf-8'))
    print((d.get('sha') or '') if isinstance(d,dict) else '')
except Exception: print('')
" 2>/dev/null || true)
[ -n "$SHA" ] && say "当前远端 sha=${SHA:0:12}（带 sha 提交，防覆盖并发改动）" || say "远端还没有这个文件（新建）"

python3 - "$SRC" "$BRANCH" "$SHA" /tmp/_put_vocab.json <<'PY'
import base64, io, json, sys
src, branch, sha, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
b = base64.b64encode(io.open(src, 'rb').read()).decode()
payload = {'message': 'feat(vocab): 词汇学习站发布（%d 字节）' % len(b),
           'content': b, 'branch': branch}
if sha:
    payload['sha'] = sha
io.open(out, 'w', encoding='utf-8').write(json.dumps(payload))
PY
curl -sS -m 120 -X PUT "$API" \
  -H "Authorization: Bearer $GH_TOKEN" -H "Content-Type: application/json" \
  --data-binary @/tmp/_put_vocab.json -o /tmp/_put_resp.json -w "    HTTP %{http_code}\n"
code=$(python3 -c "import json,io;print(json.load(io.open('/tmp/_put_resp.json',encoding='utf-8')).get('content',{}).get('sha',''))" 2>/dev/null || true)
[ -n "$code" ] || { cat /tmp/_put_resp.json; die "推送失败"; }
say "已推送 sha=${code:0:12}"

# ── 4. 轮询等 Pages 渲染（实测有延迟，历史上可差几小时）──
say "等 Pages 渲染（最多 5 分钟）…"
ok=0
for i in $(seq 1 15); do
  sleep 20
  sz=$(curl -sS -m 30 -o /tmp/_pg.html -w '%{size_download}' "$PAGES_URL" 2>/dev/null || echo 0)
  src_size=$(stat -c%s "$SRC")
  printf '    第 %2d 轮: %s bytes / 源 %s bytes\n' "$i" "$sz" "$src_size"
  if [ "${sz:-0}" -ge "$((src_size - 2000))" ] 2>/dev/null; then ok=1; break; fi
done
[ "$ok" = "1" ] || die "渲染超时（URL 本身可能已就绪，再手动确认一下）"

# ── 5. 真浏览器验（curl 200 ≠ 浏览器 200）──
say "真浏览器验收"
cat > /tmp/_pgcheck.js <<'EOF'
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome', args: ['--no-sandbox'] });
  const p = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage();
  const r = await p.goto(process.argv[2], { waitUntil: 'domcontentloaded', timeout: 40000 });
  console.log('    HTTP 状态码 = ' + r.status());
  await p.waitForSelector('#student-id', { timeout: 20000 });
  console.log('    登录页 ✅  title=' + await p.title());
  const ball = await p.evaluate(() => { const e = document.getElementById('minimax-floating-ball'); return e ? getComputedStyle(e).display : 'none'; });
  console.log('    悬浮球 = ' + ball);
  await b.close();
})();
EOF
NODE_PATH=$(npm root -g) node /tmp/_pgcheck.js "$PAGES_URL"

say "✅ 发布完成：$PAGES_URL"
say "   （URL 永久不变；以后发新版就重跑这一条命令）"
