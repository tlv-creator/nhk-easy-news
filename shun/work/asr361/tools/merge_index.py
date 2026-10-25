#!/usr/bin/env python3
"""
合并 index.json（共享文件 ⇒ compare-and-swap，纪律：不整份覆盖别人的）

🔴 为什么要单独一个脚本：
   `index.json` 是 peer（网页层）的文件，线上结构是 `{"eps":[...]}`（13 集）。
   如果我按自己的假设写（哪怕只是「加几项」），就会：
     · 用错 key（`eps` vs `episodes`）⇒ 读到空 ⇒ **把人家的 13 集索引冲掉**
     · 用错顶层结构（list vs dict）⇒ 渲染器直接读不到
   ⇒ **唯一正确的做法：从线上拉当基底，只加自己的条目，原样保留别人的。**

流程：
  ① 拉线上 index.json（基���）→ 记 sha256
  ② 合并我的 7 集（按 tag 去重；同 tag 我的优先但要记录冲突）
  ③ 写本地 index.json（待推）
  ④ --push 时：再拉一次线上，比对 sha256 是否变化
     · 变了 ⇒ **拒绝推送**（别人改过了）⇒ 打印重拉命令
     · 没变 ⇒ 用 Git Data API（blob→tree→commit→ref，**一次提交 = 一次 Pages build**）推
  ⑤ 推完回读复核
"""
import json, os, sys, hashlib, base64, urllib.request, urllib.parse, argparse, time

API = "https://api.github.com/repos/tlv-creator/nhk-easy-news"
OWNER, REPO = "tlv-creator", "nhk-easy-news"
MY_PREFIX = 's'          # 我的 tag 前缀（s001..s361）⇒ 避免和 peer 的 ep01..ep13 排序混
D = '/workspace/shun-web/shun-web/data'
OUT = os.path.join(D, 'index.json')
TOK = os.environ.get('GH_TOKEN')


def api(url, method='GET', payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "mavis-shun-361")
    if payload is not None:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    return json.load(urllib.request.urlopen(req, timeout=60))


def fetch_index():
    """返回 (dict, sha256hex) —— sha256 是内容指纹（不是 blob sha）"""
    f = api(API + "/contents/shun-web/data/index.json")
    raw = base64.b64decode(f['content'])
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


def my_entries():
    """从本地 s*.json 抽索引条目（字段与 peer 一致）"""
    out = []
    for fn in sorted(os.listdir(D)):
        if not (fn.startswith(MY_PREFIX) and fn.endswith('.json')):
            continue
        d = json.load(open(os.path.join(D, fn), encoding='utf-8'))
        out.append({'tag': d['tag'], 'ep': d['ep'], 'title': d['title'],
                    'desc': d['desc'], 'dur': d['dur'], 'audio': d['audio'],
                    'n': len(d.get('sents', []))})
    return out


def merge(base, mine):
    """基底的 eps 原样保留 + 我的追加（按 tag 去重）"""
    if not isinstance(base, dict) or 'eps' not in base:
        raise SystemExit("❌ 线上 index.json 结构不是 {eps:[...]}（实际 %r）⇒ 停下问人，别猜"
                         % (list(base) if isinstance(base, dict) else type(base).__name__))
    eps = list(base['eps'])
    have = {e.get('tag') for e in eps}
    added = 0
    for m in mine:
        if m['tag'] in have:
            continue
        eps.append(m)
        have.add(m['tag'])
        added += 1
    base = dict(base)
    base['eps'] = eps
    return base, added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--message', default='feat(shun-361): 7 集按 peer 契约的 sents/gs 数据 + index 合并')
    a = ap.parse_args()

    base, sha0 = fetch_index()
    print("线上 index.json: eps=%d  sha256=%s" % (len(base['eps']), sha0[:16]))
    mine = my_entries()
    print("我的条目: %d（%s ...）" % (len(mine), ', '.join(e['tag'] for e in mine)))
    merged, added = merge(base, mine)
    print("合并后: eps=%d（新增 %d）" % (len(merged['eps']), added))
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, separators=(',', ':'))
    print("已写本地: %s" % OUT)
    if not a.push:
        print("（--push 未加 ⇒ 只写本地，不推）")
        return

    # 🔴 compare-and-swap：推前再拉一次，别人改过就拒
    base2, sha1 = fetch_index()
    if sha1 != sha0:
        print("❌ 线上 index.json 在我们处理期间被改过（%s → %s）⇒ 拒绝推送"
              % (sha0[:16], sha1[:16]))
        sys.exit(1)
    ref = api(API + "/git/ref/heads/main")
    head = ref['object']['sha']
    tree = api(API + "/git/trees/" + head + "?recursive=1")
    # 找 index.json 的 blob sha
    blob_sha = None
    for e in tree['tree']:
        if e['path'] == 'shun-web/data/index.json' and e['type'] == 'blob':
            blob_sha = e['sha']
    if not blob_sha:
        print("❌ 线上找不到 shun-web/data/index.json ⇒ 停下")
        sys.exit(1)
    # 校验：我们要推的内容必须**只比线上多**，不能少
    old_tags = {e['tag'] for e in base['eps']}
    new_tags = {e['tag'] for e in merged['eps']}
    lost = old_tags - new_tags
    if lost:
        print("❌ 合并后丢了原有条目 %s ⇒ 拒绝推送" % sorted(lost))
        sys.exit(1)
    content = open(OUT, 'rb').read()
    nb = api(API + "/git/blobs", 'POST', {'content': content.decode('utf-8'),
                                          'encoding': 'utf-8'})
    nt = api(API + "/git/trees", 'POST',
             {'base_tree': head, 'tree': [{'path': 'shun-web/data/index.json',
                                           'mode': '100644', 'type': 'blob',
                                           'sha': nb['sha']}]})
    cm = api(API + "/git/commits", 'POST',
             {'message': a.message, 'tree': nt['sha'], 'parents': [head]})
    api(API + "/git/refs/heads/main", 'PATCH', {'sha': cm['sha'], 'force': False})
    print("✅ 已推 commit=%s" % cm['sha'])
    # 回读复核
    time.sleep(1.5)
    f = api(API + "/contents/shun-web/data/index.json")
    back = json.loads(base64.b64decode(f['content']).decode())
    print("回读: eps=%d  原 13 集还在=%s  我的 %d 集都在=%s"
          % (len(back['eps']),
             all(t in {e['tag'] for e in back['eps']} for t in
                 ['ep%02d' % i for i in range(1, 14)]),
             all(e['tag'] in {x['tag'] for x in back['eps']} for e in mine),
             ))


if __name__ == '__main__':
    main()
