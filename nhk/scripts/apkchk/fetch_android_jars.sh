#!/bin/bash
# 拉取官方 android.jar（API 等级闸门的判据来源）
# 🔴 jar 不进发布包（220MB 会撑爆 source archive 200MB 上限）→ 只留本脚本 + .py
# 🔴 用 Python 的 zipfile 解压：旧版 unzip 会误报
#    "invalid zip file with overlapped components (possible zip bomb)"（官方包其实正常）
set -e
cd "$(dirname "$0")"
mkdir -p jars && cd jars
dl() {
  if [ ! -f "android-$1.jar" ]; then
    echo "  下载 platform $1 ..."
    curl -sL -m 1200 -o "p-$1.zip" "https://dl.google.com/android/repository/$2" \
      || { echo "  ❌ 下载失败: $2"; return 1; }
    python3 - "$1" <<'PY'
import sys, zipfile, os
api = sys.argv[1]
z = zipfile.ZipFile("p-%s.zip" % api)
names = [n for n in z.namelist() if n.endswith("/android.jar") or n == "android.jar"]
if not names:
    print("  ❌ zip 里没有 android.jar"); sys.exit(1)
with z.open(names[0]) as src, open("android-%s.jar" % api, "wb") as dst:
    while True:
        b = src.read(1 << 20)
        if not b: break
        dst.write(b)
print("  ✓ 解出 android-%s.jar  %d bytes" % (api, os.path.getsize("android-%s.jar" % api)))
PY
    rm -f "p-$1.zip"
  else
    echo "  ✓ android-$1.jar 已存在"
  fi
}
dl 24 platform-24_r02.zip
dl 26 platform-26_r02.zip
dl 29 platform-29_r05.zip
echo "== 拉取完成 =="
