/* test_shun_shell.js —— Japanese with Shun 日中对照精读 · 回归测试
 *
 * 跑法：NODE_PATH=/usr/local/lib/node_modules node /workspace/shun/scripts/test_shun_shell.js
 *
 * 四拓扑：
 *   T1 网页版        —— 真实 <audio> 播 CDN 地址（本机 audio/ 模拟 CDN）
 *   T2 原生桥        —— 注入 window.Android，走原生播放/下载/缓存链路
 *   T3 APK 拓扑      —— 页面从 /app/** 取（模拟 MediaProxy 伺服内置资源，origin 固定）
 *   T4 线上站        —— scripts/online_regress.js（部署后单独跑）
 *
 * 断言：顶栏剧名 / 底栏「⬇ 下載」/ 缓存「N 集 · M MB」/「全部刪除」二次确认 /
 *       按地址判已存 / 7 套主题 + 纯黑白 / 字幕流占屏 ≥80% /
 *       点句弹 92% 大窗四段齐全 / 弹窗内点词就地展开 / 弹窗字号独立 / 无 JS 错误
 */
const http = require('http');
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const WS = path.resolve(__dirname, '..');
const WEB = path.join(WS, 'web');
/* 🔴 用 **web/data**（正式数据）而不是 dev/data：
     dev/data 是从 work/ 重新组装的**旧格式**（没有 gs 字段）⇒
     弹窗里的「句子结构详解」会是空的 ⇒ 测试测不到新结构。
     ⇒ 正式数据才是测试的基线；SHUN_DATA 环境变量仍可切。 */
const DATA = process.env.SHUN_DATA || path.join(WS, 'web', 'data');
const AUDIO = path.join(WS, 'audio');
const PORT = Number(process.env.SHUN_PORT || 18931);
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.png': 'image/png', '.m4a': 'audio/mp4', '.mp3': 'audio/mpeg',
  '.apk': 'application/vnd.android.package-archive', '.txt': 'text/plain; charset=utf-8'
};

