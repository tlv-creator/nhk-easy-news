/* NHKやさしいことばニュース · 日中对照精读 —— Service Worker
 *
 * 作用：
 *   1) 首屏资源预缓存 → 断网也能打开（墨水屏设备流量敏感）
 *   2) 音频按需缓存 → 点「⬇ 下載」时顺手存进 Cache Storage，之后离线可听
 *   3) 点「全部刪除」时前端能列出所有已缓存的音频
 */
/* 🔴 缓存名带内容版本：改了 sw.js 本身 ⇒ 浏览器会重装 SW ⇒ 预缓存里的
   data/episodes.js 等才会刷新。否则 stale-while-revalidate 会把**旧期数**
   先发给用户（第一次打开看不到新一期，得开第二次）—— 2026-10-24 修 */
const CACHE = 'nhk-easynews-v31';
const CORE = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.webmanifest',
  './data/episodes.js',
  './data/patterns.js',
  './data/syntax.js',
  './data/reading.js',
  './data/chains.js',
  './data/cdn.js',
  './icon-192.png',
  './icon-512.png',
  './NHK.apk'
];

/* 全部 44 集字幕 + 全部词库：一次预热，之后**完全离线可读**
   （墨水屏设备流量敏感 ⇒ 一次性 4.6MB 换以后零流量，比每次开都要下划算）
   —— 词库（vocab/lexicon/alias）是**点句详解面板**要用的：
      不预热的话，断网点句就只剩原文没有逐词释义。 */
const SUBS_GLOB = './subs/';
const DATA_PRELOAD = [
  './data/vocab.js', './data/lexicon.js', './data/alias.js',
  './data/reading.js', './data/patterns.js', './data/syntax.js',
  './data/chains.js', './data/episodes.js', './data/cdn.js'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.all(CORE.map(u => c.add(u).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
  // 预热放在 activate 之后、**不 waitUntil** ⇒ 慢也不影响 SW 激活
  setTimeout(function () { warmSubs().catch(function () {}); }, 1500);
});

/* 预热全部字幕：🔴 **绝不放在 install 的 waitUntil 里** ——
   2.8MB 的预热会把 install 拖住 ⇒ SW 永远不激活 ⇒ 离线能力等于没有。
   这里改成 activate 之后**不阻塞**地跑：失败无所谓，SW 已能用了。 */
async function warmOne(c, u) {
  try {
    const hit = await c.match(u);
    if (hit) return null;
    const r = await fetch(u);
    if (r && r.ok) return await c.put(u, r.clone());
    return null;
  } catch (e) { return null; }
}

async function warmSubs() {
  try {
    const c = await caches.open(CACHE);
    const res = await fetch('./data/episodes.js', { cache: 'no-store' });
    if (!res || !res.ok) return;
    const txt = await res.text();
    const ids = [];
    const re = /"subs"\s*:\s*"subs\/([A-Za-z0-9]+)\.js"/g;
    let m;
    while ((m = re.exec(txt)) !== null) ids.push(m[1]);
    if (!ids.length) return;
    await Promise.all(DATA_PRELOAD.map(u =>
      c.match(u).then(hit => {
        if (hit) return null;
        return fetch(u).then(r => { if (r && r.ok) return c.put(u, r.clone()); return null; });
      }).catch(() => null)
    ));
    await Promise.all(DATA_PRELOAD.map(u =>
      c.match(u).then(hit => {
        if (hit) return null;
        return fetch(u).then(r => { if (r && r.ok) return c.put(u, r.clone()); return null; });
      }).catch(() => null)
    ));
    await Promise.all(DATA_PRELOAD.map(u =>
      c.match(u).then(hit => {
        if (hit) return null;
        return fetch(u).then(r => { if (r && r.ok) return c.put(u, r.clone()); return null; });
      }).catch(() => null)
    ));
    // 🔴 限并发：一次并发 53 个请求会把首屏带宽吃光（实测字幕都渲染不出来）
    //    ⇒ 一次只下 3 个，慢慢来；预热慢没关系，**不能抢用户正在看的内容**。
    const queue = DATA_PRELOAD.map(u => () => warmOne(c, u))
      .concat(ids.map(id => () => warmOne(c, SUBS_GLOB + id + '.js')));
    for (let i = 0; i < queue.length; i += 3) {
      await Promise.all(queue.slice(i, i + 3).map(fn => fn().catch(() => null)));
    }
  } catch (e) { /* 预热失败不影响使用 */ }
}


function isAudio(url) {
  return /\.(m4a|mp3|aac|m4s)$/i.test(url.pathname);
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 🔴🔴 2026-10-23：**HTML 改成网络优先**（原来是 stale-while-revalidate）
  //   为什么：index.html 里写着 app.js/style.css 的 **?v= 版本号**。
  //   SWR ⇒ 第一次打开拿到的是**缓存里的旧 index.html** ⇒ 于是旧的 ?v= ⇒
  //   于是旧的 app.js ⇒ **用户看到的还是没修的版本**（得开两次）。
  //   index.html 只有 10 KB，流量代价可忽略；断网时回落到缓存 ⇒ 离线照常用。
  const wantsHtml = (req.mode === 'navigate') ||
                   ((req.headers.get('accept') || '').indexOf('text/html') >= 0);
  if (wantsHtml) {
    e.respondWith(
      fetch(req).then(res => {
        if (res && res.ok) {
          try { caches.open(CACHE).then(c => c.put(req, res.clone())).catch(() => {}); } catch (err) {}
        }
        return res;
      }).catch(() => caches.match(req).then(hit =>
        hit || caches.match('./index.html').then(h2 =>
          h2 || new Response('offline', { status: 503 }))))
    );
    return;
  }

  // 音频：缓存优先（命中就离线放；未命中就下载并顺手存下来）
  if (isAudio(url)) {
    e.respondWith(
      caches.open(CACHE).then(async c => {
        const hit = await c.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res && (res.ok || res.type === 'opaque')) {
          try { c.put(req, res.clone()); } catch (err) { /* 配额满了就算了 */ }
        }
        return res;
      }).catch(() => fetch(req))
    );
    return;
  }

  // 其它同源资源：stale-while-revalidate
  e.respondWith(
    caches.open(CACHE).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(res => {
        if (res && res.ok) { try { c.put(req, res.clone()); } catch (err) { } }
        return res;
      }).catch(() => null);
      return hit || (await net) || new Response('offline', { status: 503 });
    })
  );
});
