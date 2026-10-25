#!/usr/bin/env bash
# work/make_src_slim.sh —— 生成「瘦源码包」目录（work/src_slim）
#
# 🔴 为什么要有它：
#   `website_deploy` 会上传一份**源码包**（source_path）⇒ 它会被推到公开仓库/云盘 ⇒
#   ⇒ 里面**绝不能有**：签名私钥（apk/keystore/*.jks）、口令、音频大文件、虚拟环境、缓存。
#   ⇒ 这个目录就是「要上传的那一份」的干净副本。
#
# 🔴 纪律：
#   ① **每次 prepare 都重新生成**（rm -rf 再拷）⇒ 上一轮的残留不会带进来
#      （手册 §34.7：闸门要扫「当轮产物」，不是上一轮的副本）
#   ② **签名私钥永远排除**（apk/keystore/、*.jks、*.keystore、*.p12）
#   ③ 媒体不进包（site/app/media/ 有 59MB）⇒ 只留目录结构说明
#   ④ 生成完由 `python3 scripts/check_no_secret_in_package.py work/src_slim` 验一遍
set -uo pipefail

ROOT=/workspace/nhk
OUT=$ROOT/work/src_slim

EXC=( --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.nohup.out'
      --exclude='media' --exclude='*/media' --exclude='*/media/*' --exclude='keystore' --exclude='*/keystore' --exclude='*.jks' --exclude='*.keystore'
      --exclude='*.p12' --exclude='*.pfx' --exclude='venv' --exclude='.venv'
      --exclude='out' --exclude='src_slim' --exclude='node_modules' )

echo "== 生成瘦源码包 → $OUT"
rm -rf "$OUT"
mkdir -p "$OUT"

cd "$ROOT" || exit 1
# 只拷「源码」层：脚本 / 手册 / 前端源码 / 配置（不含媒体、不含私钥、不含产物）
for d in scripts work apk data site boot; do
  [ -e "$ROOT/$d" ] || continue
  # 🔴 tar 的选项必须写在**成员列表前面**（写在后面会被当成文件操作数 ⇒ --exclude **静默失效**
  #    ⇒ 60MB 音频被拷进源码包（今天实测）
  tar cf - "${EXC[@]}" "$d" 2>/dev/null | (cd "$OUT" && tar xf -)
done
# 根目录的零散文件
for f in 手册.md setup_env.sh update.py cron_tools.py t_lib.py mock_ai.py \
         test_local.py test_native_tick.py test_ai.py test_ai_copy.py test_online.py \
         sites_nhk.json; do
  [ -f "$ROOT/$f" ] && cp -f "$ROOT/$f" "$OUT/$f"
done

# 媒体目录留个说明（免得看的人以为漏了）
mkdir -p "$OUT/site/app/media"
cat > "$OUT/site/app/media/README.txt" <<'EOF'
音频不进源码包（59MB）⇒ setup_env.sh 会从线上站按需取回：
    python3 scripts/fetch_nhk.py --restore-site --site <当前站> --with-media
EOF

echo "== 瘦源码包：$(du -sh "$OUT" | cut -f1) · $(find "$OUT" -type f | wc -l) 个文件"
if find "$OUT" \( -name '*.jks' -o -name '*.keystore' -o -name '*.p12' -o -name '*.pfx' \) | grep -q .; then
  echo "❌ 瘦源码包里还有密钥文件 ⇒ 停"
  exit 1
fi
echo "✅ 无密钥文件"
