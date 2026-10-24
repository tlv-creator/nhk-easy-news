#!/usr/bin/env node
/* 🔴🔴 测试：音频源加载失败时，网页版应该**自动重试**、最终**如实报错**，且**绝不静默**。
 *
 * 起因（2026-10-23 实测）：音频站第一次请求偶发 503（连试即 200）
 *   → 网页版原来第一次失败就弹「这一集没声音」，用户得手动重试 = **假故障报给用户**。
 *
 * 三个场景（**正反两向**）：
 *   A. 第一次 503、之后正常 → **必须自动救回来**（能解码出时长 + 请求 ≥2 次 + 没有「没声音」提示）
 *   B. 一直 503              → **必须最终报「没声音」** + **请求次数有上限**（不无限刷）
 *   C. 🔴 **服务端挂起**（不返回、连 error 事件都不触发）
 *      → **必须靠看门狗兜住**（4 秒内没拿到元数据 ⇒ 判定失败）→ 最终如实报错
 *      （这一条是防「静默失败」的：**用户必须看到提示**）
 *
 * 跑法：node scripts/test_audio_retry.js
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PORT = 18741;
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.m4a': 'audio/mp4' };

let pass = 0, fail = 0; const fails = [];
function ok(cond, name, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; fails.push(name + (extra !== undefined ? '  → ' + extra : '')); console.log('  ❌ ' + name + (extra !== undefined ? '  → ' + extra : '')); }
}

const hits = { flaky: 0, dead: 0, dead2: 0, gone: 0, hang: 0, ok: 0, tried: [] };

const srv = http.createServer((req, res) => {
  const u = req.url.split('?')[0];
  hits.all = hits.all || []; hits.all.push(u);
  const AUDIO = path.join(ROOT, 'audio');
  /* 场景 A：第一次 503，之后真的把音频发出去 */
  if (u.startsWith('/flaky/')) {
    hits.flaky++;
    if (hits.flaky === 1) { res.writeHead(503); return res.end('cold start'); }
    const fa = path.join(AUDIO, path.basename(u));
    if (!fs.existsSync(fa)) { res.writeHead(404); res.end('404'); return; }
    res.writeHead(200, {
      'Content-Type': 'audio/mp4',
      'Content-Length': fs.statSync(fa).size,
      'Accept-Ranges': 'bytes'
    });
    return fs.createReadStream(fa).pipe(res);
  }
  /* 场景 B：一直 503 */
  if (u.startsWith('/dead/')) { hits.dead++; res.writeHead(503); return res.end('always down'); }
  /* 🔴 场景 E：**另一个也 503 的地址**（备用源也挂 ⇒ 用户断网的真实场景） */
  if (u.startsWith('/dead2/')) { hits.dead2++; res.writeHead(503); return res.end('backup down too'); }
  /* 🔴 场景 F：**404**（源站被删）⇒ 与 503 是两种不同故障 */
  if (u.startsWith('/gone/')) { hits.gone++; res.writeHead(404); return res.end('gone'); }
  /* 场景 C：挂起（不返回）—— 连 error 事件都不触发，只能靠看门狗 */
  if (u.startsWith('/hang/')) { hits.hang++; return; }
  /* 🔴 场景 D：**换源目标**（正常返回音频）⇒ 用来验证「主源挂了能自动换源」
   *   （不返回这个路由 ⇒ 换源逻辑等于没被测到） */
  if (u.startsWith('/ok/')) {
    hits.ok++;
    hits.tried.push(u);
    const fa = path.join(AUDIO, path.basename(u));
    if (!fs.existsSync(fa)) { res.writeHead(404); res.end('404'); return; }
    res.writeHead(200, {
      'Content-Type': 'audio/mp4',
      'Content-Length': fs.statSync(fa).size,
      'Accept-Ranges': 'bytes'
    });
    return fs.createReadStream(fa).pipe(res);
  }

  let p = u; if (p === '/') p = '/index.html';
  const f = path.join(ROOT, 'web', p);
  if (fs.existsSync(f) && fs.statSync(f).isFile()) {
    res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
    return fs.createReadStream(f).pipe(res);
  }
  res.writeHead(404); res.end('nf');
});

