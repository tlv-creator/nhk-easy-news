#!/bin/bash
# 批量下载 Shun 361 集音频（redcircle 直链）
DST=/workspace/shun-web/audio
LIST=/workspace/shun-web/urls.txt
mkdir -p "$DST"
n=0
while IFS=$'\t' read -r url dur date title; do
  id=$(echo "$url" | sed 's#.*/episodes/\([0-9a-f-]*\)/.*#\1#')
  out="$DST/$id.mp3"
  if [ -s "$out" ]; then n=$((n+1)); continue; fi
  for try in 1 2 3; do
    curl -sL --max-time 300 --connect-timeout 10 -A "Mozilla/5.0" -o "$out.part" "$url" && [ -s "$out.part" ] && mv "$out.part" "$out" && break
    sleep 3
  done
done < "$LIST"
echo "=== 下载完成，文件数: $(ls -1 $DST/*.mp3 2>/dev/null | wc -l) ==="
du -sh $DST
