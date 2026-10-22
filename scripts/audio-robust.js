/* audio-robust.js —— **音频失败处理三问**（通用补丁，hub 六个项目共用）
 *
 * 🔴🔴 **为什么需要这个文件**（2026-10-23 逐项体检现役站 `8mbic178h1f8s` 的结论）：
 * | 项目 | ①error监听 | ②重试 | ③看门狗 | 症状 |
 * |---|---|---|---|---|
 * | **ama**  | ❌ | ❌ | ❌ | 🔴 **完全静默**（用户只觉 App 坏了） |
 * | **dora** | ❌ | ❌ | ❌ | 🔴 **完全静默** |
 * | pony    | ✅ | ❌ | ❌ | 第一次 503 就报「没声音」= **假故障** |
 * | sakura  | ✅ | ✅ | ❌ | 缺看门狗 |
 * | wakakusa| ✅ | ✅ | ❌ | 缺看门狗 |
 * | tadoku  | ✅ | ❌ | ❌ | 线上是旧版 |
 *
 * ⇒ **三问**：
 *   ① 有没有 **error 监听**（没有 = **完全静默** —— 最伤，用户以为 App 坏了）
 *   ② 失败后**会不会重试**（不会 = **假故障报给用户** —— CDN 冷启动 503 其实刷新就好）
 *   ③ 有没有**看门狗**（没有 = error 只触发一次时**两头都坏**：救不回来 + 永远不报错）
 *
 * 🔴🔴 **为什么必须有看门狗**（实测）：
 *   `<audio>` 的 error 事件**连续失败时只触发 1 次**（第 2、3 次 503 不再发 error）
 *   ⇒ 只靠 error 事件做重试循环 ⇒ **只救得回一次，而且永远不报错**（静默失败）
 *
 * 🔴🔴 **怎么用**（**不改任何现有逻辑，只加一行**）：
 *   在页面里加一行（**只写开始标签，不要写结束标签** —— 见下方「坑」）：
 *   <script src="audio-robust.js">
 *
 * 🔴🔴🔴 **坑（我当场踩了）**：**这个文件的注释里如果出现完整的结束标签，
 *   HTML 解析器会认为 script 标签到此结束** ⇒ 剩下的代码被当成 HTML 文本
 *   ⇒ 报「Invalid or unexpected token」⇒ **补丁整个不执行**（而且没有明显报错）
 *   ⇒ **所以：脚本文件里（尤其注释里）绝不能出现「斜杠 + script + 大于号」连写**
 *   ⇒ ⚠️ **连「提到这个坑的那句话」也不能含它** —— 否则自我指涉地又踩一次 💀✅
 *   ★ **必须在 `<audio>` 元素之后**（本文件也处理「元素还没进 DOM」的情况）
 *   ★ APK 里要把这个文件也打进 assets
 *
 * 🔴 **行为约定**（对用户可见）：
 *   · 重试期间**不弹提示**（墨水屏上每次 toast 都是一次整屏重绘）
 *   · 两次都失败才**如实报错**（宁可慢一点，也不要给错消息）
 */
(function () {
  'use strict';
  if (window.__AUDIO_ROBUST__) return;      /* 幂等：重复注入不重复挂监听 */
  window.__AUDIO_ROBUST__ = true;

  var RETRIES = 2;          /* 最多重试 2 次 */
  var WATCH_MS = 3000;      /* 看门狗：3 秒内音频没到位 ⇒ 判失败 */
  var GAP_MS = 1200;        /* 两次尝试之间的间隔 */
  var DEDUP_MS = 800;       /* 去重：800ms 内只算一次失败 */
  var IMG = /\.(m4a|mp3|m4s|aac|ogg|wav|flac)(\?|$)/i;

  var el = null, tries = 0, guard = 0, watchTimer = null;

  function findAudio() {
    return document.querySelector('#audio') || document.querySelector('#au') ||
           document.querySelector('audio');
  }

  function say(msg) {
    try {
      if (window.HubShell && typeof window.HubShell.toast === 'function') { window.HubShell.toast(msg); return; }
    } catch (e) { }
    if (typeof window.toast === 'function') { try { window.toast(msg); return; } catch (e2) { } }
    /* 兜底：自己弹一个（不依赖任何宿主 API） */
    try {
      var t = document.createElement('div');
      t.textContent = msg;
      t.style.cssText = 'position:fixed;left:50%;bottom:80px;transform:translateX(-50%);z-index:9999;' +
        'background:rgba(20,20,20,.92);color:#fff;padding:10px 18px;border-radius:10px;font:16px/1.4 system-ui;max-width:88%';
      document.body.appendChild(t);
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 2600);
    } catch (e3) { }
  }

  function why() {
    var st = (el && el.error) || {};
    if (st.code === 4) return '（网络不通或地址失效）';
    if (st.code === 3) return '（解码失败）';
    if (st.code === 2) return '（网络错误）';
    return '（' + (st.code || '?') + '）';
  }

  function armWatch() {
    clearTimeout(watchTimer);
    watchTimer = setTimeout(function () {
      try {
        /* 🔴 用 `readyState < 1`（连元数据都没有）而不是 `< 2`
           —— moov 在文件头 ⇒ 元数据正常秒级拿到 ⇒ `< 1` 判失败更快也更准 */
        if (el && el.readyState < 1) fail();
      } catch (e) { }
    }, WATCH_MS);
  }

  /* 🔴🔴 **重试单独一个函数** —— 原来把它内联在 `fail()` 的 setTimeout 里，
   *   而那个 setTimeout 里的 `if (!u) return;` **会把它整段跳掉**
   *   ⇒ 结果：只重试一次，**看门狗再也不 arm** ⇒ 永远不报错（静默失败）💀
   *   ⇒ 拆开之后：**无论有没有 src，都必须重新 arm** ✅ */
  function retryOnce() {
    try {
      var u = (el && el.currentSrc) || (el && el.src);
      if (u) {
        if (tries > 1 && IMG.test(u)) {
          u = u + (u.indexOf('?') < 0 ? '?' : '&') + 'r=' + Date.now();  /* 逼 CDN 换副本 */
        }
        el.src = u;
        el.load();
      }
    } catch (e) { }
    armWatch();          /* 🔴 无论上面成没成，都要重新 arm */
  }

  function fail() {
    var now = Date.now();
    if (now - guard < DEDUP_MS) return;   /* 去重：error 与 watchdog 可能同时到 */
    guard = now;
    if (tries >= RETRIES) {
      say('这一集没声音：音频源加载失败' + why());
      return;
    }
    tries++;
    setTimeout(retryOnce, GAP_MS);
    return;                               /* 🔴 重试期间不弹提示 */
  }

  function attach() {
    el = findAudio();
    if (!el) return false;
    el.addEventListener('error', fail);
    /* 🔴🔴 **loadstart 里绝不能重置 tries** —— **重试本身也会触发 loadstart**
     *   ⇒ 重置 ⇒ tries 永远回到 0 ⇒ **无限重试**（实测请求 8 次不封顶）
     *   ⇒ 正确的归位时机是「**真的成功**」（canplay / loadeddata） */
    el.addEventListener('loadstart', function () { armWatch(); });
    el.addEventListener('canplay', function () { tries = 0; guard = 0; });
    el.addEventListener('loadeddata', function () { tries = 0; guard = 0; });
    return true;
  }

  function boot() {
    if (attach()) return;
    /* <audio> 还没进 DOM ⇒ 等它 */
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { setTimeout(attach, 0); });
    } else {
      setTimeout(attach, 200);
    }
  }
  boot();
})();
