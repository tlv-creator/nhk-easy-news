/*!
 * AI 清洗层专项测试（🔴 全程 mock，不打真接口）
 *
 * 覆盖两处清洗：
 *   ① 上游 ai-client.js 的 cleanOrphan / cleanBadParen（在 md() 里）
 *   ② Shun 侧 shun-ai-ui.js 的 cleanAiHtml（只处理幻觉英文）
 *
 * ⚠️ 重点断言**合法的相似物不能被误伤**：
 *   `- て：此处连用…` 必须保留（不能「把 `：` 前面都删」）
 *   `“after graduating from university”` 整段英文引用必须保留
 *   `- 最高気温（31.2度）` 必须保留（括号里是数字不是假名）
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PORT = process.env.SHUN_AI_PORT || 18993;
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';

let pass = 0, fail = 0; const fails = [];
const ok = (c, n, e) => {
  if (c) { pass++; console.log('  ✅ ' + n); }
  else { fail++; fails.push(n + (e !== undefined ? '  → ' + e : '')); console.log('  ❌ ' + n + (e !== undefined ? '  → ' + e : '')); }
};
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };

(async () => {
  const srv = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url !== '/ai' && req.url !== '/quota' && !req.url.startsWith('/ai?')) {
      let p = req.url.split('?')[0];
      if (p === '/') p = '/index.html';
      const f = path.join(ROOT, 'web', p);
      if (fs.existsSync(f) && fs.statSync(f).isFile()) {
        res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
        return fs.createReadStream(f).pipe(res);
      }
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
  await page.goto(`http://127.0.0.1:${PORT}/index.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.cue', { timeout: 20000 });
  await page.waitForTimeout(800);

  /* ---------- A. prompt 与中转逐字一致 ---------- */
  console.log('\n【A】prompt 与中转一致（防漂移）');
  const pv = await page.evaluate(() => ({
    len: (window.AIC && window.AIC.CFG.systemPrompt.length) || 0,
    /* v1.5 的实际措辞（别按旧版猜）：
     *   「例句是**可选**的」                ← 新增
     *   「一旦要写，必须逐字取自上下文」    ← 注意：**没有「例句」前缀**
     *   「例句优先取上一句」                ← v1.4 有，v1.5 已删 */
    hasNew: !!(window.AIC && window.AIC.CFG.systemPrompt.indexOf('例句是**可选**的') >= 0),
    hasOld: !!(window.AIC && window.AIC.CFG.systemPrompt.indexOf('必须逐字取自上下文') >= 0)
  }));
  /* 🔴 不断言**硬编码长度** —— 中转改一次 prompt 长度就变（743 → 1005），
   *    硬编码会让测试变成「每次中转升级都要改测试」的假绿灯。
   *    正确做法：**跟中转实时比对**（能拉到就逐字比，拉不到就退回长度下限）。 */
  let remote = null, remoteErr = '';
  try {
    const r = await fetch('https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai/prompt');
    if (r.ok) { const j = await r.json(); if (j && j.ok && j.prompt) remote = j.prompt; }
  } catch (e) { remoteErr = String(e && e.message || e); }
  if (remote) {
    const local = await page.evaluate(() => window.AIC.CFG.systemPrompt);
    ok(!/\uFFFD/.test(remote), '中转 prompt 无 U+FFFD（解码没坏）');
    ok(local === remote, 'prompt 与中转**逐字一致**', `本地 ${local.length} vs 中转 ${remote.length}`);
  } else {
    ok(pv.len >= 1000, 'prompt 长度 ≥1000（中转拉不到，退回长度下限）', pv.len + ' ' + remoteErr);
    console.log('  ⚠️ 中转 prompt 拉不到（' + remoteErr + '）—— 跳过逐字比对');
  }
  /* 🔴 v1.5（prompt 1036）：例句改成**可选** ——
   *    「例句优先取上一句」整行被删（那是 v1.4 的措辞，已不存在），
   *    但「必须逐字取自上下文」和「不许把当前句重复当例句」**仍在**（硬性规则里）。 */
  ok(pv.hasNew, '含「例句是**可选**的」', pv.hasNew ? '' : 'prompt 里没找到');
  ok(pv.hasOld, '含「必须逐字取自上下文…」');
  const noDup = await page.evaluate(() =>
    !!(window.AIC && window.AIC.CFG.systemPrompt.indexOf('不许把当前句重复当例句') >= 0));
  ok(noDup, '含「不许把当前句重复当例句」');
  const gone = await page.evaluate(() =>
    !!(window.AIC && window.AIC.CFG.systemPrompt.indexOf('例句优先取上一句') >= 0));
  ok(!gone, '已移除 v1.4 的「例句优先取上一句…」');
  /* 🔴 新 prompt 的核心：**讲句子结构**，不做逐词拆解 */
  const hasStruct = await page.evaluate(() =>
    !!(window.AIC && window.AIC.CFG.systemPrompt.indexOf('句子结构') >= 0));
  ok(hasStruct, '含「句子结构」（新答题模式）');

  /* ---------- B2. 结构段英文垃圾（v1.5 新增 cleanStructLines）---------- */
  console.log('\n【B2】成分内部骨架的英文垃圾行');
  const struct = await page.evaluate(() => {
    const t = x => window.AIC.aiStruct(x).replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
    return {
      /* 模型先吐错版再吐对版 → 错版整行要丢 */
      wrongFirst: t('C 内部骨架：X で Y ickle?\nC 内部骨架：X で Y'),
      wrongLast:  t('C 内部骨架：X で Y\nC 内部骨架：X で Y ickle?'),
      /* 正常日文行不能误伤 */
      normal:     t('C 内部骨架：X に Y ように'),
      /* 普通成分行不受影响 */
      plain:      t('A：私')
    };
  });
  ok(struct.wrongFirst.indexOf('ickle') < 0, '英文垃圾行被丢弃（先错后对）', struct.wrongFirst);
  ok(struct.wrongLast.indexOf('ickle') < 0, '英文垃圾行被丢弃（对后错）', struct.wrongLast);
  ok(struct.normal.indexOf('ように') >= 0, '正常日文内部骨架保留', struct.normal);
  ok(struct.plain.indexOf('私') >= 0, '普通成分行不受影响', struct.plain);

  /* ---------- B. 孤儿括号 ---------- */
  console.log('\n【B】孤儿括号（上游 cleanOrphan）');
  const orphan = await page.evaluate(() => {
    const strip = h => h.replace(/<[^>]*>/g, '').replace(/^\s*[-*]\s*/, '').replace(/\s+/g, '');
    return {
      bad: strip(window.AIC.md('- 】：原句中的…')),
      good: strip(window.AIC.md('- て：此处连用…')),
      good2: strip(window.AIC.md('- 【重点】主语提示：大家'))
    };
  });
  ok(orphan.bad === '原句中的…', '`- 】：…` 的孤立括号被清掉', orphan.bad);
  ok(orphan.good === 'て：此处连用…', '`- て：…` 合法行**保留**', orphan.good);
  ok(orphan.good2.indexOf('【重点】') >= 0, '`- 【重点】…` 保留', orphan.good2);

  /* ---------- C. 崩括号 ---------- */
  console.log('\n【C】模型把假名标崩（上游 cleanBadParen）');
  const bad = await page.evaluate(() => {
    const strip = h => h.replace(/<[^>]*>/g, '').replace(/^\s*[-*]\s*/, '').replace(/\s+/g, '');
    return {
      cjk: strip(window.AIC.md('- 緑茶（りょ在国际这里指"绿茶"）')),
      eng: strip(window.AIC.md('- 緑茶（りょ-cha tea）')),
      ok1: strip(window.AIC.md('- 緑茶（げんき）')),
      ok2: strip(window.AIC.md('- 緑茶（绿茶）')),
      ok3: strip(window.AIC.md('- 最高気温（31.2度）')),
      ok4: strip(window.AIC.md('- 英語（English）'))
    };
  });
  ok(bad.cjk === '緑茶', '崩括号（混中文）→ 整组剥掉', bad.cjk);
  ok(bad.eng === '緑茶', '崩括号（混英文）→ 整组剥掉', bad.eng);
  ok(bad.ok1 === '緑茶（げんき）', '正常假名注音保留', bad.ok1);
  ok(bad.ok2 === '緑茶（绿茶）', '中文注音保留', bad.ok2);
  ok(bad.ok3 === '最高気温（31.2度）', '数字内容保留', bad.ok3);
  ok(bad.ok4 === '英語', '英文注音剥掉', bad.ok4);

  /* ---------- D. 幻觉英文（Shun 侧） ---------- */
  console.log('\n【D】幻觉英文（Shun 侧 cleanAiHtml）');
  const hall = await page.evaluate(() => {
    /* cleanAiHtml 在 shun-ai-ui.js 里，没导出 → 用 md() 后的结果模拟 */
    const html = window.AIC.md('- て：此处连用"cling"式判断的"です"');
    /* 从 shun-ai-ui.js 抠出 cleanAiHtml */
    const src = window.__shunAiSrc || '';
    return { html: html.replace(/<[^>]*>/g, '') };
  });
  /* 直接对 cleanAiHtml 做单元测试（从源码抠函数） */
  const aiUiSrc = fs.readFileSync(path.join(ROOT, 'web/shun-ai-ui.js'), 'utf-8');
  const m = aiUiSrc.match(/function cleanAiHtml\(html\)\s*\{[\s\S]*?\n  \}/);
  ok(!!m, 'shun-ai-ui.js 里有 cleanAiHtml');
  if (m) {
    const fn = new Function('return (' + m[0] + ')')();
    const cases = [
      ['连用“cling”式判断', '连用“「cling」”式判断', '幻觉单词降级（外层引号保留，内层降级）'],
      ['相当于“after graduating from university”', '相当于“after graduating from university”', '整段英文引用**不动**'],
      ['对应下文的“new friends”', '对应下文的“new friends”', '双词英文引用**不动**'],
      ['助词“wa”', '助词“wa”', '罗马音**不动**'],
      ['他说“です”', '他说“です”', '日文**不动**']
    ];
    for (const [inp, exp, note] of cases) {
      const got = fn(inp);
      ok(got === exp, note, '得到: ' + got);
    }
  }

  /* ---------- D2. 墨水屏：不能有纯黑/纯白**盒子**描边（能解析 var 变量链）---------- */
  console.log('\n【D2】墨水屏描边（盒子边框不能纯黑/纯白）');
  {
    /* 🔴 用 check_eink_css.js —— 它能**解析 var() 变量链**。
     *    字面量正则（`border: Npx solid #000`）查不到 `var(--fg)` 形式
     *    → 曾经漏检（NHK 那边就栽在这个变体上）。 */
    const { spawnSync } = require('child_process');
    const cssFiles = ['ai-client.css', 'style.css', 'shell.css']
      .map(f => path.join(ROOT, 'web', f)).filter(f => fs.existsSync(f));
    /* 🔴 必须**把所有 css 一起传** —— 变量表跨文件合并
     *    （style.css 的 --line 定义在 shell.css 里；单查一个文件 → 误报一堆）*/
    const r = spawnSync(process.execPath,
      [path.join(ROOT, 'scripts', 'check_eink_css.js')].concat(cssFiles), { encoding: 'utf-8' });
    ok(r.status === 0, '全站 css 无纯黑/纯白盒子描边（var 变量链也算）',
       (r.stdout || '').trim().split('\n').slice(-3).join(' / '));
    /* 结构线（border-bottom）保留实黑是**对的** —— 只查数量上限 */
    const css = fs.readFileSync(path.join(ROOT, 'web', 'ai-client.css'), 'utf-8');
    const bb = (css.match(/border-bottom:[^;]+;/g) || []).filter(x => /#000/.test(x));
    ok(bb.length <= 2, 'ai-client.css 的 border-bottom 实黑 ≤2 条（结构线）', bb.length + ' 条');
  }

  /* ---------- E. 无 JS 错误 ---------- */
  console.log('\n【E】无 JS 错误');
  ok(errs.length === 0, '无 JS 错误', errs.slice(0, 3).join(' | '));

  await browser.close();
  srv.close();
  console.log('\n═══ ' + pass + ' 通过 / ' + fail + ' 失败 ═══');
  if (fail) { console.log('失败项：\n  ' + fails.join('\n  ')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
