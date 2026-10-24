#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删站前的闸门：**扫引导页里每一个链接**，有打不开的就拒绝删站。

🔴 为什么要有这个（peer 2026-10-23 的建议，我同意并立即做了）：
   「先部署新站 → 验 → 再删旧站」这条纪律，一直靠**记得**扫引用。
   ⇒ 挂载点太弱（「记忆」是最不可靠的挂载点）。
   ⇒ 把「扫引用」做成**一条命令** = 提到「闸门」级。

🔴 判据：**每个链接都必须能打开**（200/3xx）。
   —— 链接打不开 = 引导页指向死链 = 用户点进来一脸懵 = **静默失败**。

用法：
    python3 scripts/check_guide_links.py                    # 扫线上引导页
    python3 scripts/check_guide_links.py --file guide/hub.html   # 扫本地文件
"""
import argparse
import re
import sys
import urllib.request

DEFAULT_URL = 'https://tlv-creator.github.io/nhk-easy-news/hub.html'
LINK_RE = re.compile(r'(?:href|src)="(https?://[^"]+)"')


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': 'mavis-link-check'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


# 🔴🔴🔴 **清单字段的「类型」也是判据**（2026-10-24 跨会话互抓后补）
#   JSON 里「看起来像布尔/数组」的字段最容易悄悄变形：
#     · "frozen": "false"（字符串）⇒ Python 真值为 True ⇒ **误判成「受保护」**
#     · protected_sites 写成 dict ⇒ 迭代出 key 字符串 ⇒ e.get 直接崩
#   ⇒ 读的时候**两种形态都认**；**类型不对就报错不放行**（宁可崩，不要默默当默认值）
def _flag(v):
    """布尔字段归一：bool / "1" / "true" / "yes" / 1 都算真；字符串 "false" 算假。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes', 'y', 'on')
    if isinstance(v, (int, float)):
        return bool(v)
    return False


def _sites(obj):
    """清单里的 protected_sites：必须是数组、每条必须是对象。类型不对 ⇒ 抛错（调用方必须不放行）。"""
    lst = obj.get('protected_sites') if isinstance(obj, dict) else None
    if not isinstance(lst, list):
        raise ValueError('protected_sites 不是数组（实际 %s）' % type(lst).__name__)
    for e in lst:
        if not isinstance(e, dict):
            raise ValueError('protected_sites 里有非对象条目：%r' % (e,))
    return lst


