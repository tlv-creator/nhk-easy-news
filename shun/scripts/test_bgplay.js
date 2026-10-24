/*!
 * 后台播放 / 播放状态 专项测试（Shun）
 *
 * 覆盖两类：
 *   ① **静态断言**：源码层面「后台播放的四道坎」是不是真的补上了
 *      —— 没有前台服务 / onPause 一刀切 pause / onDestroy 就 stop /
 *         播放器非静态（重建就两个一起响 + 抢端口丢 localStorage）
 *   ② **行为断言**：假原生桥喂 tick → 播放按钮**不能左右横跳**
 *      （准备期原生报 playing=false，把前端的乐观状态顶回去）
 *
 * 🔴 判据用「最后 4 拍必须一模一样」而不是「变化次数」——
 *    一次性变化是正常的（音频真开始播了），横跳才是 bug。
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
/* 🔴 端口用 0（系统分配空闲端口）—— 固定端口会打到上一个没退干净的进程上
 *   （我这轮就踩过：EADDRINUSE + 测到的其实是旧服务器）*/
const PORT0 = process.env.SHUN_BG_PORT ? parseInt(process.env.SHUN_BG_PORT, 10) : 0;
const CHROME = process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome';
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };

let pass = 0, fail = 0; const fails = [];
const ok = (c, n, e) => {
  if (c) { pass++; console.log('  ✅ ' + n); }
  else { fail++; fails.push(n + (e !== undefined ? '  → ' + e : '')); console.log('  ❌ ' + n + (e !== undefined ? '  → ' + e : '')); }
};
/* 🔴🔴🔴 **mock 的单位必须从原生源码推导，不能手写**（NHK 2026-10-23 的教训，我原样也中了）
 *
 *   NHK 的原话：「mock 是『关于设备的断言』，手写就等于把自己的假设当真。」
 *   ⇒ 我原来的假原生桥**手写**了「pos 用秒」——那是我自己的假设，不是设备的真相
 *   ⇒ 一旦原生层改成毫秒，我的测试照样全绿，而真机是坏的。
 *
 *   做法：读 NativePlayer.java 里 **getCurrentPosition() 的赋值行**
 *         —— 有没有 /1000 ⇒ 秒 / 毫秒 ⇒ mock 按这个单位喂。
 *   ⇒ 好处：原生层一改单位，**测试立刻变红**（ticker 的换算与源码不再匹配）
 *         ⇒ 两层被这条测试**绑在一起**，不会再各改各的。
 */
function nativeUnitFromSource(src) {
  const lines = (src || '').split('\n');
  let saw = false, divides = false;
  for (const l of lines) {
    if (l.indexOf('getCurrentPosition()') < 0) continue;
    saw = true;
    if (/\/\s*1000/.test(l)) divides = true;
  }
  if (!saw) return { unit: 'unknown', evidence: '(源码里没有 getCurrentPosition())' };
  return {
    unit: divides ? 'sec' : 'ms',
    evidence: lines.filter(l => l.indexOf('getCurrentPosition()') >= 0).map(l => l.trim()).join(' | ')
  };
}

const read = (p) => fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : '';

/* 🔴 从 Java 源码里抠出 `String js = "..." + "...";` 的实际内容。
 *    用它来跑「真机上跑的那段 JS」⇒ 测的是代码，不是「我以为的代码」。*/
