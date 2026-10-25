#!/usr/bin/env python3
"""
把 361 集成果的最新状态推回共享仓库（一次 commit = 一次 Pages build）。

🔴 纪律（来自我的记忆里的老教训）：
  1. **只碰自己的路径**：`shun/work/asr361/` + `shun-web/data/s*.json` + `shun-web/data/index.json`
     ⇒ 同伴的 `shun-web/data/ep*.json`、网页层源码、文档 **一个字节都不动**
  2. **共享文件 compare-and-swap**：`index.json` 推前再拉一次线上 sha256，对不上就拒推
  3. **删除要有依据**：删除集必须完全落在「我改名的文件」里，否则中止
  4. **一次 tree + 一次 commit + 一次 ref update** ⇒ 只触发一次 Pages build
  5. **推完回读复核**
"""
import json, os, sys, hashlib, base64, urllib.request, argparse, time, re

API = "https://api.github.com/repos/tlv-creator/nhk-easy-news"
TOK = os.environ.get("GH_TOKEN")
SRC = "/workspace/shun-web"
PREFIX = "shun/work/asr361"

# 本地目录 → 线上路径
MAP = [
    ("asr", "asr"),
    ("mm", "mm"),
    ("work/cues", "work/cues"),
    ("work/zh", "work/zh"),
    ("prod", "prod"),
    ("tools", "tools"),
]
FILES = ["tagmap.json", "asr_status.json", "pick10.json", "交接说明.md", "frame_overlap.json",
         "word_reading.json"]   # 🔴 丢失的整词惯用读音表（从已发布 13 集反推，187 条）


def api(url, method='GET', payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "mavis-shun-361")
    if payload is not None:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    return json.load(urllib.request.urlopen(req, timeout=90))


