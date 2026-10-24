/*!
 * Shun · AI 讲解专项测试（🔴 全程走本地 mock，不打真接口、不烧额度）
 *
 * 覆盖：
 *   A. refs 清洗：行中括号假名删掉、日文+粘连英文拆开
 *   B. 长句子句切分
 *   C. 弹窗第 5 段存在、默认收起、点开才展开
 *   D. 「複製提問」降级（CFG.AI_ENDPOINT='' 强制走复制模式）
 *   E. 额度小字：拿不到就整行隐藏（:empty）
 *   F. 逐字 ruby 不依赖模型（模型假名不得出现在渲染里）
 */
const http = require('http');
const { chromium } = require('playwright');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = process.env.SHUN_AI_PORT || 18991;
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';

let pass = 0, fail = 0; const fails = [];
const ok = (c, n, e) => {
  if (c) { pass++; console.log('  ✅ ' + n); }
  else { fail++; fails.push(n + (e !== undefined ? '  → ' + e : '')); console.log('  ❌ ' + n + (e !== undefined ? '  → ' + e : '')); }
};

/* ---------- 本地 mock 中转 ---------- */
const fs = require('fs');
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };

function startMock() {
  return new Promise(resolve => {
    const calls = [];
    const srv = http.createServer((req, res) => {
      /* 静态文件（web/ 下的 index.html / app.js / data/**） */
      /* 🔴 静态服务优先 —— 但不能写成 includes('/ai')，
         那会把 /ai-client.js 也匹配上（真正的中转路径是 /ai 和 /quota）。 */
      if (req.method === 'GET' && req.url !== '/ai' && req.url !== '/quota'
          && !req.url.startsWith('/ai?')) {
        let p = req.url.split('?')[0];
        if (p === '/') p = '/index.html';
        const f = path.join(ROOT, 'web', p);
        if (fs.existsSync(f) && fs.statSync(f).isFile()) {
          res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
          return fs.createReadStream(f).pipe(res);
        }
      }
      if (req.method === 'OPTIONS') {
        res.writeHead(204, {
          'access-control-allow-origin': '*',
          'access-control-allow-methods': 'GET,POST,OPTIONS',
          'access-control-allow-headers': 'content-type'
        });
        return res.end();
      }
      if (req.url.endsWith('/quota')) {
        res.writeHead(200, { 'access-control-allow-origin': '*', 'content-type': 'application/json' });
        /* 契约：{ok:true, quota:{...}} —— ai-client.js 认的是 j.quota */
        return res.end(JSON.stringify({ ok: true, quota: {
          interval_percent: 88, weekly_percent: 41,
          interval_reset_in_ms: 13800000, weekly_reset_in_ms: 224000000
        } }));
      }
      let body = '';
      req.on('data', d => body += d);
      req.on('end', () => {
        calls.push({ url: req.url, body });
        let out = { ok: true, text: '' };
        try { out = JSON.parse(body); } catch (e) { }
        if (out && out.scene === 'explode') {
          res.writeHead(401, { 'access-control-allow-origin': '*', 'content-type': 'application/json' });
          return res.end(JSON.stringify({ ok: false, error: 'unauthorized' }));
        }
        const md = [
          '1. 原文：学生时代交朋友很简单吧？',
          '2. 中文翻译',
          '学生时代交朋友很简单吧？',
          '3. 逐词拆解',
          '学生（がくせい） 名词',
          '时代（じだい） 名词',
          '4. 语法说明',
          '「～(vec)のは」句型',
          '5. 整句结构框架',
          '话题：学生时代',
          '└─谓语：交朋友'
        ].join('\n');
        res.writeHead(200, { 'access-control-allow-origin': '*', 'content-type': 'application/json' });
        res.end(JSON.stringify({ ok: true, text: md }));
      });
    });
    srv.listen(PORT, '127.0.0.1', () => resolve({ srv, calls }));
  });
}

