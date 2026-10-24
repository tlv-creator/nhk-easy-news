#!/usr/bin/env node
/* 🔴🔴 场景测试：**内容站被误删**（引导页活着，但它指向的站 404 / 拒连）
 *
 * 为什么要有这个测试（2026-10-23 广播的由来）：
 *   有人清理站点时把「别的 APK 编译进去的兜底依赖」误删了 ⇒ 用户的 App 引导页读不到就白屏。
 *   ⇒ 我们的 App 必须**对「站没了」有免疫能力**：
 *      ① 引导页读不到 → 用内置集（test_boot.js 已覆盖）
 *      ② 引导页读得到、但**它指向的站没了** → 仍然用内置集，不白屏（**本文件**）
 *
 * 判据（三条都要）：
 *   - 页面**不是白屏**：字幕流里有内容（.cue 存在）
 *   - 集数仍然是**内置的 12 集**（不是 0，也不是 1）
 *   - **无 JS 错误**（引导页指向死站不该抛异常）
 *
 * 跑法：node scripts/test_site_gone.js
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PORT = 18733;
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png' };

let pass = 0, fail = 0; const fails = [];
function ok(cond, name, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; fails.push(name + (extra !== undefined ? '  → ' + extra : '')); console.log('  ❌ ' + name + (extra !== undefined ? '  → ' + extra : '')); }
}

(async () => {
  const srv = http.createServer((req, res) => {
    const u = req.url.split('?')[0];
    let p = u; if (p === '/') p = '/index.html';
    const f = path.join(ROOT, 'web', p);
    if (fs.existsSync(f) && fs.statSync(f).isFile()) {
      res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
      return fs.createReadStream(f).pipe(res);
    }
    res.writeHead(404); res.end('nf');
  });
  await new Promise(r => srv.listen(PORT, '127.0.0.1', r));

  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors']
  });
  const page = await browser.newPage({ viewport: { width: 412, height: 900 } });
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));

  /* 🔴 在页面脚本跑起来**之前**拦掉引导页请求 → 返回一个「指向死站」的引导页
   *    （死站 = 端口 9 discard，连接必被拒 —— 模拟「站被删了」） */
  await page.addInitScript((deadSite) => {
    const _fetch = window.fetch;
    window.fetch = function (input, init) {
      const url = (typeof input === 'string') ? input : (input && input.url) || '';
      if (/shun\.json|version\.json/.test(url)) {
        return Promise.resolve({
          ok: true, status: 200,
          json: function () {
            return Promise.resolve({
              ver: 99, verName: '9.9',
              site: deadSite, audio: deadSite + 'media/',
              apk: deadSite + 'shun.apk',
              updated: '2026-10-23T00:00:00Z', note: '测试：引导页活着，但站没了'
            });
          }
        });
      }
      return _fetch.apply(this, arguments);
    };
  }, 'http://127.0.0.1:9/gone/');

  console.log('\n【场景】引导页正常返回，但它指向的内容站已不存在');
  await page.goto(`http://127.0.0.1:${PORT}/index.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.cue', { timeout: 20000 });
  await page.waitForTimeout(1500);

  const st = await page.evaluate(() => ({
    cues: document.querySelectorAll('.cue').length,
    cueText: (document.querySelector('.cue') || {}).textContent || '',
    bootOk: !!(window.ShunBoot && window.ShunBoot.bootOk),
    dataBase: (window.ShunBoot && window.ShunBoot.dataBase) || '',
    siteBase: (window.ShunBoot && window.ShunBoot.siteBase) || ''
  }));

  ok(st.bootOk === true, '引导页本身读到了（bootOk=true）', JSON.stringify(st));
  ok(st.cues > 0, '不是白屏（字幕流有内容）', 'cues=' + st.cues);
  ok(st.cueText.trim().length > 0, '当前句有文字', JSON.stringify(st.cueText.slice(0, 30)));
  ok(st.dataBase.indexOf('127.0.0.1:9') >= 0, '数据基址确实指向死站（场景生效）', st.dataBase);

  /* 集数：应为内置的 12 集 */
  const eps = await page.evaluate(() => {
    const el = document.getElementById('epList');
    return el ? el.querySelectorAll('.ep-item').length : -1;
  });
  /* 🔴 集数**不能写死**：Ep278（ep13）入仓后本地内置集数从 12 变成 13。
   *    判据落在「本地 index.json 说有几集就有几集」上 —— 事实量。 */
  const wantEps = (JSON.parse(fs.readFileSync(path.join(ROOT, 'web', 'data', 'eps', 'index.json'), 'utf8')).episodes || []).length;
  ok(eps === wantEps, '集数仍是内置的全部 ' + wantEps + ' 集（不是 0、不是只剩 1）', '实际 ' + eps + ' want ' + wantEps);

  /* 无 JS 错误 */
  ok(errs.length === 0, '无 JS 错误', errs.slice(0, 2).join(' | '));

  await browser.close();
  srv.close();

  console.log('\n═══ ' + pass + ' 通过 / ' + fail + ' 失败 ═══');
  if (fail) { console.log('失败项：\n  ' + fails.join('\n  ')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
