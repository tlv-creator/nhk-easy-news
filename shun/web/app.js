/* Japanese with Shun · 日中对照精读 —— 前端主程序
 *
 * 数据（构建期由 scripts/build_web_data.py 生成）：
 *   data/eps/index.json      集数索引
 *   data/eps/epNN.json       {ep,tag,title,desc,dur,audio,cues:[{s,e,rows:[]}]}
 *       row = { t:日文, ru:[[表层,读音]](完整覆盖 t), zh:中文,
 *               w:[[词,读音]](逐词), st:句型名, blk:[[日文块,中文块]] }
 *   data/lexicon/vocab.json  点词查释义词典（释义/词性/用法/本剧例句）
 *   data/lexicon/styles.json 句型说明（接续/意思/语感）
 *   data/lexicon/grammar.json 句型表（例句全是本剧真台词）
 *
 * ⚠️ ru / w 都是**数组对** [[表层,读音],...]；渲染时统一走 pairText()/pairRead()，
 *    同时兼容 pony 那套对象对 {k,s}（万一以后混进来也不会炸）。
 *
 * 音频：
 *   网页版 = 远端 CDN（AUDIO_BASE）+ Cache Storage 离线副本
 *   APK    = 内置在 assets/audio/ 里（离线可听），可选复制一份到本地缓存
 */