async function newPage(browser, audioBase, fallbacks) {
  const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  /* 🔴🔴 **必须拦掉引导页** —— 引导页的 `audio` 字段会**覆盖** `?audio=` 参数
   *    （引导页才是权威源，这个优先级是对的）→ 不拦的话测试会打到**真实音频站**，
   *    真实站好好的 → 永远不会失败 → **测试变成恒绿**。 */
  await page.route('**/shun.json*', r => r.abort());
  /* 🔴 显式固定加载 **ep01**：
   *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）⇒ 默认加载 ep13，
   *   而本地 audio 目录只有 ep01~ep12 ⇒ 音频必然加载失败 ⇒ 假红。 */
  await page.addInitScript(() => {
    try { localStorage.setItem('shun.ep', JSON.stringify('ep01')); } catch (e) { }
  });
  /* 🔴🔴 **必须注入 fallback 链**（2026-10-24）：
   *   app.js 的换源逻辑会跳到**备用地址** ⇒ 不注入的话它跳到**真实生产站**
   *   ⇒ 真实站好好的 ⇒「一直 503」场景永远不报错 ⇒ **测试假红/恒绿**
   *   ⇒ 生产环境不注入（走写死的链）⇒ 这里注入的是**本地地址** */
  if (fallbacks && fallbacks.length) {
    await page.addInitScript(fb => { window.__SHUN_AUDIO_FALLBACKS = fb; }, fallbacks);
  }
  /* 🔴🔴 **主源：拦截 app.js 并替换 AUDIO_BASE_LOCAL 常量**（2026-10-24）
   *   为何不用 ?audio= 参数：实测在真实浏览器里 app.js 读到的 AUDIO_BASE
   *   **不是** URL 参数的值（引导页路径会覆盖它）⇒ 参数式调试不可靠
   *   为何不用 window 注入点：同理不生效
   *   ⇒ 而**替换常量**正好对应生产行为（部署时 scripts/set_audio_base.sh 就是这么换站的）
   *   ⇒ 测的仍是真实代码路径（只是配置项换成了本地地址） */
  await page.route('**/app.js', async route => {
    const res = await route.fetch();
    let body = await res.text();
    body = body.replace(/var AUDIO_BASE_LOCAL = '[^']*';/,
      "var AUDIO_BASE_LOCAL = '" + audioBase + "';");
    await route.fulfill({ status: 200, contentType: 'application/javascript', body });
  });
  await page.goto(`http://127.0.0.1:${PORT}/index.html?audio=${encodeURIComponent(audioBase)}`,
    { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.cue', { timeout: 20000 });
  return { page, errors };
}

/* 打开第 1 集，等一段时间后读状态（**轮询**而不是固定 sleep —— 机器忙时固定等待会假红） */
async function openEp1(page, waitMs) {
  await page.evaluate(() => {
    /* 🔴 点 **ep01**（不是「第一个」）：
     *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）
     *   ⇒ 点第一个会点到 ep13 ⇒ 请求 ep13.m4a ⇒ 本地没有 ⇒ 404 ⇒ 假红。 */
    const it = document.querySelector('#epList .ep-item[data-tag="ep01"]')
            || document.querySelector('#epList .ep-item');
    if (it) it.click();
  });
  const deadline = Date.now() + waitMs;
  let st = null;
  while (Date.now() < deadline) {
    st = await page.evaluate(() => {
      const a = document.getElementById('au');
      return {
        ready: a ? a.readyState : -1,
        dur: a ? a.duration : 0,
        err: a && a.error ? a.error.code : 0,
        /* 读 innerText：不管 toast 是 div 还是 HubShell 画的，都能读到用户真正看到的东西
         * （只取「含『没声音』的片段」，别把整页文本塞进日志） */
        txt: (document.body.innerText || '').split('\n')
              .filter(l => l.indexOf('没声音') >= 0).join(' / ')
      };
    });
    if (st.ready >= 1 && st.dur > 60) break;
    if (/没声音/.test(st.txt)) break;
    await page.waitForTimeout(300);
  }
  st.toast = (/没声音/.test(st.txt)) ? '这一集没声音' : '';
  return st;
}

(async () => {
  await new Promise(r => srv.listen(PORT, '127.0.0.1', r));
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors']
  });

  /* 场景 A：第一次 503 */
  console.log('\n【A】第一次请求 503（CDN 冷启动）');
  hits.flaky = 0;
  {
    const base = `http://127.0.0.1:${PORT}/flaky/`;
    const { page, errors } = await newPage(browser, base, [base]);
    const st = await openEp1(page, 9000);
    ok(hits.flaky >= 2, '确实重试了（请求次数 ≥ 2）', '实际 ' + hits.flaky);
    ok(st.ready >= 1 && st.dur > 60, '重试后音频真的解码出来了', JSON.stringify(st));
    ok(st.toast === '', '**没有**弹「没声音」（重试期间不打扰用户）', st.toast);
    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await page.close();
  }

  /* 场景 B：一直 503 */
  console.log('\n【B】一直 503（真故障）');
  hits.dead = 0;
  {
    const base = `http://127.0.0.1:${PORT}/dead/`;
    const { page, errors } = await newPage(browser, base, [base]);
    const st = await openEp1(page, 12000);
    ok(hits.dead <= 5, '重试有上限（不会无限刷）', '实际请求 ' + hits.dead);
    ok(st.toast.indexOf('没声音') >= 0, '最终如实提示「没声音」', JSON.stringify(st));
    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await page.close();
  }

  /* 场景 C：服务端挂起（不返回、error 事件都不触发）→ 只能靠看门狗 */
  console.log('\n【C】服务端挂起（error 事件不触发）→ 看门狗必须兜住');
  hits.hang = 0;
  {
    const base = `http://127.0.0.1:${PORT}/hang/`;
    const { page, errors } = await newPage(browser, base, [base]);
    const st = await openEp1(page, 16000);
    ok(hits.hang >= 1, '请求确实发出去了', '实际 ' + hits.hang);
    ok(st.toast.indexOf('没声音') >= 0, '**看门狗兜住了**并如实提示（没有静默失败）', JSON.stringify(st));
    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await page.close();
  }

  /* 🔴 场景 D：**主源一直 503，但备用源是好的** ⇒ 必须自动换源成功
   *   （这是 2026-10-24 新加的「备用地址链」的核心语义）
   *   ⚠️ 原来**没有这条测试** ⇒ 换源逻辑等于没被验证过 */
  console.log('\n【D】主源 503 + 备用源正常 ⇒ 必须自动换源成功');
  hits.dead = 0; hits.ok = 0; hits.tried = [];
  {
    const main = `http://127.0.0.1:${PORT}/dead/`;
    const backup = `http://127.0.0.1:${PORT}/ok/`;
    const { page, errors } = await newPage(browser, main, [backup]);
    const st = await openEp1(page, 12000);
    ok(hits.dead >= 1, '主源确实被请求过', '实际 ' + hits.dead);
    ok(hits.ok >= 1, '**自动换源**去请求备用地址了', '实际 ' + hits.ok);
    ok(st.ready >= 1 && st.dur > 60, '换源后音频真的解码出来了', JSON.stringify(st));
    ok(st.toast === '', '换源成功后**不报错**', st.toast);
    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await page.close();
  }

  /* 🔴 场景 E：**主源 503 + 备用源也 503** ⇒ 必须最终如实报「没声音」
   *   （用户断网 / 两个站都挂时的真实场景 —— 主线点名要补的那条）
   *   ⇒ 关键：**不能静默失败**（播放器条亮着但声音不动 = 最阴的故障） */
  console.log('\n【E】主源 503 + 备用源也 503 ⇒ 必须最终如实报「没声音」');
  hits.dead = 0; hits.dead2 = 0;
  {
    const main = `http://127.0.0.1:${PORT}/dead/`;
    const backup = `http://127.0.0.1:${PORT}/dead2/`;
    const { page, errors } = await newPage(browser, main, [backup]);
    const st = await openEp1(page, 14000);
    ok(hits.dead >= 1, '主源被请求过', '实际 ' + hits.dead);
    ok(hits.dead2 >= 1, '**备用源也被请求过**（换源真的发生了）', '实际 ' + hits.dead2);
    ok(st.toast.indexOf('没声音') >= 0, '**最终如实提示「没声音」**（没有静默失败）', JSON.stringify(st));
    ok(hits.dead + hits.dead2 <= 8, '请求次数有上限（不会无限刷）', '实际 ' + (hits.dead + hits.dead2));
    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await page.close();
  }

  /* 🔴 场景 F：**主源 404（源站被删）+ 备用源 503**
   *   ⇒ 「源站被删」与「源站活着但报错」是**两种不同故障** ⇒ 换源逻辑对两者的反应可能不同
   *   ⇒ 断言语义同场景 E：换源真的发生 + 最终如实报「没声音」+ 不静默 */
  console.log('\n【F】主源 404（源站被删）+ 备用源 503 ⇒ 也要换源并如实报「没声音」');
  hits.gone = 0; hits.dead2 = 0;
  {
    const main = `http://127.0.0.1:${PORT}/gone/`;
    const backup = `http://127.0.0.1:${PORT}/dead2/`;
    const { page, errors } = await newPage(browser, main, [backup]);
    const st = await openEp1(page, 14000);
    ok(hits.gone >= 1, '主源(404)被请求过', '实际 ' + hits.gone);
    ok(hits.dead2 >= 1, '**404 也会触发换源**（不是只有 503 才换）', '实际 ' + hits.dead2);
    ok(st.toast.indexOf('没声音') >= 0, '最终如实提示「没声音」', JSON.stringify(st));
    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await page.close();
  }

  await browser.close();
  srv.close();

  console.log('\n[debug] 所有请求路径：');
  console.log(JSON.stringify(hits.all.filter(x => /m4a|audio|json/.test(x)).slice(0, 30), null, 0));
  console.log('\n═══ ' + pass + ' 通过 / ' + fail + ' 失败 ═══');
  if (fail) { console.log('失败项：\n  ' + fails.join('\n  ')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