function extractTickerJs(javaSrc) {
  /* ⚠️ 两个坑（都踩过）：
   *   ① 不能按「第一个 ;」截断 —— 字符串**里面**就有分号（`playerState());"`）
   *   ② 拼接串的**中间夹着 // 注释行**（`+ "..."` 之后下一行是注释，再下一行才是 `+ "..."`）
   *      ⇒ 跳过空白/注释之后必须还能看到 '+' 才继续。 */
  const key = 'String js =';
  let p = javaSrc.indexOf(key);
  if (p < 0) return null;
  p += key.length;
  const out = [];
  const lit = /"((?:[^"\\]|\\.)*)"/;
  const skip = () => {
    while (p < javaSrc.length) {
      const c = javaSrc[p];
      if (c === ' ' || c === '\t' || c === '\n' || c === '\r') { p++; continue; }
      if (c === '/' && javaSrc[p + 1] === '/') { const nl = javaSrc.indexOf('\n', p); p = nl < 0 ? javaSrc.length : nl + 1; continue; }
      break;
    }
  };
  for (;;) {
    skip();
    const m = lit.exec(javaSrc.slice(p));
    if (!m) break;
    out.push(m[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\'));
    p += m[0].length;
    skip();
    if (javaSrc[p] !== '+') break;
    p += 1;
  }
  return out.length ? out.join('') : null;
}
const J = (p) => path.join(ROOT, p);
/* 🔴🔴 断言前必须**去掉注释** ——
 *   「注释里出现过 player.stop()」不等于「代码里调用了 player.stop()」。
 *   （我自己写过一句「这里不能 player.stop()」的注释，被自己的断言判成了 bug。）
 *   `//` 只在前面不是冒号时才当注释（免得把 http:// 砍掉半行）。*/
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

/* ══════════════════════════ ① 静态断言 ══════════════════════════ */
console.log('\n【A】后台播放的四道坎（源码断言）');

const manifest = read(J('apk-template/AndroidManifest.xml'));
const act = strip(read(J('apk-template/java/com/shun/study/MainActivity.java')));
const svc = strip(read(J('apk-template/java/com/shun/study/PlaybackService.java')));
const np = strip(read(J('apk-template/java/com/shun/study/NativePlayer.java')));
const rt = strip(read(J('apk-template/java/com/shun/study/ShunRuntime.java')));
const appjs = strip(read(J('web/app.js')));

ok(/android:name="\.PlaybackService"/.test(manifest), 'manifest 声明了 PlaybackService');
ok(/foregroundServiceType="mediaPlayback"/.test(manifest), 'manifest 声明 foregroundServiceType=mediaPlayback');
ok(/FOREGROUND_SERVICE"/.test(manifest), 'manifest 有 FOREGROUND_SERVICE 权限');
ok(/FOREGROUND_SERVICE_MEDIA_PLAYBACK/.test(manifest), 'manifest 有 FOREGROUND_SERVICE_MEDIA_PLAYBACK 权限');
ok(/POST_NOTIFICATIONS/.test(manifest), 'manifest 有 POST_NOTIFICATIONS 权限（Android 13+）');

ok(!!svc, 'PlaybackService.java 存在');
ok(svc.length > 0 && /startForeground\s*\(/.test(svc), 'PlaybackService 调了 startForeground');
{
  // 🔴 startForeground 必须是 onCreate 的**第一行**（前台服务 5 秒红线）
  const m = svc.match(/public void onCreate\(\)\s*\{([\s\S]{0,400})/);
  const body = m ? m[1] : '';
  const firstCall = body.match(/startForeground/);
  ok(!!firstCall, 'onCreate 里有 startForeground');
  ok(body.indexOf('startForeground') < 260, 'startForeground 在 onCreate 的开头（5 秒红线）',
    body.slice(0, 120).replace(/\s+/g, ' '));
}
ok(/ensureStarted/.test(svc), 'PlaybackService 有 ensureStarted（服务移出启动路径）');
{
  const m = act.match(/public void onPause\(\)\s*\{([\s\S]{0,300}?)\n    \}/);
  const body = m ? m[1] : '';
  ok(!/player\.pause\s*\(/.test(body), 'onPause 不再一刀切 player.pause()（否则一进后台就断声）', body.trim().slice(0, 90));
}
{
  const m = act.match(/protected void onDestroy\(\)\s*\{([\s\S]{0,600}?)\n    \}/);
  const body = m ? m[1] : '';
  ok(!/player\.stop\s*\(/.test(body), 'onDestroy 不再 player.stop()（否则划掉界面就断声）');
  ok(!/proxy\.stop\s*\(/.test(body), 'onDestroy 不再 proxy.stop()（服务还活着时不能拆代理）');
}
ok(/ShunRuntime\.init\s*\(/.test(act), 'MainActivity 走 ShunRuntime（静态复用）');
ok(rt.length > 0 && /static/.test(rt) && /NativePlayer player/.test(rt), 'ShunRuntime 静态持有播放器');
ok(!/new\s+NativePlayer\s*\(/.test(act), 'MainActivity 不再自己 new 播放器（否则重建出两个）');

ok(/wantPlay/.test(np), 'NativePlayer 有 wantPlay（用户意图）');
ok(/isWanted\s*\(/.test(np), 'NativePlayer 有 isWanted()');
ok(/isActuallyPlaying\s*\(/.test(np), 'NativePlayer 有 isActuallyPlaying()');
ok(/isPlaying\s*\(\)\s*\{\s*return wantPlay \|\| playing/.test(np.replace(/\s+/g, ' ')),
  'isPlaying() 按**意图**回答（准备期也 true → 按钮不横跳）');

/* API 26 隔离：NotificationChannel 只能出现在 Api26 内部类里
 * 🔴 注意：源码里的 `import android.app.NotificationChannel;` 是**编译期**的，
 *    **不产生任何字节码引用** → 本身无害，不能算违规。
 *    真正要看的是「类本体里有没有代码引用它」——那要看字节码（apk_check_api.py 干这个）。
 *    所以这里只查 import 之外的代码。 */
{
  const svcBody = svc.replace(/static final class Api26[\s\S]*$/, '')
    .replace(/^\s*import\s+[^;]+;\s*$/gm, '');
  ok(!/NotificationChannel/.test(svcBody), 'PlaybackService 本体不引用 NotificationChannel（老系统类加载就炸）');
  ok(/static final class Api26/.test(svc), 'API 26 符号隔离在 Api26 内部类里');
  const api26 = svc.match(/static final class Api26[\s\S]*$/);
  ok(!!api26 && /Build\.VERSION\.SDK_INT\s*>=\s*Build\.VERSION_CODES\.O/.test(api26[0]),
    'Api26 内部也有 SDK_INT 守卫（静态检查器按方法内字节码判定）');
}

/* 前端守卫 */
ok(/d\.ready \|\| d\.err/.test(appjs), '前端 __hubTick 有 ready 守卫（原生未就绪不覆盖乐观状态）');
ok(/Math\.round\(pct \* 200\) \/ 2/.test(appjs.replace(/\s+/g, ' ')), '进度百分比取整到 0.5%（墨水屏少重绘）');

/* ══════════════════════════ ② 行为断言 ══════════════════════════ */
(async () => {
  const srv = http.createServer((req, res) => {
    const u = req.url.split('?')[0];
    /* 🔴 引导页必须**隔离**：不隔离的话页面会去 GitHub Pages 拉**线上**的 shun.json
     *   ⇒ 拉到线上最新的集数（Ep278 上线后是 13 集、最新在前）
     *   ⇒ 页面加载的集与测试按本地算的期望对不上 ⇒ 假红。
     *   ⇒ 这里让引导页 404 ⇒ 页面回落到**本地** data/eps/（测试的基线）。 */
    if (/shun\.json$/.test(u)) { res.writeHead(404); return res.end('boot blocked'); }
    let p = u; if (p === '/') p = '/index.html';
    const f = path.join(ROOT, 'web', p);
    if (fs.existsSync(f) && fs.statSync(f).isFile()) {
      res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
      return fs.createReadStream(f).pipe(res);
    }
    res.writeHead(404); res.end('nf');
  });
  await new Promise(r => srv.listen(PORT0, '127.0.0.1', r));
  const PORT = srv.address().port;   /* 🔴 实际端口（0 = 让系统分配） */

  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors']
  });

  /* 两种原生语义都要测：
   *   A 新语义：playing = 意图（准备期 true）
   *   B 老语义：playing = 实际（准备期 false）← 前端守卫就是为它准备的 */
  const scenarios = [
    { name: '新原生语义（playing=意图）', tick: { pos: 0, dur: 60000, playing: true, actualPlaying: false, ready: false, completed: false, error: '' } },
    { name: '老原生语义（playing=实际）', tick: { pos: 0, dur: 60000, playing: false, actualPlaying: false, ready: false, completed: false, error: '' } }
  ];

  for (const sc of scenarios) {
    console.log('\n【B】播放按钮不横跳 · ' + sc.name);
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    /* 🔴 引导页**跨域**（GitHub Pages）⇒ 服务器层拦不到 ⇒ 必须用 page.route 拦。
     *   不拦 ⇒ 页面拉到线上最新集（Ep278 上线后 13 集、最新在前）⇒ 页面加载的是 ep13，
     *   而测试按本地 ep01 算期望 ⇒ 差一句的假红。 */
    await page.route('**/shun.json*', r => r.fulfill({ status: 404, body: 'boot blocked' }));
    /* 🔴 显式指定加载 **ep01**：
     *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）⇒ 默认加载的不是 ep01，
     *   而下面的期望值是按 web/data/eps/ep01.json 算的 ⇒ 对不上（差一句的假红）。
     *   ⇒ 把「加载哪一集」固定住，测试才有确定基线。 */
    await page.addInitScript(() => {
      try { localStorage.setItem('shun.ep', JSON.stringify('ep01')); } catch (e) { }
    });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));

    await page.addInitScript(() => {
      window.__T = { state: { pos: 0, dur: 60000, playing: false, actualPlaying: false, ready: false, completed: false, error: '' }, calls: [] };
      const rec = (k) => function () { window.__T.calls.push(k); };
      window.Android = {
        playEpisode: rec('playEpisode'), playAudio: rec('playAudio'),
        resumeAudio: rec('resumeAudio'), pauseAudio: rec('pauseAudio'),
        seekAudio: rec('seek'), stopAudio: rec('stop'), setSpeed: rec('speed'),
        playerState: function () { return JSON.stringify(window.__T.state); },
        ping: function () { return '{"port":0,"native":true,"proxyOk":true}'; },
        version: function () { return '1.2'; },
        cacheEpisode: function () { return '{"ok":true}'; },
        downloadedList: function () { return '{}'; },
        downloadStatus: function () { return '{}'; },
        clearDownloads: function () { return '{}'; },
        resolveAudio: function () { return ''; },
        selfTest: function () { return '{}'; },
        downloadAudio: rec('downloadAudio'), deleteAudio: rec('deleteAudio')
      };
    });

    await page.goto('http://127.0.0.1:' + PORT + '/index.html', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!window.__hubTick, { timeout: 15000 });

    /* 用户点了播放 → 前端乐观置位 */
    await page.evaluate(() => {
      const b = document.getElementById('btnPlay');
      if (b) b.click();
    });
    const afterClick = await page.evaluate(() => document.getElementById('btnPlay').textContent);
    ok(afterClick === '❚❚', '点播放后按钮显示「播放中」', afterClick);

    /* 连喂 4 拍「准备期」的 tick（老语义下 playing=false）→ 按钮不能被顶回 ▶ */
    const shots = [];
    for (let i = 0; i < 4; i++) {
      await page.evaluate((t) => window.__hubTick(t), sc.tick);
      shots.push(await page.evaluate(() => document.getElementById('btnPlay').textContent));
    }
    ok(shots.slice(1).every(s => s === shots[1]), '最后 3 拍按钮一模一样（不横跳）', shots.join(' '));
    ok(shots[3] === '❚❚', '准备期按钮保持「播放中」（不被原生顶回 ▶）', shots[3]);

    /* 真的暂停了 → 必须变成 ▶ */
    await page.evaluate(() => window.__hubTick({ pos: 1000, dur: 60000, playing: false, actualPlaying: false, ready: true, completed: false, error: '' }));
    const paused = await page.evaluate(() => document.getElementById('btnPlay').textContent);
    ok(paused === '▶', '暂停后按钮变回 ▶', paused);

    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await ctx.close();
  }

  /* ══════════════════════════ ③ 当前句不变色 ══════════════════════════ */
  console.log('\n【C】当前句不变色（底色/字色跟普通行一样，只留左侧竖条）');
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    /* 🔴 引导页**跨域**（GitHub Pages）⇒ 服务器层拦不到 ⇒ 必须用 page.route 拦。
     *   不拦 ⇒ 页面拉到线上最新集（Ep278 上线后 13 集、最新在前）⇒ 页面加载的是 ep13，
     *   而测试按本地 ep01 算期望 ⇒ 差一句的假红。 */
    await page.route('**/shun.json*', r => r.fulfill({ status: 404, body: 'boot blocked' }));
    /* 🔴 显式指定加载 **ep01**：
     *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）⇒ 默认加载的不是 ep01，
     *   而下面的期望值是按 web/data/eps/ep01.json 算的 ⇒ 对不上（差一句的假红）。
     *   ⇒ 把「加载哪一集」固定住，测试才有确定基线。 */
    await page.addInitScript(() => {
      try { localStorage.setItem('shun.ep', JSON.stringify('ep01')); } catch (e) { }
    });
    await page.goto('http://127.0.0.1:' + PORT + '/index.html', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!document.getElementById('btnPlay'), { timeout: 15000 });

    for (const pal of ['bw', 'ink', 'light']) {
      const r = await page.evaluate((p) => {
        document.documentElement.setAttribute('data-palette', p);
        const mk = (on) => {
          const d = document.createElement('div');
          d.className = 'cue' + (on ? ' on' : '');
          d.innerHTML = '<div class="cue-ja">テスト<span class="cue-zh">测试</span></div>';
          document.body.appendChild(d);
          return d;
        };
        const a = mk(false), b = mk(true);
        const cs = (el) => {
          const s = getComputedStyle(el);
          const ja = el.querySelector('.cue-ja');
          return {
            bg: s.backgroundColor, color: s.color,
            bw: s.borderLeftWidth, bc: s.borderLeftColor,
            jaColor: ja ? getComputedStyle(ja).color : ''
          };
        };
        const ra = cs(a), rb = cs(b);
        a.remove(); b.remove();
        return { ra, rb };
      }, pal);

      ok(r.rb.bg === r.ra.bg, pal + '：当前句底色与普通行一致', r.rb.bg + ' vs ' + r.ra.bg);
      ok(r.rb.color === r.ra.color, pal + '：当前句字色与普通行一致', r.rb.color + ' vs ' + r.ra.color);
      ok(r.rb.jaColor === r.ra.jaColor, pal + '：当前句日文字色与普通行一致', r.rb.jaColor + ' vs ' + r.ra.jaColor);
      ok(r.rb.bc !== r.ra.bc, pal + '：当前句有左侧竖条（颜色与普通行不同）', r.rb.bc + ' vs ' + r.ra.bc);
      ok(r.rb.bw === '4px', pal + '：竖条宽度 4px（普通行预留，切句不左右跳）', r.rb.bw);
    }
    /* 源码层：不能再有反色高亮规则 */
    const css = strip(read(J('web/style.css')));
    ok(!/\.cue\.on\s*\{[^}]*background\s*:/.test(css), '源码：.cue.on 不设 background');
    ok(!/data-palette="bw"\]\s*\.cue\.on\s*\{[^}]*background\s*:#000/.test(css), '源码：bw 主题不再反色高亮');
    ok(!/data-palette="ink"\]\s*\.cue\.on\s*\{[^}]*background\s*:#/.test(css), '源码：ink 主题不再反色高亮');
    await ctx.close();
  }

  /* ══════════════════════════ ④ 原生报错要说人话（不是静默失败） ══════════════════════════ */
  console.log('\n【D】原生层报错必须让用户看见（静默失败 → 如实报错）');
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    /* 🔴 引导页**跨域**（GitHub Pages）⇒ 服务器层拦不到 ⇒ 必须用 page.route 拦。
     *   不拦 ⇒ 页面拉到线上最新集（Ep278 上线后 13 集、最新在前）⇒ 页面加载的是 ep13，
     *   而测试按本地 ep01 算期望 ⇒ 差一句的假红。 */
    await page.route('**/shun.json*', r => r.fulfill({ status: 404, body: 'boot blocked' }));
    /* 🔴 显式指定加载 **ep01**：
     *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）⇒ 默认加载的不是 ep01，
     *   而下面的期望值是按 web/data/eps/ep01.json 算的 ⇒ 对不上（差一句的假红）。
     *   ⇒ 把「加载哪一集」固定住，测试才有确定基线。 */
    await page.addInitScript(() => {
      try { localStorage.setItem('shun.ep', JSON.stringify('ep01')); } catch (e) { }
    });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.addInitScript(() => {
      window.__T = { state: { pos: 0, dur: 60000, playing: false, actualPlaying: false, ready: false, completed: false, error: '' }, calls: [] };
      const rec = (k) => function () { window.__T.calls.push(k); };
      window.Android = {
        playEpisode: rec('playEpisode'), playAudio: rec('playAudio'),
        resumeAudio: rec('resumeAudio'), pauseAudio: rec('pauseAudio'),
        seekAudio: rec('seek'), stopAudio: rec('stop'), setSpeed: rec('speed'),
        playerState: function () { return JSON.stringify(window.__T.state); },
        ping: function () { return '{"port":0,"native":true,"proxyOk":true}'; },
        version: function () { return '1.2'; },
        cacheEpisode: function () { return '{"ok":true}'; },
        downloadedList: function () { return '{}'; },
        downloadStatus: function () { return '{}'; },
        clearDownloads: function () { return '{}'; },
        resolveAudio: function () { return ''; },
        selfTest: function () { return '{}'; },
        downloadAudio: rec('downloadAudio'), deleteAudio: rec('deleteAudio')
      };
    });
    await page.goto('http://127.0.0.1:' + PORT + '/index.html', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!window.__hubTick, { timeout: 15000 });
    /* 🔴🔴 等「这一集真的加载完」再喂错误 tick —— 判据落在**用户看得见的字幕行**上。
     *   ⚠️ 第一版等的是 #epLabel 的文字 —— **错**：index.html 里 #epLabel 的初始内容就是「第1集」
     *   （写死的占位）⇒ 条件立刻成立 ⇒ loadEp 还没跑就去喂 tick
     *   ⇒ init 的 loadEp 随后把 loadSeq 加一 ⇒ 去重键变了 ⇒ **同一个错提示两次** ⇒ 假红。
     *   ⇒ 教训：**「条件成立」不等于「我等的那件事发生了」** —— 要等一个只有那件事发生才会出现的信号。 */
    await page.waitForFunction(() => document.querySelectorAll('.cue').length > 0, { timeout: 25000 });

    /* 🔴 判据落在「用户真正看到的文字」上 —— 不是 hook createElement，
     *   也不是数函数调用（那是表示）。这里既看文字，也数 toast 次数（去重要验）。*/
    await page.evaluate(() => {
      window.__toastCalls = [];
      const h = window.HubShell;
      if (h && h.toast) {
        const orig = h.toast;
        h.toast = function (m) { window.__toastCalls.push(String(m)); return orig(m); };
      }
    });

    const errMsg = '准备超时（15 秒没有响应）';
    /* ① 同一个错误连推 3 拍 → 必须提示，且**只提示一次**（墨水屏上重复 toast = 整屏重绘）
     *   ⚠️ 键名用 **err** —— 这才是原生 ticker 真正推的字段名（MainActivity: err:s.error）
     *   （第一版这里写成 error ⇒ 前端读 d.err 读不到 ⇒ 提示不出现 ⇒ 差点以为是代码没生效）*/
    for (let i = 0; i < 3; i++) {
      await page.evaluate((e) => window.__hubTick({
        pos: 0, dur: 60000, playing: false, actualPlaying: false,
        ready: false, completed: false, err: e
      }), errMsg);
      await page.waitForTimeout(120);
    }
    const body1 = await page.evaluate(() => document.body.innerText);
    const calls1 = await page.evaluate(() => window.__toastCalls.slice());
    const m1 = calls1.filter(m => m.indexOf('这一集没声音') >= 0);
    ok(body1.includes('这一集没声音'), '原生错误有文字提示（不再静默失败）', body1.slice(-140).replace(/\s+/g, ' '));
    ok(body1.includes('准备超时'), '提示里带上了原生给的错误内容', body1.slice(-140).replace(/\s+/g, ' '));
    ok(m1.length === 1,
      '同一个错误连推 3 拍只提示一次（去重）',
      'calls=' + calls1.length + ' | ' + calls1.map(x => x.slice(0, 40)).join(' // '));

    /* ② 换成另一个错误 → 必须再提示一次（不能被去重逻辑吃掉）*/
    await page.evaluate(() => window.__hubTick({
      pos: 0, dur: 60000, playing: false, actualPlaying: false,
      ready: false, completed: false, err: 'MediaPlayer 错误 what=1 extra=-2147483648'
    }));
    await page.waitForTimeout(120);
    const calls2 = await page.evaluate(() => window.__toastCalls.slice());
    ok(calls2.filter(m => m.indexOf('这一集没声音') >= 0).length === 2,
      '换了一个错误就再提示一次', 'calls=' + calls2.length);

    /* ②b 另一种键名（error）也要认 —— playerState 的 JSON 用这个字段，少认一个就静默失效 */
    await page.evaluate(() => window.__hubTick({
      pos: 0, dur: 60000, playing: false, actualPlaying: false,
      ready: false, completed: false, error: '另一种键名的错误'
    }));
    await page.waitForTimeout(120);
    const calls2b = await page.evaluate(() => window.__toastCalls.slice());
    ok(calls2b.filter(m => m.indexOf('另一种键名的错误') >= 0).length === 1,
      '原生用 error 键时报错也要提示', 'calls=' + calls2b.length);

    /* ③ 原生错误清空 → 不再提示 */
    await page.evaluate(() => window.__hubTick({
      pos: 0, dur: 60000, playing: false, actualPlaying: false,
      ready: true, completed: false, err: '', error: ''
    }));
    await page.waitForTimeout(120);
    const calls3 = await page.evaluate(() => window.__toastCalls.slice());
    ok(calls3.length === calls2b.length, '错误消失后不再重复提示', 'calls=' + calls3.length);

    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await ctx.close();
  }

  /* ══════════════════════════ ⑤ 每条路径都要有兜底 ══════════════════════════ */
  console.log('\n【E】重试 / 看门狗：结构上保证「兜底会响」');
  {
    const m = appjs.match(/function audioRetryOnce\(\)\s*\{([\s\S]*?)\n {2}\}/);
    ok(!!m, '有独立的 audioRetryOnce（重试单独一个函数）');
    const body = m ? m[1] : '';
    ok(!/\breturn\b/.test(body),
      'audioRetryOnce 里没有 return（return 会把后面的 armWatch 一起跳掉 ⇒ 静默失败）',
      body.trim().slice(0, 140).replace(/\s+/g, ' '));
    ok(/audioArmWatch\(\)/.test(body), 'audioRetryOnce 里有 audioArmWatch（重试后重新 arm 看门狗）');
    ok(/loadedmetadata/.test(appjs), '重试计数只在 loadedmetadata（真成功）时归零');
    ok(!/loadstart/.test(appjs), '没有在 loadstart 上归零计数（否则无限重试）');

    ok(/PREPARE_TIMEOUT_MS/.test(np), 'NativePlayer 有准备超时常量');
    ok(/armPrepareWatch/.test(np), 'NativePlayer 装了准备超时看门狗');
    ok(/prepareAsync\(\);[\s\S]{0,160}armPrepareWatch\(/.test(np), 'prepareAsync 之后立刻 arm 看门狗');
    ok(/cancelPrepareWatch/.test(np), '有 cancelPrepareWatch（成功/失败/释放都要撤掉）');
    ok(/S\.lastErr/.test(appjs), '前端记着上一次的原生错误（去重）');
    ok(/S\.loadSeq/.test(appjs), '去重键含加载次数（重新点一次这一集 ⇒ 同一个错会再提示一次）');
  }

  /* ══════════════════════════ ⑥ 字幕必须跟着音频走（原生 tick 的单位） ══════════════════════════ */
  console.log('\n【F】字幕跟着音频走（原生 tick 的 pos/dur 单位）');
  {
    /* 🔴🔴 为什么这条能抓到别人抓不到的 bug：
     *   ① **直接跑 MainActivity.java 里那段 ticker JS**（从源码里抠出来）——
     *      不自己重写一份 ⇒ 测的是**真机上跑的那段代码**，不是「我以为的」。
     *   ② 喂一个**真实时间点**（30.0 秒），不是 pos:0 ——
     *      单位错 1000 倍时，pos:0 看起来完全正常（0 秒和 0 毫秒一样）💥
     *   ③ 判据落在**行为**上：那一刻高亮的必须是那一刻的句子。
     *
     * 背景：原生层的 pos/dur 是**秒**（getCurrentPosition()/1000），
     *      而前端 S.pos / binarySearch() 期望**毫秒** ⇒ 单位差 1000 倍
     *      ⇒ APK 里高亮整集停在第一句、进度条不动、时间标签 0:00。
     */
    const javaSrc = read(J('apk-template/java/com/shun/study/MainActivity.java'));
    const tickerJs = extractTickerJs(javaSrc);
    ok(!!tickerJs, '能从 MainActivity.java 里抠出 ticker 的 JS（不重写一份）');

    const data = JSON.parse(read(J('web/data/eps/ep01.json')));
    const lines = [];
    (data.cues || []).forEach(c => (c.rows || []).forEach(r => { if (r.t) lines.push({ s: c.s, e: c.e, t: r.t }); }));
    const T = 30.0;
    let exp = -1;
    for (let i = 0; i < lines.length; i++) if (lines[i].s <= T) exp = i;
    ok(exp >= 0, '数据里 30 秒处有对应的句子', 'exp=' + exp);

    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    /* 🔴 引导页**跨域**（GitHub Pages）⇒ 服务器层拦不到 ⇒ 必须用 page.route 拦。
     *   不拦 ⇒ 页面拉到线上最新集（Ep278 上线后 13 集、最新在前）⇒ 页面加载的是 ep13，
     *   而测试按本地 ep01 算期望 ⇒ 差一句的假红。 */
    await page.route('**/shun.json*', r => r.fulfill({ status: 404, body: 'boot blocked' }));
    /* 🔴 显式指定加载 **ep01**：
     *   index.json 是「最新在前」（Ep278 上线后第 0 条是 ep13）⇒ 默认加载的不是 ep01，
     *   而下面的期望值是按 web/data/eps/ep01.json 算的 ⇒ 对不上（差一句的假红）。
     *   ⇒ 把「加载哪一集」固定住，测试才有确定基线。 */
    await page.addInitScript(() => {
      try { localStorage.setItem('shun.ep', JSON.stringify('ep01')); } catch (e) { }
    });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.addInitScript(() => {
      window.__T = { state: { pos: 0, dur: 0, playing: false, actualPlaying: false, ready: false, completed: false, error: '' }, calls: [] };
      const rec = (k) => function () { window.__T.calls.push(k); };
      window.Android = {
        playEpisode: rec('playEpisode'), playAudio: rec('playAudio'),
        resumeAudio: rec('resumeAudio'), pauseAudio: rec('pauseAudio'),
        seekAudio: rec('seek'), stopAudio: rec('stop'), setSpeed: rec('speed'),
        playerState: function () { return JSON.stringify(window.__T.state); },
        ping: function () { return '{"port":0,"native":true}'; },
        version: function () { return '1.2'; },
        cacheEpisode: function () { return '{"ok":true}'; },
        downloadedList: function () { return '{}'; },
        downloadStatus: function () { return '{}'; },
        clearDownloads: function () { return '{}'; },
        resolveAudio: function () { return ''; },
        selfTest: function () { return '{}'; }
      };
    });
    await page.goto('http://127.0.0.1:' + PORT + '/index.html', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('.cue').length > 0, { timeout: 25000 });

    /* 🔴🔴 mock 的单位**从原生源码推导**（不手写）——NHK 2026-10-23 的教训，我原样也中了：
     *   「mock 是『关于设备的断言』，手写就等于把自己的假设当真。」
     *   ⇒ 读 NativePlayer.java 里 getCurrentPosition() 的赋值行：有没有 /1000 ⇒ 秒 / 毫秒
     *   ⇒ 原生层一改单位，这条测试就红（ticker 的换算与源码不再匹配）⇒ 两层被绑在一起。 */
    const unitInfo = nativeUnitFromSource(read(J('apk-template/java/com/shun/study/NativePlayer.java')));
    ok(unitInfo.unit !== 'unknown', '能从原生源码推导出播放位置的单位', unitInfo.evidence);
    const scale = unitInfo.unit === 'sec' ? 1 : 1000;
    ok((unitInfo.unit === 'sec') === /pos:s\.pos\*1000/.test(tickerJs),
      'ticker 的换算与原生单位的匹配（秒→毫秒 才乘 1000）',
      '原生单位=' + unitInfo.unit + ' | ' + unitInfo.evidence);

    /* 喂一个**真实时间点**的原生 tick（单位由源码决定） */
    await page.evaluate((args) => {
      window.__T.state = { pos: args.p, dur: args.d, playing: true, actualPlaying: true, ready: true, completed: false, error: '' };
    }, { p: T * scale, d: 881.6 * scale });
    await page.evaluate(tickerJs);
    await page.waitForTimeout(250);

    const got = await page.evaluate(() => {
      const e = document.querySelector('.cue.on');
      return e ? parseInt(e.getAttribute('data-i'), 10) : -1;
    });
    ok(got === exp, '原生 tick 之后高亮的是那一刻的句子（字幕跟着音频走）',
      'got=' + got + ' exp=' + exp + '（差 1000 倍 = 原生给的是秒、前端要的是毫秒）');

    const pr = await page.evaluate(() => ({
      w: (document.getElementById('scrubFill') || {}).style.width || '',
      t: (document.getElementById('timeLabel') || {}).textContent || ''
    }));
    const wantPct = T / 881.6 * 100;
    const gotPct = parseFloat(pr.w);
    ok(Math.abs(gotPct - wantPct) < 1.0, '进度条按真实位置走（不是 0%）',
      'got=' + pr.w + ' want≈' + wantPct.toFixed(1) + '%');
    ok(/0:30/.test(pr.t), '时间标签显示 0:30', pr.t);
    /* ②b 网页路径的 tick 是**毫秒**（au.currentTime*1000）⇒ 同样的期望必须成立
     *    （两条路径的单位必须一致，否则「网页对、App 错」这种事没人查） */
    await page.evaluate(() => {
      window.__T.state = { pos: 0, dur: 0, playing: false, actualPlaying: false, ready: false, completed: false, error: '' };
    });
    await page.evaluate(() => window.__hubTick({ pos: 30.0 * 1000, dur: 881.6 * 1000, playing: true, actualPlaying: true, ready: true, completed: false, err: '' }));
    await page.waitForTimeout(200);
    const gotWeb = await page.evaluate(() => {
      const e = document.querySelector('.cue.on');
      return e ? parseInt(e.getAttribute('data-i'), 10) : -1;
    });
    ok(gotWeb === exp, '网页路径（毫秒）高亮的是同一句（两条路径单位一致）', 'got=' + gotWeb + ' exp=' + exp);

    ok(errors.length === 0, '无 JS 错误', errors.slice(0, 2).join(' | '));
    await ctx.close();
  }

  await browser.close();
  srv.close();

  console.log('\n═══ ' + pass + ' 通过 / ' + fail + ' 失败 ═══');
  if (fail) { console.log('失败项：'); fails.forEach(f => console.log('  ✗ ' + f)); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