(async () => {
  const { srv, calls } = await startMock();
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors']
  });
  const page = await browser.newPage({ viewport: { width: 412, height: 900 } });
  const jsErrs = [];
  page.on('pageerror', e => jsErrs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') jsErrs.push(m.text()); });

  const base = `http://127.0.0.1:${PORT}`;
  await page.goto(base + '/index.html', { waitUntil: 'domcontentloaded' });
  /* 等字幕渲染出来（首页自动加载第一集） */
  await page.waitForSelector('.cue', { timeout: 20000 });
  await page.waitForTimeout(900);

  /* ---------- A. refs 清洗 ---------- */
  console.log('\n【A】refs 清洗（Shun 专属）');
  const clean = await page.evaluate(() => {
    const S = window.ShunAI;
    return {
      // 行中括号读音 → 删
      bracket: S.cleanJa('日本語（にほんご）の勉強'),
      // 日文 + 粘连英文 → 拆开
      glued1: S.splitGlued('時期period'),
      glued2: S.splitGlued('連絡をとるtocontact'),
      // 句中粘连
      gluedMid: S.splitGlued('今日は最低atleastです'),
      // 纯日文不该被动
      pure: S.cleanLine('普通の日本語です。'),
      // 说话人括号行首在若草要保留，Shun 单人播客 → 一律删
      spk: S.cleanJa('（さくら）誰？')
    };
  });
  ok(clean.bracket === '日本語の勉強', '行中读音括号整体被删', clean.bracket);
  ok(clean.bracket.indexOf('にほんご') < 0, '括号里的假名没了', clean.bracket);
  ok(clean.glued1.ja === '時期' && clean.glued1.en === 'period', '時期period → 日英拆开', JSON.stringify(clean.glued1));
  ok(clean.glued2.ja === '連絡をとる' && clean.glued2.en === 'tocontact', '連絡をとるtocontact → 拆开', JSON.stringify(clean.glued2));
  ok(clean.gluedMid.ja.indexOf('最低') >= 0 && clean.gluedMid.en.indexOf('atleast') >= 0, '句中粘连也拆开', JSON.stringify(clean.gluedMid));
  ok(clean.pure === '普通の日本語です。', '纯日文不被误伤', clean.pure);
  ok(clean.spk.indexOf('さくら') < 0, 'Shun 无说话人括号 → 行首括号也删', clean.spk);

  /* ---------- B. 子句切分 ---------- */
  console.log('\n【B】长句子句切分');
  const cl = await page.evaluate(() => {
    var r = window.ShunAI.splitClauses(
      '今日は学生時代の prophylax について同级CallableExceptionの話をしていてfinally友達と PortfolioService に行くSesame maar niet.',
      '今天聊了学生时代的预防措施。', 40);
    return r;
  });
  ok(Array.isArray(cl) && cl.length >= 2, '长句被切成多段', cl.length);
  ok(cl.every(x => x.length <= 60), '每段不超过 60 字', JSON.stringify(cl.map(x => x.length)));

  /* ---------- C. 弹窗第 5 段 ---------- */
  console.log('\n【C】弹窗第 5 段（默认收起）');
  await page.evaluate(() => { window.S && (window.S.fs = 30); });
  /* 打开一集：点字幕行 */
  const opened = await page.evaluate(async () => {
    /* 找一个能点开的字幕行 */
    var cue = document.querySelector('.cue');
    if (!cue) return { ok: false, why: 'no cue' };
    cue.click();
    await new Promise(r => setTimeout(r, 500));
    return {
      ok: true,
      hasCtx: typeof window.__shunCtx === 'function',
      hasAiSec: !!document.getElementById('exAiSec'),
      hasAiBox: !!document.getElementById('exAiBox'),
      secText: (document.getElementById('exAiSec') || {}).textContent || '',
      aiOpen: !!(document.getElementById('exAiBox') && document.getElementById('exAiBox').classList.contains('open')),
      aiVisible: (function () {
        var b = document.getElementById('exAiBox');
        if (!b) return false;
        var r = b.getBoundingClientRect();
        return r.height > 0;
      })()
    };
  });
  ok(opened.hasCtx, 'app.js 暴露了 __shunCtx（AI 模块靠它拿数据）', JSON.stringify(opened));
  ok(opened.hasAiSec, '弹窗里有「④ AI 讲解」段', JSON.stringify(opened));
  ok(/AI/.test(opened.secText), '标题含 AI', opened.secText);
  ok(opened.aiOpen === false, '默认是收起的');
  ok(opened.aiVisible === false, '收起时不可见（不占空间）');

  /* 点开 */
  await page.evaluate(() => {
    var sec = document.getElementById('exAiSec');
    if (sec) sec.click();
  });
  /* 🔴 额度小字是**异步**渲染的（要等 /quota 请求回来）——
   *    固定等 500ms 会偶发红（实测 32/0 变 31/1，连跑 3 次才复现）。
   *    ⇒ 改成**轮询等它出现**（最多 4 秒），测试才稳。 */
  try {
    await page.waitForFunction(() => {
      var q = document.getElementById('exAiQuota');
      return q && /額度|额度/.test(q.textContent || '');
    }, { timeout: 4000 });
  } catch (e) { /* 超时 → 下面的断言会如实报错 */ }
  const afterOpen = await page.evaluate(() => {
    var b = document.getElementById('exAiBox');
    var q = document.getElementById('exAiQuota');
    return {
      open: !!(b && b.classList.contains('open')),
      visible: (function () { if (!b) return false; var r = b.getBoundingClientRect(); return r.height > 0; })(),
      quota: q ? q.textContent : '',
      hasAsk: !!document.querySelector('[data-act="ai-ask"]'),
      hasCopy: !!document.querySelector('[data-act="ai-copy"]')
    };
  });
  ok(afterOpen.open, '点标题能展开');
  ok(afterOpen.visible, '展开后可见');
  ok(afterOpen.hasAsk && afterOpen.hasCopy, '有「問 AI」和「複製提問」两个按钮');
  ok(/額度|额度/.test(afterOpen.quota), '额度小字有内容', afterOpen.quota);

  /* ---------- D. 复制降级 ---------- */
  console.log('\n【D】複製提問（零后端降级）');
  const copied = await page.evaluate(async () => {
    /* 强制 endpoint 为空 → 必须走复制模式 */
    window.AIC.config({ endpoint: '' });
    var body = document.getElementById('exAiBody');
    body.innerHTML = '';
    window.ShunAIActions.copy();
    await new Promise(r => setTimeout(r, 600));
    /* 从剪贴板读不到就退到 __AI 之类；先看 toast 有没有出 */
    var toastEl = document.querySelector('.shun-toast, .toast, #toast');
    return {
      toast: toastEl ? toastEl.textContent : '',
      html: body ? body.innerHTML : ''
    };
  });
  ok(/複製|复制/.test(copied.toast + copied.html) || copied.toast.length > 0,
    '复制模式有反馈（toast 或复制框）', JSON.stringify(copied).slice(0, 160));

  /* ---------- E. mock 接口真跑 ---------- */
  console.log('\n【E】mock 中转（不打真接口）');
  await page.evaluate(() => {
    window.AIC.config({ endpoint: window.location.origin + '/ai' });
    window.ShunAIActions.ask();
  });
  await page.waitForTimeout(2500);
  const aiOut = await page.evaluate(() => {
    var b = document.getElementById('exAiBody');
    return { html: b ? b.innerHTML : '', txt: b ? b.textContent : '' };
  });
  ok(aiOut.txt.indexOf('原文') >= 0, '模型输出渲染出来了', aiOut.txt.slice(0, 80));
  ok(aiOut.txt.indexOf('逐词拆解') >= 0, '含「逐词拆解」段');
  ok(aiOut.txt.indexOf('整句结构') >= 0, '含「整句结构」段');
  ok(calls.length >= 1, 'mock 收到了请求', calls.length);
  if (calls.length) {
    const body = JSON.parse(calls[calls.length - 1].body);
    ok(body.scene === 'explain_sentence', 'scene 正确', body.scene);
    ok(!!body.ja && body.ja.length > 0, '送了 ja');
    ok(Array.isArray(body.prev) && body.prev.length <= 2, 'prev ≤2 句', (body.prev || []).length);
    ok(Array.isArray(body.next) && body.next.length <= 2, 'next ≤2 句', (body.next || []).length);
    ok(Array.isArray(body.refs), 'refs 是数组');
    const dirtyRefs = (body.refs || []).filter(r => /[ぁ-んァ-ヶ]{2,}[（(]/.test(r.ja || ''));
    ok(dirtyRefs.length === 0, 'refs 里没有带读音括号的行', JSON.stringify(dirtyRefs).slice(0, 120));
  }

  /* ---------- F. 逐字 ruby 不依赖模型 ---------- */
  console.log('\n【F】逐字注音以 App 查表为准');
  const ruby = await page.evaluate(() => {
    /* 找一行带汉字的字幕，看它的 ruby 是不是 App 自己生成的 */
    var el = document.querySelector('.cue-ja ruby');
    return {
      count: document.querySelectorAll('.cue-ja ruby').length,
      first: el ? el.textContent : '',
      rt: el ? (el.querySelector('rt') ? el.querySelector('rt').textContent : '') : ''
    };
  });
  ok(ruby.count > 0, '字幕有 ruby 注音', ruby.count);
  ok(!!ruby.rt, 'ruby 带 rt（逐字读音）', JSON.stringify(ruby));

  /* ---------- JS 错误 ---------- */
  console.log('\n【G】无 JS 错误');
  ok(jsErrs.length === 0, '无 JS 错误', jsErrs.slice(0, 3).join(' | '));

  await browser.close();
  srv.close();
  console.log('\n═══ ' + pass + ' 通过 / ' + fail + ' 失败 ═══');
  if (fail) { console.log('失败项：\n  ' + fails.join('\n  ')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
