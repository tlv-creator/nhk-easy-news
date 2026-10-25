#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线上站点实测（浏览器，用户视角）：抽屉 50 集 + 点集能加载 + 弹窗三段 + 0 JS 错误"""
import random, sys, time
from playwright.sync_api import sync_playwright

V = random.randint(100000, 999999)
URL = 'https://tlv-creator.github.io/nhk-easy-news/shun-web/?v=%d' % V
SINGLE = 'https://tlv-creator.github.io/nhk-easy-news/shun-text.html?v=%d' % V
CHROME = '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome'
fails = []
NP = [0]


def ck(name, cond, extra=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (('  ' + str(extra)) if extra else ''))
    if cond:
        NP[0] += 1
    else:
        fails.append(name)


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=CHROME,
                           args=['--ignore-certificate-errors', '--no-sandbox',
                                 '--autoplay-policy=no-user-gesture-required'])
    pg = b.new_page(viewport={'width': 412, 'height': 915})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('console', lambda m: errs.append('console:' + m.text) if m.type == 'error' else None)
    pg.goto(URL, wait_until='load')
    pg.wait_for_timeout(2500)

    print('【1】抽屉与集数')
    pg.click('#epBtn')
    pg.wait_for_timeout(900)
    items = pg.eval_on_selector_all('.eitem', 'els=>els.map(e=>e.innerText.replace(/\\n/g," "))')
    ck('抽屉 50 集', len(items) == 50, '实际 %d' % len(items))
    ck('第1集是 Ep1（Genki 基础）', 'Ep1' in items[0], items[0][:40])
    ck('第7集是教材课 L8', 'Ep8' in items[6], items[6][:40])
    ck('最后第50集是 Ep278', 'Ep278' in items[-1], items[-1][:40])
    stat = pg.evaluate("()=>({e:(document.querySelector('#statEps')||{}).textContent,"
                        "s:(document.querySelector('#statSents')||{}).textContent})")
    ck('footer 集数 50', stat.get('e') == '50', str(stat))
    ck('footer 句数 3618', stat.get('s') == '3618', str(stat))

    print('\n【2】点新增集能懒加载（新增集在第 15 集 = Ep41）')
    pg.evaluate("i=>document.querySelectorAll('.eitem')[14].click()", 14)
    pg.wait_for_timeout(2500)
    btn = pg.eval_on_selector('#epBtn', 'el=>el.innerText')
    ck('切到新增集', 'Ep41' in btn, btn)
    n = pg.locator('.cue').count()
    ck('新增集渲染出句子', n > 50, '= %d 句' % n)

    print('\n【3】弹窗三段 + 注音')
    pg.locator('.cue').first.click()
    pg.wait_for_timeout(900)
    ck('弹窗打开', pg.locator('#sheet').is_visible())
    h = pg.locator('#sheet').inner_text()
    ck('① 分层大框架', '分层大框架' in h)
    ck('② 逐块拆解', '逐块拆解' in h)
    ck('③ 主句骨架与修饰链', '主句骨架与修饰链' in h)
    ck('注音 ruby', pg.locator('#sheet .jp ruby').count() > 0,
        '= %d' % pg.locator('#sheet .jp ruby').count())
    ck('中文', pg.locator('#sheet .zh').count() > 0)

    print('\n【4】无 JS 错误')
    ck('0 pageerror / console.error', len(errs) == 0, '; '.join(errs[:3]))

    print('\n【5】单文件离线版')
    pg2 = b.new_page(viewport={'width': 412, 'height': 915})
    e2 = []
    pg2.on('pageerror', lambda e: e2.append(str(e)))
    pg2.goto(SINGLE, wait_until='load')
    pg2.wait_for_timeout(4000)
    ck('单文件版渲染出句子', pg2.locator('.cue').count() > 0,
        '= %d' % pg2.locator('.cue').count())
    n2 = pg2.evaluate("()=>window.__shun.EPS.length")
    ck('单文件版 50 集', n2 == 50, '= %d' % n2)
    ck('0 JS 错误', len(e2) == 0, '; '.join(e2[:3]))
    b.close()

print('\n' + '=' * 54)
print('通过 %d 项，失败 %d 项' % (NP[0], len(fails)))
for f in fails:
    print('  ❌', f)
sys.exit(1 if fails else 0)
