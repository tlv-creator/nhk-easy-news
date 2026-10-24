#!/usr/bin/env bash
# setup_env.sh —— 沙箱重置后**自愈**：把项目、依赖、模型、共享工具都准备好
#
# 🔴 为什么必须有这个：沙箱会重置，项目/venv/模型全没 ⇒ 不自愈就得从头重建管线。
#    本脚本只做「环境准备」，**不碰任何内容**（不下载不发布）。
# 🔴 2026-10-24：原项目随沙箱重置丢失 ⇒ 现在**项目本体在 GitHub 共享仓库的 nhk/ 子目录**
#    （和 Shun 一个套路）⇒ 重置后从这里恢复，而不是重建。
set -uo pipefail

ROOT=/workspace
PROJ=$ROOT/nhk
VENV=$ROOT/venv
MODELS=$ROOT/models/fw-base
SHARED=$ROOT/nhk-easy-news
REPO=tlv-creator/nhk-easy-news
TOKEN="${GH_TOKEN:-}"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m! %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

say "1/6 项目目录"
if [ -d "$PROJ/scripts" ] && [ -f "$PROJ/手册.md" ]; then
  echo "  已在：$PROJ"
else
  [ -n "$TOKEN" ] || die "没有 GH_TOKEN，恢复不了项目"
  if [ ! -d "$SHARED" ]; then
    echo "  克隆共享仓库…"
    mkdir -p "$SHARED"
    # 🔴 放工作区，不要放 /tmp（沙箱会回收 /tmp）
    curl -sL --retry 3 -o "$ROOT/shared.tar.gz" \
      "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" || die "克隆失败"
    tar xzf "$ROOT/shared.tar.gz" -C "$SHARED" --strip-components=1 || die "解压失败"
  fi
  [ -d "$SHARED/nhk" ] || die "共享仓库里没有 nhk/ 目录（项目丢了，需要人工恢复）"
  # 🔴 用 `cp -r src/. dst/` ⇒ 拷**内容**；写成 `cp -r src dst` 会多套一层 nhk/nhk/
  cp -r "$SHARED/nhk/." "$PROJ/" && echo "  已从共享仓库恢复：$PROJ"
fi
mkdir -p "$PROJ"/{work,data,deploy_site}
mkdir -p "$PROJ/work"/{out,syn_out,raw,asr,zh}
mkdir -p "$PROJ/data"/{episodes,audio,asr,zh}
mkdir -p "$PROJ/scripts"

say "2/6 共享工具（音频体检 / 死链扫描 / 引导页推送）"
if [ ! -f "$SHARED/scripts/audio_health.py" ]; then
  [ -n "$TOKEN" ] || die "没有 GH_TOKEN，拉不了共享工具"
  mkdir -p "$SHARED"
  curl -sL --retry 3 -o "$ROOT/shared.tar.gz" \
    "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" || die "克隆失败"
  tar xzf "$ROOT/shared.tar.gz" -C "$SHARED" --strip-components=1 || die "解压失败"
fi
for t in audio_health.py check_dead_refs.py update_guide_page.py strip_inject.py; do
  [ -f "$SHARED/scripts/$t" ] && cp -f "$SHARED/scripts/$t" "$PROJ/scripts/$t" && echo "  复制 scripts/$t"
done
echo "  共享工具目录：$SHARED/scripts"

say "3/6 Python 环境"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || die "建 venv 失败"
fi
"$VENV/bin/pip" install --no-input -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
  faster-whisper sudachipy sudachidict-core requests janome 2>&1 | tail -2
"$VENV/bin/python" - <<'PY' || die "依赖没装上"
import faster_whisper, sudachipy, requests  # noqa: F401
from janome.tokenizer import Tokenizer
Tokenizer()
print("  依赖 OK：faster-whisper / sudachipy / requests / janome")
PY

say "4/6 ASR 模型（CTranslate2 版 faster-whisper-base）"
mkdir -p "$MODELS"
if [ ! -s "$MODELS/model.bin" ]; then
  # ModelScope 镜像（HF 主站在沙箱里经常连不上）
  BASE="https://www.modelscope.cn/api/v1/models/pengzhendong/faster-whisper-base/repo?Revision=master&FilePath="
  for f in config.json configuration.json tokenizer.json vocabulary.txt; do
    [ -s "$MODELS/$f" ] || curl -sL -o "$MODELS/$f" "${BASE}${f}" || warn "$f 下载失败"
  done
  [ -s "$MODELS/model.bin" ] || curl -sL -o "$MODELS/model.bin" "${BASE}model.bin" \
    || warn "model.bin 下载失败（ASR 会跑不起来）"
fi
ls -la "$MODELS/model.bin" 2>/dev/null | awk '{print "  model.bin",$5,"bytes"}'

say "5/6 媒体工具"
command -v ffmpeg >/dev/null || warn "没有 ffmpeg（转码/体检要用）"
command -v ffprobe >/dev/null || warn "没有 ffprobe"

say "6/6 站点/工具检查"
for t in update.py scripts/fetch_nhk.py scripts/asr_align.py scripts/translate.py \
         scripts/build_site.py scripts/cleanup_sites.py scripts/syn_zh.py \
         scripts/update_nhk_guide.py work/deploy_nhk.sh apk/build.sh apk/stage_apk.sh; do
  [ -f "$PROJ/$t" ] || warn "缺 $t"
done
[ -f "$PROJ/site/app/app.js" ] || warn "site/app 不在（从线上站取回：python3 scripts/fetch_nhk.py --restore-site --site <线上站> --with-media）"
# 音频不进仓库 ⇒ 缺了就从线上站拉（引导页的 site = 当前站）
if [ ! -d "$PROJ/site/app/media/slow" ] || [ -z "$(ls -A "$PROJ/site/app/media/slow" 2>/dev/null)" ]; then
  CUR=$(curl -s -m 20 -H 'Cache-Control: no-cache' \
        https://tlv-creator.github.io/nhk-easy-news/version.json \
        | python3 -c "import json,sys; print((json.load(sys.stdin).get('site') or '').rstrip('/'))" 2>/dev/null)
  if [ -n "$CUR" ]; then
    warn "音频不在（沙箱重置会丢）⇒ 从 $CUR 取回"
    python3 "$PROJ/scripts/fetch_nhk.py" --restore-site --site "$CUR" --with-media \
      || warn "取回失败（没有音频就不能 build）"
  else
    warn "读不到引导页当前站 ⇒ 音频没法自动取回"
  fi
else
  echo "  音频：$(ls "$PROJ/site/app/media/slow" 2>/dev/null | wc -l) 个慢速 / $(ls "$PROJ/site/app/media/fast" 2>/dev/null | wc -l) 个原速"
fi
[ -n "$TOKEN" ] || warn "没有 GH_TOKEN（推引导页要用）"

cat <<'EOF'

✅ 环境就绪。下一步（照 手册.md §7）：
    python3 /workspace/nhk/update.py --all
EOF
