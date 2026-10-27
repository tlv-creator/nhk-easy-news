#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**删站前的完整引用扫描**（四层）—— 这是 2026-10-23 一次真事故逼出来的。

🔴🔴 起因：删了 `9chr6us9mlatt`（NHK v1.13 的站），
   它在 **NHK App 的 classes.dex 里是写死的兜底域名** ⇒ 删完 = 那个 App 的安全网没了。
   而 grep 源码**根本找不到**（它是**编译进去**的）。
🔴 又起因：删了 `lnh1i27d4m3vn`（**Tadoku 的正式站**）—— 而那个地址**就写在我的记忆里**
   ⇒ 第 4 层「对话/记忆里发出去的链接」我根本没扫。

⇒ **四层引用**：
   ① 代码里的 URL（links.js / cdn.json / assets/*.js）
   ② 引导页 / 文档 / README
   ③ 🔴 **APK 的 classes.dex 里写死的域名**（最容易被忘：编译进去的，grep 源码找不到）
   ④ 🔴 **对话/记忆里发出去的链接**（用户手里拿着的那一份）

用法：
    python3 scripts/check_dead_refs.py --dead 9chr6us9mlatt lnh1i27d4m3vn
    python3 scripts/check_dead_refs.py --dead <域...> --apk /path/to.apk [...]
"""
import argparse
import glob
import json
import os
import re
import urllib.request
import zipfile

# 🔴🔴 永久「不可删清单」= **唯一权威**（跨会话共同事实）
#   为什么要放永久地址：mcode 域名每次发版都变，GitHub Pages 不会
#   ⇒ 它是唯一「两个 agent 都能 curl 到」的地方
PROTECTED_URL = 'https://tlv-creator.github.io/nhk-easy-news/protected-sites.json'

SCAN_ROOTS = [
    '/workspace/.mavis',          # 记忆 + 历史对话（工具输出）
    '/workspace/scripts',
    '/workspace/audio-config',
    '/workspace/apk-tools',
    '/workspace/eink-gate',
    '/workspace/ai-client',
    '/workspace/guide',
    '/workspace/deploy-site',
]
TEXT_EXT = ('.md', '.py', '.js', '.json', '.txt', '.sh', '.html')
# 🔴🔴 **编译产物不扫**（实测踩到）：
#   `scripts/__pycache__/check_dead_refs.cpython-311.pyc` 里**二进制**存着源文件里的字符串
#   ⇒ 剥不掉注释（注释也进了 .pyc）⇒ 扫到它 = **假红灯**
#   ⇒ 而且 .pyc 是**构建产物**，不是「有人在用的依赖」⇒ 不该参与判据
SKIP_DIRS = {'__pycache__', 'node_modules', '.git', '.mavis', 'work', 'output'}
SKIP_EXT = ('.pyc', '.pyo', '.so', '.o', '.a', '.class', '.jar', '.apk', '.zip', '.dex')
# 注释里出现的域名（无害，但「看起来像引用」→ 让人紧张）
COMMENT_RE = re.compile(r'^\s*(//|\*|<!--|#)')


def scan_texts(dead):
    """扫代码/文档/记忆层 —— **🔴 必须分两类**（pony 2026-10-23 指出同一个 bug 的另一半）

    ① **代码/文档层**（`/workspace/scripts`、`/workspace/guide` …）
       ⇒ 命中 = **活依赖**（真在用）⇒ **必须 exit 1**
    ② **记忆/历史层**（`/workspace/.mavis` = 历史对话 + 工具输出）
       ⇒ 命中 = **只是「提过一次」** ⇒ **不该拦**（exit 0，但要提示）
    ⇒ 混在一起判 = 「历史对话里提过就拦住删」= 假红灯；
       反过来「代码里在用也放过」= 假绿灯。**两边都得能区分。**
    """
    code_hits = {}      # 代码/文档层 ⇒ 活依赖
    mem_hits = {}       # 记忆/历史层 ⇒ 只是提过
    files = set()
    for r in SCAN_ROOTS:
        if not os.path.isdir(r):
            continue
        for dp, dn, fn in os.walk(r):
            # 🔴 跳过编译产物目录
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if f.endswith(TEXT_EXT) and not f.endswith(SKIP_EXT):
                    files.add(os.path.join(dp, f))
    for p in glob.glob('/workspace/**/MEMORY.md', recursive=True):
        files.add(p)
    # 🔴🔴 **必须排除扫描器自己**（pony 2026-10-23 抓到的自指假阳性）：
    #    本文件的文档字符串里就写着示例域名 → grep 到自己 → 「自己引用自己」
    #    ⇒ 通则：**任何扫描器的样本都住在它自己的文件里 ⇒ 扫描器必须排除自己**
    #    （与「判据要排除『上一次的那个东西』」同族：要排除的名单里得包括「我脚下的地板」）
    # 🔴🔴 **必须用 realpath，不能用 abspath** —— `/workspace` 是 **NAS 挂载**
    #    （`/workspace` → `/run/csi/mount-root/nas/<id>`）：
    #    `os.walk` 返回的是**已解析**的真实路径，而 `__file__` 是**未解析**的
    #    ⇒ 两者字符串不同 ⇒ 「排除自己」**静默失效**（我第一版就犯了这个）
    #    ⇒ 教训：**路径比较一律用 realpath**（NAS / 符号链接 / 挂载点都会咬人）。
    me = os.path.realpath(__file__)
    for f in files:
        if os.path.realpath(f) == me:
            continue
        try:
            # 🔴🔴 **代码层也要剥注释**（与 APK 层判据一致 —— 同一个概念，两个层）
            #   不剥的后果：本扫描器**自己的注释里**写着示例域名（如 `u8tpuxfku1xoi`）
            #   ⇒ 跑异地副本时会把自己报成「代码层活依赖」= **假红灯**
            #   🔴 而且这说明「本地跑通」可能是**巧合** —— 排除自己用的是 `__file__`，
            #      跑仓库版时排除的是 /tmp 那份，/workspace 这份反而被扫到
            s = strip_comments(open(f, encoding='utf-8', errors='replace').read())
        except Exception:
            continue
        # 🔴 分类：**记忆/历史层**单独算（`/workspace/.mavis` = 历史对话与工具输出）
        is_mem = ('/.mavis/' in f) or f.endswith('/MEMORY.md')
        for d in dead:
            if d in s:
                (mem_hits if is_mem else code_hits).setdefault(d, []).append(f)
    return code_hits, mem_hits