def _sites_safe(obj):
    """同上，但类型不对就返回空列表（用于「只做提示、不参与放行判定」的次要分支）。"""
    try:
        return _sites(obj)
    except ValueError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', help='扫本地 html（默认扫线上）')
    ap.add_argument('--url', default=DEFAULT_URL)
    a = ap.parse_args()

    if a.file:
        html = open(a.file, encoding='utf-8').read()
        src = a.file
    else:
        try:
            st, raw = fetch(a.url)
        except Exception as e:
            print('❌ 读不到引导页 %s：%s' % (a.url, e))
            return 1
        html = raw.decode('utf-8', 'replace')
        src = a.url

    links = sorted(set(LINK_RE.findall(html)))
    if not links:
        print('❌ 页面里一个链接都没有 —— 是不是页面读错了？拒绝放行')
        return 1

    # 🔴🔴🔴 **「链都能打开」≠「该有的都在」**
    #   （hub pony 2026-10-23：引导页该有 5 个链接，线上只有 4 个 ⇒ 全部 200 ⇒ 我放行了 ❌）
    #   ⇒ 少一个必需条目 = 用户点不到那个功能 = **静默失败**（链没坏，功能没了）
    #   ⇒ 闸门必须同时验两件事：**① 每个现存链能打开 ② 每个必需条目都在**
    REQUIRED = ('jpstudy-apk.zip', 'ai-proxy-code.zip',
                'ai-proxy-rollback.zip', 'audio-config.zip')
    missing = [r for r in REQUIRED if not re.search(
        r'href="[^"]*%s"' % re.escape(r), html)]
    if missing:
        print('\n❌ 缺少必需链接 %d 个：%s' % (len(missing), ' '.join(missing)))
        print('   ⇒ **不许放行**（链都通 ≠ 功能齐全）')
        return 1
    print('  ✅ 必需链接齐全：%s' % ' '.join(REQUIRED))

    # 🔴🔴🔴 **引导页指向的站，必须是清单里 frozen 的**（元层交叉验证）
    #   「今晚做的每一个保护，最后都要落到一个**别人也会去的地方**才算数」
    #   ⇒ 清单是权威的「能不能动」；引导页是权威的「现在用哪个」
    #   ⇒ 两边必须一致：引导页指着的站，**如果没被清单冻结**，
    #      就等于有人绕过了保护 —— 改个引导页就能让一个没保护的站上线
    #   ⇒ 「查不到就不能放行」：清单拉不到 ⇒ 拒绝
    # 🔴🔴 **「读到的旧内容」有两种可能：① 真被覆盖了 ② 显示端还没更新**
    #   （hub pony 2026-10-23 的第九例归纳，我抄进注释）
    #   ⇒ **复核必须回「源」**（仓库 API / raw），不能只看渲染端 ✅
    #   ⇒ GitHub Pages 会缓存旧版 ⇒ 同一 URL 连续几轮可能是不同版本
    #   （我今天在这上面栽过：清单前两轮是旧的、第三轮才是新的）
    #   ⇒ 「清单看起来不对」≠「清单真的不对」⇒ **拉多次取最新** ✅
    #
    # 🟢 **权威 ≠ 新鲜**（pony 补的半句，比我原句完整）：
    #   **每加一层，两个问题都要问：① 它比下面那层权威吗？② 它自己是最新的吗？**
    #   ⇒ 元层（清单）：权威 ✅ + 新鲜 ✅（现取）
    #   ⇒ 兜底模板：不是权威 ❌ + 可能不新鲜 ❌ ⇒ 只能当最后一道，且必须被校验 ✅
    import json as _json
    import time as _t
    _pl = None
    _err = None
    for _try in range(5):
        try:
            _req = urllib.request.Request(
                'https://tlv-creator.github.io/nhk-easy-news/protected-sites.json?nc=%d'
                % int(_t.time() * 1000),
                headers={'User-Agent': 'mavis-link-check', 'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(_req, timeout=25) as _r:
                _cand = _json.loads(_r.read().decode('utf-8'))
            if _pl is None or len(_cand.get('protected_sites', [])) >= len(_pl.get('protected_sites', [])):
                _pl = _cand
        except Exception as e:
            _err = e
        _t.sleep(2)
    if _pl is None:
        print('\n❌ 拉不到保护清单（%s）⇒ **不放行**（查不到就不能放行）' % _err)
        return 1
    try:
        _pl_sites = _sites(_pl)
    except ValueError as _ve:
        print('\n❌ 清单结构不对（%s）⇒ **不放行**（类型不对就不能当默认值）' % _ve)
        return 1
    _frozen = set()
    for _s in _pl_sites:
        if str(_s.get('status') or '').strip().upper() == 'DELETED':
            continue
        if _flag(_s.get('frozen')) or _s.get('role') == 'content':
            _frozen.add((_s.get('domain') or '').strip())
    _hosts = set()
    for _l in links:
        m = re.match(r'https?://([a-z0-9]+)\.space\.mcode\.cn', _l)
        if m:
            _hosts.add(m.group(1))      # 🔴 清单里存的是**裸域**（不带 .space.mcode.cn）
    _unprot = sorted(h for h in _hosts if h not in _frozen)
    if _unprot:
        print('\n❌ 引导页指向了**清单里没有冻结**的站：%s' % ' '.join(_unprot))
        print('   ⇒ **不放行**（改引导页就能绕过保护）')
        return 1
    print('  ✅ 引导页指向的 %d 个站都在清单冻结名单里：%s'
          % (len(_hosts), ' '.join(sorted(_hosts))))

    print('=== 扫 %s（%d 个链接）===' % (src, len(links)))
    bad = []
    for l in links:
        # 🔴 CDN 冷启动会 503 → 重试 3 次（别把冷启动当成死链）
        code = 0
        for i in range(3):
            try:
                req = urllib.request.Request(l, method='GET', headers={'User-Agent': 'mavis-link-check'})
                with urllib.request.urlopen(req, timeout=25) as r:
                    code = r.status
            except Exception as e:
                code = getattr(e, 'code', 0) or 0
            if code and code < 400:
                break
            import time
            time.sleep(2)
        ok = (code and code < 400)
        print('  %s %s  %s' % ('✅' if ok else '❌', code or 'ERR', l))
        if not ok:
            bad.append(l)

    if bad:
        print('\n❌ %d 个链接打不开 —— **不许删站**（引导页会指向死链）' % len(bad))
        for b in bad:
            print('   ', b)
        return 1
    print('\n✅ 全部链接可打开 —— 可以删站了')
    return 0


if __name__ == '__main__':
    sys.exit(main())
