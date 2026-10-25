/* 端到端验证：peer 的 7 集 + 我补的第二层 ⇒ 我的渲染层能不能直接吃 */
const { chromium } = require('/usr/local/lib/node_modules/playwright');
const path = require('path');
const fs = require('fs');

const FILE = 'file://' + path.resolve(__dirname, 'peer-render-test.html');
const CHROME = '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--allow-file-access-from-files']
  });
  const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(FILE, { waitUntil: 'load', timeout: 90000 });
  await page.waitForTimeout(700);

  const R = {};
  R.eps = await page.evaluate(() => window.SHUN.eps.length);
  R.sentsTotal = await page.evaluate(() => window.SHUN.eps.reduce((a, e) => a + e.sents.length, 0));
  R.gsLen = await page.evaluate(() => window.SHUNG.length);
  R.predLen = await page.evaluate(() => window.SHUNP.length);
  R.blocksTotal = await page.evaluate(() =>
    window.SHUNG.reduce((a, g) => a + ((g && g.blocks && g.blocks.length) || 0), 0));
  R.partsTotal = await page.evaluate(() =>
    window.SHUNG.reduce((a, g) => a + ((g && g.blocks || []).reduce((x, b) => x + ((b.parts && b.parts.length) || 0), 0)), 0));
  R.innerTotal = await page.evaluate(() =>
    window.SHUNG.reduce((a, g) => a + ((g && g.blocks || []).filter(b => b.inner).length), 0));
  R.skelTotal = await page.evaluate(() =>
    window.SHUNG.filter(g => g && g.skel).length);
  R.rubyTotal = await page.evaluate(() =>
    window.SHUN.eps.reduce((a, e) => a + e.sents.reduce((x, s) => x + ((s.ru && s.ru.length) || 0), 0), 0));

  // 逐集：切到每一集，点开第一句，统计渲染完整性
  const per = [];
  for (let i = 0; i < R.eps; i++) {
    await page.evaluate(() => document.querySelector('#epBtn').click());
    await page.waitForTimeout(80);
    await page.evaluate((k) => document.querySelectorAll('.eitem')[k].click(), i);
    await page.waitForTimeout(160);
    const st = await page.evaluate(() => {
      const cues = [...document.querySelectorAll('.cue')];
      let noJp = 0, noFrame = 0, emptyBlock = 0, hasRuby = 0;
      for (const c of cues) {
        const jp = c.querySelector('.jp');
        if (!jp || !jp.textContent.trim()) noJp++;
        if (!c.querySelector('.frame .fr')) noFrame++;
        const bl = c.querySelectorAll('.frame .fr .fb');
        if (bl.length === 0) emptyBlock++;
        if (c.querySelector('.jp ruby')) hasRuby++;
      }
      return { n: cues.length, noJp, noFrame, emptyBlock, hasRuby };
    });
    per.push({ ep: i, ...st });
  }
  R.per = per;

  // 点开一句 → 底部弹窗：4 段是否都在
  // 点开**有 frame 的**那一句（第一句可能是音效/空句）
  const clicked = await page.evaluate(() => {
    const cs = [...document.querySelectorAll('.cue')];
    // 选**块最多**的那一句（内容最丰富的）
    let c = null, best = -1;
    for (const x of cs) {
      const n = (x.querySelector('.frame .fr') || { children: [] }).children.length;
      if (n > best) { best = n; c = x; }
    }
    c = c || cs[0];
    if (c) { c.click(); return (c.textContent || '').slice(0, 40); }
    return null;
  });
  R.clickedCue = clicked;
  await page.waitForTimeout(400);
  R.sheet = await page.evaluate(() => {
    const sh = document.querySelector('#sheet');
    if (!sh) return { exists: false };
    const secs = [...sh.querySelectorAll('.sh-sec h4')].map(x => x.textContent.trim());
    return {
      exists: true,
      open: sh.classList.contains('on') || getComputedStyle(sh).display !== 'none',
      secs,
      blocks: sh.querySelectorAll('.bk').length,
      parts: sh.querySelectorAll('.bp').length,
      inner: sh.querySelectorAll('.bin').length,
      skel: sh.querySelectorAll('.sk').length,
      pred: sh.querySelectorAll('.pd').length,
    };
  });
  await page.screenshot({ path: path.join(__dirname, 'peer-shot-sheet.png') });
  await page.evaluate(() => { const s = document.querySelector('#sheet'); if (s) s.classList.remove('on'); });
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(__dirname, 'peer-shot-list.png') });

  R.errors = errors;
  console.log(JSON.stringify(R, null, 1));
  await browser.close();
})();