(function () {
  'use strict';

  /* 音频站域名（部署后由 scripts/set_audio_base.sh 写死）
   * 调试时可以用 ?audio=<绝对地址前缀> 临时换源（测试脚本用） */
  var AUDIO_BASE_LOCAL = 'https://qtjgfga76bu4o.space.mcode.cn/media/';
  /* 🔴🔴 **备用音频地址链**（2026-10-24 采纳 hub 课程站线的建议）
   *   起因：**地址写死 = 站点一挂/被删 ⇒ App 永久哑**，而且用户不知道原因
   *   ⇒ 同一集**换源重试**（不是只加 ?r= 破缓存 —— 那是重试同一个死地址）
   *   ⇒ 顺序：引导页给的（最新）→ v3（13 集）→ v2（12 集，永久冻结，兜底）
   *   ⚠️ v2 没有 ep13 ⇒ ep13 只能靠 v3/引导页 ⇒ 兜底只保 ep01~ep12 */
  var AUDIO_FALLBACKS = (function () {
    /* 可注入（**仅测试用**）：window.__SHUN_AUDIO_FALLBACKS
     *   ⇒ 回归测试要让 fallback 指向本地，否则换源会跳到**真实生产站**
     *   ⇒ 真实站好好的 ⇒ 「一直 503」场景永远不报错 ⇒ **测试变成恒绿/假红**
     *   ⇒ 生产环境不注入 ⇒ 走下面的写死链 */
    var inj = (typeof window !== 'undefined') && window.__SHUN_AUDIO_FALLBACKS;
    if (Array.isArray(inj) && inj.length) return inj.slice();
    return [
      'https://qtjgfga76bu4o.space.mcode.cn/media/',
      'https://da4lxn2phccck.space.mcode.cn/media/'
    ];
  })();
  // 🔴 2026-10-24：音频兜底站换成 v3（含 13 集）
  //    旧站 da4lxn2phccck **没有 ep13** ⇒ 引导页拉不到时 Ep278 没声音
  //    ⇒ 换站必须改**所有**引用方：引导页 shun.json ✅ + 这里（APK 内的兜底）✅
  var AUDIO_BASE = (function () {
    try {
      /* 🔴 **测试注入点**（2026-10-24）：比 ?audio= 参数可靠
       *   ⇒ ?audio= 在真实浏览器里**时序上不可靠**（实测 app.js 执行时 search 为空）
       *   ⇒ 生产不注入 ⇒ 走引导页 / 本地常量 */
      if (window.__SHUN_AUDIO_BASE) return window.__SHUN_AUDIO_BASE;
      var m = /[?&]audio=([^&]+)/.exec(location.search);
      if (m) return decodeURIComponent(m[1]);
    } catch (e) { }
    return AUDIO_BASE_LOCAL;
  })();
  /* Cache Storage 桶名（网页版离线缓存） */
  var CACHE_NAME = 'shun-audio-v1';

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };

  /* ---------------- 状态 ---------------- */
  var S = {
    eps: [], ep: null, lines: [], idx: -1,
    playing: false, pos: 0, dur: 0,
    bilingual: true, follow: true, loopOne: false,
    fs: 30, pfs: 17,
    fav: {}, progress: {},
    lex: { vocab: {}, styles: {}, grammar: {} },
    blk: {},                 /* tag → {"ep01:12": [[日文块,中文块]]} 旁挂中文块 */
    dl: {},                 /* 网页版已下载：{url:{bytes,ts}} */
    exIdx: -1, exWord: '', studyTab: 'fav',
    lastErr: '',             /* 原生层最后一次错误（去重键 = loadSeq|错误内容） */
    loadSeq: 0               /* 加载尝试计数（每次 loadEp +1） */
  };

  var LS = {
    get: function (k, d) {
      try { var v = localStorage.getItem('shun.' + k); return v == null ? d : JSON.parse(v); }
      catch (e) { return d; }
    },
    set: function (k, v) { try { localStorage.setItem('shun.' + k, JSON.stringify(v)); } catch (e) { } }
  };

  var au = $('#au');
  var isNative = !!(window.Android && typeof window.Android.playEpisode === 'function');
  var tickCb = null;
  var blobUrl = null;      /* 网页版离线副本的临时 URL */

  /* ---------------- 工具 ---------------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmt(sec) {
    sec = Math.max(0, sec || 0);
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ':' + (s < 10 ? '0' + s : s);
  }
  function fmtMs(ms) { return fmt((ms || 0) / 1000); }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /* ru / w 的元素：数组对 [表层,读音] 或对象对 {k,s} —— 都吃得下 */
  function pairText(p) {
    if (p == null) return '';
    if (Array.isArray(p)) return p[0] == null ? '' : String(p[0]);
    if (typeof p === 'string') return p;
    return p.k == null ? '' : String(p.k);
  }
  function pairRead(p) {
    if (p == null) return '';
    if (Array.isArray(p)) return p[1] == null ? '' : String(p[1]);
    if (typeof p === 'string') return '';
    return p.s == null ? '' : String(p.s);
  }

  /* ---------------- 播放层 ---------------- */
  function audioUrl(base) {
    var tag = S.ep ? (S.ep.audio || (S.ep.tag + '.m4a')) : 'ep01.m4a';
    return (base || AUDIO_BASE) + tag;
  }

  /* 🔴 换源：当前 base 失败 ⇒ 试下一个（整条链试完才算真失败） */
  function audioNextBase() {
    var chain = [AUDIO_BASE].concat(AUDIO_FALLBACKS);
    for (var i = 0; i < chain.length; i++) {
      if (chain[i] !== AUDIO_BASE) { AUDIO_BASE = chain[i]; return true; }
    }
    return false;
  }

  /* 网页版：优先播本地缓存副本（离线可听），没有就用远端 */
  function resolveSrc(url) {
    if (isNative || !window.caches) return Promise.resolve(url);
    try {
      return caches.open(CACHE_NAME).then(function (c) {
        return c.match(url).then(function (hit) {
          if (!hit) return url;
          return hit.blob().then(function (b) {
            if (blobUrl) { try { URL.revokeObjectURL(blobUrl); } catch (e) { } }
            blobUrl = URL.createObjectURL(b);
            return blobUrl;
          });
        });
      }).catch(function () { return url; });
    } catch (e) { return Promise.resolve(url); }
  }

  function nativeLoad(tag, url, posMs) {
    if (window.Android.playEpisode) window.Android.playEpisode(tag, url, posMs || 0);
    else window.Android.playAudio(url, posMs || 0);
  }
  function nativePlay() { window.Android.resumeAudio(); }
  function nativePause() { window.Android.pauseAudio(); }
  function nativeSeek(ms) { window.Android.seekAudio(ms); }
  function nativeStop() { try { window.Android.stopAudio(); } catch (e) { } }

  function loadEp(ep, posMs) {
    if (!ep) return;
    S.ep = ep;
    S.loadSeq++;              /* 每一次加载尝试 +1 ⇒ 同一个错误在「新一轮加载」里会再提示一次 */
    S.pos = 0;
    S.dur = Math.round((ep.dur || 0) * 1000);
    S.idx = -1;
    audioRetry.swapped = false;   /* 换集 ⇒ 允许再换一次源 */
    var url = audioUrl();
    if (isNative) {
      nativeLoad(ep.tag, url, posMs || 0);
    } else {
      resolveSrc(url).then(function (src) {
        au.src = src;
        /* 🔴 记住这次的源：CDN 503 时要靠它重试（换集时重置计数） */
        audioRetry.src = src; audioRetry.n = 0; audioRetry.lastFail = 0;
        if (posMs) au.currentTime = posMs / 1000;
        au.load();
        audioArmWatch();
      });
    }
    $('#epLabel').textContent = '第' + ep.ep + '集';
    renderCues();
    setIdx(binarySearch(posMs || 0), true);
    updateProgress();
    refreshTools();
  }

  function play() {
    if (isNative) nativePlay(); else au.play().catch(function () { });
    S.playing = true; $('#btnPlay').textContent = '❚❚';
  }
  function pause() {
    if (isNative) nativePause(); else au.pause();
    S.playing = false; $('#btnPlay').textContent = '▶';
  }
  function togglePlay() { S.playing ? pause() : play(); }
  function seek(ms) {
    ms = Math.max(0, Math.min(ms, S.dur || 1e9));
    if (isNative) nativeSeek(ms); else au.currentTime = ms / 1000;
    S.pos = ms;
    setIdx(binarySearch(ms), true);
    updateProgress();
  }

  /* 通用原生钩子：原生层每 500ms 推一次播放进度 */
  /* 🔴 给外部模块（shun-ai-ui.js）的受控只读接口
     —— 之前 S 是 IIFE 局部变量，外部拿不到，AI 功能会直接哑掉。 */
  window.__shunCtx = function (idx) {
    var i = (idx === undefined) ? S.exIdx : idx;
    var L = S.lines[i];
    if (!L || !L.row) return null;
    return {
      line: L,
      ja: L.row.t || '',
      zh: L.row.zh || '',
      lines: S.lines,
      index: i,
      epTitle: S.ep ? (S.ep.title || S.ep.tag) : '',
      epNo: S.ep ? (S.ep.no || '') : ''
    };
  };
  window.__shunLex = function () { return S.lex || {}; };

  window.__hubTick = function (d) {
    S.pos = d.pos; S.dur = d.dur || S.dur;
    /* 🔴🔴 原生层报错了要说人话（2026-10-23 补）：
     *   之前 d.err **只当门控用**（决定要不要用原生状态覆盖乐观状态），
     *   **从不显示给用户** ⇒ 用户点了没声音、什么提示都没有 = **静默失败**。
     *   ⚠️ 键名：原生推的是 **err**（MainActivity 的 ticker）；playerState 的 JSON 用 **error**
     *   ⇒ 两个都认，少一个就静默失效。
     *   ⚠️ 去重键 = 「第几次加载 + 错误内容」：
     *     · 同一次加载里同一个错 → 只提示一次（墨水屏上重复 toast = 整屏重绘）
     *     · 用户重新点一次这一集（= 新的加载尝试）→ 同一个错**也会再提示一次**（重试要有反馈） */
    var nErr = d.err || d.error || '';
    var nKey = S.loadSeq + '|' + nErr;
    if (nErr && nKey !== S.lastErr) {
      S.lastErr = nKey;
      toast('这一集没声音：' + nErr + '（重新点一次这一集可重试）');
    }
    /* 🔴🔴 只有「原生已就绪 / 有明确错误」才用原生状态覆盖前端的乐观状态。
     *    否则准备期（网络流 1~5 秒）原生报 playing=false → 用户的乐观状态被顶回去
     *    → **播放按钮左右横跳**（❚❚ 和 ▶ 宽度不同，看着就是左右动）。
     *    原生侧的 playing 现在报的是**用户意图**（准备期也 true），双保险。 */
    if (d.ready || d.err) S.playing = !!d.playing;
    $('#btnPlay').textContent = S.playing ? '❚❚' : '▶';
    if (tickCb) tickCb(d);
  };
  if (!isNative) {
    var send = function () {
      window.__hubTick({
        pos: au.currentTime * 1000, dur: (au.duration || 0) * 1000, playing: !au.paused
      });
    };
    au.addEventListener('timeupdate', send);
    au.addEventListener('ended', function () { window.__hubTick({ pos: S.dur, dur: S.dur, playing: false }); });
    au.addEventListener('play', send);
    au.addEventListener('pause', send);
    /* 诊断：音频源拿不到时要说人话，别静默失败 */
    au.addEventListener('error', audioFailed);
    au.addEventListener('loadedmetadata', function () { audioRetry.n = 0; });
    if (AUDIO_BASE.indexOf('PLACEHOLDER') >= 0) {
      setTimeout(function () { toast('这一集没声音：音频站还没发布'); }, 600);
    }
  }

  /* ---------------- 音频加载失败处理（网页态） ----------------
   * 🔴🔴 2026-10-23 两处修正（都是真问题）：
   *  ① **CDN 冷启动会瞬时 503** → 第一次失败就报「没声音」是**假故障**（实测：连试即 200）
   *     ⇒ 失败后等 1.2 秒**自动重试**（最多 2 次；第 2 次带 cache-buster 逼 CDN 换副本）
   *  ② 🔴🔴 **`<audio>` 的 error 事件在连续失败时可能只触发一次**
   *     ⇒ 纯靠 error 事件做重试循环 = **既救不回来、又永远不报错（静默失败）** ⇒ 两头都坏
   *     ⇒ 改成**双路判定**：① error 事件 ② **看门狗**（4 秒内没拿到元数据 ⇒ 判定失败）
   *     + **800ms 去重**（两路可能同时到）
   * ⚠️ 这两个函数**必须在 `if (!isNative)` 块外面**（loadEp 也要用它们）——
   *    写在块里 → 块级作用域不提升 → loadEp 调用会 ReferenceError。
   */
  var audioRetry = { n: 0, timer: null, watch: null, src: '', lastFail: 0, swapped: false };

  function audioArmWatch() {
    if (audioRetry.watch) clearTimeout(audioRetry.watch);
    /* readyState < 1 = 连元数据都没拿到（moov 在文件头，正常秒级拿到）⇒ 判定失败 */
    audioRetry.watch = setTimeout(function () {
      try { if (au.readyState < 1) audioFailed(); } catch (e) { }
    }, 4000);
  }

  /* 🔴🔴 重试单独一个函数（2026-10-23 修正 —— 抄 pony 的坑③）：
   *   之前重试逻辑**内联在 setTimeout 里**，`if (!src) return;` 会把后面的
   *   audioArmWatch() **一起跳掉** ⇒ 只重试一次 ⇒ **看门狗再也不 arm**
   *   ⇒ **永远不报错**（静默失败）。
   *   ⇒ 现在**无论有没有 src，都重新 arm 看门狗**（arm 放在 try 之外，任何 return 都跳不掉它）。 */
  function audioRetryOnce() {
    var src = audioRetry.src;
    try {
      if (src) {
        if (audioRetry.n >= 2) {
          src += (src.indexOf('?') >= 0 ? '&' : '?') + 'r=' + Date.now();
        }
        au.src = src;
        au.load();
        if (S.playing) au.play().catch(function () { });
      }
    } catch (e) { }
    audioArmWatch();   /* 🔴 必须在 try 之外：不能被任何 return 跳过 */
  }

  function audioFailed() {
    var now = Date.now();
    if (now - audioRetry.lastFail < 800) return;   /* 去重：error 与看门狗可能同时到 */
    audioRetry.lastFail = now;
    var st = au.error || {};
    var code = st.code;
    /* 🔴🔴 **先换源**（2026-10-24）：地址失效（code 4 / 网络错误）时
     *   重试同一个死地址没有意义 ⇒ 先换备用地址链
     *   ⇒ 判据：**这次还没换过源** 且 **有备用地址** */
    if ((code === 4 || code === 2 || code === undefined) && !audioRetry.swapped && audioNextBase()) {
      audioRetry.swapped = true;
      var tag2 = S.ep ? (S.ep.audio || (S.ep.tag + '.m4a')) : 'ep01.m4a';
      try {
        audioRetry.src = audioUrl();
        au.src = audioRetry.src;
        au.load();
        if (S.playing) au.play().catch(function () { });
      } catch (e) { }
      audioArmWatch();
      return;   /* 换源期间不弹提示（墨水屏上每次 toast 都是一次整屏重绘） */
    }
    /* code 3 = 解码失败（重试没用）→ 直接报错，不浪费用户时间 */
    if (code !== 3 && audioRetry.n < 2 && audioRetry.src) {
      audioRetry.n++;
      if (audioRetry.timer) clearTimeout(audioRetry.timer);
      audioRetry.timer = setTimeout(function () { audioRetryOnce(); }, 1200);
      return;   /* 重试期间**不弹提示**（墨水屏上每次 toast 都是一次整屏重绘） */
    }
    var why = code === 4 ? '（网络不通或地址失效）'
      : code === 3 ? '（解码失败）'
      : code === 2 ? '（网络错误）' : ('（' + (code || '?') + '）');
    toast('这一集没声音：音频源加载失败' + why);
  }

  /* ---------------- 数据 ---------------- */
  /* 词条字段名有两套（主会话那套 def/ex/exzh，pony 那套 meaning/example/exampleZh），
   * 统一用这几个取值函数，前端不关心数据是哪套。 */
  function vMeaning(v) { return (v && (v.meaning || v.def)) || ''; }
  function vExample(v) { return (v && (v.example || v.ex)) || ''; }
  function vExampleZh(v) { return (v && (v.exampleZh || v.exzh)) || ''; }
  function vPos(v) { return (v && v.pos) || ''; }
  function vUsage(v) { return (v && v.usage) || ''; }
  function vReading(v) { return (v && v.reading) || ''; }

  function fetchJson(url) {
    return fetch(url).then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; });
  }

  function loadAll() {
    return fetchJson('data/eps/index.json').then(function (o) {
      S.eps = o.episodes || [];
      /* 🔴 引导页：拉到就合并远端新增集；拉不到就静默用内置（不影响使用） */
      if (!window.ShunBoot) return;
      return window.ShunBoot.fetch().then(function () {
        if (!window.ShunBoot.bootOk) return;
        /* 远端音频基址优先（引导页里最准） */
        if (window.ShunBoot.audioBase) AUDIO_BASE = window.ShunBoot.audioBase;
        return window.ShunBoot.fetchRemoteIndex().then(function (rem) {
          if (!rem || !rem.length) return;
          var merged = window.ShunBoot.mergeEpisodes(S.eps, rem);
          /* 标记哪些是远端集（数据要从远端拿） */
          var localTags = {};
          S.eps.forEach(function (e) { localTags[e.tag] = 1; });
          merged.forEach(function (e) { e._remote = !localTags[e.tag]; });
          S.eps = merged;
        });
      }).catch(function () { });
    }).then(function () {
      /* 词库/句型表体积不小，放到后台预热，不挡首屏。
       * 主会话的 vocab/styles/grammar 优先，*-extra.json 是补充（缺的那些词兜底）。 */
      return Promise.all([
        fetchJson('data/lexicon/vocab.json'),
        fetchJson('data/lexicon/vocab-extra.json'),
        fetchJson('data/lexicon/styles.json'),
        fetchJson('data/lexicon/styles-extra.json'),
        fetchJson('data/lexicon/grammar.json'),
        fetchJson('data/lexicon/grammar-extra.json')
      ]).then(function (lex) {
        function merge(a, b) {
          var o = {}, k;
          for (k in b) if (b.hasOwnProperty(k)) o[k] = b[k];
          for (k in a) if (a.hasOwnProperty(k)) o[k] = a[k];
          return o;
        }
        S.lex = {
          vocab: merge(lex[1], lex[0]),
          styles: merge(lex[3], lex[2]),
          grammar: merge(lex[5], lex[4])
        };
      });
    });
  }

  function loadCues(ep) {
    var du = (window.ShunBoot && ShunBoot.dataUrlFor) ? ShunBoot.dataUrlFor(ep) : ('data/eps/' + ep.tag + '.json');
    var bu = (window.ShunBoot && ShunBoot.blkUrlFor) ? ShunBoot.blkUrlFor(ep) : ('data/eps/' + ep.tag + '.blk.json');
    return fetchJson(du).then(function (o) {
      /* 旁挂的整句结构中文块（有就取，没有就算了） */
      return fetchJson(bu).then(function (b) {
        if (b && Object.keys(b).length) S.blk[ep.tag] = b;
        return o;
      });
    });
  }

  /**
   * 逐词表：数据里的 w 是「逐字 ruby 段」（日本語 → 日/本/語），
   * 这里拿词库当词典做**最长匹配合并**，把相邻的字合成真正的词（日本語 / 友達…）。
   * 词库还没加载好时就按原样显示。
   */
  function buildWords(row) {
    var segs = (row.w && row.w.length) ? row.w : (row.ru || []);
    var arr = [];
    for (var i = 0; i < segs.length; i++) {
      var t = pairText(segs[i]);
      if (!t) continue;
      arr.push({ t: t, r: pairRead(segs[i]) });
    }
    var vocab = S.lex.vocab || {};
    var out = [], i = 0;
    while (i < arr.length) {
      var hit = 0;
      for (var len = Math.min(6, arr.length - i); len >= 2; len--) {
        var cand = '', rd = '';
        for (var k = 0; k < len; k++) { cand += arr[i + k].t; rd += arr[i + k].r; }
        if (vocab[cand]) { out.push({ t: cand, r: rd }); hit = len; break; }
      }
      if (!hit) { out.push(arr[i]); hit = 1; }
      i += hit;
    }
    return out;
  }

  /* ================================================================
   * 句子结构详解（gs 字段）
   *   数据由 tools/gen_gs.py 用 MiniMax-Text-01 离线生成 + 事实校验，
   *   schema 见 docs/新结构注释设计.md。
   *   渲染目标（用户给的样例形态）：
   *     一、大框架   →  一行分块（括号 + 原文）+ 下一行对齐角色标签
   *     主句骨架   →  公式行
   *     层层修饰主句 → 箭头串
   *     二、逐块拆解 →  每块：块内骨架 + 逐成分说明 + 整块意思
   *     整句意思
   * ============================================================== */

  /* 括号 → CSS 类后缀（决定视觉区分：连接 / 从句 / 内容 / 谓语） */
  function gsBrkClass(b) {
    if (b === '[]') return 'k0';
    if (b === '［］') return 'k1';
    if (b === '〈〉') return 'k2';
    if (b === '（）') return 'k3';
    return 'k1';
  }

  /* 括号字符本身要**显示出来**（它是语义标记：连接/从句/内容/谓语）
   * CSS 边框只是辅助区分（墨水屏黑白下靠粗细认）。 */
  function gsBrkText(b) {
    if (b === '[]') return ['[', ']'];
    if (b === '［］') return ['［', '］'];
    if (b === '〈〉') return ['〈', '〉'];
    if (b === '（）') return ['（', '）'];
    return ['', ''];
  }

  /* 一、大框架：块 + 对齐的角色标签 */
  function gsFrameHtml(gs) {
    var fr = gs.frame || [];
    if (!fr.length) return '';
    var h = '<div class="gs-frame">';
    for (var i = 0; i < fr.length; i++) {
      var bk = gsBrkText(fr[i].b);
      h += '<span class="gs-bk gs-b' + gsBrkClass(fr[i].b) + '">' +
        '<span class="gs-bt"><span class="gs-op">' + bk[0] + '</span>' +
        esc(fr[i].s || '') +
        '<span class="gs-op">' + bk[1] + '</span></span></span>';
    }
    h += '</div>';
    h += '<div class="gs-roles">';
    for (var j = 0; j < fr.length; j++) {
      h += '<span class="gs-rl gs-r' + gsBrkClass(fr[j].b) + '">' +
        esc(fr[j].role || '') + '</span>';
    }
    h += '</div>';
    return h;
  }

  /* 逐块拆解 */
  function gsBlocksHtml(gs) {
    var bl = gs.blocks || [];
    if (!bl.length) return '';
    var h = '';
    for (var i = 0; i < bl.length; i++) {
      var b = bl[i];
      h += '<div class="gs-bkcard">';
      h += '<div class="gs-bkhead"><span class="gs-bknum">第' +
        ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'][i] +
        '块</span><span class="gs-bktext">' + esc(b.s || '') + '</span></div>';
      if (b.inner) {
        h += '<div class="gs-inner">' + esc(b.inner) + '</div>';
      }
      var ps = b.parts || [];
      if (ps.length) {
        h += '<ul class="gs-parts">';
        for (var j = 0; j < ps.length; j++) {
          h += '<li><span class="gs-ps">' + esc(ps[j].s || '') + '</span>' +
            '<span class="gs-pn">' + esc(ps[j].note || '') + '</span></li>';
        }
        h += '</ul>';
      }
      if (b.mean) {
        h += '<div class="gs-mean">意思：<b>' + esc(b.mean) + '</b></div>';
      }
      h += '</div>';
    }
    return h;
  }

  /* 整节渲染 */
  function gsHtml(gs) {
    if (!gs || !gs.frame || !gs.frame.length) return '';
    var h = '<div class="ex-sec">② 句子结构详解</div><div class="gs">';

    h += '<div class="gs-h">一、大框架</div>';
    h += gsFrameHtml(gs);

    if (gs.skel) {
      h += '<div class="gs-skel"><span class="gs-lb">主句骨架</span>' +
        '<span class="gs-sv">' + esc(gs.skel);
      if (gs.skelMean) h += ' <span class="gs-eq">＝"' + esc(gs.skelMean) + '"</span>';
      h += '</span></div>';
    }

    if (gs.chain && gs.chain.length) {
      h += '<div class="gs-h gs-h2">层层修饰主句</div>';
      h += '<div class="gs-chain">';
      for (var i = 0; i < gs.chain.length; i++) {
        if (i) h += '<span class="gs-ar">→</span>';
        h += '<span class="gs-ch">' + esc(gs.chain[i]) + '</span>';
      }
      h += '</div>';
    }

    h += '<div class="gs-h gs-h2">二、逐块拆解</div>';
    h += gsBlocksHtml(gs);

    if (gs.whole) {
      h += '<div class="gs-whole"><span class="gs-lb">整句意思</span>' +
        esc(gs.whole) + '</div>';
    }
    h += '</div>';
    return h;
  }

  /* ---------------- 渲染字幕流 ---------------- */
  function rubyHtml(row) {
    if (!row.ru || !row.ru.length) return esc(row.t || '');
    var out = '';
    for (var i = 0; i < row.ru.length; i++) {
      var t = pairText(row.ru[i]), rr = pairRead(row.ru[i]);
      if (!t) continue;
      var inner = rr ? '<ruby>' + esc(t) + '<rt>' + esc(rr) + '</rt></ruby>' : esc(t);
      out += '<span class="jpw" data-w="' + esc(t) + '">' + inner + '</span>';
    }
    return out;
  }

  /* 把 cue 摊平成「行」：一个 cue 里可能有多行台词（rows） */
  function flatten(cues) {
    var lines = [];
    for (var i = 0; i < cues.length; i++) {
      var c = cues[i];
      var rows = c.rows || [];
      for (var j = 0; j < rows.length; j++) {
        var r = rows[j];
        if (!r.t) continue;
        lines.push({ cue: c, row: r, s: c.s, e: c.e, first: j === 0, cueIdx: i });
      }
    }
    return lines;
  }

  function renderCues() {
    var box = $('#cueBox');
    var html = '';
    for (var i = 0; i < S.lines.length; i++) {
      var L = S.lines[i];
      html += '<div class="cue" data-i="' + i + '">' +
        '<div class="cue-t">' + (L.first ? fmt(L.s) : '&nbsp;') + '</div>' +
        '<div class="cue-ja">' + rubyHtml(L.row) + '</div>' +
        (S.bilingual ? '<div class="cue-zh">' + esc(L.row.zh || '') + '</div>' : '') +
        '</div>';
    }
    box.innerHTML = html;
  }

  function binarySearch(ms) {
    var lo = 0, hi = S.lines.length - 1, ans = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (S.lines[mid].s * 1000 <= ms) { ans = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return ans;
  }

  function setIdx(i, scroll) {
    if (i === S.idx) return;
    var old = $('.cue.on');
    if (old) old.classList.remove('on');
    S.idx = i;
    var el = $('.cue[data-i="' + i + '"]');
    if (el) {
      el.classList.add('on');
      if (scroll && S.follow) {
        var box = $('#stream');
        var r = el.getBoundingClientRect(), br = box.getBoundingClientRect();
        if (r.top < br.top + 8 || r.bottom > br.bottom - 8) {
          box.scrollTop += (r.top - br.top) - br.height * 0.38;
        }
      }
    }
    if (S.loopOne && S.playing) {
      var L = S.lines[i];
      if (L) seek(L.s * 1000);
    }
  }

  function updateProgress() {
    var pct = S.dur ? Math.max(0, Math.min(1, S.pos / S.dur)) : 0;
    /* 🔴🔴 墨水屏：**显示值没变就绝不写 DOM**。
     *    每 500ms 重写一次 style.width → 每 500ms 触发一次重绘 → 字幕一动就闪。
     *    百分比取整到 0.5%（12 分钟一集 → 0.5% ≈ 3.6 秒才动一次）。*/
    var v = (Math.round(pct * 200) / 2) + '%';
    var fill = $('#scrubFill'), dot = $('#scrubDot');
    if (fill && fill.style.width !== v) fill.style.width = v;
    if (dot && dot.style.left !== v) dot.style.left = v;
    var t = fmtMs(S.pos) + ' / ' + fmtMs(S.dur);
    var lab = $('#timeLabel');
    if (lab && lab.textContent !== t) lab.textContent = t;
  }

  /* ---------------- 详解大弹窗 ---------------- */
  function openEx(i) {
    var L = S.lines[i];
    if (!L) return;
    S.exIdx = i;
    var r = L.row;
    var h = '';

    /* ① 原句（全注音）+ 中文 */
    h += '<div class="ex-orig">' +
      '<div class="ex-jp">' + rubyHtml(r) + '</div>' +
      '<div class="ex-zh">' + esc(r.zh || '') + '</div>' +
      '<div class="ex-act">' +
      '<button class="mini-btn" data-act="speak">🔊 朗读</button>' +
      '<button class="mini-btn" data-act="seek">▶ 跳到這句</button>' +
      '<button class="mini-btn" data-act="copy-sent">📋 复制整句</button>' +
      '</div></div>';

    /* ② 逐词拆解 —— 覆盖这一行每一个词
     * w 是逐字 ruby 段，用词库做最长匹配合并成真正的词。 */
    var w = buildWords(r);

    h += '<div class="ex-sec">① 逐词拆解</div>';
    if (w.length) {
      h += '<ol class="ex-wd">';
      for (var i = 0; i < w.length; i++) {
        var word = w[i].t, rd = w[i].r;
        if (!word) continue;
        if (rd === word) rd = '';           /* 纯假名词不重复显示读音 */
        var v = S.lex.vocab[word] || null;
        h += '<li data-word="' + esc(word) + '" data-read="' + esc(rd) + '">' +
          '<span class="n">' + (i + 1) + '</span>' +
          '<span class="w">' + esc(word) + '</span>' +
          (rd ? '<span class="rd">' + esc(rd) + '</span>' : '') +
          (v ? '<span class="m">' + esc(vMeaning(v)) + '</span>' : '') +
          (v ? '<span class="p">' + esc(vPos(v)) + '</span>' : '') +
          (v ? '<span class="u">' + esc(vUsage(v)) + '</span>' : '') +
          '</li>';
      }
      h += '</ol>';
    } else {
      h += '<div class="ex-none">这一行是语气词或音效，没有可拆的词。</div>';
    }

    /* ② 句子结构详解（新）—— 大框架 / 主句骨架 / 层层修饰 / 逐块拆解
     * 数据由 tools/gen_gs.py 离线生成并做事实校验（docs/新结构注释设计.md）。
     * 没有 gs 的句子（旧数据 / 生成失败）⇒ 这一节不显示，不留空标题。 */
    h += gsHtml(r.gs);

    /* ③ 句型说明 —— 句型名 → styles.json（接续/意思/语感） */
    h += '<div class="ex-sec">③ 句型说明</div>';
    var st = r.st ? (S.lex.styles[r.st] || null) : null;
    if (st) {
      h += '<div class="ex-gram"><div class="g-name">' + esc(st.name || r.st) + '</div>';
      if (st.conn) h += '<div class="g-row"><b>结构</b>' + esc(st.conn) + '</div>';
      if (st.mean) h += '<div class="g-row"><b>意思</b>' + esc(st.mean) + '</div>';
      if (st.feel) h += '<div class="g-row"><b>语感</b>' + esc(st.feel) + '</div>';
      if (st.note) h += '<div class="g-note">' + esc(st.note) + '</div>';
      h += '</div>';
    } else {
      h += '<div class="ex-none">这一行没有句型标记。</div>';
    }
    /* 句型表里的真实例句（例句全部来自本剧台词） */
    var hit = matchGrammar(r.t);
    if (hit) {
      h += '<div class="ex-gram"><div class="g-name">' + esc(hit.name) + '（本剧真实例句）</div>';
      if (hit.conn) h += '<div class="g-row"><b>接续</b>' + esc(hit.conn) + '</div>';
      if (hit.mean) h += '<div class="g-row"><b>意思</b>' + esc(hit.mean) + '</div>';
      if (hit.feel) h += '<div class="g-row"><b>语感</b>' + esc(hit.feel) + '</div>';
      if (hit.note) h += '<div class="g-note">' + esc(hit.note) + '</div>';
      if (hit.example) h += '<div class="g-ex">' + esc(hit.example) +
        (hit.exampleZh ? '<em>' + esc(hit.exampleZh) + '</em>' : '') + '</div>';
      h += '</div>';
    }

    /* ④ AI 讲解（实时）—— 默认收起，点开才发请求
     * 不点就不花额度、不等 8~15 秒；静态段落照旧秒开。 */
    h += '<div class="ex-sec ex-sec-ai" id="exAiSec">④ AI 讲解（实时）'
      + '<span class="ex-sec-hint">点开让 AI 讲这一句</span></div>'
      + '<div class="ex-ai" id="exAiBox">'
      + '<div class="ex-ai-quota" id="exAiQuota"></div>'
      + '<div class="ex-ai-body" id="exAiBody"></div>'
      + '<div class="ex-ai-foot">'
      + '<button class="mini-btn" data-act="ai-ask">問 AI：講這一句</button>'
      + '<button class="mini-btn" data-act="ai-copy">複製提問</button>'
      + '</div></div>';

    /* 底部操作 */
    var isFav = !!S.fav[favKey()];
    h += '<div class="ex-foot">' +
      '<button class="mini-btn" data-act="loop">' + (S.loopOne ? '🔁 循环中' : '🔁 循环本句') + '</button>' +
      '<button class="mini-btn' + (isFav ? ' on' : '') + '" data-act="fav">' + (isFav ? '★ 已收藏' : '☆ 收藏本句') + '</button>' +
      '<button class="mini-btn" data-act="dl">⬇ 存本集</button>' +
      '<span class="sp"></span>' +
      '<button class="mini-btn" data-act="close">收起 ▲</button>' +
      '</div>';

    $('#exBody').innerHTML = h;
    $('#exTime').textContent = fmt(L.s);
    $('#exSheet').classList.remove('hidden');
    $('#exMask').classList.remove('hidden');
    $('#exBody').scrollTop = 0;
    syncOverlay();
  }

  /* 句型例句匹配：句型表里的 example 都是本剧真台词 */
  var gramIndex = null;
  function buildGramIndex() {
    if (gramIndex) return gramIndex;
    gramIndex = {};
    var g = S.lex.grammar || {};
    for (var k in g) {
      var ex = (g[k].example || '').replace(/\s+/g, '');
      if (ex) gramIndex[ex] = g[k];
    }
    return gramIndex;
  }
  function matchGrammar(text) {
    if (!text) return null;
    var idx = buildGramIndex();
    var t = String(text).replace(/\s+/g, '');
    if (idx[t]) return idx[t];
    /* 退一步：用句首 6 字匹配 */
    if (t.length > 6) {
      for (var ex in idx) {
        if (ex.length > 6 && t.indexOf(ex) >= 0) return idx[ex];
      }
    }
    return null;
  }

  function closeEx() {
    $('#exSheet').classList.add('hidden');
    $('#exMask').classList.add('hidden');
    S.exIdx = -1;
    syncOverlay();
  }

  /* 浮层打开时把 shell 的「設定」浮标藏掉 —— 它浮在右下角，会压住弹窗内容 */
  function syncOverlay() {
    var open = false;
    ['#exSheet', '#epPanel', '#cachePanel', '#fontPanel', '#morePanel'].forEach(function (id) {
      var el = $(id);
      if (el && !el.classList.contains('hidden')) open = true;
    });
    document.body.classList.toggle('pop-open', open);
  }

  /* 弹窗内点词 → 在弹窗里就地展开（就地展开唯一该用的地方） */
  function expandWord(li, word) {
    var open = li.querySelector('.ex-detail');
    if (open) { open.remove(); li.classList.remove('on'); return; }
    $$('.ex-wd li.on').forEach(function (x) { x.classList.remove('on'); var d = x.querySelector('.ex-detail'); if (d) d.remove(); });
    var v = S.lex.vocab[word] || null;
    var rd = li.getAttribute('data-read') || '';
    if (rd === word) rd = '';
    var h = '<div class="ex-detail">';
    h += '<div class="d-h"><span class="d-w">' + esc(word) + '</span>' +
      (rd ? '<span class="d-r">' + esc(rd) + '</span>' : (v && vReading(v) ? '<span class="d-r">' + esc(vReading(v)) + '</span>' : '')) +
      (v && vPos(v) ? '<span class="d-p">' + esc(vPos(v)) + '</span>' : '') + '</div>';
    if (v) {
      if (vMeaning(v)) h += '<div class="d-m">' + esc(vMeaning(v)) + '</div>';
      if (vUsage(v)) h += '<div class="d-u">' + esc(vUsage(v)) + '</div>';
      if (vExample(v)) h += '<div class="d-e"><b>本剧例句</b>' + esc(vExample(v)) +
        (vExampleZh(v) ? '<em>' + esc(vExampleZh(v)) + '</em>' : '') + '</div>';
    } else {
      h += '<div class="d-m">词库里没有这个词。点「🔊 朗读」可以听发音。</div>';
    }
    h += '</div>';
    li.insertAdjacentHTML('beforeend', h);
    li.classList.add('on');
    speakText(word);
  }

  /* 字幕流里点词 → 只查这个词（不弹整句）：小卡片 + 朗读 */
  function showWordCard(word, reading) {
    var box = $('#wordCard');
    if (!box) return;
    var v = S.lex.vocab[word] || null;
    var h = '<div class="wc-h"><span class="wc-w">' + esc(word) + '</span>' +
      (reading ? '<span class="wc-r">' + esc(reading) + '</span>' : '') +
      (v && vPos(v) ? '<span class="wc-p">' + esc(vPos(v)) + '</span>' : '') +
      '<button class="wc-x" id="wcClose" aria-label="关闭">✕</button></div>';
    h += '<div class="wc-m">' + esc(v && vMeaning(v) ? vMeaning(v) : '词库里没有这个词。') + '</div>';
    if (v && vUsage(v)) h += '<div class="wc-u">' + esc(vUsage(v)) + '</div>';
    if (v && vExample(v)) h += '<div class="wc-e"><b>本剧例句</b>' + esc(vExample(v)) +
      (vExampleZh(v) ? '<em>' + esc(vExampleZh(v)) + '</em>' : '') + '</div>';
    box.innerHTML = h;
    box.classList.remove('hidden');
    var x = document.getElementById('wcClose');
    if (x) x.addEventListener('click', function () { box.classList.add('hidden'); });
    speakText(word);
  }

  /* ---------------- 收藏 ---------------- */
  function favKey() {
    var L = S.lines[S.exIdx];
    if (!L) return '';
    return S.ep.tag + '@' + L.cueIdx + '@' + (L.row.t || '').slice(0, 24);
  }
  function toggleFav() {
    var k = favKey();
    if (!k) return;
    if (S.fav[k]) delete S.fav[k];
    else S.fav[k] = { ep: S.ep.tag, epNo: S.ep.ep, ja: S.lines[S.exIdx].row.t, zh: S.lines[S.exIdx].row.zh };
    LS.set('fav', S.fav);
    renderStudy();
    return !!S.fav[k];
  }

  /* ---------------- 学习页（我的收藏 + 句型表） ---------------- */
  function renderStudy() {
    var list = $('#studyList');
    if (S.studyTab === 'fav') {
      var keys = Object.keys(S.fav);
      $('#studyCount').textContent = keys.length ? keys.length + ' 条' : '';
      if (!keys.length) {
        list.innerHTML = '<div class="ex-none" style="padding:16px 4px">还没有收藏。点任意一句 → 弹窗里「☆ 收藏本句」。</div>';
        return;
      }
      var h = '';
      for (var i = 0; i < keys.length; i++) {
        var f = S.fav[keys[i]];
        h += '<div class="st-item"><div class="st-h">' +
          '<span class="st-w">' + esc(f.ja) + '</span>' +
          '<span class="st-cat">第' + f.epNo + '集</span>' +
          '<button class="st-del" data-fav="' + esc(keys[i]) + '">删除</button></div>' +
          '<div class="st-m">' + esc(f.zh || '') + '</div></div>';
      }
      list.innerHTML = h;
    } else {
      var g = S.lex.grammar || {};
      var gk = Object.keys(g);
      $('#studyCount').textContent = gk.length ? gk.length + ' 条' : '';
      var h2 = '';
      for (var j = 0; j < gk.length; j++) {
        var p = g[gk[j]];
        h2 += '<div class="st-item"><div class="st-h">' +
          '<span class="st-w">' + esc(p.name) + '</span>' +
          (p.short ? '<span class="st-r">' + esc(p.short) + '</span>' : '') +
          (p.cat ? '<span class="st-cat">' + esc(p.cat) + '</span>' : '') + '</div>' +
          (p.conn ? '<div class="st-u"><b>接续</b> ' + esc(p.conn) + '</div>' : '') +
          (p.mean ? '<div class="st-m">' + esc(p.mean) + '</div>' : '') +
          (p.feel ? '<div class="st-u"><b>语感</b> ' + esc(p.feel) + '</div>' : '') +
          (p.note ? '<div class="st-u">' + esc(p.note) + '</div>' : '') +
          (p.example ? '<div class="st-e"><b>本剧例句</b>' + esc(p.example) +
            (p.exampleZh ? '<em>' + esc(p.exampleZh) + '</em>' : '') + '</div>' : '') +
          '</div>';
      }
      list.innerHTML = h2 || '<div class="ex-none" style="padding:16px 4px">句型表是空的。</div>';
    }
  }

  function openStudy() {
    $('#viewPlayer').classList.add('hidden');
    $('#viewStudy').classList.remove('hidden');
    $('#btnBack').classList.remove('hidden');
    renderStudy();
  }
  function backToPlayer() {
    $('#viewStudy').classList.add('hidden');
    $('#viewPlayer').classList.remove('hidden');
    $('#btnBack').classList.add('hidden');
  }

  /* ---------------- 下载 / 缓存 ---------------- */
  function inApk() { return !!(window.Android && typeof window.Android.cacheEpisode === 'function'); }

  /* 🔴 判断「已存」必须按**音频地址**比，不能按 id 比
   * （各项目的音频文件名都叫 epNN.m4a，只按 id 会跨项目串台） */
  function epDownloaded(url) {
    if (inApk()) {
      try {
        var st = JSON.parse(window.Android.downloadedList() || '{}');
        var items = st.items || [];
        for (var i = 0; i < items.length; i++) {
          if (items[i].url && items[i].url === url) return true;
        }
      } catch (e) { }
      return false;
    }
    return !!S.dl[url];
  }

  function refreshTools() {
    var url = audioUrl();
    var dl = $('#btnDl');
    if (dl) {
      var done = epDownloaded(url);
      dl.textContent = done ? '✓ 已存' : '⬇ 下載';
      dl.classList.toggle('done', done);
      dl.setAttribute('data-done', done ? '1' : '0');
    }
    var lo = $('#btnLoopOne2');
    if (lo) lo.classList.toggle('on', S.loopOne);
    var tr = $('#btnTrad2');
    if (tr) tr.classList.toggle('on', S.bilingual);
  }

  /* 网页版下载：存进 Cache Storage（离线也能播），大小记在 localStorage */
  function webDownload(url) {
    if (!window.caches) { toast('这个浏览器不支持离线缓存'); return; }
    toast('正在下载…');
    var cache = null;
    caches.open(CACHE_NAME).then(function (c) {
      cache = c;
      return fetch(url, { cache: 'no-store' });
    }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.arrayBuffer();
    }).then(function (buf) {
      return cache.put(url, new Response(buf, { headers: { 'Content-Type': 'audio/mp4' } }))
        .then(function () {
          S.dl[url] = { bytes: buf.byteLength, ts: Date.now() };
          LS.set('dl', S.dl);
          toast('已存到本机（离线可听）');
          refreshCache(); refreshTools();
        });
    }).catch(function (e) {
      toast('下载失败：' + (e && e.message ? e.message : e));
    });
  }

  function downloadCurrent() {
    var url = audioUrl();
    if (epDownloaded(url)) { toast('第' + S.ep.ep + '集已经在本机里了'); return; }
    if (inApk()) {
      try {
        window.Android.cacheEpisode(S.ep.tag, url);
        toast('正在保存第' + S.ep.ep + '集…');
        var t = setInterval(function () {
          try {
            var st = JSON.parse(window.Android.downloadStatus() || '{}');
            if (st.state === 'done') { clearInterval(t); toast('已存到手机'); refreshCache(); refreshTools(); }
            else if (st.state === 'error') { clearInterval(t); toast('保存失败：' + (st.error || '')); }
          } catch (e) { clearInterval(t); }
        }, 800);
      } catch (e) { toast('保存失败'); }
    } else {
      webDownload(url);
    }
  }

  function fmtCache(n, b) {
    var mb = (b || 0) / 1048576;
    return n + ' 集 · ' + (mb === 0 ? '0.0' : (mb < 0.1 ? '<0.1' : mb.toFixed(1))) + ' MB';
  }
  function refreshCache() {
    var el = $('#cacheVal');
    if (!el) return;
    if (inApk()) {
      try {
        var info = JSON.parse(window.Android.downloadedList() || '{}');
        el.textContent = fmtCache(info.count || 0, info.bytes || 0);
      } catch (e) { el.textContent = '—'; }
    } else {
      var n = 0, b = 0;
      for (var k in S.dl) { if (S.dl.hasOwnProperty(k)) { n++; b += S.dl[k].bytes || 0; } }
      el.textContent = fmtCache(n, b);
    }
    var note = $('#cacheNote');
    if (note) {
      note.textContent = inApk()
        ? '12 集音频已经内置在 App 里（离线也能听）。这里的缓存是可选副本，删了不影响收听。'
        : '网页版把音频存在浏览器缓存里，断网也能听；清空浏览器数据会一起删掉。';
    }
  }
  function clearCache() {
    if (!window.confirm('確定刪除所有已下載的音頻？\n（會釋放手機空間，已聽過的集需要重新下載）')) return;
    if (inApk()) {
      try {
        var r = JSON.parse(window.Android.clearDownloads() || '{}');
        toast(r.removed ? ('已刪除 ' + r.removed + ' 個音頻') : '沒有已下載的音頻');
      } catch (e) { toast('刪除失敗'); }
    } else {
      var urls = Object.keys(S.dl);
      Promise.all(urls.map(function (u) {
        return caches.open(CACHE_NAME).then(function (c) { return c.delete(u); });
      })).then(function () {
        S.dl = {};
        LS.set('dl', S.dl);
        toast('已刪除 ' + urls.length + ' 個音頻');
      }).catch(function () { toast('刪除失敗'); });
    }
    refreshCache(); refreshTools();
  }

  /* ---------------- 字号（字幕 / 弹窗独立） ---------------- */
  function applyFs() {
    document.documentElement.style.setProperty('--fs', S.fs + 'px');
    document.documentElement.style.setProperty('--fszh', Math.round(S.fs * 0.87) + 'px');
    var r = $('#fsRange'); if (r) r.value = S.fs;
  }
  function applyPfs() {
    document.documentElement.style.setProperty('--pfs', S.pfs + 'px');
    var r = $('#pfsRange'); if (r) r.value = S.pfs;
    var v = $('#pfsVal'); if (v) v.textContent = S.pfs;
  }
  function setFs(n) {
    n = Math.max(14, Math.min(40, Math.round(n)));
    S.fs = n; LS.set('fs', n); applyFs();
    if (window.HubShell && window.HubShell.setFs) { try { window.HubShell.setFs(n); } catch (e) { } }
  }
  function setPfs(n) {
    n = Math.max(14, Math.min(30, Math.round(n)));
    S.pfs = n; LS.set('pfs', n); applyPfs();
    if (window.HubShell && window.HubShell.setPfs) { try { window.HubShell.setPfs(n); } catch (e) { } }
  }
  /* shell 接管字号时同步回来（两边都存，改一次两边都记得） */
  window.onHubShellFs = function (n) {
    S.fs = Math.max(14, Math.min(40, n)); LS.set('fs', S.fs); applyFs();
  };
  window.onHubShellPfs = function (n) {
    S.pfs = Math.max(14, Math.min(30, n)); LS.set('pfs', S.pfs); applyPfs();
  };

  /* ---------------- 轻提示 ---------------- */
  function toast(msg) {
    try {
      if (window.HubShell && typeof window.HubShell.toast === 'function') { window.HubShell.toast(msg); return; }
    } catch (e) { }
    var t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;left:50%;bottom:80px;transform:translateX(-50%);z-index:99;' +
      'background:rgba(20,20,20,.9);color:#fff;padding:10px 18px;border-radius:10px;font:15px/1.4 system-ui;max-width:80%';
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  /* ---------------- 选集抽屉 ---------------- */
  function renderEpList() {
    var h = '';
    for (var i = 0; i < S.eps.length; i++) {
      var e = S.eps[i];
      var on = S.ep && e.tag === S.ep.tag;
      h += '<div class="ep-item' + (on ? ' on' : '') + '" data-tag="' + esc(e.tag) + '">' +
        '<span class="ep-n">' + e.ep + '</span>' +
        '<span class="ep-t">' + esc(e.title || e.tag) +
        (e.desc ? '<span class="ep-d2">' + esc(e.desc) + '</span>' : '') + '</span>' +
        '<span class="ep-d">' + Math.round((e.dur || 0) / 60) + ' 分</span></div>';
    }
    $('#epList').innerHTML = h;
  }
  function openSheet(id) {
    var el = $(id);
    if (el) el.classList.remove('hidden');
    syncOverlay();
  }
  function closeSheet(id) {
    var el = $(id);
    if (el) el.classList.add('hidden');
    syncOverlay();
  }
  function togglePanel(id) {
    var el = $(id);
    if (!el) return;
    var wasHidden = el.classList.contains('hidden');
    ['#fontPanel', '#morePanel'].forEach(function (x) { var e = $(x); if (e) e.classList.add('hidden'); });
    if (wasHidden) el.classList.remove('hidden');
    syncOverlay();
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    $('#btnPlay').addEventListener('click', togglePlay);
    $('#btnEp').addEventListener('click', function () { renderEpList(); openSheet('#epPanel'); });
    $('#btnCloseEp').addEventListener('click', function () { closeSheet('#epPanel'); });
    $('#btnFont').addEventListener('click', function () { togglePanel('#fontPanel'); });
    $('#btnMore').addEventListener('click', function () { togglePanel('#morePanel'); });
    $('#btnBack').addEventListener('click', backToPlayer);

    /* 字号面板 */
    $$('.fp-btn').forEach(function (b) {
      b.addEventListener('click', function () { setFs(S.fs + (+b.getAttribute('data-fs'))); });
    });
    $$('.fp-p[data-preset]').forEach(function (b) {
      b.addEventListener('click', function () { setFs(+b.getAttribute('data-preset')); });
    });
    var fr = $('#fsRange');
    if (fr) fr.addEventListener('input', function () { setFs(+this.value); });

    /* 弹窗字号条（独立于字幕字号） */
    $$('.fp-p[data-pfs]').forEach(function (b) {
      b.addEventListener('click', function () { setPfs(+b.getAttribute('data-pfs')); });
    });
    var pr = $('#pfsRange');
    if (pr) pr.addEventListener('input', function () { setPfs(+this.value); });
    $('#exAa').addEventListener('click', function () {
      var bar = $('#exSizeBar');
      if (bar) bar.classList.toggle('hidden');
    });

    /* 工具条 */
    $('#btnBack10').addEventListener('click', function () { seek(S.pos - 10000); });
    $('#btnFwd10').addEventListener('click', function () { seek(S.pos + 10000); });
    $('#btnLoopOne2').addEventListener('click', function () {
      S.loopOne = !S.loopOne; LS.set('loopOne', S.loopOne); refreshTools();
      toast(S.loopOne ? '单句循环：开' : '单句循环：关');
    });
    /* 「雙語」= 中日对照 / 纯日文（本剧只有简体，繁简开关没有意义） */
    $('#btnTrad2').addEventListener('click', function () {
      S.bilingual = !S.bilingual; LS.set('bilingual', S.bilingual);
      renderCues(); setIdx(S.idx, false); refreshTools();
      toast(S.bilingual ? '双语文幕：开' : '双语文幕：关（只看日文）');
    });
    $('#btnFontBig2').addEventListener('click', function () { setFs(S.fs + 2); });
    $('#btnDl').addEventListener('click', downloadCurrent);

    /* 更多菜单 */
    $('#btnStudy').addEventListener('click', function () { closeSheet('#morePanel'); openStudy(); });
    $('#btnCache').addEventListener('click', function () { closeSheet('#morePanel'); refreshCache(); openSheet('#cachePanel'); });
    $('#btnFollow').addEventListener('click', function () {
      S.follow = !S.follow; LS.set('follow', S.follow);
      this.textContent = S.follow ? '开' : '关'; this.classList.toggle('on', S.follow);
    });
    $('#btnApk').addEventListener('click', function () { location.href = 'shun.apk'; });
    $('#btnApk2').addEventListener('click', function () { location.href = 'shun.apk'; });
    $('#btnCloseCache').addEventListener('click', function () { closeSheet('#cachePanel'); });
    $('#btnClearCache').addEventListener('click', clearCache);

    /* 学习页 tab */
    $$('#studyTabs .tab').forEach(function (t) {
      t.addEventListener('click', function () {
        $$('#studyTabs .tab').forEach(function (x) { x.classList.remove('on'); });
        t.classList.add('on');
        S.studyTab = t.getAttribute('data-tab');
        renderStudy();
      });
    });

    /* 字幕流：点词 → 只查这个词；点句 → 弹大窗 */
    $('#cueBox').addEventListener('click', function (e) {
      var w = e.target.closest ? e.target.closest('.jpw') : null;
      if (w) {
        var word = w.getAttribute('data-w') || '';
        var rd = w.querySelector('rt') ? w.querySelector('rt').textContent : '';
        showWordCard(word, rd);
        return;
      }
      var el = e.target.closest ? e.target.closest('.cue') : null;
      if (!el) return;
      var i = +el.getAttribute('data-i');
      openEx(i);
    });

    /* 弹窗：点词就地展开 / 底部操作 */
    $('#exBody').addEventListener('click', function (e) {
      var li = e.target.closest ? e.target.closest('.ex-wd li') : null;
      if (li) { expandWord(li, li.getAttribute('data-word')); return; }
      var btn = e.target.closest ? e.target.closest('[data-act]') : null;
      if (!btn) return;
      var act = btn.getAttribute('data-act');
      if (act === 'close') closeEx();
      else if (act === 'seek') { var L = S.lines[S.exIdx]; if (L) seek(L.s * 1000); closeEx(); }
      else if (act === 'speak') speakCurrent();
      else if (act === 'copy-sent') copySentence();
      else if (act === 'fav') { var on = toggleFav(); btn.textContent = on ? '★ 已收藏' : '☆ 收藏本句'; btn.classList.toggle('on', on); }
      else if (act === 'loop') { S.loopOne = !S.loopOne; LS.set('loopOne', S.loopOne); refreshTools(); btn.textContent = S.loopOne ? '🔁 循环中' : '🔁 循环本句'; }
      else if (act === 'dl') downloadCurrent();
      else if (act === 'ai-ask' && window.ShunAIActions) window.ShunAIActions.ask();
      else if (act === 'ai-copy' && window.ShunAIActions) window.ShunAIActions.copy();
    });

    /* 点「④ AI 讲解」标题 → 展开/收起（默认收起，点开才发请求） */
    $('#exBody').addEventListener('click', function (e) {
      var sec = e.target.closest ? e.target.closest('#exAiSec') : null;
      if (sec && window.ShunAIActions) window.ShunAIActions.toggle();
    });

    /* 弹窗头部 */
    $('#exClose').addEventListener('click', closeEx);
    $('#exX').addEventListener('click', closeEx);
    $('#exMask').addEventListener('click', closeEx);
    $('#exJump').addEventListener('click', function () { var L = S.lines[S.exIdx]; if (L) seek(L.s * 1000); closeEx(); });
    $('#exDl').addEventListener('click', downloadCurrent);

    /* 学习页删除收藏 */
    $('#studyList').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-fav]') : null;
      if (!b) return;
      delete S.fav[b.getAttribute('data-fav')];
      LS.set('fav', S.fav);
      renderStudy();
    });

    /* 选集 */
    $('#epList').addEventListener('click', function (e) {
      var el = e.target.closest ? e.target.closest('.ep-item') : null;
      if (!el) return;
      var tag = el.getAttribute('data-tag');
      closeSheet('#epPanel');
      var ep = null;
      for (var i = 0; i < S.eps.length; i++) if (S.eps[i].tag === tag) ep = S.eps[i];
      if (!ep) return;
      loadCues(ep).then(function (o) {
        S.lines = flatten(o.cues || []);
        loadEp(ep, 0);
        LS.set('ep', tag);
      }).catch(function () { toast('这一集的数据还没准备好'); });
    });

    /* 进度条 */
    $('#scrub').addEventListener('click', function (e) {
      var r = this.getBoundingClientRect();
      seek((e.clientX - r.left) / r.width * (S.dur || 0));
    });

    /* 键盘（桌面/横屏用） */
    document.addEventListener('keydown', function (e) {
      if (e.key === ' ') { e.preventDefault(); togglePlay(); }
      else if (e.key === 'ArrowLeft') seek(S.pos - 5000);
      else if (e.key === 'ArrowRight') seek(S.pos + 5000);
      else if (e.key === 'Escape') { closeEx(); closeSheet('#epPanel'); closeSheet('#cachePanel'); }
    });
  }

  /* ---------------- 朗读（Web Speech API） ---------------- */
  function speakText(txt) {
    if (!txt) return;
    try {
      if (!window.speechSynthesis) { toast('这台设备不支持朗读'); return; }
      var u = new SpeechSynthesisUtterance(String(txt));
      u.lang = 'ja-JP';
      u.rate = 0.85;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    } catch (e) { }
  }
  function speakCurrent() {
    var L = S.lines[S.exIdx];
    if (!L || !L.row.t) return;
    speakText(L.row.t);
  }

  /* ---------------- 一键复制整句 ----------------
   * 复制内容 = 日文原句 + 换行 + 中文翻译（学习者拿去查 / 提问都用得上）。
   * WebView 里 navigator.clipboard 常常不可用 ⇒ 必须有 execCommand 兜底，
   * 兜底也失败 ⇒ 把文字显示出来让用户长按选（和「複製提問」同一套做法）。
   */
  function copyText(text, okMsg) {
    if (!text) { toast('没有可复制的内容'); return; }
    var done = function () { toast(okMsg || '已复制', 2200); };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallbackCopyText(text, okMsg); });
        return;
      }
    } catch (e) { }
    fallbackCopyText(text, okMsg);
  }

  function fallbackCopyText(text, okMsg) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '0';
    document.body.appendChild(ta);
    try { ta.select(); ta.setSelectionRange(0, ta.value.length); } catch (e) { }
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { }
    document.body.removeChild(ta);
    if (ok) { toast(okMsg || '已复制', 2200); return; }
    showGsCopyBox(text);
  }

  /* 🔴 函数名**不能**叫 showCopyBox —— shun-ai-ui.js 里已经有同名函数（AI 复制降级用的）。
   *    两者在同一作用域 ⇒ 后加载的会覆盖先加载的 ⇒ AI 的降级路径会被顶掉。
   *    ⇒ 这里用 gs 前缀区分。 */
  function showGsCopyBox(text) {
    var body = document.getElementById('exBody');
    if (!body) { toast('复制失败，请手动记录', 2200); return; }
    var d = document.createElement('div');
    d.className = 'ex-ai-err';
    d.innerHTML = '复制失败，请长按下面文字全选复制：';
    var ta = document.createElement('textarea');
    ta.className = 'ex-ai-copy';
    ta.setAttribute('readonly', 'readonly');
    ta.value = text;
    d.appendChild(ta);
    body.insertBefore(d, body.firstChild);
  }

  function copySentence() {
    var L = S.lines[S.exIdx];
    if (!L || !L.row.t) { toast('没有可复制的句子'); return; }
    var ja = L.row.t;
    var zh = L.row.zh || '';
    var text = zh ? (ja + '\n' + zh) : ja;
    copyText(text, '已复制整句（日文+中文）');
  }

  /* ---------------- 启动 ---------------- */
  function init() {
    S.bilingual = LS.get('bilingual', true) !== false;
    S.follow = LS.get('follow', true) !== false;
    S.loopOne = !!LS.get('loopOne', false);
    S.fav = LS.get('fav', {});
    S.dl = LS.get('dl', {});
    /* 字号：shell 的全局 key（hubshell）优先 —— 同一个人的眼睛只有一套，
     * 不该每个 App 各调一次；没有全局设置才用本项目自己的。 */
    var glob = null;
    try { glob = JSON.parse(localStorage.getItem('hubshell') || 'null'); } catch (e) { glob = null; }
    var fs0 = (glob && glob.fs) ? glob.fs : LS.get('fs', 30);
    var pfs0 = (glob && glob.pfs) ? glob.pfs : LS.get('pfs', 17);
    S.fs = Math.max(14, Math.min(40, fs0));
    S.pfs = Math.max(14, Math.min(30, pfs0));
    applyFs(); applyPfs();
    if (window.HubShell && window.HubShell.setFs) {
      try { window.HubShell.setFs(S.fs); window.HubShell.setPfs(S.pfs); } catch (e) { }
    }

    var fo = $('#btnFollow');
    if (fo) { fo.textContent = S.follow ? '开' : '关'; fo.classList.toggle('on', S.follow); }

    /* APK 里已经在 App 里了 → 「下载 Android App」没意义（点了会跳 404） */
    if (isNative) {
      var apkRow = $('#btnApk');
      if (apkRow && apkRow.parentElement) apkRow.parentElement.style.display = 'none';
      var apk2 = $('#btnApk2');
      if (apk2) { apk2.textContent = '已是最新版本'; apk2.disabled = true; apk2.style.opacity = '.55'; }
    }

    bind();

    loadAll().then(function () {
      if (!S.eps.length) { toast('集数索引读不出来'); return; }
      var last = LS.get('ep', null);
      var ep = null;
      for (var i = 0; i < S.eps.length; i++) if (S.eps[i].tag === last) ep = S.eps[i];
      if (!ep) ep = S.eps[0];
      return loadCues(ep).then(function (o) {
        S.lines = flatten(o.cues || []);
        loadEp(ep, 0);
        renderEpList();
        refreshCache();
        refreshTools();
      });
    }).catch(function (e) {
      toast('数据加载失败：' + (e && e.message ? e.message : e));
    });

    /* 位置回传 → 高亮当前句 */
    tickCb = function (d) {
      updateProgress();
      if (!S.lines.length) return;
      var i = binarySearch(d.pos);
      if (i >= 0) setIdx(i, true);
    };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
