#!/usr/bin/env bash
# setup_env.sh —— 沙箱重置后**自愈**：把项目、依赖、模型、共享工具都准备好
#
# 🔴 为什么必须有这个：沙箱每周重置一次，项目、venv、模型全没 ⇒ 不自愈就得从头重建管线。
#    本脚本只做「环境准备」，**不碰任何内容**（不下载不发布）。
set -uo pipefail

ROOT=/workspace
PROJ=$ROOT/shun
VENV=$ROOT/venv
MODELS=$ROOT/models/fw-base
SHARED=$ROOT/nhk-easy-news
REPO=tlv-creator/nhk-easy-news
TOKEN="${GH_TOKEN:-}"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

say "1/5 项目目录"
if [ -d "$PROJ/tools" ]; then
  echo "  已在：$PROJ"
else
  # 从共享仓库恢复 Shun 目录（项目就存在共享仓库的 shun/ 子目录里）
  [ -n "$TOKEN" ] || die "没有 GH_TOKEN，恢复不了项目"
  if [ ! -d "$SHARED" ]; then
    echo "  克隆共享仓库…"
    mkdir -p "$SHARED"
    curl -sL -o /tmp/shared.tar.gz "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" \
      || die "克隆失败"
    tar xzf /tmp/shared.tar.gz -C "$SHARED" --strip-components=1 || die "解压失败"
  fi
  [ -d "$SHARED/shun" ] || die "共享仓库里没有 shun/ 目录（项目丢了，需要人工恢复）"
  cp -r "$SHARED/shun" "$PROJ" && echo "  已从共享仓库恢复：$PROJ"
fi
mkdir -p "$PROJ"/{work,deploy,media}
mkdir -p "$PROJ/work"/{asr,cues,zh,lex,audio}

say "2/5 共享工具（音频体检等）"
if [ ! -f "$SHARED/scripts/audio_health.py" ]; then
  [ -n "$TOKEN" ] || die "没有 GH_TOKEN，拉不了共享工具"
  curl -sL -o /tmp/shared.tar.gz "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" \
    || die "克隆失败"
  mkdir -p "$SHARED" && tar xzf /tmp/shared.tar.gz -C "$SHARED" --strip-components=1 || die "解压失败"
fi
echo "  体检工具：$SHARED/scripts/audio_health.py"

say "3/5 Python 环境"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || die "建 venv 失败"
fi
# 🔴 janome 是**硬依赖**：tools/token_match.py 用它做句型的 **token 边界匹配**
#    （子串匹配会把「だけ」误标到「だけど」上 ⇒ 真实事故 ep13:69），
#    tools/gen_gs.py 也用它做词性校验（独立事实源）。
#    漏装 ⇒ 周更跑到句型标注那步直接崩。
"$VENV/bin/pip" install --no-input -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
  faster-whisper sudachipy sudachidict-core requests 2>&1 | tail -2
"$VENV/bin/python" - <<'PY' || die "依赖没装上"
import faster_whisper, sudachipy, requests  # noqa: F401
from janome.tokenizer import Tokenizer
Tokenizer()  # 真的能初始化才算装上
print("  依赖 OK：faster-whisper / sudachipy / requests / janome")
PY

say "4/5 ASR 模型（CTranslate2 版 faster-whisper-base）"
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

say "5/5 站点/工具检查"
for t in fetch_new.py asr.py transcribe_minimax.py build_cues.py translate_zh.py \
         build_lexicon.py build_ep_data.py verify.py push_boot.py \
         token_match.py gen_gs.py; do
  [ -f "$PROJ/tools/$t" ] || warn "缺 tools/$t"
done
command -v ffmpeg >/dev/null || warn "没有 ffmpeg（转码/体检要用）"
command -v mcode-tools >/dev/null || warn "没有 mcode-tools（听写/部署要用）"
[ -n "$TOKEN" ] || warn "没有 GH_TOKEN（推引导页要用）"

cat <<'EOF'

✅ 环境就绪。下一步（按 tools/手册.md 第 2 节）：
    python3 /workspace/shun/tools/fetch_new.py --dry-run
EOF
