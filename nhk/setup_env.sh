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
  if [ ! -f "$SHARED/scripts/$t" ]; then continue; fi
  if [ ! -f "$PROJ/scripts/$t" ]; then
    cp -f "$SHARED/scripts/$t" "$PROJ/scripts/$t" && echo "  复制 scripts/$t（本地没有）"
  elif ! cmp -s "$SHARED/scripts/$t" "$PROJ/scripts/$t"; then
    # 🔴🔴 2026-10-25 修（今天真踩到）：原来**无条件覆盖**本地文件
    #    ⇒ 本地刚修好的 strip_inject.py 被共享仓库的旧版**静默覆盖回去**
    #    ⇒ 修的改动凭空消失（而且没有任何提示）⇒ 排查半天
    #    ⇒ 现在：内容不同就**保留本地** + 明确提示（共享仓库才是权威 ⇒ 该把改动推上去）
    echo "  ⚠️ scripts/$t：本地与共享仓库**不一致** ⇒ 保留本地版"
    echo "     （共享仓库是权威 ⇒ 确认本地改动是对的之后，要推上去：scripts/push_files.py）"
  else
    echo "  scripts/$t：与共享仓库一致 ✅"
  fi
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
print("  依赖 OK（venv）：faster-whisper / sudachipy / requests / janome")
PY

# 🔴🔴 2026-11-21 修：**系统 python3 也要有 janome/sudachipy**
#    坑：cron prompt 写的是 `python3 update.py --all`（不是 venv 的 python）
#    ⇒ 依赖只在 /workspace/venv 里 ⇒ `import parse_text` 直接 ModuleNotFoundError
#    ⇒ 每日自动更新在第一步就崩（而且报错信息指向 janome，很难联想到是「venv 没进 PATH」）
#    ⇒ 现在：venv 装一份（跑 ASR 用），系统 python3 也装一份（脚本默认用 python3）
# 🔴 2026-11-22 修：原来这里用**反引号**包着 `python3 xxx.py`
#    ⇒ bash 把它当**命令替换** ⇒ 真的去跑 `python3 xxx.py`
#    ⇒ 日志里多一行「python3: can't open file '…/nhk/xxx.py'」（看着像脚本路径写错了，其实是假报错）
echo "  （给系统 python3 也装一份：cron prompt 里的「python3 xxx.py」才跑得起来）"
python3 -m pip install --break-system-packages --no-input -q \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  sudachipy sudachidict-core requests janome pyaxmlparser 2>&1 | tail -2
python3 - <<'PY' || warn "系统 python3 的依赖没装上（venv 那份仍然可用）"
from janome.tokenizer import Tokenizer
Tokenizer()
import sudachipy, requests  # noqa: F401
import pyaxmlparser  # noqa: F401   # 🔴 读 APK versionCode（引导页 apkVer 要用它核对）
print("  依赖 OK（系统 python3）：sudachipy / requests / janome / pyaxmlparser")
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

# 🔴🔴 2026-11-24 修（手册 §35.1 ③）：**恢复站点文件不许带 --skip-existing**
#    为什么：共享仓库里的 nhk/site/app 只是一份**快照**（可能停在几期前）
#       ⇒ --skip-existing 会让**旧文件留在本地**（线上那一版被跳过）
#       ⇒ 沙箱重置后本地目录是旧的 ⇒ 明天 build 会拿旧数据当底 ⇒ **静默出错**
#    ⇒ 现在：**整份从线上现役站取回**（本地目录永远是"线上那一版"）
#       （代价：多下 ~10MB；换来的是「本地 == 线上」这个不变量）
CUR=$(curl -s -m 20 -H 'Cache-Control: no-cache' \
      https://tlv-creator.github.io/nhk-easy-news/version.json \
      | python3 -c "import json,sys; print((json.load(sys.stdin).get('site') or '').rstrip('/'))" 2>/dev/null)
if [ -n "$CUR" ]; then
  echo "  当前站：$CUR ⇒ 整份对齐站点文件（**不带 --skip-existing** ⇒ 本地 == 线上）…"
  python3 "$PROJ/scripts/fetch_nhk.py" --restore-site --site "$CUR" \
    || warn "站点文件对齐失败（继续，但本地可能是旧快照）"
  # 🔴 index.html 特判：**每次都取回 + 重新剥平台注入**
  #    （线上那份 index.html **带着平台注入的统计脚本** ⇒ 不能直接当本地源码）
  if curl -s -m 30 -o "$PROJ/site/app/index.html" "$CUR/index.html"; then
    python3 "$PROJ/scripts/strip_inject.py" "$PROJ/site/app/index.html" >/dev/null 2>&1 \
      && echo "  index.html：已取回 + 平台注入已剥" \
      || warn "index.html 剥注入失败（查 scripts/strip_inject.py）"
  else
    warn "index.html 取回失败"
  fi
else
  warn "读不到引导页当前站 ⇒ 站点文件没法自动对齐"
fi
[ -f "$PROJ/site/app/app.js" ] || warn "site/app 不在（从线上站取回：python3 scripts/fetch_nhk.py --restore-site --site <线上站> --with-media）"
# 🔴🔴 2026-11-24 修：音频**每次都补齐**（不是「目录空着才拉」）
#    坑：原来只在 media/slow **目录为空**时才拉音频
#       ⇒ 仓库里若存着 110 期的旧音频（媒体在仓库里是有先例的）⇒ **目录不为空** ⇒ 不拉
#       ⇒ 新发的 4 期音频**永远补不上** ⇒ 那几期「有字幕没声音」（**静默**！用户以为功能坏了）
#    ✅ 修：**每次都跑一次 --with-media --skip-existing**（幂等 ⇒ 只下载缺的那些）
#       ⇒ 本地音频永远 == 线上站声明的音频集合
if [ -n "$CUR" ]; then
  echo "  音频：对齐线上站（只补缺的 ⇒ 幂等）…"
  python3 "$PROJ/scripts/fetch_nhk.py" --restore-site --site "$CUR" --with-media --skip-existing \
    >/dev/null 2>&1 && echo "  音频：$(ls "$PROJ/site/app/media/slow" 2>/dev/null | wc -l) 个慢速 / $(ls "$PROJ/site/app/media/fast" 2>/dev/null | wc -l) 个原速" \
    || warn "音频补齐失败（没有音频就不能 build）"
else
  warn "读不到引导页当前站 ⇒ 音频没法自动取回"
fi
[ -n "$TOKEN" ] || warn "没有 GH_TOKEN（推引导页要用）"

cat <<'EOF'

✅ 环境就绪。下一步（照 手册.md §7）：
    python3 /workspace/nhk/update.py --all
EOF
