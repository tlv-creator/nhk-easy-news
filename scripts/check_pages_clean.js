#!/usr/bin/env node
/**
 * check_pages_clean.js —— 发布闸门（行为级）：线上那份必须是干净的
 *
 * 为什么不用「源码里有没有 minimax 字样」来判断：
 *   那是**表示**。我要验的是**事实**：页面在学生手机上到底会不会
 *   ① 偷偷往 agent.minimax.cn 发统计请求（带 node_id/session_id）
 *   ② 有没有一个可见的悬浮球盖住按钮
 *
 * 🔴 写这道闸门时修掉的一个「恒绿」：
 *   原来的验收只有一句
 *       const e = getElementById('minimax-floating-ball'); return e ? display : 'none';
 *   ⇒ 「元素根本不存在」和「元素存在但被 CSS 藏了」**都打印 'none'**，
 *     看起来永远通过 ⇒ 就算页面里带着完整注入，它也照样放行。
 *   ⇒ 现在分腿报告，且「不存在」和「被藏」分开说。
 *
 * 判据（全部满足才算干净）：
 *   ① 向 agent.minimax.cn 发出的请求数 == 0        ← 行为级
 *   ② 页面里 <script data-minimax-analytics> 数 == 0  ← 结构级
 *   ③ 悬浮球元素若存在，computed display 必须是 none
 *
 * 用法：node tools/check_pages_clean.js <url>
 * 退出码：0 干净 / 1 有问题 / 2 没验成（脚本自身出错）
 */
const { chromium } = require('playwright');

const URL_ = process.argv[2];
const CHROME = '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';

if (!URL_) {
  console.error('❌ 没给 URL（查不到就不能放行）');
  process.exit(2);
}

(async () => {
  const b = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  const ctx = await b.newContext({ ignoreHTTPSErrors: true });
  const p = await ctx.newPage();

  // ── 行为级：数真实发出去的请求 ──
  const beaconReqs = [];
  const pageErrors = [];
  p.on('request', (r) => {
    const u = r.url() || '';
    if (u.indexOf('agent.minimax.cn') >= 0) beaconReqs.push(u);
  });
  p.on('pageerror', (e) => pageErrors.push(String(e && e.message ? e.message : e)));

  let status = null;
  try {
    const r = await p.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 40000 });
    status = r ? r.status() : null;
    await p.waitForSelector('#student-id', { timeout: 20000 });
    // 多等一会儿：统计脚本可能在 load 之后才发请求
    await p.waitForTimeout(2500);

    const info = await p.evaluate(() => {
      const scripts = document.querySelectorAll('script[data-minimax-analytics]');
      const el = document.getElementById('minimax-floating-ball');
      return {
        analyticsScripts: scripts.length,
        ballExists: !!el,
        ballDisplay: el ? getComputedStyle(el).display : null,
        ballRect: el ? (function () {
          const r = el.getBoundingClientRect();
          return { w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), left: Math.round(r.left) };
        })() : null,
        title: document.title,
        studentIdInput: !!document.getElementById('student-id'),
        bodyLen: (document.body && document.body.innerText || '').length,
      };
    });

    console.log('    HTTP 状态码 = ' + status);
    console.log('    title = ' + info.title);
    console.log('    登录框 = ' + (info.studentIdInput ? '有 ✅' : '无 ❌') + '   正文长度 = ' + info.bodyLen);

    // ── 分腿报告 ──
    console.log('    ── ① 统计脚本腿（源码里有没有）──');
    console.log('       <script data-minimax-analytics> 个数 = ' + info.analyticsScripts);
    console.log('    ── ② 悬浮球腿（行为上会不会挡按钮）──');
    if (!info.ballExists) {
      console.log('       悬浮球元素：不存在（页面里没有这个东西）');
    } else {
      console.log('       悬浮球元素：存在，display = ' + info.ballDisplay +
                  '，占位 = ' + JSON.stringify(info.ballRect) +
                  (info.ballDisplay === 'none' ? '  ← 被 CSS 藏住了 ✅' : '  ← 🔴 可见！会盖住按钮'));
    }
    console.log('    ── ③ 出网腿（真的发出去没有）──');
    console.log('       发往 agent.minimax.cn 的请求数 = ' + beaconReqs.length);
    if (pageErrors.length) {
      console.log('    ⚠ 页面 JS 错误 ' + pageErrors.length + ' 条:');
      pageErrors.slice(0, 3).forEach((e) => console.log('       ' + e.slice(0, 160)));
    }

    const problems = [];
    if (beaconReqs.length > 0) {
      problems.push('向 agent.minimax.cn 发了 ' + beaconReqs.length + ' 个请求（统计上报，会带设备标识）');
    }
    if (info.analyticsScripts > 0) {
      problems.push('页面里有 ' + info.analyticsScripts + ' 个统计脚本标签');
    }
    if (info.ballExists && info.ballDisplay !== 'none') {
      problems.push('悬浮球可见（display=' + info.ballDisplay + '），会盖住按钮');
    }
    if (!info.studentIdInput) {
      problems.push('登录框 #student-id 不存在 —— 页面没正常渲染');
    }
    if (status !== 200) {
      problems.push('HTTP 状态码 = ' + status + '（不是 200）');
    }

    await b.close();

    if (problems.length) {
      console.log('');
      console.log('⛔ 不干净：');
      problems.forEach((p2) => console.log('   · ' + p2));
      process.exit(1);
    }
    console.log('');
    console.log('✅ 干净：无统计上报、无可见悬浮球、登录页正常、HTTP 200');
    process.exit(0);
  } catch (e) {
    await b.close();
    console.error('❌ 验收脚本自己失败（这不算「干净」，也不能算「不干净」）：');
    console.error('   ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n   ') : String(e)));
    process.exit(2);
  }
})();
