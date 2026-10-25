#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
50 集 · 浏览器层验收（Playwright + Chromium）
验证渲染层在新数据上没崩、没退化：
  ① 站点版按集懒加载（点第 20 集能取到数据）
  ② 分层行渲染 + 四类括号都在
  ③ 相邻同类型块没被合并（块数 == frame 数）
  ④ 点句 → 底部大弹窗，三段都在
  ⑤ 墨水屏（黑白）下四类仍可区分
  ⑥ 0 JS 错误
  ⑦ 中文与注音都渲染出来
"""
import json, os, pathlib, subprocess, sys, time
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path('/workspace/shun')
SITE = ROOT / 'site-web-50'
SINGLE = ROOT / 'output' / 'Japanese-with-Shun-Text-50.html'
CHROME = '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome'
SHOTS = str(ROOT / 'shots')
os.makedirs(SHOTS, exist_ok=True)
ok = 0
fail = []


def chk(c, n, x=''):
    global ok
    if c:
        ok += 1
        print('  ✅ %s %s' % (n, x))
    else:
        fail.append(n + ' ' + x)
        print('  ❌ %s %s' % (n, x))


# 起本地静态服务（站点版要 fetch，file:// 会被 CORS 拦）
PORT = 8899
srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '-d', str(SITE)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
BASE = 'http://127.0.0.1:%d/index.html' % PORT

try:
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME,
                               args=['--no-sandbox', '--ignore-certificate-errors',
                                     '--autoplay-policy=no-user-gesture-required'])
        pg = br.new_page(viewport={'width': 412, 'height': 915}, device_scale_factor=2)
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.on('console', lambda m: errs.append('console:' + m.text) if m.type == 'error' else None)
        pg.goto(BASE)
        pg.wait_for_timeout(1500)

        print('\n【1】索引与懒加载')
        idx = json.load(open(SITE / 'data' / 'index.json', encoding='utf-8'))
        chk(len(idx['eps']) == 50, 'index 50 集', '= %d' % len(idx['eps']))
        reals = [e['realEp'] for e in idx['eps']]
        chk(reals == sorted(reals), 'realEp 升序')
        chk(len(set(e['tag'] for e in idx['eps'])) == 50, 'tag 唯一')
        # 点第 20 集（新增集）触发懒加载
        pg.evaluate("""() => {
          const i = window.__shun.EPS.findIndex(x => x.tag === 'ep041');
          window.__shun.S.ep = i; window.__shun.renderEp();
        }""")
        pg.wait_for_timeout(2000)
        n_cue = pg.locator('.cue').count()
        chk(n_cue > 0, '新增集能懒加载出句子', '= %d 句' % n_cue)
        chk(pg.locator('#epBtn').inner_text().startswith('第'), '集按钮有显示',
            repr(pg.locator('#epBtn').inner_text()))

        print('\n【2】分层行 + 四类括号')
        chk(pg.locator('.frame').count() > 0, '分层行存在', '= %d 行' % pg.locator('.frame').count())
        chk(pg.locator('.frame .fb').count() > 0, '成分块存在', '= %d' % pg.locator('.frame .fb').count())
        kinds = pg.evaluate("""() => {
          const c = {};
          document.querySelectorAll('.frame .fb').forEach(el => {
            const o = el.textContent.trim().charAt(0);
            c[o] = (c[o] || 0) + 1;
          });
          return c;
        }""")
        print('     括号分布:', json.dumps(kinds, ensure_ascii=False))
        for o, nm in [('[', '半角 [ ] 连接'), ('［', '全角 ［ ］ 从句'),
                      ('〈', '〈 〉 宾语'), ('（', '（ ） 谓语')]:
            chk(kinds.get(o, 0) > 0, '出现 %s' % nm, '= %d' % kinds.get(o, 0))
        pair = pg.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('.frame').forEach((f, k) => {
            const fb = f.querySelectorAll('.fb').length, fl = f.querySelectorAll('.fl').length;
            if (fb !== fl) bad.push(k + ':' + fb + '/' + fl);
          });
          return bad;
        }""")
        chk(len(pair) == 0, '每行「块数 == 角色标签数」', str(pair[:3]))

        print('\n【3】点句 → 底部大弹窗')
        pg.locator('.cue').first.click()
        pg.wait_for_timeout(700)
        chk(pg.locator('#sheet').is_visible(), '弹窗打开')
        h = pg.locator('#sheet').inner_text()
        chk('分层大框架' in h, '① 分层大框架')
        chk('逐块拆解' in h, '② 逐块拆解')
        chk('主句骨架与修饰链' in h, '③ 主句骨架与修饰链')
        chk(pg.locator('#sheet .sh-part').count() > 0, '成分说明列表',
            '= %d 条' % pg.locator('#sheet .sh-part').count())
        box = pg.evaluate("() => { const r = document.querySelector('#sheet').getBoundingClientRect();"
                          "return {h: r.height, vh: window.innerHeight}; }")
        chk(box['h'] > box['vh'] * 0.5, '弹窗占屏足够大', '= %.0f/%d' % (box['h'], box['vh']))
        # 中文 + 注音
        chk(pg.locator('#sheet .jp ruby').count() > 0, '注音 ruby 渲染',
            '= %d' % pg.locator('#sheet .jp ruby').count())
        pg.screenshot(path=SHOTS + '/50-sheet.png')
        pg.locator('#sheetClose').click()
        pg.wait_for_timeout(300)

        print('\n【3.5】横向溢出（墨水屏 412px：长块必须换行，不能被截断）')
        over = pg.evaluate("""() => {
          const bad = {over: 0, tight: 0, mis: 0, total: 0};
          document.querySelectorAll('.frame').forEach(f => {
            bad.total++;
            if (f.classList.contains('tight')) bad.tight++;
            const fbs = f.querySelectorAll('.fb');
            for (let k=0;k<fbs.length;k++){
              if (fbs[k].getBoundingClientRect().right > window.innerWidth + 1){ bad.over++; break; }
            }
            const fls = f.querySelectorAll('.fl');
            for (let i=0;i<Math.min(fbs.length,fls.length);i++){
              const a=fbs[i].getBoundingClientRect(), c=fls[i].getBoundingClientRect();
              if (Math.abs((a.left+a.width/2)-(c.left+c.width/2)) > 2) bad.mis++;
            }
          });
          return bad;
        }""")
        print('     ', json.dumps(over))
        chk(over['over'] == 0, '主区无横向溢出（块被换行，不截断）', '溢出 %d 行' % over['over'])
        chk(over['mis'] == 0, '角色标签仍水平居中于对应块', '错位 %d' % over['mis'])
        chk(not pg.evaluate("()=>document.documentElement.scrollWidth>window.innerWidth+1"),
            '页面无横向滚动条')
        # 弹窗内同样
        pg.locator('.cue').first.click(); pg.wait_for_timeout(700)
        sh = pg.evaluate("""() => {
          const s=document.getElementById('sheet'), lim=s.getBoundingClientRect().right;
          let over=0;
          document.querySelectorAll('#sheet .frame').forEach(f=>{
            const fb=f.querySelectorAll('.fb');
            for(let k=0;k<fb.length;k++){ if(fb[k].getBoundingClientRect().right>lim-2){over++;break;} }
          });
          return over;
        }""")
        chk(sh == 0, '弹窗内无横向溢出', '溢出 %d' % sh)
        pg.locator('#sheetClose').click(); pg.wait_for_timeout(300)

        print('\n【4】墨水屏（黑白）下四类仍可区分')
        pg.click('#btnK')
        pg.wait_for_timeout(500)
        sty = pg.evaluate("""() => {
          const get = (cls) => { const e = document.querySelector('.frame .fb.'+cls); if(!e) return null;
            const s = getComputedStyle(e);
            return {deco: s.textDecorationLine, bt: s.borderTopWidth, fw: s.fontWeight}; };
          return {b2: get('b2'), b3: get('b3'), b4: get('b4')};
        }""")
        print('     黑白样式:', json.dumps(sty))
        chk(sty['b2'] and 'underline' in (sty['b2']['deco'] or ''), '从句块有下划线')
        chk(sty['b3'] and sty['b3']['bt'] not in (None, '0px'), '宾语块有上边线')
        chk(sty['b4'] and int(sty['b4']['fw'] or 400) >= 600, '谓语块加粗')
        pg.screenshot(path=SHOTS + '/51-bw-50.png')
        pg.click('#btnK')
        pg.wait_for_timeout(300)

        print('\n【5】单文件离线版（file:// 断网可读）')
        pg2 = br.new_page(viewport={'width': 412, 'height': 915})
        errs2 = []
        pg2.on('pageerror', lambda e: errs2.append(str(e)))
        pg2.goto(SINGLE.as_uri())
        pg2.wait_for_timeout(2500)
        chk(pg2.locator('.cue').count() > 0, '单文件版渲染出句子',
            '= %d' % pg2.locator('.cue').count())
        # ⚠️ 打开时默认在第 1 集 ⇒ 断言「集数是 50」，不是「第50集」
        n_eps = pg2.evaluate("()=>window.__shun.EPS.length")
        chk(n_eps == 50, '单文件版内联 50 集', '= %d' % n_eps)
        chk(pg2.locator('#epBtn').inner_text().startswith('第1集'), '默认显示第1集',
            repr(pg2.locator('#epBtn').inner_text()))
        chk(len(errs2) == 0, '单文件版 0 JS 错误', '; '.join(errs2[:3]))
        pg2.screenshot(path=SHOTS + '/52-single-50.png')

        print('\n【6】无 JS 错误（站点版）')
        chk(len(errs) == 0, '0 pageerror / console.error', '; '.join(errs[:3]))
        br.close()
finally:
    srv.terminate()

print('\n' + '=' * 54)
print('通过 %d 项，失败 %d 项' % (ok, len(fail)))
for f in fail:
    print('  ❌', f)
sys.exit(1 if fail else 0)
