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
const CACHE = 'nhk-easynews-v30';
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
  './data/cdn.js'
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
});

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