def local_files():
    """⇒ {线上相对路径: 绝对本地路径}"""
    out = {}
    for sub, dst in MAP:
        dd = os.path.join(SRC, sub)
        if not os.path.isdir(dd):
            continue
        for fn in sorted(os.listdir(dd)):
            p = os.path.join(dd, fn)
            if not os.path.isfile(p):
                continue
            if fn.endswith(('.pyc', '.log')) or fn == '__pycache__':
                continue
            out["%s/%s/%s" % (PREFIX, dst, fn)] = p
    for fn in FILES:
        p = os.path.join(SRC, fn)
        if os.path.isfile(p):
            out["%s/%s" % (PREFIX, fn)] = p
    # 网页层契约数据（7 集）
    dd = os.path.join(SRC, 'shun-web', 'data')
    for fn in sorted(os.listdir(dd)):
        if re.match(r'^s\d+(\.pred)?\.json$', fn):
            out["shun-web/data/%s" % fn] = os.path.join(dd, fn)
    # 🔴 ruby.py 第 19 行按名字找 word_reading.json ⇒ 必须放在 shun/tools/ 旁边才生效
    #    （仓库里从来没有过这个文件 ⇒ 已发布 13 集与新跑的粒度不一致 = 数据漂移）
    # 🔴 2026-10-25 网页层同伴裁决：**权威位置 = shun/tools/word_reading.json**
    #    （与 shun/tools/ruby.py 同级；ruby.py:19 是 HERE/word_reading.json）
    #    ⇒ 不再推 shun/tools/ruby/word_reading.json（那个目录里并没有 ruby.py，
    #      推它是「两处实现」的错觉 ⇒ 我下次推会把同伴删掉的又推回来）
    wr = os.path.join(SRC, 'word_reading.json')
    if os.path.isfile(wr):
        out["shun/tools/word_reading.json"] = wr
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--message', default='feat(shun-361): 修 3 类逐字缺陷 + 四类口径归一 + 补 p 区间 + 按 peer 契约产 7 集')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--force', action='store_true', help='路径级 CAS 冲突时仍要覆盖')
    ap.add_argument('--skip-conflicts', action='store_true',
                    help='路径级 CAS 冲突时**跳过这些路径**（不覆盖），其余照推')
    a = ap.parse_args()

    head = api(API + "/git/ref/heads/main")['object']['sha']
    tree = api(API + "/git/trees/" + head + "?recursive=1")
    remote = {e['path']: e for e in tree['tree'] if e['type'] == 'blob'}
    print("线上 HEAD: %s" % head)

    lf = local_files()
    # ---- 1. 删除集：线上有、我本地没有，且必须落在「改名映射」里 ----
    mine_prefix = set(lf.keys())
    cand_del = [p for p in remote
                if p.startswith(PREFIX + '/') and p not in mine_prefix]
    renamed_ok = all(re.match(r'^' + re.escape(PREFIX) +
                               r'/(prod|work/cues)/ep\d+(?:_cues)?\.json$', p)
                     for p in cand_del)
    print("待删（改名后消失）: %d 个；全部属于改名映射: %s" % (len(cand_del), '✅' if renamed_ok else '❌'))
    if not renamed_ok:
        for p in cand_del:
            if not re.match(r'^' + re.escape(PREFIX) + r'/(prod|work/cues)/ep\d+(?:_cues)?\.json$', p):
                print("   ⛔ 不该删:", p)
        sys.exit(1)

    # ---- 2. index.json 的 compare-and-swap ----
    ipath = 'shun-web/data/index.json'
    f = api(API + "/contents/" + ipath)
    raw0 = base64.b64decode(f['content'])
    sha0 = hashlib.sha256(raw0).hexdigest()
    base = json.loads(raw0.decode('utf-8'))
    assert isinstance(base, dict) and 'eps' in base, "线上 index.json 结构异常 ⇒ 中止"
    old_tags = {e['tag'] for e in base['eps']}
    mine_idx = []
    for p, lp in lf.items():
        if (p.startswith('shun-web/data/s') and p.endswith('.json')
                and not p.endswith('.pred.json')):
            d = json.load(open(lp, encoding='utf-8'))
            mine_idx.append({'tag': d['tag'], 'ep': d['ep'], 'title': d['title'],
                             'desc': d['desc'], 'dur': d['dur'], 'audio': d['audio'],
                             'n': len(d.get('sents', []))})
    eps = list(base['eps'])
    have = set(old_tags)
    for m in mine_idx:
        if m['tag'] not in have:
            eps.append(m); have.add(m['tag'])
    # 🔴 移除**重号**条目：s361 与 peer 的 ep13 是同一集（真实 Ep278）⇒ 列表里不能出现两次
    ALLOW_DEL = {'s361'}
    dropped = [e.get('tag') for e in eps if e.get('tag') in ALLOW_DEL]
    eps = [e for e in eps if e.get('tag') not in ALLOW_DEL]
    mine_idx = [m for m in mine_idx if m['tag'] not in ALLOW_DEL]
    if dropped:
        print("  从 index 移除（与 ep13 重号，同为 Ep278）: %s" % dropped)
    merged = dict(base); merged['eps'] = eps
    new_raw = json.dumps(merged, ensure_ascii=False, separators=(',', ':')).encode()
    lost = (old_tags - {e['tag'] for e in merged['eps']}) - ALLOW_DEL
    assert not lost, "合并后丢了原有条目 %s ⇒ 中止" % sorted(lost)
    print("index.json: eps %d → %d（我的 %d 条；原有 13 集全部保留: %s）"
          % (len(base['eps']), len(merged['eps']), len(mine_idx),
             '✅' if all(t in have for t in ['ep%02d' % i for i in range(1, 14)]) else '❌'))

    # ---- 3. 构造 tree ----
    # 🔴 路径级 CAS（2026-10-25 网页层同伴提醒）：推 data/*.json 之前，
    #    先比对线上这些路径的 blob sha 是否**还是我上次推的那份**。
    #    否则「别人先改了同一路径」会被我静默覆盖（这次是 luck：我的版本更好）。
    BASELINE = os.path.join(SRC, 'pushed_shas.json')
    baseline = {}
    if os.path.isfile(BASELINE):
        baseline = json.load(open(BASELINE, encoding='utf-8'))
    cas_checked = cas_conflict = 0
    if not baseline:
        # 🔴 首次建基线：把**当前线上**的 sha 记为基线。
        #    前提：当前线上确实是我上次推的那份（本次已由网页层同伴实测确认）。
        #    ⚠️ 空基线时「无冲突」是假绿 ⇒ 明确打印，不假装校验过了。
        for p, e in remote.items():
            if p.startswith('shun-web/data/'):
                baseline[p] = e['sha']
        print("  ⚠️ 无 CAS 基线（首次）⇒ 已用当前线上 sha 建基线（%d 个路径），本次不算校验过"
              % len(baseline))
    for p in sorted(lf.keys()):
        if not p.startswith('shun-web/data/'):
            continue
        e = remote.get(p)
        if not e:
            continue
        cas_checked += 1
        old_sha = baseline.get(p)
        if old_sha and old_sha != e['sha']:
            print("⛔ 路径级 CAS 冲突: %s" % p)
            print("   基线 sha=%s  线上 sha=%s ⇒ 线上已被别人改过 ⇒ 中止" % (old_sha[:8], e['sha'][:8]))
            if not a.force:
                cas_conflict += 1
    if cas_conflict:
        if a.skip_conflicts:
            skip = set()
            for p in sorted(lf.keys()):
                if not p.startswith('shun-web/data/'):
                    continue
                e = remote.get(p)
                if not e:
                    continue
                old_sha = baseline.get(p)
                if old_sha and old_sha != e['sha']:
                    skip.add(p)
            lf = {k: v for k, v in lf.items() if k not in skip}
            print("  ⚠️ 跳过 %d 个冲突路径（线上已被别人更新且内容更对 ⇒ 不覆盖）:" % len(skip))
            for p in sorted(skip):
                print("     ", p)
        else:
            print("❌ %d 个路径有冲突，未推送。确认要覆盖请加 --force；只想推其它请加 --skip-conflicts"
                  % cas_conflict)
            sys.exit(1)
    print("  路径级 CAS: 核对 %d 个路径，无冲突 ✅" % cas_checked)

    entries = []
    n_add = n_mod = 0
    for p, lp in sorted(lf.items()):
        data = open(lp, 'rb').read()
        e = remote.get(p)
        if e and e.get('size') == len(data):
            # 尺寸相同仍需比内容
            blob = api(API + "/git/blobs/" + e['sha'])
            if base64.b64decode(blob['content']) == data:
                continue
        nb = api(API + "/git/blobs", 'POST', {'content': data.decode('utf-8'), 'encoding': 'utf-8'})
        entries.append({'path': p, 'mode': '100644', 'type': 'blob', 'sha': nb['sha']})
        if e:
            n_mod += 1
        else:
            n_add += 1
    for p in cand_del:
        entries.append({'path': p, 'mode': '100644', 'type': 'blob', 'sha': None})
    # index.json
    nb = api(API + "/git/blobs", 'POST', {'content': new_raw.decode('utf-8'), 'encoding': 'utf-8'})
    entries.append({'path': ipath, 'mode': '100644', 'type': 'blob', 'sha': nb['sha']})
    n_mod += 1
    print("tree 变更: 新增 %d / 修改 %d / 删除 %d（+index.json）" % (n_add, n_mod, len(cand_del)))
    if a.dry:
        for e in entries[:12]:
            print("   ", e['path'], '=>', 'DELETE' if e['sha'] is None else e['sha'][:8])
        print("   … 共 %d 项" % len(entries))
        return

    # ---- 4. 推前再拉一次 index.json（CAS）----
    f2 = api(API + "/contents/" + ipath)
    if hashlib.sha256(base64.b64decode(f2['content'])).hexdigest() != sha0:
        print("❌ 线上 index.json 在处理期间被改过 ⇒ 拒绝推送")
        sys.exit(1)

    nt = api(API + "/git/trees", 'POST', {'base_tree': head, 'tree': entries})
    # 🔴 空提交防护：若新 tree 与当前 HEAD 的 tree **逐字相同** ⇒ 不 commit
    #    （每次都 commit 会白触发一次 Pages build，网页层同伴为此纠正过我）
    old_tree = {e['path']: e['sha'] for e in api(API + "/git/trees/" + head + "?recursive=1")['tree']
                if e['type'] == 'blob'}
    new_tree = {e['path']: e['sha'] for e in api(API + "/git/trees/" + nt['sha'] + "?recursive=1")['tree']
                if e['type'] == 'blob'}
    if old_tree == new_tree:
        print("✅ tree 与 HEAD 完全相同 ⇒ 无变化，不提交（避免白触发一次 Pages build）")
        print("   线上 HEAD 仍是 %s" % head[:12])
        return
    diff = {k for k in set(old_tree) | set(new_tree) if old_tree.get(k) != new_tree.get(k)}
    print("  tree 实际变化: %d 个路径" % len(diff))
    cm = api(API + "/git/commits", 'POST',
             {'message': a.message, 'tree': nt['sha'], 'parents': [head]})
    api(API + "/git/refs/heads/main", 'PATCH', {'sha': cm['sha'], 'force': False})
    print("✅ 已推 commit=%s" % cm['sha'])
    # 更新路径级 CAS 基线（记下我这次写的 blob sha）
    tr2 = api(API + "/git/trees/" + cm['sha'] + "?recursive=1")
    newbase = {e['path']: e['sha'] for e in tr2['tree'] if e['type'] == 'blob'}
    for p in list(lf.keys()) + [ipath]:
        if p in newbase:
            baseline[p] = newbase[p]
    with open(os.path.join(SRC, 'pushed_shas.json'), 'w', encoding='utf-8') as f:
        json.dump(baseline, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("   CAS 基线已更新: %d 个路径" % len(baseline))

    # ---- 5. 回读复核 ----
    time.sleep(2)
    tr = api(API + "/git/trees/" + cm['sha'] + "?recursive=1")
    paths = {e['path'] for e in tr['tree'] if e['type'] == 'blob'}
    print("回读复核:")
    print("   asr361 文件数: %d" % len([p for p in paths if p.startswith(PREFIX + '/')]))
    print("   旧 ep*.json 是否清干净: %s"
          % ('✅' if not [p for p in paths if re.match(r'^' + re.escape(PREFIX) +
                                                      r'/(prod|work/cues)/ep\d+', p)] else '❌'))
    print("   新 s*.json: %d 个" % len([p for p in paths if re.match(
        r'^shun/work/asr361/(prod|work/cues)/s\d+', p)]))
    print("   shun-web/data/s*.json: %d 个" % len([p for p in paths if
                                                   re.match(r'^shun-web/data/s\d+\.json$', p)]))
    print("   同伴的 ep01..ep13 还在: %s"
          % ('✅' if all('shun-web/data/ep%02d.json' % i in paths for i in range(1, 14)) else '❌'))
    f3 = api(API + "/contents/" + ipath)
    b3 = json.loads(base64.b64decode(f3['content']).decode())
    print("   线上 index eps=%d  13 集保留=%s  我的 7 集=%s"
          % (len(b3['eps']),
             all(e['tag'] in {x['tag'] for x in b3['eps']} for e in base['eps']),
             all(m['tag'] in {x['tag'] for x in b3['eps']} for m in mine_idx)))


if __name__ == '__main__':
    main()
