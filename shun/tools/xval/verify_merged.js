/* 合并版端到端验证：19 集（13 集逐字 SHUN + peer 的 6 集） */
const { chromium } = require('/usr/local/lib/node_modules/playwright');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const FILE = 'file://' + path.resolve(__dirname, 'shun-grammar-merged.html');
const CHROME = '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';
const SRC = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html';

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--allow-file-access-from-files']
  });
  const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(FILE, { waitUntil: 'load', timeout: 120000 });
  await page.waitForTimeout(900);

  const R = {};
  R.eps = await page.evaluate(() => window.SHUN.eps.length + (window.SHUN2 ? window.SHUN2.eps.length : 0));
  R.sentsTotal = await page.evaluate(() => {
    const a = window.SHUN.eps, b = (window.SHUN2 && window.SHUN2.eps) || [];
    return a.concat(b).reduce((x, e) => x + e.sents.length, 0);
  });
  R.gsLen = await page.evaluate(() => window.SHUNG.length);
  R.predLen = await page.evaluate(() => window.SHUNP.length);
  R.epsOrder = await page.evaluate(() => {
    const E = window.SHUN.eps.concat((window.SHUN2 && window.SHUN2.eps) || []);
    const M = window.EPREAL || {};
    const real = e => (e.epReal != null) ? e.epReal : (M[e.tag] != null ? M[e.tag] : e.ep);
    return E.map(real).sort((a, b) => a - b);
  });
  R.dupEp = await page.evaluate(() => {
    const E = window.SHUN.eps.concat((window.SHUN2 && window.SHUN2.eps) || []);
    const M = window.EPREAL || {};
    const real = e => (e.epReal != null) ? e.epReal : (M[e.tag] != null ? M[e.tag] : e.ep);
    const v = E.map(real);
    return v.length - new Set(v).size;
  });
  R.blocksTotal = await page.evaluate(() => window.SHUNG.reduce((a, g) => a + ((g && g.blocks || []).length), 0));
  R.partsTotal = await page.evaluate(() => window.SHUNG.reduce((a, g) => a + ((g && g.blocks || []).reduce((x, b) => x + ((b.parts || []).length), 0)), 0));
  R.innerTotal = await page.evaluate(() => window.SHUNG.reduce((a, g) => a + ((g && g.blocks || []).filter(b => b.inner).length), 0));
  R.skelTotal = await page.evaluate(() => window.SHUNG.filter(g => g && g.skel).length);
  R.predWithChain = await page.evaluate(() => window.SHUNP.filter(p => p && p.p && p.p.length).length);
  R.mcTotal = await page.evaluate(() => window.SHUNP.filter(p => p && p.mc && p.mc.length).length);

  // 逐集渲染
  const per = [];
  for (let i = 0; i < R.eps; i++) {
    await page.evaluate(() => document.querySelector('#epBtn').click());
    await page.waitForTimeout(60);
    await page.evaluate(k => document.querySelectorAll('.eitem')[k].click(), i);
    await page.waitForTimeout(130);
    const st = await page.evaluate(() => {
      const cs = [...document.querySelectorAll('.cue')];
      let noJp = 0, noFrame = 0, hasRuby = 0, sfx = 0;
      for (const c of cs) {
        const jp = c.querySelector('.jp');
        if (!jp || !jp.textContent.trim()) noJp++;
        if (!c.querySelector('.frame .fr')) noFrame++;
        if (c.querySelector('.jp ruby')) hasRuby++;
        if (c.textContent.includes('音效，非语言内容')) sfx++;
      }
      return { n: cs.length, noJp, noFrame, hasRuby, sfx,
               title: (document.querySelector('#epTitle') || {}).textContent };
    });
    per.push(st);
  }
  R.per = per;

  // 抽屉
  await page.evaluate(() => document.querySelector('#epBtn').click());
  await page.waitForTimeout(200);
  R.drawer = await page.evaluate(() => ({
    title: (document.querySelector('#drawer h2') || {}).textContent,
    items: [...document.querySelectorAll('.eitem')].map(x => x.textContent.trim().slice(0, 40)),
  }));
  await page.screenshot({ path: path.join(__dirname, 'merged-shot-drawer.png') });
  await page.evaluate(() => document.querySelector('#closeDrawer').click());
  await page.waitForTimeout(150);

  // 弹窗：选块最多的一句
  await page.evaluate(() => {
    const cs = [...document.querySelectorAll('.cue')];
    let c = null, best = -1;
    for (const x of cs) {
      const fr = x.querySelector('.frame .fr');
      const n = fr ? fr.children.length : 0;
      if (n > best) { best = n; c = x; }
    }
    (c || cs[0]).click();
  });
  await page.waitForTimeout(400);
  R.sheet = await page.evaluate(() => {
    const sh = document.querySelector('#sheet');
    if (!sh) return { exists: false };
    return {
      exists: true,
      secs: [...sh.querySelectorAll('.sh-sec h4')].map(x => x.textContent.trim()),
      blocks: sh.querySelectorAll('.bk').length,
      parts: sh.querySelectorAll('.bp').length,
      inner: sh.querySelectorAll('.bin').length,
      skel: sh.querySelectorAll('.sk').length,
      pred: sh.querySelectorAll('.pd').length,
    };
  });
  await page.screenshot({ path: path.join(__dirname, 'merged-shot-sheet.png') });
  await page.evaluate(() => { const s = document.querySelector('#sheet'); if (s) s.classList.remove('on'); });
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(__dirname, 'merged-shot-list.png') });

  // 产物里 13 集 SHUN 行是否逐字一致
  const html = fs.readFileSync(path.resolve(__dirname, 'shun-grammar-merged.html'), 'utf8');
  const srcHtml = fs.readFileSync(SRC, 'utf8');
  const line = srcHtml.split('\n').find(l => l.startsWith('<script>window.SHUN='));
  const m = html.match(/<script>window\.SHUN=[\s\S]*?<\/script>/);
  R.shunLineIdentical = m ? (m[0] === line) : false;
  R.shunLineSha = crypto.createHash('sha256').update(line).digest('hex').slice(0, 16);

  R.errors = errors;
  console.log(JSON.stringify(R, null, 1));
  await browser.close();
})();
