/*!
 * 引导页（Boot）链路专项测试
 *
 * 验证：
 *   ① 能从引导页拉到 site/audio/ver
 *   ② 集数列表 = 内置 + 远端新增（按集号去重，远端优先）
 *   ③ 远端集的数据 URL 指向远端站，内置集仍指本地（离线可用）
 *   ④ 引导页挂掉 → 静默回落，内置功能完全不受影响
 *   ⑤ 音频基址跟随引导页
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PORT = process.env.SHUN_BOOT_PORT || 18995;
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };

let pass = 0, fail = 0; const fails = [];
const ok = (c, n, e) => {
  if (c) { pass++; console.log('  ✅ ' + n); }
  else { fail++; fails.push(n + (e !== undefined ? '  → ' + e : '')); console.log('  ❌ ' + n + (e !== undefined ? '  → ' + e : '')); }
};

(async () => {
  const srv = http.createServer((req, res) => {
    const u = req.url.split('?')[0];
    /* 模拟引导页 */
    if (u === '/boot/shun.json') {
      res.writeHead(200, { 'content-type': 'application/json', 'access-control-allow-origin': '*' });
      return res.end(JSON.stringify({
        ver: 9, verName: '1.9',
        site: 'http://127.0.0.1:' + PORT + '/remote/',
        audio: 'http://127.0.0.1:' + PORT + '/remote/media/',
        updated: '2026-10-22T00:00:00Z', note: '测试'
      }));
    }
    /* 模拟引导页挂了 */
    if (u === '/bootfail/shun.json') { res.writeHead(500); return res.end('boom'); }
    /* 模拟远端站 */
    if (u.startsWith('/remote/')) {
      const p = u.slice('/remote/'.length);
      if (p === 'data/eps/index.json') {
        res.writeHead(200, { 'content-type': 'application/json', 'access-control-allow-origin': '*' });
        return res.end(JSON.stringify({ episodes: [
          { ep: 13, tag: 'ep13', title: '【N5-N4】Ep278 New', desc: 'd', dur: 700, audio: 'ep13.m4a', n: 100, no: '278' },
          { ep: 14, tag: 'ep14', title: '【N5-N4】Ep279 New', desc: 'd', dur: 700, audio: 'ep14.m4a', n: 100, no: '279' }
        ] }));
      }
      if (p === 'data/eps/ep13.json') {
        res.writeHead(200, { 'content-type': 'application/json', 'access-control-allow-origin': '*' });
        return res.end(JSON.stringify({
          ep: 13, tag: 'ep13', title: '【N5-N4】Ep278 New', desc: 'd', dur: 700, audio: 'ep13.m4a',
          cues: [{ s: 0, e: 5, rows: [{ t: '遠隔です', zh: '远距离', ru: [['遠隔', 'えんかく']], w: [['遠隔', 'えんかく']], blk: [] }] }]
        }));
      }
      res.writeHead(404); return res.end('nf');
    }
    /* 本地静态 */
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

  /* ---------- 先加载页面（boot.js 已在里面）---------- */
  await page.goto(`http://127.0.0.1:${PORT}/index.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.cue', { timeout: 20000 });
  await page.waitForTimeout(800);

  /* ---------- A. 引导页能拉到 ---------- */
  console.log('\n【A】引导页');
  const boot = await page.evaluate(async (base) => {
    /* 直接改 ShunBoot 的引导页地址（不重新 eval 整份文件） */
    const b = window.ShunBoot;
    b.BOOT_URL = base + '/boot/shun.json';
    const j = await b.fetch();
    return {
      ok: window.ShunBoot.bootOk,
      ver: j && j.ver,
      siteBase: window.ShunBoot.siteBase,
      audioBase: window.ShunBoot.audioBase,
      dataBase: window.ShunBoot.dataBase
    };
  }, `http://127.0.0.1:${PORT}`);
  ok(boot.ok === true, '引导页拉到了', JSON.stringify(boot));
  ok(boot.ver === 9, 'ver 正确', boot.ver);
  ok(boot.siteBase.indexOf('/remote/') > 0, 'siteBase 正确', boot.siteBase);
  ok(boot.audioBase.indexOf('/remote/media/') > 0, 'audioBase 正确', boot.audioBase);

  /* ---------- B. 合并集数 ---------- */
  console.log('\n【B】集数合并');
  const merge = await page.evaluate(async (base) => {
    const b = window.ShunBoot;
    b.BOOT_URL = base + '/boot/shun.json';
    await b.fetch();
    const local = [
      { tag: 'ep12', title: '本地12', no: '266' }, { tag: 'ep11', title: '本地11', no: '267' }
    ];
    const rem = await window.ShunBoot.fetchRemoteIndex();
    const merged = window.ShunBoot.mergeEpisodes(local, rem);
    return { rem: rem.length, merged: merged.map(e => e.tag) };
  }, `http://127.0.0.1:${PORT}`);
  ok(merge.rem === 2, '拉到 2 个远端集', merge.rem);
  ok(merge.merged.length === 4, '合并后 4 集（2 内置 + 2 远端）', JSON.stringify(merge.merged));
  /* 远端 no=279/278，本地 no=267/266 → 顺序应是 278,279,267,266 */
  /* 远端 no=279/278，本地 no=267/266 → 顺序应是 279(ep14),278(ep13),267(ep11),266(ep12) */
  ok(merge.merged[0] === 'ep14' && merge.merged[1] === 'ep13' && merge.merged[2] === 'ep11' && merge.merged[3] === 'ep12',
    '按原始集号 no 倒序（新的在前）', JSON.stringify(merge.merged));

  /* ---------- C. 同集号去重（远端优先） ---------- */
  console.log('\n【C】同集号去重');
  const dedup = await page.evaluate(async (base) => {
    const b = window.ShunBoot;
    b.BOOT_URL = base + '/boot/shun.json';
    const merged = window.ShunBoot.mergeEpisodes(
      [{ tag: 'ep13', title: '本地旧版' }],
      [{ tag: 'ep13', title: '远端新版' }]
    );
    return { n: merged.length, title: merged[0].title };
  }, `http://127.0.0.1:${PORT}`);
  ok(dedup.n === 1, '同集号只留一个', dedup.n);
  ok(dedup.title === '远端新版', '远端优先', dedup.title);

  /* ---------- D. 数据源选择 ---------- */
  console.log('\n【D】数据源选择');
  const src2 = await page.evaluate(async (base) => {
    const b = window.ShunBoot;
    b.BOOT_URL = base + '/boot/shun.json';
    await b.fetch();
    return {
      remote: window.ShunBoot.dataUrlFor({ tag: 'ep13', _remote: true }),
      local: window.ShunBoot.dataUrlFor({ tag: 'ep05', _remote: false })
    };
  }, `http://127.0.0.1:${PORT}`);
  ok(src2.remote.indexOf('/remote/data/eps/ep13.json') >= 0, '远端集 → 远端站', src2.remote);
  ok(src2.local === 'data/eps/ep05.json', '内置集 → 本地（离线可用）', src2.local);

  /* ---------- E. 引导页挂了 → 静默回落 ---------- */
  console.log('\n【E】引导页挂了不影响使用');
  const down = await page.evaluate(async (base) => {
    const b = window.ShunBoot;
    b.BOOT_URL = base + '/bootfail/shun.json';
    const j = await b.fetch();
    return { j: j, ok: window.ShunBoot.bootOk };
  }, `http://127.0.0.1:${PORT}`);
  ok(down.j === null, '拉不到时返回 null');
  ok(down.ok === false, 'bootOk 保持 false');

  /* ---------- F. 真实页面在引导页挂掉时仍能用 ---------- */
  console.log('\n【F】真实页面（引导页不可达）仍能用');
  await page.goto(`http://127.0.0.1:${PORT}/index.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.cue', { timeout: 20000 });
  await page.waitForTimeout(1200);
  const live = await page.evaluate(async () => {
    /* app.js 的 S 是 IIFE 局部变量 → 外部拿不到 → 走 DOM 判据 */
    document.getElementById('btnEp').click();
    await new Promise(r => setTimeout(r, 400));
    const items = document.querySelectorAll('#epList .ep-item, #epList > *');
    return { cues: document.querySelectorAll('.cue').length, eps: items.length };
  });
  ok(live.cues > 0, '字幕照常渲染', live.cues);
  /* 🔴 集数**不能写死**：Ep278（ep13）上线后本地内置集数从 12 变成 13。
   *    判据应落在「本地 index.json 说有几集就有几集」上 —— 事实量，不是记忆里的数字。 */
  const localIdx = JSON.parse(fs.readFileSync(path.join(ROOT, 'web', 'data', 'eps', 'index.json'), 'utf8'));
  const wantEps = (localIdx.episodes || []).length;
  ok(live.eps === wantEps, '引导页挂了仍能用**本地全部**集（' + wantEps + ' 集）', 'got=' + live.eps + ' want=' + wantEps);

  /* ---------- G. 无 JS 错误 ---------- */
  console.log('\n【G】无 JS 错误');
  ok(errs.length === 0, '无 JS 错误', errs.slice(0, 3).join(' | '));

  await browser.close();
  srv.close();
  console.log('\n═══ ' + pass + ' 通过 / ' + fail + ' 失败 ═══');
  if (fail) { console.log('失败项：\n  ' + fails.join('\n  ')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