def strip_comments(text):
    """🔴 剥掉注释 —— **注释里出现的域不是依赖**（pony 2026-10-23 实测抓到）

    🔴🔴 **同一个概念，两个层，判据必须一致**：
      代码层我分了「memory / code」，**但 APK 层没分** ⇒
      `assets/shell/shell.css` 第 4 行的注释
      `/* 模板来源：魔卡少女樱 v2.10（https://u8tpuxfku1xoi.space.mcode.cn） */`
      被判成「活依赖」⇒ **假红灯**（那个域早就 404，它只是「来源说明」）

    ⇒ 做法抄墨水屏闸门那招：**等长剥注释**（换行保留、其余换空格）⇒ **行号/偏移不变**
    ⚠️ **行注释只认「行首的 //」** —— `https://` 里的 `//` **不能剥** ❌
    """
    text = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S)
    text = re.sub(r'<!--.*?-->', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S)
    text = re.sub(r'(?m)^[ \t]*//[^\n]*', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)
    # 🔴🔴 **必须覆盖 Python/shell 的 `#` 行注释** —— 漏了它就会漏掉
    #    本扫描器自己注释里的示例域名（实测：异地副本跑时自己被报成「代码层活依赖」）
    #    ⚠️ 只认**行首**的 `#` ⇒ URL 里的 `#fragment` 不会被误伤 ✅
    text = re.sub(r'(?m)^[ \t]*#[^\n]*', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)
    return text


def scan_dex(apk, dead):
    """③ APK 里写死的域名 —— **dex 和 assets 都要扫**（pony 2026-10-23 指出）

    🔴🔴 **dex 不够**：WebView 型 App（NHK / Shun / **hub** / 多読絵本）的
    「编译进去的依赖」在 **`assets/*.js` / `assets/*.html`** 里 ——
    **hub 的 13 个域全在 assets 里，dex 里一个都没有** ❌
    ⇒ 只扫 dex 会把 hub 判成「零依赖」⇒ **删掉它依赖的站它照样白屏** ✅

    🔴 **assets 层要剥注释**（注释里的域不是依赖）⇒ 见 `strip_comments`
    （**dex 不剥** —— 字节码里没有注释概念）
    """
    out = {}
    skipped = []
    try:
        z = zipfile.ZipFile(apk)
        names = z.namelist()
        blob = []
        if 'classes.dex' in names:
            blob.append(z.read('classes.dex').decode('latin-1'))
        # 🔴🔴 **assets 层不限后缀，全扫**（pony 2026-10-23）
        #   理由：白名单（*.js|html|json|txt|css）是「已知够了」的**乐观假设** ——
        #   万一依赖写在 .dat / .bin / 别的后缀里，就会漏
        #   ⇒ **宁可多扫**（APK 里的文本文件不多，成本可忽略）
        for n in names:
            if not n.startswith('assets/') or n.endswith('/'):
                continue
            try:
                info = z.getinfo(n)
                if info.file_size > 2 * 1024 * 1024:      # 单文件 >2MB 跳过（图片/大资源）
                    skipped.append('%s (%d B)' % (n, info.file_size))
                    continue
                # 🔴 assets 层要剥注释（注释里的域不是依赖 —— 见 strip_comments）
                blob.append(strip_comments(z.read(n).decode('utf-8', 'replace')))
            except Exception:
                pass
        alltext = '\n'.join(blob)
    except Exception as e:
        return {'__err__': ['%s: %s' % (apk, e)]}
    for d in dead:
        if d in alltext:
            out.setdefault(d, []).append(apk)
    allm = sorted(set(re.findall(r'[a-z0-9]{4,}\.space\.mcode\.cn', alltext)))
    if skipped:
        # 🔴 跳过的必须**说出来**（静默跳过 = 假装扫过了 = 假绿灯）
        print('      ⚠️ 跳过 %d 个 >2MB 的 assets 条目（未扫描）：%s' % (len(skipped), ', '.join(skipped[:3])))
    return out, allm


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


