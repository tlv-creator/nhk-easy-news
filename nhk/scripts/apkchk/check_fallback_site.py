#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兜底站内容闸门 —— 扫**最终 APK 产物**的 dex。

🔴🔴 这道闸门是 2026-10-23 一次真事故逼出来的：
   交付给用户的 v1.12 里，dex 写死的是 `https://PLACEHOLDER/`，
   原因是 build.sh 用「默认值」静默兜住了没传的 SITE_URL → **不报错**。
   ⇒ 引导页（GitHub Pages）读得到就正常；读不到（网络/抽风）→ App 直接白屏。
   ⇒ 而那时我唯一的真兜底站还被 cleanup 脚本当成「旧站」准备删掉。

判据（两条都要满足，缺一即 exit 1）：
  ① dex 里必须出现至少一个 <域名>.space.mcode.cn —— 证明写进去的是真站
  ② dex 里不得出现 PLACEHOLDER —— 证明占位符没混进产物

为什么要在「出包后」再验一次：
  Config.java 是**源码**，会被 sed 改、会被手工还原；
  **dex 才是用户手机上真正跑的东西** —— 它才是 ground truth。

用法：
  python3 check_fallback_site.py <apk> [--expect <域名>] [--allow-no-site]

  --expect <域名>   期望的兜底站（URL / 裸域名 / 带路径都吃）
  --allow-no-site   资产型项目模式：页面在 APK assets 里、不依赖站
                    → 不要求 dex 里有 mcode 域名（但 PLACEHOLDER 仍然拦）

退出码：0 = 通过；1 = 拒绝出包。
"""
import sys
import re
import zipfile

PLACEHOLDER = b'PLACEHOLDER'
DOMAIN_RE = re.compile(rb'([a-z0-9]{4,})\.space\.mcode\.cn')


def scan(apk_path):
    """返回 (domains, placeholder_count, dex_bytes)。读不出 dex 抛异常。"""
    with zipfile.ZipFile(apk_path) as zf:
        names = zf.namelist()
        if 'classes.dex' not in names:
            raise RuntimeError('APK 里没有 classes.dex（包结构不对？）')
        dex = zf.read('classes.dex')
    doms = sorted(set(m.decode() for m in DOMAIN_RE.findall(dex)))
    return doms, dex.count(PLACEHOLDER), dex


def main():
    argv = sys.argv[1:]
    apks = [a for a in argv if a.endswith('.apk')]
    expect = None
    if '--expect' in argv:
        i = argv.index('--expect')
        if i + 1 < len(argv):
            expect = argv[i + 1]
    # 🔴 资产型项目（网页打进 APK assets 自己加载，如 hub）不依赖任何站
    #    → 用这个开关跳过「必须有 mcode 域名」这条硬门槛（但 PLACEHOLDER 仍然拦）
    allow_no_site = '--allow-no-site' in argv

    if not apks:
        print('[fallback] ❌ 没给 APK —— 拒绝放行（不许「没东西可检」就当通过）')
        return 1

    apk = apks[0]
    try:
        doms, ph, dex = scan(apk)
    except Exception as e:
        print('[fallback] ❌ 读不出 dex：%s' % e)
        return 1

    print('[fallback] dex 里的站域名: %s ｜ PLACEHOLDER 出现 %d 次'
          % (doms or '(无)', ph))
    # 🔴「出现 1 次」比「出现过」更有力 —— 它同时证明「没漏」和「没多」。
    #   ⚠️ 但**判据要用「不同的站域名集合大小」，不是「字面量出现次数」**（pony 2026-10-23 指出）：
    #   同一个 host 的不同写法（带不带端口/斜杠）会分开计数 → 次数虚高 → 误报。
    #   本文件的 DOMAIN_RE 只取 host 部分 → set 去重后天然按「不同域名」计。
    if len(doms) > 1:
        print('[fallback] ⚠️  dex 里有 %d 个**不同**的站域名：%s' % (len(doms), doms))

    # 🔴 资产型项目（网页打进 APK assets 自己加载，如 hub）**不依赖任何站**
    #   → 不能要求「必须有 mcode 域名」，否则硬门槛会误伤。
    #   → 用 --allow-no-site 打开这个模式。
    if not doms:
        if allow_no_site:
            print('[fallback] ℹ️  dex 里没有站域名 —— 资产型项目（页面在 assets 里），符合预期')
            if ph:
                print('[fallback] ❌ dex 里还有 %d 处 PLACEHOLDER —— 占位符混进了产物' % ph)
                return 1
            print('[fallback] ✅ 兜底站内容闸门通过（资产型模式）')
            return 0
        print('[fallback] ❌ dex 里没有任何 mcode 站域名 —— APK 落不了地'
              '（引导页一挂就白屏）')
        return 1
    if ph:
        print('[fallback] ❌ dex 里还有 %d 处 PLACEHOLDER —— 占位符混进了产物' % ph)
        return 1
    if expect:
        # 🔴 expect 要同时接受「完整 URL」和「裸域名」两种写法 ——
        #    否则会出现「拦对了、但理由写错了」（域名 vs URL 不匹配）。
        #    规范化：取 host 小写。
        want = expect.strip().lower()
        if '://' in want:
            want = want.split('://', 1)[1].split('/', 1)[0]
        want = want.split('.')[0]
        if not any(want in d for d in doms):
            print('[fallback] ❌ 期望的兜底站 %s 不在 dex 里（实际: %s）'
                  % (want, doms))
            return 1
        print('[fallback] ✅ 期望的兜底站 %s 已确认' % want)

    print('[fallback] ✅ 兜底站内容闸门通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