let pass = 0, fail = 0; const fails = [];
const ok = (cond, name, extra) => {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; fails.push(name + (extra !== undefined ? '  → ' + extra : '')); console.log('  ❌ ' + name + (extra !== undefined ? '  → ' + extra : '')); }
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ── 本机服务：既当「CDN 音频站」，也当「APK 内置资源代理」 ── */
function sendFile(req, res, file) {
  const st = fs.statSync(file);
  const type = MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
  const range = req.headers['range'];
  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    let start = m && m[1] ? parseInt(m[1], 10) : 0;
    let end = m && m[2] ? parseInt(m[2], 10) : st.size - 1;
    if (isNaN(start)) start = 0;
    if (isNaN(end) || end >= st.size) end = st.size - 1;
    if (start > end) {
      res.writeHead(416, { 'Content-Range': 'bytes */' + st.size });
      res.end(); return;
    }
    res.writeHead(206, {
      'Content-Type': type, 'Accept-Ranges': 'bytes',
      'Content-Range': 'bytes ' + start + '-' + end + '/' + st.size,
      'Content-Length': end - start + 1
    });
    fs.createReadStream(file, { start, end }).pipe(res);
    return;
  }
  res.writeHead(200, { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Content-Length': st.size });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  const qs = req.url.split('?')[1] || '';
  /* 🔴 引导页必须**隔离**（同 test_bgplay）：
   *   不隔离 ⇒ 页面去 GitHub Pages 拉线上 shun.json ⇒ 拉到线上最新集数
   *   ⇒ 页面加载的集与本地基线对不上 ⇒ 假红。
   *   ⇒ 404 ⇒ 页面回落到本地 data/eps/。 */
  if (/shun\.json$/.test(p)) { res.writeHead(404); res.end('boot blocked'); return; }
  /* 音频站：/audio/epNN.m4a */
  if (p.startsWith('/audio/')) {
    const f = path.join(AUDIO, path.basename(p));
    if (!fs.existsSync(f)) { res.writeHead(404); res.end('404'); return; }
    sendFile(req, res, f); return;
  }
  /* APK 内置音频：/media/epNN.m4a → assets/audio/epNN.m4a */
  if (p.startsWith('/media/')) {
    const f = path.join(AUDIO, path.basename(p));
    if (!fs.existsSync(f)) { res.writeHead(404); res.end('404'); return; }
    sendFile(req, res, f); return;
  }
  /* 数据目录 */
  let rel = p.replace(/^\//, '');
  if (p.startsWith('/app/')) rel = p.slice('/app/'.length).replace(/^\//, '');
  if (rel === '' || rel.endsWith('/')) rel += 'index.html';
  let base = WEB, file = path.join(WEB, rel);
  if (rel.startsWith('data/')) { base = DATA; file = path.join(DATA, rel.slice('data/'.length)); }
  if (!file.startsWith(base) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    res.writeHead(404); res.end('404 ' + p); return;
  }
  if (file.endsWith('.m4a')) { sendFile(req, res, file); return; }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
  fs.createReadStream(file).pipe(res);
});

const AUDIO_BASE_LOCAL = `http://127.0.0.1:${PORT}/audio/`;

/* 🔴🔴 错误分类：**同源失败 = 致命，外部失败 = 只告警**（2026-10-23）
 *   起因：批量跑批时偶发「Access to fetch at 'https://rqud34vh5k65y…' blocked by CORS」
 *   → 那���是**外部站点**的抖动（引导页 / 远端站），不是本地漏打包
 *   → 当成致命错误 ⇒ **假红灯**（单跑必过、批量偶发红）⇒ 整套回归失去「可复现」前提。
 *   ⚠️ 保守默认：**拿不到来源（location 为空）时按致命算** —— 不能因为分不清就放过。
 *   判据落在「失败资源的那个 URL」上（Chrome console.error 的 location().url），
 *   不是靠错误文案里有没有某个域名（那是表示，不是事实）。
 */
function classifyConsoleError(m) {
  let loc = '';
  try { loc = (m && m.location && m.location() && m.location().url) || ''; } catch (e) { loc = ''; }
  let txt = '';
  try { txt = (m && typeof m.text === 'function') ? m.text() : ((m && m.text) || String(m)); } catch (e) { txt = String(m); }
  const local = `http://127.0.0.1:${PORT}`;
  if (loc && loc.indexOf(local) !== 0) return { fatal: false, text: txt, loc };
  return { fatal: true, text: txt, loc };
}

async function newPage(browser, native) {
  const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
  /* 🔴 引导页**跨域**（GitHub Pages）⇒ 服务器层拦不到 ⇒ 必须用 page.route 拦。
   *   不拦 ⇒ 页面拉到线上最新集（Ep278 上线后 13 集、最新在前）⇒ 页面加载的是 ep13，
   *   而测试按本地 ep01 算期望 ⇒ 假红。 */
  await page.route('**/shun.json*', r => r.fulfill({ status: 404, body: 'boot blocked' }));
  /* 🔴 显式固定加载 **ep01**：
   *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）⇒ 默认加载的集在变，
   *   而这个套件的断言（音频缓存键、playEpisode 传集号…）都是按 ep01 写的
   *   ⇒ 不固定就会有「今天还绿、明天红」的假回归。 */
  await page.addInitScript(() => {
    try { localStorage.setItem('shun.ep', JSON.stringify('ep01')); } catch (e) { }
  });
  const errors = [], warns = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const c = classifyConsoleError(m);
    (c.fatal ? errors : warns).push(c.text + '   ← ' + c.loc);
  });
  if (native) {
    await page.addInitScript(() => {
      window.__nativeCalls = [];
      window.__dlItems = [];
      window.Android = {
        playEpisode: (tag, url, ms) => { window.__nativeCalls.push(['playEpisode', tag, url, ms]); },
        playAudio: (u, p) => { window.__nativeCalls.push(['playAudio', u, p]); },
        resumeAudio: () => { window.__nativeCalls.push(['resume']); },
        pauseAudio: () => { window.__nativeCalls.push(['pause']); },
        seekAudio: (ms) => { window.__nativeCalls.push(['seek', ms]); },
        stopAudio: () => { window.__nativeCalls.push(['stop']); },
        cacheEpisode: (tag, url) => {
          window.__nativeCalls.push(['cacheEpisode', tag, url]);
          window.__dlItems.push({ id: 'shun_' + tag, url: url, size: 7 * 1024 * 1024 });
          return '{"ok":true}';
        },
        downloadStatus: () => JSON.stringify({ state: 'done', progress: 1 }),
        downloadedList: () => JSON.stringify({
          count: window.__dlItems.length,
          bytes: window.__dlItems.reduce((a, b) => a + (b.size || 0), 0),
          items: window.__dlItems
        }),
        clearDownloads: () => { window.__nativeCalls.push(['clear']); window.__dlItems = []; return JSON.stringify({ removed: 1 }); }
      };
    });
  }
  return { page, errors, warns };
}