# 🔴 2026-10-26（跨会话修复）：清单里条目数组的键名有两个
#    · `sites`            —— **线上权威键**（2026-10-25 的 diff 证明老版本就是它）
#    · `protected_sites`  —— 老代码在用，但**线上从来就没有这个键**
#    ⇒ 只认旧键的老实现会读到**空列表**：
#      清理脚本的「冻结保护集」为空 ⇒ 可能误删现役站；
#      闸门则变成「校验代码在、校验对象是空的」⇒ **恒绿**。
#    ⇒ 两个键都认（收进词汇表，不是把线上的改回去）。
#    ⚠️ 两个都没有 ⇒ **报错不放行**（不能当成空清单，那正是恒绿的成因）。
def _sites_of(obj, strict=False):
    if not isinstance(obj, dict):
        if strict:
            raise ValueError('清单不是对象（实际 %s）' % type(obj).__name__)
        return []
    for _k in ('sites', 'protected_sites'):
        if isinstance(obj.get(_k), list):
            return obj[_k]
    if strict:
        raise ValueError('清单里既没有 sites 也没有 protected_sites 字段')
    return []


def _sites(obj):
    """清单里的 protected_sites：必须是数组、每条必须是对象。类型不对 ⇒ 抛错（调用方必须不放行）。"""
    lst = _sites_of(obj, strict=True)
    if not isinstance(lst, list):
        raise ValueError('清单条目不是数组（实际 %s）' % type(lst).__name__)
    for e in lst:
        if not isinstance(e, dict):
            raise ValueError('清单里有非对象条目：%r' % (e,))
    return lst


def _sites_safe(obj):
    """同上，但类型不对就返回空列表（用于「只做提示、不参与放行判定」的次要分支）。"""
    try:
        return _sites(obj)
    except ValueError:
        return []


