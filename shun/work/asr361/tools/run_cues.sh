#!/bin/bash
cd /workspace/shun-web
python3 - <<'PY' > /tmp/all_ok.txt
import json
d=json.load(open('/workspace/shun-web/asr_status.json'))
print('\n'.join(d['ok']))
PY
n=0
while read -r uid; do
  [ -z "$uid" ] && continue
  tag=$(python3 -c "
import json
for r in json.load(open('/workspace/shun-web/tagmap.json')):
    if r['uid']=='$uid': print(r['tag'])")
  [ -z "$tag" ] && continue
  out=work/cues/${tag}_cues.json
  [ -s "$out" ] && continue
  if python3 /workspace/nhk-easy-news/shun/tools/build_cues.py --asr asr/$uid.json --mm mm/$uid.json --out $out 2>/dev/null; then
    c=$(python3 -c "import json;print(len(json.load(open('$out')).get('cues',[])))")
    n=$((n+1)); echo "  $tag cues=$c"
  else
    echo "  $tag FAIL"
  fi
done < /tmp/all_ok.txt
echo "=== 切句完成 $n 集 ==="