(async () => {
  await new Promise(r => server.listen(PORT, '127.0.0.1', r));
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors',
      '--autoplay-policy=no-user-gesture-required']
  });

  /* ══════════ T1 网页版 ══════════ */
  console.log('\n═══ T1 网页版（真实 <audio>）═══');
  {
    const { page, errors, warns } = await newPage(browser, false);
    await page.goto(`http://127.0.0.1:${PORT}/index.html?audio=${encodeURIComponent(AUDIO_BASE_LOCAL)}`,
      { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.cue', { timeout: 20000 });
    await sleep(900);

    console.log('\n【1】基础结构 + 顶栏剧名');
    const st = await page.evaluate(() => ({
      title: document.title,
      series: (document.getElementById('seriesLabel') || {}).textContent || '',
      sub: (document.getElementById('seriesSub') || {}).textContent || '',
      ep: (document.getElementById('epLabel') || {}).textContent || '',
      topH: document.getElementById('topbar').getBoundingClientRect().height,
      cues: document.querySelectorAll('.cue').length,
      ruby: document.querySelectorAll('.cue-ja rt').length,
      zh: document.querySelectorAll('.cue-zh').length,
      zhNotEmpty: [...document.querySelectorAll('.cue-zh')].filter(e => e.textContent.trim().length > 0).length,
      jaChars: [...document.querySelectorAll('.cue-ja')].map(e => e.textContent).join('').trim().length,
      seriesVisible: (() => {
        const el = document.getElementById('seriesLabel');
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.top >= 0 && r.bottom <= window.innerHeight && r.width > 20 && r.height > 8;
      })()
    }));
    ok(st.title.includes('Japanese with Shun'), '标题带剧名', st.title);
    ok(st.series.includes('Japanese with Shun'), '顶栏显式写剧名', st.series);
    ok(st.seriesVisible, '顶栏剧名**可见**（不只是 DOM 存在）');
    ok(st.sub.includes('日语播客'), '顶栏有中文副标题', st.sub);
    ok(/第\d+集/.test(st.ep), '集数徽章显示「第N集」', st.ep);
    ok(st.topH <= 56, '顶栏 ≤56px', st.topH);
    ok(st.cues > 30, '字幕流有内容', st.cues + ' 行');
    ok(st.ruby > 10, '有汉字注音（ruby）', st.ruby + ' 个');
    ok(st.zh > 0 && st.zhNotEmpty === st.zh, '每行都有中文', `${st.zhNotEmpty}/${st.zh}`);
    ok(st.jaChars > 100, '日文有内容', st.jaChars + ' 字');
    ok(!/[A-Za-z]{3,}\s+[A-Za-z]{3,}/.test(st.jaChars), '正文里没有罗马字（抽查）');

    console.log('\n【2】屏幕占比 + 底栏工具条');
    const ratio = await page.evaluate(() => {
      const r = document.getElementById('stream').getBoundingClientRect();
      return (r.bottom - r.top) / window.innerHeight;
    });
    ok(ratio >= 0.80, '字幕流占屏 ≥80%', (ratio * 100).toFixed(1) + '%');
    const tools = await page.$$eval('#tools .tool', els => els.map(e => ({
      t: e.textContent.replace(/\s+/g, ''), h: e.getBoundingClientRect().height
    })));
    const toolTxt = tools.map(t => t.t).join('|');
    ok(tools.some(t => t.t.includes('⬇下載')), '底栏有「⬇ 下載」', toolTxt);
    ok(tools.some(t => t.t.includes('«10')), '底栏有「«10」', toolTxt);
    ok(tools.some(t => t.t.includes('10»')), '底栏有「10»」', toolTxt);
    ok(tools.some(t => t.t.includes('單句循環')), '底栏有单句循环', toolTxt);
    ok(tools.some(t => t.t.includes('雙語')), '底栏有「雙語」', toolTxt);
    ok(tools.some(t => t.t.includes('A＋')), '底栏有放大字体', toolTxt);
    ok(tools.every(t => t.h >= 44), '所有工具按钮 ≥44px 高', tools.map(t => t.h).join(','));
    ok(await page.$eval('#tools', e => e.scrollWidth > e.clientWidth + 4), '工具条可横向滚动');

    console.log('\n【3】音频：真的出声（网页态）');
    const au = await page.evaluate(() => {
      const a = document.getElementById('au');
      return { src: a ? a.src : '', ready: a ? a.readyState : 0, dur: a ? a.duration : 0, err: a && a.error ? a.error.code : 0 };
    });
    ok(/^https?:\/\//.test(au.src), '音频是绝对地址', au.src);
    ok(/\/ep\d{2}\.m4a$/.test(au.src), '音频文件名是 epNN.m4a', au.src);
    ok(!au.src.includes('PLACEHOLDER'), '音频地址已填真实域名', au.src);
    /* 等音频真的解码出时长（能出声的前提）
     * 🔴 2026-10-23：原来只等 9 秒 + 一次性 load() → 批量跑时（2 核机器，上一套 Chrome 还没退干净）
     *    偶发「解码不出来」→ **假红**（单跑必过、批量跑偶发红）。
     *    改成**轮询 + 20 秒预算 + 一次重试**：轮询能区分「还没好」与「真的报错」。*/
    const dur = await page.evaluate(async () => {
      const a = document.getElementById('au');
      if (!a) return -1;
      /* 🔴 2026-10-23 再加一档：**预算 20s→30s、重试 1→2 次**。
       *   连续跑批时（2 核机器 + 上一套 Chrome 还没退干净）本地 3MB 音频偶发传不完
       *   ⇒ 「The element has no supported sources」⇒ 假红。
       *   音频是**本地文件**（web/media/），所以这是纯机器负载问题，不是产品问题。 */
      const deadline = Date.now() + 30000;
      let retried = 0;
      while (Date.now() < deadline) {
        if (a.readyState >= 1 && a.duration > 0) return a.duration;
        if (a.error) {
          if (retried++ < 2) { try { a.load(); } catch (e) { } }
          else return 0;
        }
        await new Promise(r => setTimeout(r, 200));
      }
      return a.duration || 0;
    });
    ok(dur > 60, '网页态音频真能解码（有时长）', dur + ' 秒');
    const played = await page.evaluate(async () => {
      const a = document.getElementById('au');
      try { await a.play(); } catch (e) { return 'play() 失败: ' + e.message; }
      await new Promise(r => setTimeout(r, 900));
      return a.currentTime;
    });
    ok(typeof played === 'number' && played > 0, '网页态音频真能出声（currentTime 在走）', String(played));

    console.log('\n【4】主题：7 套 + 纯黑白');
    await page.evaluate(() => document.getElementById('shFab').click());
    await sleep(400);
    const grid = await page.evaluate(() => document.querySelectorAll('#shThemeGrid .sh-theme-opt').length);
    ok(grid === 7, '主题网格 7 套', grid);
    const pals = await page.$$eval('#shThemeGrid .sh-theme-opt', els => els.map(e => e.getAttribute('data-pid')));
    ok(['sakura', 'night', 'paper', 'soft', 'bw', 'ink', 'green'].every(p => pals.includes(p)),
      '7 套主题齐全（sakura/night/paper/soft/bw/ink/green）', pals.join(','));
    await page.evaluate(() => document.querySelector('#shThemeGrid [data-pid="bw"]').click());
    await sleep(400);
    const bw = await page.evaluate(() => ({
      pal: document.documentElement.getAttribute('data-palette'),
      bg: getComputedStyle(document.body).backgroundColor,
      fg: getComputedStyle(document.body).color
    }));
    ok(bw.pal === 'bw', 'data-palette=bw', bw.pal);
    ok(bw.bg === 'rgb(255, 255, 255)', '纯黑白：底是纯白', bw.bg);
    ok(bw.fg === 'rgb(0, 0, 0)', '纯黑白：字是纯黑', bw.fg);
    const lsTheme = await page.evaluate(() => {
      let hub = null;
      try { hub = JSON.parse(localStorage.getItem('hubshell') || 'null'); } catch (e) { }
      const own = Object.keys(localStorage).filter(k => /palette|theme/i.test(k) && k !== 'hubshell');
      return { hub, own, keys: Object.keys(localStorage) };
    });
    ok(lsTheme.hub && lsTheme.hub.palette === 'bw', '主题写进全局 key hubshell', JSON.stringify(lsTheme.hub));
    const lsPref = await page.evaluate(() => Object.keys(localStorage).filter(k => k !== 'hubshell'));
    ok(lsPref.every(k => k.indexOf('shun.') === 0), '其余 localStorage 都带 shun. 前缀', lsPref.join(',') || '(空)');

    console.log('\n【5】黑底白字（ink）：描边半透明 + 无渐变');
    await page.evaluate(() => document.querySelector('#shThemeGrid [data-pid="ink"]').click());
    await sleep(400);
    const ink = await page.evaluate(() => {
      const cue = document.querySelector('.cue');
      return {
        bg: getComputedStyle(document.body).backgroundColor,
        img: getComputedStyle(document.body).backgroundImage,
        line: cue ? getComputedStyle(cue).borderLeftColor : null
      };
    });
    ok(ink.bg === 'rgb(0, 0, 0)', 'ink：底是纯黑', ink.bg);
    ok(ink.img === 'none', 'ink：无渐变（墨水屏会糊成脏灰块）', ink.img);
    ok(!!ink.line && ink.line !== 'rgb(255, 255, 255)', 'ink：描边不是纯白', ink.line);
    const hard = await page.evaluate(() => {
      const bad = [];
      document.querySelectorAll('#app *').forEach(el => {
        const s = getComputedStyle(el);
        ['borderTopColor', 'borderLeftColor', 'borderRightColor', 'borderBottomColor'].forEach(k => {
          const v = s[k];
          if (v === 'rgb(0, 0, 0)' || v === 'rgb(255, 255, 255)') {
            const b = s[k.replace('Color', 'Width')];
            if (parseFloat(b) > 0) bad.push((el.className || el.tagName) + ' ' + k + '=' + v);
          }
        });
      });
      return bad.slice(0, 5);
    });
    ok(hard.length === 0, '本项目没有纯黑/纯白硬描边', hard.join(' | '));
    await page.evaluate(() => document.querySelector('#shThemeGrid [data-pid="bw"]').click());
    await sleep(300);

    console.log('\n【6】字号：字幕 ≥24px + 弹窗字号独立');
    const fs = await page.$eval('#stream .cue-ja', e => parseFloat(getComputedStyle(e).fontSize));
    ok(fs >= 24, '字幕字号 ≥24px（老花眼）', fs + 'px');
    const before = await page.$eval('#stream .cue-ja', e => getComputedStyle(e).fontSize);
    await page.evaluate(() => {
      const r = document.getElementById('pfsRange');
      r.value = 26; r.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await sleep(300);
    const after = await page.$eval('#stream .cue-ja', e => getComputedStyle(e).fontSize);
    ok(before === after, '调弹窗字号不影响字幕字号', `${before} → ${after}`);
    const pfsVar = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--pfs').trim());
    ok(pfsVar === '26px', '弹窗字号写进 --pfs（独立变量）', pfsVar);

    console.log('\n【7】点句弹大弹窗（≈92% 屏高，四段齐全）');
    const target = await page.evaluate(() => {
      const cs = [...document.querySelectorAll('.cue')];
      for (let i = 0; i < cs.length; i++) {
        if (cs[i].querySelector('ruby') && cs[i].querySelector('.cue-zh').textContent.trim()) return i;
      }
      return 0;
    });
    await page.evaluate(i => document.querySelector('.cue[data-i="' + i + '"]').click(), target);
    await sleep(700);
    const pop = await page.evaluate(() => {
      const s = document.querySelector('#exSheet');
      if (!s || s.classList.contains('hidden')) return null;
      const r = s.getBoundingClientRect();
      return {
        ratio: r.height / window.innerHeight,
        visible: r.top >= 0 && r.bottom <= window.innerHeight + 1,
        secs: [...s.querySelectorAll('.ex-sec')].map(e => e.textContent.trim()),
        hasWd: !!s.querySelector('.ex-wd'), hasGram: !!s.querySelector('.ex-gram'),
        hasStruct: !!s.querySelector('.gs'), hasJa: !!s.querySelector('.ex-jp'),
        hasZh: !!s.querySelector('.ex-zh'),
        rubyInPop: s.querySelectorAll('.ex-jp rt').length,
        wdCount: s.querySelectorAll('.ex-wd li').length,
        wdWithMeaning: [...s.querySelectorAll('.ex-wd li')].filter(li => {
          const m = li.querySelector('.m'); return m && m.textContent.trim();
        }).length,
        gsFrame: s.querySelectorAll('.gs-frame .gs-bk').length,
        gsRoles: s.querySelectorAll('.gs-roles .gs-rl').length,
        gsBlocks: s.querySelectorAll('.gs-bkcard').length,
        gsParts: s.querySelectorAll('.gs-parts li').length,
        gsChain: s.querySelectorAll('.gs-chain .gs-ch').length,
        gsWhole: (s.querySelector('.gs-whole') || {}).textContent || '',
        copyBtn: [...s.querySelectorAll('[data-act="copy-sent"]')].length,
        heads: [...s.querySelectorAll('.ex-sheet-hd .mini-btn, .ex-sheet-hd .sh-btn')].map(e => e.textContent.trim())
      };
    });
    ok(!!pop, '点句弹出大弹窗');
    if (pop) {
      ok(pop.ratio >= 0.90, '弹窗 ≥90% 屏高（不是就地展开）', (pop.ratio * 100).toFixed(1) + '%');
      ok(pop.visible, '弹窗在视口内可见');
      ok(pop.hasJa && pop.hasZh, '第①段：原句 + 中文');
      ok(pop.rubyInPop > 0, '原句带注音（ruby）', pop.rubyInPop + ' 个');
      const j = pop.secs.join('|');
      ok(j.indexOf('①') >= 0 && j.indexOf('②') >= 0 && j.indexOf('③') >= 0, '三段带编号 ①②③', j);
      ok(pop.hasWd && pop.wdCount > 0, '① 逐词拆解（且不为空）', pop.wdCount);
      ok(pop.wdWithMeaning > 0, '逐词里有带释义的词', pop.wdWithMeaning);
      ok(pop.hasStruct, '② 句子结构详解（新版）');
      ok(pop.gsFrame > 0, '结构详解有大框架分块', pop.gsFrame);
      ok(pop.gsRoles > 0, '结构详解有角色标签', pop.gsRoles);
      ok(pop.gsBlocks > 0, '结构详解有逐块拆解', pop.gsBlocks);
      ok(pop.gsParts > 0, '逐块拆解里有成分说明', pop.gsParts);
      ok(pop.gsChain > 0, '结构详解有层层修饰链', pop.gsChain);
      ok(pop.gsWhole.trim().length > 0, '结构详解有整句意思', pop.gsWhole.slice(0, 30));
      ok(pop.hasGram, '③ 句型说明');
      ok(pop.copyBtn > 0, '弹窗里有「复制整句」按钮', pop.copyBtn);
      const h = pop.heads.join('|');
      ok(/Aa/.test(h), '弹窗头部有 Aa（弹窗字号）', h);
      ok(/存本集/.test(h), '弹窗头部有「⬇ 存本集」', h);
      ok(/跳到這句/.test(h), '弹窗头部有「▶ 跳到這句」', h);
      ok(/收起/.test(h), '弹窗头部有「收起 ▲」', h);
      ok(/✕|×/.test(h), '弹窗头部有关闭 ×', h);
    }

    console.log('\n【8】弹窗内点词就地展开');
    const picked = await page.evaluate(() => {
      const lis = [...document.querySelectorAll('#exBody .ex-wd li')];
      const hit = lis.find(li => { const m = li.querySelector('.m'); return m && m.textContent.trim(); });
      if (hit) { hit.click(); return hit.querySelector('.w').textContent; }
      return '';
    });
    ok(!!picked, '逐词里有词库收录的词（可查释义）', picked || '本行没有收录词');
    await sleep(400);
    const det = await page.evaluate(() => {
      const li = document.querySelector('#exBody .ex-wd li.on');
      if (!li) return null;
      return {
        detail: !!li.querySelector('.ex-detail'),
        meaning: (li.querySelector('.d-m') || {}).textContent || '',
        example: (li.querySelector('.d-e') || {}).textContent || ''
      };
    });
    ok(!!det && det.detail, '点词后就地展开释义');
    if (det) {
      ok(det.meaning.trim().length > 0 && !det.meaning.includes('没有这个词'), '释义有内容', det.meaning);
      ok(det.example.length > 0, '带本剧例句', det.example);
    }

    console.log('\n【9】弹窗底部操作 + 收藏');
    const acts = await page.$$eval('#exBody .ex-foot .mini-btn', els => els.map(e => e.textContent.replace(/\s+/g, '')));
    ok(acts.some(t => t.includes('循环本句')), '有「循环本句」', acts.join(' | '));
    ok(acts.some(t => t.includes('收藏本句')), '有「收藏本句」', acts.join(' | '));
    ok(acts.some(t => t.includes('存本集')), '有「⬇ 存本集」', acts.join(' | '));
    ok(acts.some(t => t.includes('收起')), '有「收起」', acts.join(' | '));
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('#exBody .ex-foot .mini-btn')].find(x => /收藏本句/.test(x.textContent));
      if (b) b.click();
    });
    await sleep(400);
    const favOn = await page.evaluate(() => {
      const b = [...document.querySelectorAll('#exBody .ex-foot .mini-btn')].find(x => /已收藏|收藏本句/.test(x.textContent));
      return b ? b.textContent : '';
    });
    ok(/已收藏/.test(favOn), '收藏能切换', favOn);
    const favStored = await page.evaluate(() => {
      try { const f = JSON.parse(localStorage.getItem('shun.fav') || '{}'); return Object.keys(f).length; } catch (e) { return -1; }
    });
    ok(favStored > 0, '收藏持久化到 shun.fav', favStored);
    await page.evaluate(() => { const b = document.querySelector('#exBody [data-act="close"]'); if (b) b.click(); });
    await sleep(300);

    console.log('\n【10】选集抽屉（带剧名 + 原作标题）');
    await page.evaluate(() => document.getElementById('btnEp').click());
    await sleep(500);
    const epInfo = await page.evaluate(() => ({
      visible: !document.getElementById('epPanel').classList.contains('hidden'),
      n: document.querySelectorAll('#epList .ep-item').length,
      title: (document.querySelector('#epPanel .sheet-hd') || {}).textContent || '',
      firstDesc: (document.querySelector('#epList .ep-item .ep-d2') || {}).textContent || ''
    }));
    ok(epInfo.visible, '选集面板能打开');
    ok(epInfo.n >= 1, '选集列表有内容', epInfo.n + ' 集');
    ok(epInfo.title.includes('Japanese with Shun'), '选集面板标题带剧名', epInfo.title);
    ok(epInfo.firstDesc.length > 0, '选集显示原作标题 desc', epInfo.firstDesc);
    await page.evaluate(() => { const b = document.getElementById('btnCloseEp'); if (b) b.click(); });
    await sleep(300);

    console.log('\n【11】缓存区：N 集 · M MB + 全部删除 + 二次确认');
    await page.evaluate(() => document.getElementById('btnMore').click());
    await sleep(400);
    const hasCacheEntry = await page.evaluate(() => {
      const p = document.getElementById('morePanel');
      return !!p && !p.classList.contains('hidden') && /音頻緩存/.test(p.textContent || '');
    });
    ok(hasCacheEntry, '更多菜单里有「音頻緩存」入口');

    /* 先在网页态真下一集（Cache Storage），再看缓存区 */
    console.log('\n【11-b】网页态下载（Cache Storage）+ 已存判断');
    await page.evaluate(() => document.getElementById('btnDl').click());
    const dlOk = await page.waitForFunction(() => {
      const b = document.getElementById('btnDl');
      return b && /已存/.test(b.textContent);
    }, { timeout: 30000 }).then(() => true).catch(() => false);
    ok(dlOk, '网页态「⬇ 下載」真能存进浏览器缓存（按钮变「✓ 已存」）');
    const cached = await page.evaluate(async () => {
      try {
        const c = await caches.open('shun-audio-v1');
        const keys = await c.keys();
        return { n: keys.length, url: keys[0] ? keys[0].url : '' };
      } catch (e) { return { n: -1, url: '' }; }
    });
    ok(cached.n === 1, 'Cache Storage 里真存了 1 个音频', JSON.stringify(cached));
    ok(/\/ep\d{2}\.m4a$/.test(cached.url), '缓存的键是音频地址（按地址判已存）', cached.url);

    await page.evaluate(() => document.getElementById('btnCache').click());
    await sleep(600);
    const cache = await page.evaluate(() => ({
      visible: !document.getElementById('cachePanel').classList.contains('hidden'),
      val: (document.getElementById('cacheVal') || {}).textContent || '',
      hasClear: !!document.getElementById('btnClearCache')
    }));
    ok(cache.visible, '缓存面板能打开');
    ok(/\d+\s*集\s*·\s*[\d.]+\s*MB/.test(cache.val), '显示「N 集 · M MB」', cache.val);
    ok(/1\s*集/.test(cache.val), '缓存集数正确（网页态 1 集）', cache.val);
    ok(cache.hasClear, '有「全部删除」按钮');
    const confirmed = await page.evaluate(() => {
      let asked = null;
      const orig = window.confirm;
      window.confirm = (msg) => { asked = msg; return false; };
      document.getElementById('btnClearCache').click();
      window.confirm = orig;
      return asked;
    });
    ok(!!confirmed, '点「全部删除」有二次确认', confirmed || '没弹确认');
    await page.evaluate(() => { const b = document.getElementById('btnCloseCache'); if (b) b.click(); });
    await sleep(300);
    await page.evaluate(() => { const b = document.getElementById('btnMore'); if (b) b.click(); });
    await sleep(300);

    console.log('\n【12】固定面板限高可滚');
    const sheets = await page.evaluate(() => [...document.querySelectorAll('.sheet,.ex-sheet')].map(s => ({
      mh: getComputedStyle(s).maxHeight, oy: getComputedStyle(s).overflowY
    })));
    ok(sheets.length > 0 && sheets.every(s => s.mh !== 'none' && s.oy !== 'visible'),
      '所有固定面板都限高 + 自己滚', JSON.stringify(sheets));

    console.log('\n【13】无 JS 错误');
    const realErr = errors.filter(e => !/PLACEHOLDER|net::ERR|Failed to load resource|ERR_NAME_NOT_RESOLVED/.test(e));
    ok(realErr.length === 0, '无 JS 错误', realErr.slice(0, 3).join(' | '));
    if (warns.length) console.log('  ⚠️ 外部资源告警（不阻断）: ' + warns.slice(0, 3).join(' | '));

    console.log('\n【13-b】错误分类器：同源致命 / 外部只告警（防「把闸门改松」）');
    ok(classifyConsoleError({ text: 'x', location: () => ({ url: `http://127.0.0.1:${PORT}/missing.js` }) }).fatal === true,
      '同源失败算致命（本地漏打包必须红）');
    ok(classifyConsoleError({ text: 'x', location: () => ({ url: 'https://rqud34vh5k65y.space.mcode.cn/data/eps/index.json' }) }).fatal === false,
      '外部站失败只告警（外部抖动不是代码 bug）');
    ok(classifyConsoleError({ text: 'x', location: () => ({ url: '' }) }).fatal === true,
      '拿不到来源时按致命算（保守默认：不因为分不清就放过）');
    if (warns.length) console.log('  ⚠️ 外部资源告警（不阻断）: ' + warns.slice(0, 3).join(' | '));

    await page.screenshot({ path: path.join(WS, 'shots', 'shun_web_home.png') });
    await page.close();
  }

  /* ══════════ T2 原生桥（模拟 APK） ══════════ */
  console.log('\n═══ T2 原生桥（模拟 APK 内置资源 + 原生播放器）═══');
  {
    const { page, errors, warns } = await newPage(browser, true);
    await page.goto(`http://127.0.0.1:${PORT}/index.html?audio=${encodeURIComponent('https://audio.example.com/media/')}`,
      { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.cue', { timeout: 20000 });
    await sleep(900);

    const calls = await page.evaluate(() => window.__nativeCalls || []);
    const play = calls.find(c => c[0] === 'playEpisode');
    ok(!!play, '原生态走 playEpisode（内置音频）', JSON.stringify(play));
    if (play) {
      ok(play[1] === 'ep01', 'playEpisode 传的是集号 ep01', play[1]);
      ok(/^https:\/\//.test(play[2]) && /ep01\.m4a$/.test(play[2]),
        '原生态也带远端地址（按地址判已存要用）', play[2]);
    }
    const dlText = await page.$eval('#btnDl', e => e.textContent.replace(/\s+/g, ''));
    ok(/⬇下載/.test(dlText), '原生态底栏有「⬇ 下載」', dlText);

    /* 按地址判已存：同 id 不同地址 → 不算已存 */
    const realUrl = play ? play[2] : '';
    await page.evaluate(u => {
      window.__dlItems = [{ id: 'ep01', url: 'https://other.example.com/media/ep01.m4a', size: 1024 }];
    }, realUrl);
    await page.evaluate(() => document.getElementById('btnLoopOne2').click());
    await sleep(300);
    const tWrong = await page.$eval('#btnDl', e => e.textContent.trim());
    ok(!/已存/.test(tWrong), '同 id 但地址不同 → 不算已存（不串台）', tWrong);
    await page.evaluate(u => {
      window.__dlItems = [{ id: '完全不同的id', url: u, size: 7 * 1024 * 1024 }];
    }, realUrl);
    await page.evaluate(() => document.getElementById('btnLoopOne2').click());
    await sleep(300);
    const tRight = await page.$eval('#btnDl', e => e.textContent.trim());
    ok(/已存/.test(tRight), '地址相同（id 不同）→ 算已存', tRight);

    /* 缓存区 */
    await page.evaluate(() => document.getElementById('btnMore').click());
    await sleep(300);
    await page.evaluate(() => document.getElementById('btnCache').click());
    await sleep(400);
    const cache = await page.evaluate(() => ({
      val: (document.getElementById('cacheVal') || {}).textContent || '',
      visible: !document.getElementById('cachePanel').classList.contains('hidden')
    }));
    ok(cache.visible, '原生态缓存面板能打开');
    ok(/1\s*集\s*·\s*[\d.]+\s*MB/.test(cache.val), '原生态显示「1 集 · M MB」', cache.val);
    const confirmed = await page.evaluate(() => {
      let asked = null;
      const orig = window.confirm;
      window.confirm = (msg) => { asked = msg; return true; };
      document.getElementById('btnClearCache').click();
      window.confirm = orig;
      return asked;
    });
    ok(!!confirmed, '原生态「全部删除」有二次确认', confirmed || '没弹确认');
    await sleep(400);
    const cleared = await page.evaluate(() => (window.__nativeCalls || []).some(c => c[0] === 'clear'));
    ok(cleared, '原生态「全部删除」真的调了原生清理');
    const after = await page.$eval('#cacheVal', e => e.textContent.trim());
    ok(/^0\s*集/.test(after), '删除后缓存显示 0 集', after);

    console.log('\n【T2-b】原生态下载（存本集）');
    await page.evaluate(() => { const b = document.getElementById('btnCloseCache'); if (b) b.click(); });
    await sleep(200);
    await page.evaluate(() => document.getElementById('btnDl').click());
    await sleep(600);
    const cacheCall = await page.evaluate(() => (window.__nativeCalls || []).find(c => c[0] === 'cacheEpisode'));
    ok(!!cacheCall, '原生态「⬇ 下載」调 cacheEpisode（内置音频 → 本地副本）', JSON.stringify(cacheCall));
    if (cacheCall) ok(cacheCall[2] === realUrl, 'cacheEpisode 带的是同一个音频地址', cacheCall[2]);

    const realErr = errors.filter(e => !/PLACEHOLDER|net::ERR|Failed to load resource/.test(e));
    ok(realErr.length === 0, '原生态无 JS 错误', realErr.slice(0, 3).join(' | '));
    if (warns.length) console.log('  ⚠️ 外部资源告警（不阻断）: ' + warns.slice(0, 3).join(' | '));
    await page.close();
  }

  /* ══════════ T3 APK 拓扑（/app/** 内置资源，origin 固定） ══════════ */
  console.log('\n═══ T3 APK 拓扑（页面走 /app/**，origin 固定 127.0.0.1）═══');
  {
    const { page, errors, warns } = await newPage(browser, true);
    await page.goto(`http://127.0.0.1:${PORT}/app/index.html?audio=${encodeURIComponent('https://audio.example.com/media/')}`,
      { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.cue', { timeout: 20000 });
    await sleep(900);
    const st = await page.evaluate(() => ({
      origin: location.origin,
      series: (document.getElementById('seriesLabel') || {}).textContent || '',
      cues: document.querySelectorAll('.cue').length,
      ratio: (() => {
        const r = document.getElementById('stream').getBoundingClientRect();
        return (r.bottom - r.top) / window.innerHeight;
      })(),
      apkRowHidden: (() => {
        const b = document.getElementById('btnApk');
        return !!(b && b.parentElement && b.parentElement.style.display === 'none');
      })()
    }));
    ok(st.origin === `http://127.0.0.1:${PORT}`, 'origin 固定（本机代理）', st.origin);
    ok(st.series.includes('Japanese with Shun'), 'APK 拓扑顶栏也有剧名', st.series);
    ok(st.cues > 30, 'APK 拓扑字幕流有内容', st.cues + ' 行');
    ok(st.ratio >= 0.80, 'APK 拓扑字幕流占屏 ≥80%', (st.ratio * 100).toFixed(1) + '%');
    ok(st.apkRowHidden, 'APK 内隐藏「下载 App」入口（避免跳 404）');
    const realErr = errors.filter(e => !/PLACEHOLDER|net::ERR|Failed to load resource/.test(e));
    ok(realErr.length === 0, 'APK 拓扑无 JS 错误', realErr.slice(0, 3).join(' | '));
    if (warns.length) console.log('  ⚠️ 外部资源告警（不阻断）: ' + warns.slice(0, 3).join(' | '));
    await page.close();
  }

  await browser.close();
  server.close();
  console.log(`\n═══ ${pass} 通过 / ${fail} 失败 ═══`);
  if (fails.length) { console.log('失败项:'); fails.forEach(f => console.log('  - ' + f)); }
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