def load_protected():
    """拉永久「不可删清单」—— **拉不到返回 None**（调用方必须拒绝放行）

    🔴 **不许「拉不到就当没事」** —— 那是把「查不到」当成「没有」，
    而今晚那两次误删正是这么来的 ⇒ **闸门找不到东西必须 FAIL**。
    """
    try:
        with urllib.request.urlopen(PROTECTED_URL, timeout=25) as r:
            d = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print('     （拉取失败：%s）' % e)
        return None
    try:
        _sites_l = _sites(d)
    except ValueError as _ve:
        print('     （清单结构不对：%s ⇒ 保护失效 ⇒ 调用方必须不放行）' % _ve)
        return None
    out = {}
    deleted = []
    for s in _sites_l:
        dom = (s.get('domain') or '').strip()
        if not dom:
            continue
        # 🔴🔴🔴 **已删的条目只是「历史记录」，不该再拦删**（hub pony 2026-10-23 抓出）
        #   `status: "DELETED"` 是**永久事实**（它死了）—— 但「死了」恰恰意味着
        #   「不需要保护」⇒ 否则清单会把自己的历史条目变成**永久黑名单**，
        #   **每删一个站就多一条**（当晚已积累 2 条：9chr6us9mlatt / lnh1i27d4m3vn）
        if str(s.get('status') or '').strip().upper() == 'DELETED':
            deleted.append(dom)
            continue
        out[dom] = s
    print('     清单已载入：%d 条生效' % len(out))
    if deleted:
        # 🔴 **排除必须显式打印**（静默排除 = 又一个看不见的地方）
        print('     ⚠️ 另有 %d 条已标 DELETED（历史记录，不参与保护）：%s'
              % (len(deleted), ' '.join(deleted)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dead', nargs='+', required=True, help='要删的域名（不含 .space.mcode.cn）')
    ap.add_argument('--apk', nargs='*', default=[], help='要扫的 APK 路径')
    ap.add_argument('--root', default='/workspace', help='扫源码的根')
    ap.add_argument('--protected', action='store_true',
                    help='自动拉永久「不可删清单」，命中即拒绝删（把清单从「文档」变成「闸门」）')
    a = ap.parse_args()
    dead = a.dead

    # 🔴🔴 ⓪ 闸门：永久清单命中 → **直接拒绝删**，不必扫别的
    #   依据 pony 2026-10-23 的建议：**不要靠人记得去查清单**
    if a.protected:
        print('=== ⓪ 永久「不可删清单」===')
        prot = load_protected()
        if prot is None:
            print('  🔴 拉不到永久清单 ⇒ **不放行**（不许「拉不到就当没事」）')
            return 2
        # 🔴 如果要删的域在清单里但已标 DELETED ⇒ 明确告诉对方「它已经不受保护」
        #    （别让人以为还被保护着）
        try:
            _pj = json.loads(urllib.request.urlopen(PROTECTED_URL, timeout=25).read().decode('utf-8'))
        except Exception:
            _pj = {}
        for s in _sites_safe(_pj):
            dom = (s.get('domain') or '').strip()
            if dom in dead and str(s.get('status') or '').strip().upper() == 'DELETED':
                print('  ℹ️ %s 在清单里但已标 DELETED ⇒ 不受保护（可删）' % dom)
        blocked = [(d, prot[d]) for d in dead if d in prot]
        if blocked:
            for d, h in blocked:
                print('  🔴 %s' % d)
                print('      项目=%s' % h.get('project', '?'))
                print('      原因=%s' % h.get('reason', '?'))
                if h.get('note'):
                    print('      备注=%s' % h['note'])
            print('  ⇒ **不许删**。要删必须先更新清单并写明理由。')
            return 1
        print('  ✅ %d 个待查域都不在清单里' % len(dead))

    print('=== ① 代码/文档层（活依赖）===')
    code_hits, mem_hits = scan_texts(dead)
    for d, fs in sorted(code_hits.items()):
        print('  🔴 %s → %d 处' % (d, len(fs)))
        for f in fs[:5]:
            print('       %s' % f.replace(a.root + '/', ''))
    if not code_hits:
        print('  ✅ 零命中')

    print('\n=== ② 记忆/历史层（只是「提过一次」，不作为拦截依据）===')
    for d, fs in sorted(mem_hits.items()):
        print('  ⚠️  %s → %d 处（历史对话/记忆里提过）' % (d, len(fs)))
        for f in fs[:3]:
            print('       %s' % f.replace(a.root + '/', ''))
    if not mem_hits:
        print('  ✅ 零命中')

    # 🔴🔴 **dex/assets 命中 = 活依赖 ⇒ 必须 exit 1**
    #   （pony 2026-10-23 实测抓到的真 bug：原来只打印「🔴 指向待删站」然后 exit 0
    #    ⇒ **判到了却没挡住** —— 违反「判据不满足 → 宁可报错，也不要悄悄用错的」）
    dex_blocked = False
    if a.apk:
        print('\n=== ③ APK 层（dex + assets）===')
        for apk in a.apk:
            r = scan_dex(apk, dead)
            if isinstance(r, dict) and '__err__' in r:
                print('  ⚠️  %s' % r['__err__'][0])
                continue
            hit, allm = r
            print('  --- %s' % apk)
            print('      APK 里的 mcode 域: %s' % (', '.join(allm) or '(无)'))
            if hit:
                dex_blocked = True
                for d, aps in hit.items():
                    print('      🔴 指向待删站: %s  ⇒ **不能删**（编译进去了）' % d)
            else:
                print('      ✅ 不指向任何待删站')

    print('\n=== 判据 ===')
    # 🔴🔴 **判据必须分层**（pony 2026-10-23 抓到同一个 bug 的两半）
    #   ① APK（dex/assets）命中 → **活依赖** ⇒ exit 1
    #   ② 代码/文档层命中        → **活依赖** ⇒ exit 1
    #   ③ 仅记忆/历史层命中      → 只是「提过一次」⇒ **不该拦** ⇒ exit 0（但要提示）
    if dex_blocked:
        print('  🔴 APK（dex/assets）里命中 ⇒ **活依赖，不能删**')
        return 1
    if code_hits:
        print('  🔴 代码/文档层命中 ⇒ **活依赖，不能删**')
        print('     · 在引导页/links.js/源码里 → 🔴 **不能删**')
        return 1
    if mem_hits:
        print('  ⚠️  仅记忆/历史层有命中（历史对话里提过）⇒ **不作为拦截依据**')
        print('     · 若确认要删，请同步更新清单与历史记录')
        return 0
    if a.apk:
        print('  ✅ 全层零命中（APK 也不指向任何待查域）')
        return 0
    print('  ✅ 全层零命中')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
