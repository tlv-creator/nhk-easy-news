#!/usr/bin/env bash
# set_audio_base.sh —— 把 **音频站兜底地址** 写进 web/app.js 的 AUDIO_BASE_LOCAL
#
# 🔴 为什么必须有（手册第 7.3 节）：
#   · 引导页（shun.json）才是真地址 ⇒ 但**引导页拉不到时**（网络抖/缓存旧 build）
#     App 会回落到 APK/网页里写死的 AUDIO_BASE_LOCAL
#   ⇒ 那个兜底地址必须指向**当前在用的音频站** ⇒ 否则「引导页挂了」＝「用户听不到声音」
#
# 用法：
#   bash scripts/set_audio_base.sh "https://<音频站域名>/media/"
set -euo pipefail
NEW="${1:-}"
[ -n "$NEW" ] || { echo "用法：bash scripts/set_audio_base.sh <audio base url>"; exit 2; }
case "$NEW" in
  */) ;;
  *) NEW="$NEW/" ;;
esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/web/app.js"
[ -f "$APP" ] || { echo "✗ 找不到 $APP"; exit 1; }

# 只改 AUDIO_BASE_LOCAL 这一处 ⇒ 引导页给的 audio 仍然优先（app.js:362）
python3 - "$APP" "$NEW" <<'PY'
import re, sys
p, new = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
pat = re.compile(r"(var\s+AUDIO_BASE_LOCAL\s*=\s*')([^']*)(')")
if not pat.search(s):
    print("✗ app.js 里找不到 AUDIO_BASE_LOCAL"); sys.exit(1)
old = pat.search(s).group(2)
s2 = pat.sub(lambda m: m.group(1) + new + m.group(3), s, count=1)
open(p, "w", encoding="utf-8").write(s2)
print("AUDIO_BASE_LOCAL: %s → %s" % (old, new))
PY
