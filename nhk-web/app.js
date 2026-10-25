/* 魔卡少女樱 · 日中对照学习
 * 同一份代码同时用于：离线 APK（本地 assets）、网页版、在线流媒体外壳 APK
 *
 * v7：播放器抽象
 *   网页版 → <audio> 直接放
 *   APK    → 原生 MediaPlayer 直连自建 CDN（WebView 兜底走本机代理）
 *   两条路对外接口一致，字幕同步 / 进度 / 变速逻辑完全共用。
 */
(function () {
  'use strict';

  var EPISODES = window.__EPISODES__ || [];
  var SUBS = window.__SUBS__ = window.__SUBS__ || {};
  // 字幕数据版本：换版时 +1，绕开浏览器/CDN 的旧缓存
  var APP_VER = 'v35';
  // 运行时配置（config.js）。🔴 这里只可能有公开信息，不会有任何 key。
  var CFG = window.CFG || {};

  var LS = {
    get: function (k, d) {
      try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; }
      catch (e) { return d; }
    },
    set: function (k, v) {
      try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ }
    }
  };

  var S = {
    ep: null,          // 当前期
    ver: 'easy',       // easy=易懂版(慢速音声) | web=普通版(原速音声)
    subs: [],          // 当前版本的字幕
    idx: -1,           // 当前字幕行索引
    mode: 'both',      // both | ja | zh | none
    speed: 1,
    loop: false,       // 单句循环
    playing: false,
    vocab: LS.get('ccs_vocab', []),
    prog: LS.get('ccs_prog', {}),
    settings: LS.get('ccs_set', { fs: 30, pfs: 17, auto: true, theme: 'light', ro: true, hc: false, spacious: false, tags: false, audio: 'auto', sp: true }),
    filter: '',
    epQuery: '',        // 首页找集
    audioOwner: null,   // 当前唯一在响的音源：'ours' | 'native' | null
    cdnUrls: [],        // 本集在 CDN 上的候选地址（第一个失败就换下一个）
    cdnTry: 0,          // 已经试到第几个候选
    studyTab: 'f',      // 学习页：f=我的收藏 p=句型表（生词表入口已按用户要求移除）
    studyQ: '',
    pendingCue: -1,     // 从例句跳转时要定位的句号
    ret: null           // 跳转前的原句位置（「回到原句」用）
  };
  if (S.settings.ro === undefined) S.settings.ro = true;   // 罗马字默认开启
  S.settings.ro = !!S.settings.ro;
  // 🔴🔴 2026-10-24 新增：普通版「音字同步」开关（默认开）
  //   开 = 跟音模式：字幕流切到 **sp 轨**（电视播报稿），按原速音时间轴高亮 + 自动滚
  //   关 = 阅读模式：字幕流用 **text 轨**（报纸原文），不高亮、不自动滚
  if (S.settings.sp === undefined) S.settings.sp = true;
  S.settings.sp = !!S.settings.sp;

  var $ = function (s) { return document.querySelector(s); };
  var audioEl = $('#audio');
  var toastTimer = null;

  function toast(msg, ms) {
    var t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    // 墨水屏刷新慢，默认 1700ms 常常来不及看；重要提示可传更长的时长
    toastTimer = setTimeout(function () { t.classList.remove('show'); }, ms || 1700);
  }

  function fmt(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
    return (h ? h + ':' + (m < 10 ? '0' : '') + m : m) + ':' + (s < 10 ? '0' : '') + s;
  }

  /* ============ 剧集列表 ============ */
  function renderList() {
    var box = $('#epList');
    box.innerHTML = '';
    var q = (S.epQuery || '').trim().toLowerCase();
    $('#epCount').textContent = q ? ('找到 ' + EPISODES.length + ' 期里的 ' + q) : (EPISODES.length + ' 期 · 離線');
    var shown = 0;
    // 按日期分组（NHK 每天一期）
    var groups = [], gmap = {};
    EPISODES.forEach(function (ep) {
      if (q) {
        var hay = (ep.no + ' ' + ep.title + ' ' + (ep.easyTitle || '') + ' ' +
                   (ep.date || '') + ' ' + (ep.time || '')).toLowerCase();
        if (hay.indexOf(q) < 0) return;
      }
      shown++;
      var gk = ep.date || '';
      if (!gmap[gk]) { gmap[gk] = { d: gk, items: [] }; groups.push(gmap[gk]); }
      gmap[gk].items.push(ep);
    });
    groups.forEach(function (grp) {
      var hd = document.createElement('div');
      hd.className = 'ep-group';
      hd.innerHTML = '<span class="ep-gd">' + (grp.d || '全部') +
        '</span><span class="ep-gn">' + grp.items.length + ' 期</span>';
      box.appendChild(hd);
      grp.items.forEach(function (ep) {
      var p = S.prog[ep.id] || {};
      var watched = (p && p.t) || 0;
      var pct = Math.min(100, Math.round((watched / (ep.duration || 1)) * 100));
      if (!isFinite(pct) || pct < 0) pct = 0;
      if (pct > 97) pct = 100;
      // 这一集收藏了几个生词
      var vc = 0;
      for (var vi = 0; vi < S.vocab.length; vi++) if (S.vocab[vi].ep === ep.no) vc++;
      var el = document.createElement('div');
      el.className = 'ep-item' + (pct >= 100 ? ' done' : '') + (pct > 0 && pct < 100 ? ' doing' : '');
      el.innerHTML =
        '<div class="ep-no">' + escapeHtml(ep.time || ep.no) + '</div>' +
        '<div class="ep-meta">' +
        '<div class="ep-name">' + escapeHtml(ep.title || '') +
        (vc ? '<em class="ep-vc">📖' + vc + '</em>' : '') +
        '<small>' + escapeHtml(ep.easyTitle || '') + '</small></div>' +
        (pct > 0 ? '<div class="ep-bar"><i style="width:' + pct + '%"></i></div>' +
          '<div class="ep-pct">' + (pct >= 100 ? '已看完' : '已看 ' + pct + '% · ' + fmt(watched)) + '</div>' : '') +
        '</div>';
      el.addEventListener('click', function () { openEpisode(ep.id); });
      box.appendChild(el);
      });
    });
    if (!shown) {
      var em = document.createElement('p');
      em.className = 'empty';
      em.textContent = '沒找到相關的劇集';
      box.appendChild(em);
    }
    renderResume();
  }

  /** 首页顶部「继续学」卡片：显示最近看的那一集 + 进度 */
  function renderResume() {
    var lw = lastWatched();
    var name = $('#resumeName'), sub = $('#resumeSub'), bar = $('#resumeBar'),
      pct = $('#resumePct'), btn = $('#btnContinue');
    if (!lw) {
      if (name) name.textContent = '還沒開始看';
      if (sub) sub.textContent = EPISODES.length + ' 期慢速新聞 · 逐句漢字注音 + 簡體中文對照 · 離線也能學';
      if (bar) bar.style.width = '0%';
      if (pct) pct.textContent = '隨便點下面一集就能開始';
      if (btn) { btn.disabled = true; btn.textContent = '▶ 開始'; }
      return;
    }
    var ep = EPISODES.filter(function (e) { return e.id === lw; })[0];
    if (!ep) return;
    var p = S.prog[lw] || {};
    var watched = (p && p.t) || 0;
    var pr = Math.min(100, Math.round((watched / (ep.duration || 1)) * 100));
    if (!isFinite(pr) || pr < 0) pr = 0;
    if (pr > 97) pr = 100;
    if (name) name.textContent = ep.title;
    if (sub) sub.textContent = (ep.zhTitle || '') + ' · ' + fmt(ep.duration);
    if (bar) bar.style.width = pr + '%';
    if (pct) pct.textContent = pr >= 100 ? '已看完，可以重看' : ('已看 ' + pr + '% · ' + fmt(watched) + ' / ' + fmt(ep.duration));
    if (btn) { btn.disabled = false; btn.textContent = '▶ 繼續'; }
  }

  function lastWatched() {
    var best = null, bt = -1;
    Object.keys(S.prog).forEach(function (k) {
      var p = S.prog[k];
      if (p && p.t > 5 && p.updated > bt) { bt = p.updated; best = k; }
    });
    return best;
  }

  /* ============ 生词本 ============ */
  function renderVocab() {
    var box = $('#vocabPanel');
    $('#vocabCount').textContent = S.vocab.length + ' 條';
    var qv = $('#qVocab');
    if (qv) qv.textContent = S.vocab.length;
    if (!S.vocab.length) {
      box.innerHTML = '<p class="empty">在播放頁點擊台詞右側的「＋」即可收藏生詞，方便複習。</p>';
      return;
    }
    box.innerHTML = '';
    S.vocab.slice().reverse().forEach(function (v) {
      var el = document.createElement('div');
      el.className = 'vocab-item';
      if (v.kind === 'w') {
        el.innerHTML =
          '<div style="flex:1;min-width:0">' +
          '<div class="v-ja">' + escapeHtml(v.ja) +
          (v.r ? ' <span class="v-r">(' + escapeHtml(v.r) + ')</span>' : '') + '</div>' +
          '<div class="v-zh">' + escapeHtml(v.zh || '（暂无释义）') + '</div>' +
          '<div class="v-meta">生词 · 来自 ' + fmt(v.t) + '</div>' +
          '</div>' +
          '<button class="v-del" title="刪除">×</button>';
      } else {
        el.innerHTML =
          '<div style="flex:1;min-width:0">' +
          '<div class="v-ja">' + escapeHtml(v.ja) + '</div>' +
          '<div class="v-zh">' + escapeHtml(v.zh) + '</div>' +
          '<div class="v-meta">' + fmt(v.t) + '</div>' +
          '</div>' +
          '<button class="v-del" title="刪除">×</button>';
      }
      el.querySelector('.v-del').addEventListener('click', function (e) {
        e.stopPropagation();
        S.vocab = S.vocab.filter(function (x) { return x.id !== v.id; });
        LS.set('ccs_vocab', S.vocab);
        renderVocab();
        toast('已刪除');
      });
      el.addEventListener('click', function () {
        openEpisode(v.epId, v.t);
      });
      box.appendChild(el);
    });
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /** 收藏一个词（不是整句） */
  function addWord(word, z, r, epId, ep, at) {
    if (!word) return;
    var id = 'w:' + word;
    var dup = S.vocab.some(function (v) { return v.id === id; });
    if (dup) { toast('「' + word + '」已在收藏裡'); return; }
    S.vocab.push({
      id: id, kind: 'w', ja: word, zh: z || '', r: r || '',
      epId: epId, ep: ep, s: at || 0, t: at || 0
    });
    LS.set('ccs_vocab', S.vocab);
    renderVocab();
    toast('已收藏「' + word + '」');
  }

  function addVocab(cue) {
    if (!cue) { toast('沒有可收藏的台詞'); return; }
    var dup = S.vocab.some(function (v) { return v.epId === S.ep.id && v.s === cue.s; });
    if (dup) { toast('已在生詞本中'); return; }
    S.vocab.push({
      id: S.ep.id + '_' + cue.s, epId: S.ep.id, ep: S.ep.no, t: cue.s / 1000,
      s: cue.s, ja: cue.ja, zh: cue.zh
    });
    LS.set('ccs_vocab', S.vocab);
    renderVocab();
    renderList();
    var ab = cueEls && cueEls[S.subs.indexOf(cue)];
    if (ab) {
      var btn = ab.querySelector('.c-add');
      if (btn) { btn.classList.add('saved'); btn.textContent = '✓'; }
    }
    toast('已加入我的收藏 · 首頁「📖 我的收藏」裡複習');
  }

  /* ============ 载入剧集 ============ */
  function subUrl(path) {
    // 离线版（file:// / android_asset）直接用原路径；在线版加版本号破缓存
    if (location.protocol === 'file:' || location.hostname === 'appassets.androidplatform.net') return path;
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'v=' + APP_VER;
  }
  function loadScript(src) {
    return new Promise(function (resolve) {
      if (document.querySelector('script[data-sub="' + src + '"]')) return resolve();
      var s = document.createElement('script');
      s.src = src;
      s.dataset.sub = src;
      s.onload = function () { resolve(); };
      s.onerror = function () { resolve(); };
      document.head.appendChild(s);
    });
  }

  /* 词库延迟加载：
     vocab.js 2.3MB + lexicon.js 1.5MB = 3.8MB，只有「点词查释义」「弹窗逐词拆解」用得上。
     放在首屏会让手机上开 App 要下 4MB（流量下等半分钟）。
     → 首屏只留 175KB，进播放页时后台预热，用户点句时基本已就绪。 */
  var wordDataReady = null;
  function ensureWordData() {
    if (wordDataReady) return wordDataReady;
    wordDataReady = Promise.all([
      loadScript('data/vocab.js?v=' + APP_VER),
      loadScript('data/lexicon.js?v=' + APP_VER)
    ]).then(function () { return true; });
    return wordDataReady;
  }
  function wordDataReadyNow() {
    return !!(window.__VOCAB__ && window.__LEXICON__);
  }

  /* ================================================================
   * 播放器抽象
   *   WebImpl    —— 网页版：<audio> 元素
   *   NativeImpl —— APK：原生 MediaPlayer（直连自建 CDN 的音频地址）
   * 事件：play / pause / timeupdate / ended / error / loadedmetadata
   * ================================================================ */

  function Emitter() {
    this._h = {};
  }
  Emitter.prototype.on = function (t, f) {
    (this._h[t] = this._h[t] || []).push(f);
  };
  Emitter.prototype.off = function (t, f) {
    var hs = this._h[t] || [], i = hs.indexOf(f);
    if (i >= 0) hs.splice(i, 1);
  };
  Emitter.prototype.emit = function (t, a) {
    var hs = (this._h[t] || []).slice();
    for (var i = 0; i < hs.length; i++) { try { hs[i](a); } catch (e) { /* ignore */ } }
  };

  function WebImpl(el) {
    Emitter.call(this);
    this.el = el;
    var self = this;
    ['play', 'pause', 'timeupdate', 'ended', 'error', 'loadedmetadata'].forEach(function (t) {
      el.addEventListener(t, function (e) { self.emit(t, e); });
    });
  }
  WebImpl.prototype = Object.create(Emitter.prototype);
  WebImpl.prototype.constructor = WebImpl;
  WebImpl.prototype.play = function () {
    var p = this.el.play();
    if (p && p.catch) p.catch(function () { });
    return p;
  };
  WebImpl.prototype.pause = function () { this.el.pause(); };
  WebImpl.prototype.load = function () { if (this.el.load) this.el.load(); };
  WebImpl.prototype.setSrc = function (v) { this.el.src = v; };
  Object.defineProperty(WebImpl.prototype, 'currentTime', {
    get: function () { return this.el.currentTime; },
    set: function (v) { try { this.el.currentTime = v; } catch (e) { } }
  });
  Object.defineProperty(WebImpl.prototype, 'duration', { get: function () { return this.el.duration; } });
  Object.defineProperty(WebImpl.prototype, 'paused', { get: function () { return this.el.paused; } });
  Object.defineProperty(WebImpl.prototype, 'error', { get: function () { return this.el.error; } });
  Object.defineProperty(WebImpl.prototype, 'playbackRate', {
    get: function () { return this.el.playbackRate; },
    set: function (v) { try { this.el.playbackRate = v; } catch (e) { } }
  });
  Object.defineProperty(WebImpl.prototype, 'src', { get: function () { return this.el.src; } });

  function NativeImpl() {
    Emitter.call(this);
    this.pos = 0; this.dur = 0; this.playing = false; this.ready = false;
    this.completed = false; this._err = ''; this.ep = null; this.speed = 1;
    this.actualPlaying = false;   // 🔴 「实际在不在播」（看门狗要用）
    this._lastErr = ''; this._wasPlaying = false; this._wasReady = false; this._wasDone = false;
    var self = this;
    this._timer = setInterval(function () { self.tick(); }, 200);
  }
  NativeImpl.prototype = Object.create(Emitter.prototype);
  NativeImpl.prototype.constructor = NativeImpl;
  NativeImpl.prototype.tick = function () {
    var st;
    try { st = JSON.parse(window.Android.playerState()); }
    catch (e) { return; }
    if (!st) return;
    /* 🔴🔴🔴 2026-10-23 单位换算（原生给毫秒，前端全程用秒）
     *
     * 原生 playerState 的 pos/dur 是**毫秒**：
     *   AudioService.position() = mp.getCurrentPosition()  ← MediaPlayer 的单位就是毫秒
     *   （dex 级实证：v1.0~v1.16 全部 16 个版本的 position() 里都有 getCurrentPosition、
     *     **没有任何 /1000 换算** ⇒ 所有历史版本都是毫秒）
     *
     * 而 P.currentTime 全程按 **HTML5 <audio> 契约 = 秒** 用：
     *   · cueIndexAt(t) 比的是 `c.s <= t * 1000`（cue 的 s/e 是毫秒）
     *   · fmt(sec) 假定输入是秒
     *   · seekAudio(Math.round(v * 1000)) 也是按秒拼毫秒
     *   · attach() 里 this.pos = startSec（秒）
     *
     * 🔴 不换算的后果（用户能看见的）：
     *   高亮永远落在**最后一句**（t*1000 变成几千万毫秒 → 二分查找一路走到末尾）
     *   时间标签显示 "500:00" 这种乱数
     *   循环本句立刻跳回句首
     *   **但进度条百分比看起来是对的** —— ms/ms 比值与单位无关 ⇒ 最容易骗过自己。
     *
     * 换算**只在这一处做**，别处一律用秒。
     */
    this.pos = ((+st.pos) || 0) / 1000;
    this.dur = ((+st.dur) || 0) / 1000;
    this.ready = !!st.ready;
    /* 🔴 playing 的语义分两种原生：
     *   新原生（带 actualPlaying）→ st.playing 已经是「用户意图」（wantPlay||playing）
     *     → 直接用，按钮不会横跳。
     *   旧原生（没有 actualPlaying）→ st.playing 是「实际在不在播」，
     *     网络流准备期间（1~5 秒）恒 false → 会把前端的乐观 ⏸ 顶回 ▶ → 图标左右横跳。
     *     → 这时用前端的意图兜底（用户手机上的旧 APK 也能立刻好转）。 */
    if ('actualPlaying' in st) {
      this.playing = !!st.playing;
      this.actualPlaying = !!st.actualPlaying;
    } else {
      this.playing = !!st.playing || (!this.ready && !!this._wantPlay);
      this.actualPlaying = !!st.playing;          // 旧原生没有该字段 → 只能拿 playing 顶
    }
    this.completed = !!st.completed;
    var err = st.error ? String(st.error) : '';
    if (err !== this._lastErr) {
      this._lastErr = err;
      this._err = err;
      if (err) this.emit('error', err);
    } else {
      this._err = err;
    }
    if (this.ready && !this._wasReady) this.emit('loadedmetadata');
    if (this.playing !== this._wasPlaying) this.emit(this.playing ? 'play' : 'pause');
    if (this.completed && !this._wasDone) this.emit('ended');
    this._wasReady = this.ready; this._wasPlaying = this.playing; this._wasDone = this.completed;
    this.emit('timeupdate');
  };
  NativeImpl.prototype.attach = function (ep, startSec, url) {
    this.ep = ep;
    this.pos = startSec || 0;
    this.completed = false; this._wasDone = false; this._err = ''; this._lastErr = '';
    try {
      window.Android.playAudio(absUrl(String(url || '')), Math.round((startSec || 0) * 1000));
    } catch (e) {
      this._err = String((e && e.message) || e);
      this.emit('error', this._err);
    }
  };
  NativeImpl.prototype.play = function () {
    this._wantPlay = true;
    /* 🔴🔴 无条件调 resumeAudio（2026-10-22 修「点了没反应」）
     *
     * 真原生 AudioService 的语义是：attach(playAudio) 只 prepare，**不会自动播**；
     * 要等 resume() 才把 wantPlay 置 true，prepare 完成后 onPrepared → doStart()。
     *
     * 旧代码在「还没 ready」时只记前端的 _wantPlay、**不调 resumeAudio**
     * → 原生的 wantPlay 永远是 false → 永远不播。
     * ⇒ 用户必须在 1~5 秒准备完成后才点播放才出声；准备期间点 = 点了没反应。
     *
     * 原生 resume() 自己会判断：没 prepared 就只置 wantPlay=true，不抛 IllegalStateException。
     */
    try { window.Android.resumeAudio(); } catch (e) { }
  };
  NativeImpl.prototype.pause = function () {
    this._wantPlay = false;               // 🔴 清意图：旧原生兜底逻辑靠它
    try { window.Android.pauseAudio(); } catch (e) { }
  };
  NativeImpl.prototype.load = function () { };
  NativeImpl.prototype.setSrc = function (v) { this.srcUrl = v; };
  Object.defineProperty(NativeImpl.prototype, 'currentTime', {
    get: function () { return this.pos; },
    set: function (v) {
      this.pos = v || 0;
      try { window.Android.seekAudio(Math.round((v || 0) * 1000)); } catch (e) { }
    }
  });
  Object.defineProperty(NativeImpl.prototype, 'duration', { get: function () { return this.dur; } });
  Object.defineProperty(NativeImpl.prototype, 'paused', { get: function () { return !this.playing; } });
  Object.defineProperty(NativeImpl.prototype, 'error', { get: function () { return this._err; } });
  Object.defineProperty(NativeImpl.prototype, 'playbackRate', {
    get: function () { return this.speed; },
    set: function (v) {
      this.speed = v;
      try { window.Android.setSpeed(v); } catch (e) { }
    }
  });
  Object.defineProperty(NativeImpl.prototype, 'src', { get: function () { return this.srcUrl || ''; } });

  var P = {
    impl: null,
    mode: 'web',
    init: function () {
      var native = !!(window.Android && typeof window.Android.playAudio === 'function');
      // 播放方式：auto（默认，原生优先，失败自动切网页）/ native（强制原生）/ web（强制网页）
      var pref = S.settings.audio || 'auto';
      if (pref === 'web') native = false;
      this.impl = native ? new NativeImpl() : new WebImpl(audioEl);
      this.mode = native ? 'native' : 'web';
    },
    swap: function (impl, mode) {
      this.impl = impl;
      this.mode = mode;
      bindPlayerEvents();
    },
    on: function (t, f) { if (this.impl) this.impl.on(t, f); },
    off: function (t, f) { if (this.impl) this.impl.off(t, f); },
    play: function () { return this.impl ? this.impl.play() : null; },
    pause: function () { if (this.impl) this.impl.pause(); },
    load: function () { if (this.impl) this.impl.load(); },
    setSrc: function (v) { if (this.impl) this.impl.setSrc(v); }
  };
  Object.defineProperty(P, 'currentTime', {
    get: function () { return this.impl ? this.impl.currentTime : 0; },
    set: function (v) { if (this.impl) this.impl.currentTime = v; }
  });
  Object.defineProperty(P, 'duration', { get: function () { return this.impl ? this.impl.duration : 0; } });
  Object.defineProperty(P, 'paused', { get: function () { return this.impl ? this.impl.paused : true; } });
  Object.defineProperty(P, 'error', { get: function () { return this.impl ? this.impl.error : null; } });
  Object.defineProperty(P, 'playbackRate', {
    get: function () { return this.impl ? this.impl.playbackRate : 1; },
    set: function (v) { if (this.impl) this.impl.playbackRate = v; }
  });
  Object.defineProperty(P, 'src', { get: function () { return this.impl ? this.impl.src : ''; } });

  /* 暴露内部状态给自动化测试用（字幕/进度/播放模式，无任何凭据） */
  try { window.__S = S; window.__P = P; } catch (e) { }

  /* ================================================================
   * 音频仲裁 —— 任何时刻只允许「一个」音源在响
   * 可同时存在的音源有两个：原生 MediaPlayer / WebView <audio>
   * 以前它们会各响各的（原生报错后切内置 → 两个都在放）
   * ================================================================ */
  function silenceWeb() {
    try {
      if (!audioEl) return;
      audioEl.pause();
      audioEl.removeAttribute('src');
      if (audioEl.load) audioEl.load();
    } catch (e) { }
  }
  function silenceNative() {
    try {
      if (window.Android && typeof window.Android.stopAudio === 'function') window.Android.stopAudio();
    } catch (e) { }
  }
  /**
   * 音频仲裁：谁在响，就把另一个掐掉。
   * who = 'ours'(WebView <audio>) | 'native'(原生 MediaPlayer)
   */
  function claimAudio(who) {
    S.audioOwner = who || 'ours';
    if (S.audioOwner === 'ours') silenceNative();   // 用内置音频 → 原生必须停
    if (S.audioOwner === 'native') silenceWeb();     // 用原生播放器 → 内置音频必须停
  }

  /* ============ 音频来源：自建 CDN（已完全脱离 B 站） ============
   * data/cdn.js 里是「每集 → 候选地址列表」，同一集通常分布在 2 个节点做冗余。
   * 第一个节点冷启动失败会自动换下一个，网页版和 APK 共用同一套地址。
   */
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /** 相对地址 → 绝对地址。
      🔴 原生播放器（MediaPlayer.setDataSource）只认绝对 URL：
      相对路径会被当成本地文件路径 → prepare 直接失败（网页版没事，App 里没声）。 */
  function absUrl(u) {
    if (!u) return '';
    var s = String(u);
    if (/^https?:\/\//i.test(s)) return s;
    try { return new URL(s, location.href).href; } catch (e) { return s; }
  }

  /** 某集在 CDN 上的候选音频地址 */
  function cdnCandidates(ep) {
    var m = window.__CDN__ || {};
    var a = m[ep && ep.id];
    if (!a) return [];
    if (typeof a === 'string') a = [a];
    // NHK：同一期有两套音声（易懂版=慢速 / 普通版=原速）
    // ⚠️ 普通版**绝不回退到慢速音**：慢速音念的是易懂版的词，
    //    配新闻稿字幕会声画错位。宁可没有声音（纯阅读），也不能放错的音。
    if (!Array.isArray(a) && typeof a === 'object') {
      a = S.ver === 'web' ? (a.fast || []) : (a.slow || []);
    }
    var out = [];
    for (var i = 0; i < a.length; i++) { if (a[i]) out.push(String(a[i])); }
    return withMediaBase(out);
  }

  /** 音频基址（window.CFG.MEDIA_BASE）
   *  · 不填 / 空数组 ⇒ 用相对路径（本站自己带 media/ 的部署形态，行为与从前完全一致）
   *  · 填绝对 URL（字符串或数组）⇒ 媒体在别处，静态资源在本处
   *    —— 用于「静态站放 GitHub Pages（永久地址）+ 媒体放 mcode 源站」这种形态。
   *    数组 ⇒ 多个候选源，按顺序 fallback（第一个源挂了自动试下一个）。
   *  🔴 唯一权威在这里；别在别处再拼一遍前缀。 */
  function withMediaBase(paths) {
    var cfg = window.CFG || {};
    var base = cfg.MEDIA_BASE;
    if (!base) return paths;
    if (!Array.isArray(base)) base = [base];
    var out = [];
    for (var b = 0; b < base.length; b++) {
      var pre = String(base[b] || '').replace(/\/+$/, '');
      if (!pre) continue;
      for (var i = 0; i < paths.length; i++) {
        var p = paths[i];
        if (/^https?:\/\//i.test(p)) { out.push(p); continue; }
        out.push(pre + '/' + p.replace(/^\/+/, ''));
      }
    }
    // 一个 base 都没配好 ⇒ 退回相对路径（至少不会变成空数组导致完全没声音）
    return out.length ? out : paths;
  }

  /** APK 内若前端映射缺失，退回原生桥接查一次 */
  function nativeAudioUrl(ep) {
    try {
      if (window.Android && typeof window.Android.resolveAudio === 'function') {
        var raw = window.Android.resolveAudio(String(ep.epId || ''));
        if (raw) {
          var info = JSON.parse(raw);
          if (info && info.url) return info.url;
          if (info && info.error) window.__ccsAudioError = String(info.error);
        }
      }
    } catch (e) { window.__ccsAudioError = String((e && e.message) || e); }
    return null;
  }

  function attachAudio(ep, startSec) {
    // 无论哪种模式，先把上一个音源彻底关掉，避免两个声音同时响
    silenceWeb();
    var urls = cdnCandidates(ep);
    S.cdnTry = 0;
    if (!urls.length) {
      var alt = nativeAudioUrl(ep);
      if (alt) urls = [alt];
    }
    S.cdnUrls = urls;
    if (!urls.length) { giveUpAudio(ep); return; }
    if (P.mode === 'native') {
      claimAudio('native');
      P.impl.attach(ep, startSec || 0, urls[0]);
      watchNativeAudio();
      return;
    }
    claimAudio('ours');
    P.setSrc(urls[0]);
    P.load();
  }

  /** 原生播放器失败 → 退回 WebView 的 <audio>（同一个 CDN 地址） */
  function fallbackToWeb(ep) {
    if (P.mode !== 'native' || !ep) return;
    silenceNative();          // 关键：原生播放器还在放的话会跟内置播放器叠成两个声音
    P.swap(new WebImpl(audioEl), 'web');
    var urls = (S.cdnUrls && S.cdnUrls.length) ? S.cdnUrls : cdnCandidates(ep);
    var url = absUrl(urls[0] || nativeAudioUrl(ep) || '');
    if (!url) { giveUpAudio(ep); return; }
    P.setSrc(url);
    P.load();
    P.play();
    toast('原生播放失敗，已切換內置播放器');
  }

  /* 原生播放器（APK）报错的兜底：
     某些 AAC 码流 Android 平台解码器解不了 → 原生层把错误抛回来 → 这里自动切回 WebView 播放。
     另外加一个看门狗：原生模式启动 7 秒后还没出声，也切回 WebView（静默失败是最坏的）。 */
  window.__nhkAudioErr = function (msg) {
    try { if (msg) console.warn('native audio error: ' + msg); } catch (e) { }
    if (P.mode === 'native' && S.ep) {
      fallbackToWeb(S.ep);
      toast('原生播放失敗，已切換內置播放器');
    }
  };
  var nativeWatch = null;
  /* 🔴 看门狗（2026-10-22 修误判）
   *   旧版：4 秒后用 `P.paused` 判断「没出声」。
   *   问题一：网络流准备本身就要 1~5 秒 → **慢网络下准备期还没过就被判定为失败**
   *           → 静默切到 WebView 播放器 → 用户听到的是「刚点就换了个播放器」。
   *   问题二：P.paused 现在是**意图**语义（准备期 paused=true）→ 更容易误判。
   *   修法：① 4 秒 → **8 秒**；② 用「**意图要播但实际没在播**」判断，
   *        准备期（意图 true、实际 false）不算异常。 */
  function watchNativeAudio() {
    if (nativeWatch) { clearTimeout(nativeWatch); nativeWatch = null; }
    if (P.mode !== 'native') return;
    nativeWatch = setTimeout(function () {
      nativeWatch = null;
      if (P.mode !== 'native' || !S.ep || P.error) return;
      var want = P.impl ? !!P.impl.playing : false;        // 意图
      var actual = P.impl ? !!P.impl.actualPlaying : false; // 实际
      if (want && !actual) {
        window.__nhkAudioErr('watchdog: 8s no sound');
      }
    }, 8000);
  }

  function onAudioError() {
    var ep = S.ep;
    if (!ep) return;
    if (P.mode === 'native') { fallbackToWeb(ep); return; }

    // 节点冷启动失败 / 网络抖动 → 换一个候选节点重试（每集有 2 个候选）
    var urls = (S.cdnUrls && S.cdnUrls.length) ? S.cdnUrls : cdnCandidates(ep);
    var i = S.cdnTry || 0;
    if (i + 1 < urls.length) {
      S.cdnTry = i + 1;
      P.setSrc(urls[i + 1]);
      P.load();
      P.play();
      toast('換了一個音頻源重試…');
      return;
    }
    giveUpAudio(ep);
  }

  /** 实在放不出来 → 说清楚，别让它哑着 */
  function giveUpAudio(ep) {
    var diag = '';
    try {
      if (window.Android && typeof window.Android.ping === 'function') {
        var pz = JSON.parse(window.Android.ping());
        if (pz && pz.lastError) diag = '（' + pz.lastError + '）';
      }
    } catch (e) { /* 忽略 */ }
    toast('音頻載入失敗' + (window.__ccsAudioError || diag) + '，請檢查網絡後重試');
  }

  /* ============ 音频状态（APK 内直接显示代理端口与真实错误，方便定位） ============ */
  function renderAudioStat() {
    var el = $('#audioStat');
    if (!el) return;
    if (!window.Android) { el.textContent = ''; return; }
    var parts = [];
    try {
      var pz = JSON.parse(window.Android.ping());
      parts.push('App v' + (pz.appVer || '?'));
      if (pz.nativePlaying) parts.push('原生：播放中');
      else if (pz.nativeReady) parts.push('原生：已就緒');
      else parts.push('原生：未就緒');
      if (pz.nativeUrl) parts.push('原生檔案 ' + String(pz.nativeUrl).split('/').pop());
      if (pz.lastError) parts.push('原生錯誤：' + pz.lastError);
    } catch (e) { parts.push('橋接異常：' + String((e && e.message) || e)); }
    var err = P.error;
    if (err) {
      parts.push(P.mode === 'native' ? ('原生錯誤：' + err) : ('媒體錯誤 code=' + (err.code || '?')));
    } else if (P.mode === 'native') {
      parts.push(P.paused ? '待播放' : '播放中');
    } else if (P.src) {
      parts.push(P.paused ? '待播放' : '播放中');
    } else {
      parts.push('尚未載入');
    }
    parts.push(P.mode === 'native' ? '原生播放器' : '內置播放器');
    parts.push('音源：自建 CDN');
    var st2 = null;
    try { st2 = JSON.parse(window.Android.playerState()); } catch (e) { }
    if (st2) {
      parts.push('原生' + (st2.playing ? '播放中' : (st2.ready ? '已就绪' : '未就绪')));
      if (st2.error) parts.push('原生錯誤：' + st2.error);
      if (st2.url) parts.push('原生檔案：' + String(st2.url).split('/').pop());
    }
    el.textContent = parts.join(' · ');
  }
  setInterval(renderAudioStat, 1500);

  function openEpisode(id, at) {
    var ep = EPISODES.filter(function (e) { return e.id === id; })[0];
    if (!ep) return;
    S.ep = ep;
    S.subs = [];
    S.idx = -1;
    S.filter = '';
    $('#search').value = '';
    var tb = $('#topBadge');
    if (tb) { tb.textContent = (ep.date || '').slice(5) + ' ' + (ep.time || ''); tb.classList.remove('hidden'); }
    $('#epTitle').textContent = ep.title || (ep.date || '');
    $('#epSub').textContent = (ep.date || '') + ' ' + (ep.time || '') +
      ' · ' + (S.ver === 'web' ? '普通版 · 原速 ' + fmt(ep.durFast || 0)
                                : '易懂版 · 慢速 ' + fmt(ep.duration || 0));
    $('#viewHome').classList.add('hidden');
    $('#viewPlayer').classList.remove('hidden');
    $('#btnBack').classList.remove('hidden');
    $('#transcript').innerHTML = '<p class="empty">載入中…</p>';
    showView('player');
    renderRetBar();

    var prog = S.prog[ep.id];
    var start = (typeof at === 'number') ? at : (prog ? prog.t : 0);
    attachAudio(ep, start);
    P.playbackRate = S.speed;
    P.play();   // 自动起播（浏览器拦自动播放时忽略，用户点 ▶ 即可）

    loadScript(subUrl(ep.subs)).then(function () {
      // 顺手把词库拉起来（用户点句时就能秒开弹窗）
      ensureWordData();
      S.subs = cuesOf(ep);
      renderTranscript();
      refreshVerBar(ep);
      if (S.pendingCue >= 0) {
        var pc = S.subs[S.pendingCue];
        S.pendingCue = -1;
        if (pc) {
          P.currentTime = pc.s / 1000 + 0.02;
          S.idx = -1;
          syncCue();
          toast('EP' + ep.no + ' · 跳到这句');
        }
      }
      if (start > 1) {
        P.currentTime = start;
        if (!S.quiet) toast('從 ' + fmt(start) + ' 繼續');
      }
      if (S.quiet) { S.quiet = false; toast('已回到第' + ep.no + '話'); }
      updateTime(true);
    });
  }

  /* ============ 版本切换：易懂版（慢速音声）／普通版（原速音声） ============
     两版是两套完全不同的文本 + 两套音声：
       easy → NHK「やさしい版」短句，慢速音声，8~12 句
       web  → 普通版書き起こし（新闻稿），原速音声，30~150 句
     所以版本和语速是绑定的，切换时文本、音声、时间轴一起换。 */
  function cuesOf(ep) {
    var d = (window.__SUBS__ || {})[ep && ep.id];
    if (!d) return [];
    if (Object.prototype.toString.call(d) === '[object Array]') return d;   // 兼容单版本
    // 🔴🔴 2026-10-25 修「声画对不上」
    //   原来：开关一关 ⇒ 字幕切成报纸原文，**但音频还是电视播报音（fast）**
    //        ⇒ 听着播报稿、看着报纸稿 ⇒ 声画全错（用户反馈「对不上」）
    //   现在：普通版**只要这一期有 sp 轨（= 播报稿，与原速音是同一份）就恒用它**
    //        「音字同步」开关**只管高亮 + 自动滚**，不再换字幕来源
    //        ⇒ 任何情况下，打开的音和看到的字都是同一份
    if (S.ver === 'web') {
      var sp = d.sp;
      if (sp && sp.length) return sp;
    }
    return d[S.ver] || d.easy || [];
  }

  /** 这一期的 sp 轨（电视播报稿）—— 没有原速音就没有 ⇒ null */
  function spTrack(ep) {
    var d = (window.__SUBS__ || {})[ep && ep.id];
    if (!d || Object.prototype.toString.call(d) === '[object Array]') return null;
    var sp = d.sp;
    return (sp && sp.length) ? sp : null;
  }

  /** 当前字幕流是「跟音」还是「阅读」—— 顶栏要显示这个（文字，别用图标） */
  function trackMode(ep) {
    if (S.ver === 'web') {
      var sp = spTrack(ep);
      // 🔴 2026-10-25：字幕源由「有没有 sp 轨」决定，**不再由开关决定**
      //   开关只表示「高亮 + 自动滚」开不开
      if (sp) {
        return { mode: 'sp', label: '跟音字幕（電視播報稿）· ' + (S.settings.sp ? '高亮同步' : '靜默跟讀') };
      }
      return { mode: 'text', label: '閱讀（報紙原文）' };
    }
    return { mode: 'sp', label: '跟音（慢速音）' };
  }

  /** 要不要做「音字同步」（高亮 + 自动滚）—— **与易懂版共用同一套逻辑**，只是这里多一个条件 */
  function syncEnabled() {
    if (S.ver !== 'web') return true;        // 易懂版：音和字本来就是一份
    // 🔴🔴 2026-10-25：**没有原速音 ⇒ 没有东西可同步** ⇒ 不高亮、不自动滚
    //   （这一期普通版根本没有音频在放，高亮一个「不存在的播放位置」只会误导）
    if (S.ep && !verHasAudio(S.ep, 'web')) return false;
    return !!S.settings.sp;                   // 开关只管高亮+自动滚
  }

  function verAvailable(ep, ver) {
    if (!ep) return false;
    var vs = ep.ver || ['easy'];
    if (vs.indexOf(ver) < 0) return false;
    return true;      // 普通版即使没有音声，也能当原文对照读
  }
  function verHasAudio(ep, ver) {
    if (!ep) return false;
    if (ver === 'web') return (ep.durFast || 0) > 1;
    return (ep.duration || 0) > 1;
  }

  function refreshVerBar(ep) {
    var bar = $('#verBar');
    if (!bar) return;
    Array.prototype.forEach.call(bar.querySelectorAll('.vb'), function (b) {
      var v = b.getAttribute('data-ver');
      b.classList.toggle('on', v === S.ver);
      var ok = verAvailable(ep, v);
      b.disabled = !ok;
      b.classList.toggle('off', !ok);
    });
    var hint = $('#vbHint');
    if (hint) {
      var has = verHasAudio(ep, S.ver);
      // 🔴 2026-10-24：普通版要给明确的模式指示（文字）
      //   跟音（電視播報稿）= 字幕跟着原速音走
      //   閱讀（報紙原文）  = 纯阅读对照，不高亮、不自动滚
      if (S.ver === 'easy') {
        hint.textContent = '簡單句 · 慢速' + (has ? '' : ' · 無音声');
      } else {
        var tm = trackMode(ep);
        hint.textContent = tm.label + (has ? ' · 原速' : ' · 無原速音 · 對照閱讀');
      }
    }
    // 🔴 2026-10-24：普通版**没有原速音**的期 ⇒ 不给播放按钮
    //   （宁可没有声音，也不能放慢速音 —— 慢速音念的是易懂版的词，配新闻稿会声画全错）
    var pb = $('#btnPlay');
    if (pb) pb.classList.toggle('hidden', !(S.ver === 'easy' ? verHasAudio(ep, 'easy')
                                                       : verHasAudio(ep, 'web')));
  }

  function setVersion(ver) {
    if (!S.ep || S.ver === ver) return;
    if (!verAvailable(S.ep, ver)) { toast('這一期沒有原速音声'); return; }
    var prevIdx = S.idx;
    S.ver = ver;
    S.subs = cuesOf(S.ep);
    S.idx = -1;
    renderTranscript();
    refreshVerBar(S.ep);
    // 换音声 + 时间轴：按句子序号在新版里找同位置的句子
    var urls = cdnCandidates(S.ep);
    if (urls.length) {
      attachAudio(S.ep, 0);
      P.playbackRate = S.speed;
      P.play();
    } else {
      // 普通版（新闻稿）不一定有配套音声 → 变成纯阅读模式
      silenceWeb();
      var hint = $('#vbHint');
      if (hint) hint.textContent = '本集普通版無音声 · 對照閱讀';
      toast('本集普通版沒有原速音声，可當作原文對照閱讀');
    }
    var target = 0;
    if (prevIdx >= 0 && S.subs[prevIdx]) target = S.subs[prevIdx].s / 1000;
    else if (S.subs[0]) target = S.subs[0].s / 1000;
    if (target) { try { P.currentTime = target + 0.05; } catch (e) { } }
    syncCue();
    $('#epSub').textContent = (S.ep.date || '') + ' ' + (S.ep.time || '') +
      ' · ' + (S.ver === 'web' ? '普通版 · 原速 ' + fmt(S.ep.durFast || 0)
                                : '易懂版 · 慢速 ' + fmt(S.ep.duration || 0));
    toast(S.ver === 'easy' ? '已切到易懂版 · 慢速音声'
        : (verHasAudio(S.ep, 'web') ? '已切到普通版 · 原速音声' : '已切到普通版（原文對照）'));
  }

  function showView(v) {
    var st = $('#viewStudy');
    // 搜索 / 字号按钮只在播放页出现
    var bs = $('#btnSearch'), ba = $('#btnAa');
    if (bs) bs.classList.toggle('hidden', v !== 'player');
    if (ba) ba.classList.toggle('hidden', v !== 'player');
    var tbd = $('#topBadge');
    if (tbd) tbd.classList.toggle('hidden', v !== 'player');
    if (v === 'study') {
      $('#viewHome').classList.add('hidden');
      $('#viewPlayer').classList.add('hidden');
      if (st) st.classList.remove('hidden');
      $('#btnBack').classList.remove('hidden');
      renderStudy();
      document.title = '生词表 · 句型表 · NHKやさしいことばニュース';
    } else if (v === 'home') {
      $('#viewHome').classList.remove('hidden');
      $('#viewPlayer').classList.add('hidden');
      if (st) st.classList.add('hidden');
      $('#btnBack').classList.add('hidden');
      renderList();
      renderVocab();
      document.title = 'NHKやさしいことばニュース · 日中对照精读';
    } else {
      $('#viewHome').classList.add('hidden');
      $('#viewPlayer').classList.remove('hidden');
      if (st) st.classList.add('hidden');
      $('#btnBack').classList.remove('hidden');
      document.title = (S.ep ? (S.ep.title || S.ep.date || '') : 'NHKやさしいことばニュース') + ' · 日中对照精读';
    }
    window.scrollTo(0, 0);
  }


  /* ================================================================
   * 生词 / 句型：行内标注 + 弹窗 + 词表
   * 标注数据在构建期写进每句的 c（生词）与 g（句型）数组里，
   * 运行时只做切片拼 HTML，不做分词 —— 快。
   * ================================================================ */

  /** 字幕里的内嵌读音（木之本(きのもと)）→ ruby 注音 */
/* ---------- 读音与朗读 ---------- */
var READINGS = null;
function readingTable() {
  if (!READINGS) READINGS = window.__READING__ || {};
  return READINGS;
}
/** 一个表层形的读音（平假名）；查不到返回空串 */
function readOf(surface) {
  var t = readingTable();
  if (t[surface]) return t[surface];
  return '';
}
function hasKanji(s) { return /[一-鿿]/.test(s); }

/** 日语朗读（点词发音）。设备没装日语语音包时会静默失败。 */
/* ---------- 日语朗读：检测语音包 + 没装时给指引 ---------- */
function jaVoices() {
  var synth = window.speechSynthesis;
  if (!synth || !synth.getVoices) return [];
  var vs = synth.getVoices() || [];
  var out = [];
  for (var i = 0; i < vs.length; i++) {
    var l = (vs[i].lang || '').toLowerCase();
    if (l.indexOf('ja') === 0 || l.indexOf('jp') === 0) out.push(vs[i]);
  }
  return out;
}
/** 语音包状态：ok / no-voice（没装日语包）/ no-api（系统不支持） */
function ttsState() {
  var synth = window.speechSynthesis;
  if (!synth || !window.SpeechSynthesisUtterance) return 'no-api';
  return jaVoices().length ? 'ok' : 'no-voice';
}
/** 弹「怎么装日语语音包」的指引 */
function showTtsGuide(reason) {
  var h = '<div class="pop-title"><span class="pop-w">點詞朗讀沒聲音？</span></div>';
  h += '<div class="pop-z" style="font-size:16px">手機的語音引擎還沒有<b>日語語音包</b>，裝上就有了。</div>';
  h += '<div class="pop-sec">通用做法（最快）</div>';
  h += '<div class="pop-row"><b>1</b>打開手機「<b>設置</b>」，在搜索框輸入「<b>文字轉語音</b>」</div>';
  h += '<div class="pop-row"><b>2</b>點進「文字轉語音 (TTS) 輸出」→「<b>首選引擎</b>」旁邊的小齒輪 ⚙️</div>';
  h += '<div class="pop-row"><b>3</b>點「<b>安裝語音數據</b>」→ 找到「<b>日本語</b>」→ 點下載 ⬇️</div>';
  h += '<div class="pop-row"><b>4</b>重開 App，再點詞就有聲了</div>';
  h += '<div class="pop-sec">各家手機的位置</div>';
  h += '<div class="pop-row"><b>小米</b>設置 → 更多設置 → 無障礙 → 文字轉語音(TTS)輸出</div>';
  h += '<div class="pop-row"><b>華為</b>設置 → 輔助功能 → 無障礙 → 文字轉語音</div>';
  h += '<div class="pop-row"><b>OPPO/vivo/一加</b>設置 → 其他設置 → 無障礙 → 文字轉語音</div>';
  h += '<div class="pop-row"><b>三星</b>設置 → 輔助功能 → 文字轉語音輸出</div>';
  h += '<div class="pop-sec">關鍵：引擎要選對</div>';
  h += '<div class="pop-row">國產 ROM 自帶的引擎常常<b>不支援日語</b> → 把「首選引擎」改成 '
     + '<b>Google 文字轉語音</b>，再在它的「安裝語音數據」裡下日本語（Play 商店搜 '
     + '<b>Google 文字轉語音</b> 更新到最新版）。</div>';
  h += '<div class="pop-row muted">iPhone：設置 → 輔助功能 → 朗讀內容 → 聲音 → 日本語 → 點雲朵下載</div>';
  h += '<div class="pop-act"><button class="btn-primary" id="ttsRetry">重新檢測</button>'
     + '<button class="tbtn" id="ttsClose">關閉</button></div>';
  popShow(h);
  var rb = $('#ttsRetry');
  if (rb) rb.addEventListener('click', function () {
    popHide();
    setTimeout(function () {
      if (ttsState() === 'ok') { toast('語音包已就緒，試試點詞'); speakJa('さくら'); }
      else { showTtsGuide(reason); }
    }, 200);
  });
  var cb = $('#ttsClose');
  if (cb) cb.addEventListener('click', popHide);
}

function speakJa(text) {
  var synth = window.speechSynthesis;
  if (!synth || !window.SpeechSynthesisUtterance) {
    toast('这个设备不支持朗读');
    return;
  }
  if (ttsState() === 'no-voice') {
    // 详解弹窗开着的时候别把它顶掉 → 只弹个 toast，指引放菜单里
    var popEl2 = document.querySelector('#pop');
    if (popEl2 && !popEl2.classList.contains('hidden')) {
      toast('還沒裝日語語音包 · 選單 →「安裝方法」');
    } else {
      showTtsGuide('no-voice');
    }
    return;
  }
  try {
    var u = new SpeechSynthesisUtterance(text);
    u.lang = 'ja-JP';
    u.rate = 0.85;
    var vs = jaVoices();
    if (vs.length) u.voice = vs[0];
    var fired = false;
    u.onstart = function () { fired = true; };
    u.onerror = function (e) {
      fired = true;
      var err = (e && e.error) || '';
      if (err.indexOf('language') >= 0 || err.indexOf('voice') >= 0) showTtsGuide(err);
      else toast('朗讀失敗：' + (err || '未知錯誤'));
    };
    synth.cancel();
    synth.speak(u);
    // 有些國產 ROM 會「靜默失敗」：onstart/onerror 都不觸發
    setTimeout(function () { if (!fired) showTtsGuide('silent'); }, 1600);
  } catch (e) {
    toast('朗读失败：' + String((e && e.message) || e));
  }
}

  var READ_RX = /([^\s（(、。！？]{1,8})[（(]([ぁ-んァ-ヶ・]{1,12})[）)]/g;
  function jaMarks(c) {
    var ja = c.ja || '';
    var marks = [];
    (c.c || []).forEach(function (m) { marks.push({ s: m[0], e: m[0] + m[1], k: 'w', v: m[2] }); });
    (c.g || []).forEach(function (m) { marks.push({ s: m[0], e: m[0] + m[1], k: 'p', v: m[2] }); });
    // 注音块
    READ_RX.lastIndex = 0;
    var m2;
    while ((m2 = READ_RX.exec(ja)) !== null) {
      marks.push({ s: m2.index, e: m2.index + m2[0].length, k: 'r',
        base: m2[1], read: m2[2] });
    }
    return { ja: ja, marks: marks };
  }

  /**
   * 渲染字幕里的日文：汉字注音（ruby）+ 生词/句型标记（可点）。
   *
   * 两条硬约束（都是踩出来的）：
   *  1) 注音块和生词/句型标记**经常是同一段文字** → 不能二选一，
   *     要把标记切进 ruby 内部：<ruby><span class="w">木之本</span><rt>きのもと</rt></ruby>
   *  2) 标记必须在 ruby **外层**或 ruby 内部但**包住 rt 的点击**——
   *     rt 是 ruby 的兄弟节点，点 rt 时 closest('.w') 会找不到 →
   *     所以标记放在 ruby 内部（rt 冒泡上去能命中）。
   */
  function renderJa(c) {
    var ja = c.ja || '';
    var KANJI = /[一-鿿]/;

    // ---- 1) 注音块：字幕自带的内嵌读音 + reading.js 全量查表
    var rubies = [];
    READ_RX.lastIndex = 0;
    var m2;
    while ((m2 = READ_RX.exec(ja)) !== null) {
      // base 不含括号（括号和读音一起吃掉），skip 走到括号之后
      rubies.push({
        s: m2.index, e: m2.index + m2[1].length, skip: m2.index + m2[0].length,
        base: m2[1], read: m2[2]
      });
    }
    var toks = c.t || [];
    for (var ti = 0; ti < toks.length; ti++) {
      var tk = toks[ti];
      var w = ja.substr(tk[0], tk[1]);
      if (!w || !KANJI.test(w)) continue;
      var rd = readOf(w);
      if (!rd) continue;
      rubies.push({ s: tk[0], e: tk[0] + tk[1], skip: tk[0] + tk[1], base: w, read: rd });
    }
    // 去重叠：同起点时长者优先；重叠的后一个丢掉
    rubies.sort(function (a, b) { return a.s - b.s || (b.e - b.s) - (a.e - a.s); });
    var kept = [];
    for (var ri = 0; ri < rubies.length; ri++) {
      var r = rubies[ri], bad = false;
      for (var rj = 0; rj < kept.length; rj++) {
        if (r.s < kept[rj].e && kept[rj].s < r.e) { bad = true; break; }
      }
      if (!bad) kept.push(r);
    }
    rubies = kept;

    // ---- 2) 生词 / 句型标记（默认不显示：字幕流保持干净，点句才进弹窗）
    var tags = [];
    if (S.settings.tags) {
      (c.c || []).forEach(function (m) { tags.push({ s: m[0], e: m[0] + m[1], k: 'w', v: m[2] }); });
      (c.g || []).forEach(function (m) { tags.push({ s: m[0], e: m[0] + m[1], k: 'p', v: m[2] }); });
      tags.sort(function (a, b) { return a.s - b.s || (b.e - b.s) - (a.e - a.s); });
    }

    if (!rubies.length && !tags.length) return escapeHtml(ja);

    function tagHTML(t) {
      var tx = escapeHtml(ja.slice(t.s, t.e));
      return (t.k === 'w')
        ? '<span class="w" data-w="' + encodeURIComponent(t.v) + '">' + tx + '</span>'
        : '<span class="pg" data-p="' + t.v + '">' + tx + '</span>';
    }

    // ---- 3) 合并输出
    var out = '', pos = 0, ri2 = 0, ti2 = 0;
    while (ri2 < rubies.length || ti2 < tags.length) {
      var nR = ri2 < rubies.length ? rubies[ri2] : null;
      var nT = ti2 < tags.length ? tags[ti2] : null;
      if (nR && (!nT || nR.s <= nT.s)) {
        if (nR.s < pos) { ri2++; continue; }
        out += escapeHtml(ja.slice(pos, nR.s));
        // 把落在这个注音块内部的标记切进 ruby
        var inner = '', p = nR.s;
        while (ti2 < tags.length && tags[ti2].e <= nR.s) ti2++;
        for (var k = ti2; k < tags.length; k++) {
          var t = tags[k];
          if (t.s >= nR.e) break;
          if (t.e > nR.e) continue;          // 超出这个 ruby → 留给外面
          inner += escapeHtml(ja.slice(p, t.s));
          inner += tagHTML(t);
          p = t.e;
          ti2 = k + 1;
        }
        inner += escapeHtml(ja.slice(p, nR.e));
        out += '<ruby>' + inner + '<rt>' + escapeHtml(nR.read) + '</rt></ruby>';
        pos = nR.skip || nR.e;
        ri2++;
      } else {
        if (nT.s < pos) { ti2++; continue; }
        out += escapeHtml(ja.slice(pos, nT.s));
        out += tagHTML(nT);
        pos = nT.e;
        ti2++;
      }
    }
    return out + escapeHtml(ja.slice(pos));
  }

  /** 结构弹窗里的成分块文本（注音 + 每个词可点） */
  function jaSlice(ja, s, l) {
    return jaRich({ ja: ja, t: [] }, s, l);
  }

  /** 渲染 ja 的 [s,e) 区间：ruby 注音 + 每个词包成可点的 .sw */
  function jaRich(c, s, e) {
    var ja = c.ja || '';
    var toks = (c.t || []).filter(function (t) {
      return t[0] >= s && t[0] + t[1] <= e;
    });
    var events = [];
    var rx = new RegExp(READ_RX.source, 'g');
    var m;
    while ((m = rx.exec(ja)) !== null) {
      if (m.index + m[0].length > e) break;
      if (m.index >= s) events.push({ s: m.index, e: m.index + m[0].length, ruby: [m[1], m[2]] });
    }
    for (var i = 0; i < toks.length; i++) {
      if (!ja.substr(toks[i][0], toks[i][1]).trim()) continue;   // 空白不参与
      events.push({ s: toks[i][0], e: toks[i][0] + toks[i][1] });
    }
    events.sort(function (a, b) { return a.s - b.s || (b.e - b.s) - (a.e - a.s); });
    var out = '', pos = s;
    for (var k = 0; k < events.length; k++) {
      var ev = events[k];
      if (ev.s < pos) continue;
      out += escapeHtml(ja.slice(pos, ev.s));
      if (ev.ruby) {
        out += '<ruby class="sw" data-s="' + ev.s + '" data-e="' + ev.e + '">' +
          escapeHtml(ev.ruby[0]) + '<rt>' + escapeHtml(ev.ruby[1]) + '</rt></ruby>';
      } else {
        var txt = ja.slice(ev.s, ev.e);
        var rd = hasKanji(txt) ? readOf(txt) : '';
        if (rd) {
          out += '<ruby class="sw" data-s="' + ev.s + '" data-e="' + ev.e + '">' +
            escapeHtml(txt) + '<rt>' + escapeHtml(rd) + '</rt></ruby>';
        } else {
          out += '<span class="sw" data-s="' + ev.s + '" data-e="' + ev.e + '">' +
            escapeHtml(txt) + '</span>';
        }
      }
      pos = ev.e;
    }
    return out + escapeHtml(ja.slice(pos, e));
  }

  /** 点一个词 → 找出最长的词条（可跨相邻词合并，如「皮肤的后面」） */
  function lookupWord(c, s, e) {
    var ja = c.ja || '';
    var LEX = window.__LEXICON__ || {};
    var VOC = window.__VOCAB__ || {};
    var toks = c.t || [];
    var idxs = [];
    for (var i = 0; i < toks.length; i++) {
      var ts = toks[i][0], te = ts + toks[i][1];
      if (te > s && ts < e) idxs.push(i);
    }
    var ALIAS = window.__ALIAS__ || {};
    var best = null;
    if (idxs.length) {
      var i0 = idxs[0], i1 = idxs[idxs.length - 1];
      // 先找「有中文释义」的最长匹配，找不到再退到只有索引的
      for (var pass = 0; pass < 2 && !best; pass++) {
        var tbl = pass === 0 ? VOC : LEX;
        for (var len = (i1 - i0 + 1); len >= 1 && !best; len--) {
          for (var st = i0; st + len - 1 <= i1; st++) {
            var a = toks[st][0], b = toks[st + len - 1][0] + toks[st + len - 1][1];
            var cand = ja.substr(a, b - a);
            if (tbl[cand]) { best = cand; break; }
          }
        }
      }
    }
    if (!best) {
      var surf = ja.substr(s, e - s);
      // 表层形 → 词典形（点「駆」查到「駆ける」）：用词库自带的 base_form
      var lx0 = LEX[surf] || {};
      var bf0 = lx0.bf || (lx0.b && lx0.b[0]) || '';
      best = (VOC[surf] || LEX[surf]) ? surf
        : (bf0 && (VOC[bf0] || LEX[bf0]) ? bf0 : surf);
    }
    return best;
  }

  /** 就地展开这个词的解释 */
  function showWordDetail(c, s, e, word) {
    var box = $('#wordDetail');
    if (!box) return;
    // 词库是延遲加載的：沒到就等一下再展開（別讓用戶點了沒反應）
    if (!wordDataReadyNow()) {
      ensureWordData().then(function () { showWordDetail(c, s, e, word); });
      return;
    }
    var w = word || lookupWord(c, s, e);
    var VOC = window.__VOCAB__ || {};
    var LEX = window.__LEXICON__ || {};
    var v = VOC[w] || {};
    var lx = LEX[w] || {};
    var SY = window.__SYNTAX__ || { p: {} };
    var pos = v.p || lx.p || '';
    var posName = SY.p[codeOfPos(pos)] || pos;

    var h = '<div class="wd-head"><span class="wd-w">' + escapeHtml(w) + '</span>' +
      (lx.r ? '<span class="wd-r">' + escapeHtml(lx.r) + '</span>' : '') +
      (posName ? '<span class="wd-p">' + escapeHtml(posName) + '</span>' : '') +
      (lx.n ? '<span class="wd-n">出现 ' + lx.n + ' 次</span>' : '') + '</div>';
    h += '<div class="wd-z">' + escapeHtml(v.z || '（这个词还没有中文释义）') + '</div>';
    if (v.u) h += '<div class="wd-u">' + escapeHtml(v.u) + '</div>';
    var saved = S.vocab.some(function (x) { return x.id === 'w:' + w; });
    h += '<div class="wd-act"><button class="tbtn wd-save" data-wd-save="' + escapeHtml(w) + '">' +
      (saved ? '✓ 已收藏' : '★ 收藏这个词') + '</button>';
    if (AI.enabled()) {
      h += '<button class="tbtn wd-ai" data-wd-ai="' + escapeHtml(w) + '">讓 AI 講這個詞</button>';
      h += '<button class="tbtn wd-ai" data-wd-copy="' + escapeHtml(w) + '">複製提問（講這個詞）</button>';
    } else {
      h += '<button class="tbtn wd-ai" data-wd-ai="' + escapeHtml(w) + '">複製提問（講這個詞）</button>';
    }
    h += '</div>';
    var ex = (v.ex && v.ex.length) ? v.ex : (lx.ex || []);
    if (ex.length) {
      h += '<div class="wd-sec">本剧例句</div>';
      for (var i = 0; i < Math.min(ex.length, 3); i++) {
        var e2 = ex[i];
        h += '<div class="pop-ex" data-ep="' + e2.ep + '" data-i="' + e2.i + '">' +
          '<div class="ex-ja">' + escapeHtml(e2.ja) + '</div>' +
          (e2.zh ? '<div class="ex-zh">' + escapeHtml(e2.zh) + '</div>' : '') +
          '<div class="ex-go">EP' + e2.ep + ' · 跳过去 ›</div></div>';
      }
    }
    box.innerHTML = h;
    box.classList.remove('hidden');
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function codeOfPos(p) {
    var SY = window.__SYNTAX__ || { p: {} };
    for (var k in SY.p) { if (SY.p[k] === p) return k; }
    return '';
  }

  /* ================================================================
   * AI 讲解
   *   两种模式，**共用同一份上下文**（aiBuildBody）：
   *     ① 复制模式（默认）：CFG.AI_ENDPOINT 为空 → 「複製提問」
   *        把 SYSTEM_PROMPT + 上下文拼成纯文本写进剪贴板，
   *        粘到 MiniMax（App / 网页版 / 微信）里，答案直接出来。
   *     ② 真接口模式：CFG.AI_ENDPOINT 有值 → POST 契约给中转
   *   - 契约：POST {AI_ENDPOINT}，body = {scene,title,episode,ja,zh,
   *            prev[],next[],word,refs[]}，期望 {ok:true,text:"..."}
   *   - 🔴 key/token 只在中转后端，前端永远看不到
   * ================================================================ */

  /** 角色提示词 —— 复制模式下原样拼进剪贴板；真接口模式下由中转使用同一份 */
  var AI_SYSTEM_PROMPT =
    '【角色】\n' +
    '你是日语老师，给中文母语的学习者讲日语。讲解用中文；日语语法术语第一次出现时写成「中文（日本語用語）」双写。\n' +
    '\n' +
    '【任务】\n' +
    '解析用户给你的一句日语台词（可能附带上下文）。严格按下面固定结构输出：不要寒暄、不要复述题目、不要用 emoji、不要自由发挥。\n' +
    '\n' +
    '【输出结构（顺序固定）】\n' +
    '1. 原文：日语文本，汉字后用括号注假名（例：元気（げんき））\n' +
    '2. 中文翻译\n' +
    '3. 句子结构剖析\n' +
    '\n' +
    '【句子结构剖析的固定写法】\n' +
    '3.1 整体大框架：只写一行，成分用字母标记（从 A 开始按出现顺序），被包住的部分用【】括起来。\n' +
    '    格式：A は 【B】 言っています\n' +
    '3.2 成分展开：每个成分写两行，格式固定\n' +
    '    A：<该成分原文，逐字取自原句>\n' +
    '    A 说明：<它是什么 + 词性/语法功能 + 在这句里跟谁是什么关系>\n' +
    '    （可选）这个成分在本剧有对应的真实台词时，紧跟着一行写：\n' +
    '    例句：<逐字取自上下文的本剧台词原文>\n' +
    '3.3 成分内部骨架：只有当某个成分内部还有结构（修饰成分、并列、介词短语、补助动词等）时才写\n' +
    '    B 内部骨架：X に Y ように\n' +
    '    X = <原文>\n' +
    '    X 说明：<说明>\n' +
    '    に说明：<助词单独解释：接在谁后面、提示什么、全句有几处这种>\n' +
    '    Y 说明：<说明>\n' +
    '    ように说明：<说明>\n' +
    '3.4 主句谓语：谓语如果已经是框架里的某个字母位，就不用单独写；否则单独写\n' +
    '    主句谓语：<原文>\n' +
    '    说明：<说明>\n' +
    '\n' +
    '【硬性规则】\n' +
    '- 成分原文必须逐字取自原句，不许改写、不许换成罗马音\n' +
    '- 内部骨架里的助词（如 に / は / を / ので）要单独拎出来解释，一行一个\n' +
    '- 说明要讲「这个成分在这句里的具体作用」，不要只抄词典释义；每条 1~2 句\n' +
    '- 框架里没有的成分不要硬凑；句子简单就少拆几层\n' +
    '- 例句是**可选**的：只在能拿到能体现该要点的**本剧真实台词**时才写；拿不到就**整行不写**\n' +
    '      - 一旦要写，必须逐字取自上下文（refs / prev / next 里原样搬运），一个字都别改\n' +
    '      - 不许编造、不许改写、不许把当前句重复当例句\n' +
    '      - 引用例句时只写日文原文，不要加括号注音、不要加下划线、不要加英文残留\n' +
    '- 不需要罗马音\n' +
    '- 不确定的地方直说不确定，不要编造语法术语或读音\n' +
    '- 假名标注只作参考，App 端有标准注音时以标准注音为准';

  var AI = {
    enabled: function () { return !!String(CFG.AI_ENDPOINT || '').trim(); },
    state: '',          // 最近一次状态（测试用）
    lastBody: null,     // 最近一次请求体（测试用）
    lastText: '',       // 最近一次拼出来的提问文本（测试用）
    lastReq: null,
    token: function () { return String(CFG.AI_TOKEN || '').trim(); }
  };
  try { window.__AI = AI; } catch (e) { }

  /* 超时：前端必须 **≥ 中转上游超时 + 余量**，否则前端先断、中转还在跑（白烧额度）。
   * 中转上游 60 秒 / CloudBase 函数上限 90 秒 → 前端 75 秒。
   * ⚠️ 改这个数时记得同步中转侧的 timeout。 */
  var AI_TIMEOUT = 75000;

  /* 中转的错误码 → 前端显示（跟中转约定好的，别改） */
  var AI_ERR = {
    not_configured: 'AI 未配置',
    unauthorized: 'AI 未配置',
    too_many: '今天用得有点多',
    quota_exceeded: '額度用完了',
    timeout: '網絡失敗，請稍後重試',
    upstream_params: 'AI 暫時不可用',
    upstream_error: 'AI 暫時不可用',
    upstream_auth: 'AI 暫時不可用'
  };
  function aiErrText(code) {
    return AI_ERR[code] || 'AI 暫時不可用';
  }

  /** 按契约拼请求体 */
  function aiBuildBody(scene, c, idx, word) {
    var subs = S.subs || [];
    var prev = [], next = [], refs = [];
    for (var k = Math.max(0, idx - 2); k < idx; k++) {
      if (subs[k]) prev.push({ ja: subs[k].ja || '', zh: subs[k].zh || '' });
    }
    for (var k2 = idx + 1; k2 < Math.min(subs.length, idx + 3); k2++) {
      if (subs[k2]) next.push({ ja: subs[k2].ja || '', zh: subs[k2].zh || '' });
    }
    // 本剧真实台词参考：让模型的例句用真台词，不是让它分析这些台词
    for (var r = 0; r < subs.length && refs.length < 4; r++) {
      if (r === idx) continue;
      if (!subs[r] || !subs[r].ja || !subs[r].zh) continue;
      refs.push({ ja: subs[r].ja, zh: subs[r].zh });
    }
    return {
      scene: scene,                                  // explain_sentence | explain_word
      title: String(CFG.AI_TITLE || 'NHKやさしいことばニュース'),
      episode: '第 ' + (S.ep ? S.ep.no : '?') + ' 期（' +
        (S.ver === 'web' ? '普通版' : '易懂版') + '）',
      ja: c ? (c.ja || '') : '',
      zh: c ? (c.zh || '') : '',
      prev: prev, next: next,
      word: word || '',
      refs: refs
    };
  }

  /** 🔴 请求体 → 纯文本提问
   *  字段跟契约一一对应（scene/title/episode/ja/zh/prev/next/word/refs），
   *  以后接真接口时这套上下文可以直接复用，不用重写。
   *  ⭐ 格式必须跟中转的 buildUserMessage() **逐字一致** ——
   *    这样「复制提问」和「真接口」发给模型的内容完全相同 → 输出也完全相同。 */
  function aiBuildPrompt(body) {
    var L = [];
    L.push(AI_SYSTEM_PROMPT);
    L.push('');
    L.push('---');
    L.push('');
    L.push('剧名：' + (body.title || '（未指定）'));
    if (body.episode) L.push('集数：' + body.episode);
    L.push('');

    var scene = body.scene === 'explain_word' ? 'explain_word' : 'explain_sentence';
    if (scene === 'explain_word' && body.word) {
      L.push('★ 本次要重点讲解的词：' + body.word);
      L.push('（仍然按五段结构输出，但「逐词拆解」和「语法说明」要围绕这个词展开）');
      L.push('');
    }

    var prevs = body.prev || [], nexts = body.next || [], refs = body.refs || [];
    L.push('【上下文】');
    if (prevs.length) {
      L.push('--- 上一句 ---');
      for (var i = 0; i < prevs.length; i++) {
        L.push((prevs[i].ja || '') + (prevs[i].zh ? '　' + prevs[i].zh : ''));
      }
    }
    L.push('--- 当前句（要解析的）---');
    L.push((body.ja || '') + (body.zh ? '　' + body.zh : ''));
    if (nexts.length) {
      L.push('--- 下一句 ---');
      for (var j = 0; j < nexts.length; j++) {
        L.push((nexts[j].ja || '') + (nexts[j].zh ? '　' + nexts[j].zh : ''));
      }
    }
    L.push('');

    if (refs.length) {
      L.push('【本剧台词参考（用来造例句，优先用这里的原句，不要自己编）】');
      for (var r = 0; r < refs.length; r++) {
        L.push('- ' + (refs[r].ja || '') + (refs[r].zh ? '（' + refs[r].zh + '）' : ''));
      }
      L.push('');
    }
    return L.join('\n');
  }
  try { window.__aiBuildPrompt = aiBuildPrompt; } catch (e) { }

  /* 把模型的 markdown 切进五个小节
   * 🔴 标题判定必须「整行相等」，不能用「包含关键词」——
   *    实测 M3 的逐词拆解用 markdown 表格，表头是
   *    「| 日语 | 中文意思 | 词性／语法功能 |」→ 含「语法」二字
   *    → 旧的包含判定会把它当成「② 语法说明」标题 → 逐词拆解整段丢失！ */
  var AI_SECS = [
    { k: 'orig', t: '原文', re: /原文/ },
    { k: 'zh', t: '中文翻译', re: /中文翻译|中文译|翻译/ },
    { k: 'word', t: '逐词拆解', re: /逐词拆解|逐词/ },
    { k: 'gram', t: '语法说明', re: /语法说明|语法/ },
    { k: 'syn', t: '整句结构框架', re: /整句结构框架|整句结构|结构框架|结构/ }
  ];
  /** 标题行的**全部**合法写法（去空格、去尾部冒号后必须完全相等） */
  var AI_SEC_NAMES = {
    '原文': 'orig', '中文翻译': 'zh', '中文': 'zh', '翻译': 'zh',
    /* 🔴 2026-10-22：新答题模式 —— 逐词拆解/语法说明/整句结构框架 三段
     *   合并成一段「句子结构剖析」（整体大框架 → 成分展开 → 内部骨架）。
     *   旧的段名仍然保留 → 万一模型给旧格式也能正确分流，不至于丢内容。 */
    '句子结构剖析': 'syn', '结构剖析': 'syn', '句子结构': 'syn',
    '逐词拆解': 'word', '逐词': 'word',
    '语法说明': 'gram', '语法': 'gram',
    /* 旧格式的「整句结构框架」形态不同（树状缩进而非标签块）→ 单独归到 synOld，
     * 渲染时用 aiMd（带树状竖线），别走 aiStruct。 */
    '整句结构框架': 'synOld', '整句结构': 'synOld', '结构框架': 'synOld', '结构': 'synOld'
  };
  function aiSplit(text) {
    var out = { orig: '', zh: '', word: '', gram: '', syn: '', synOld: '' };
    var lines = String(text || '').split(/\r?\n/);
    var cur = null;
    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i];
      var trimmed = raw.replace(/^\s+|\s+$/g, '');
      // 表格行不是标题
      if (trimmed.charAt(0) === '|') { if (cur) out[cur] += (out[cur] ? '\n' : '') + raw; continue; }
      var t2 = raw.replace(/^[#*\s>]+/, '');   // 去掉 markdown 记号
      var hit = null, inline = '';
      // ① 行内标题：「1. 原文：内容」/「3. 逐词拆解：」
      var mA = t2.match(/^(?:\d{1,2}\s*[.、)．]\s*)?([^\s：:]{2,12})\s*[：:]\s*(.*)$/);
      if (mA && AI_SEC_NAMES[mA[1]]) { hit = AI_SEC_NAMES[mA[1]]; inline = mA[2]; }
      // ② 独立标题行：「## 1. 原文」/「原文」
      if (!hit) {
        var mB = t2.replace(/^(?:\d{1,2}\s*[.、)．]\s*)?/, '')
          .replace(/[【】\[\]]/g, '').replace(/[：:]\s*$/, '').replace(/^\s+|\s+$/g, '');
        if (AI_SEC_NAMES[mB]) hit = AI_SEC_NAMES[mB];
      }
      if (hit) {
        cur = hit;
        if (inline) out[cur] += inline;      // 行内内容归入本段
        continue;
      }
      if (cur) out[cur] += (out[cur] ? '\n' : '') + raw;
    }
    return out;
  }

  /** 🔴 纠正模型标的假名
   *  实测：模型会把「女の子」的「子」标成「このこ」（正确是「こ」）→ **模型标的假名不可信**。
   *  规则：**以 App 查表（reading.js）为准**，模型输出的只作 fallback（查不到才保留）。
   *  这样同一处永远只出现一套假名，不会跟「原文」那行的 ruby 打架。 */
  function aiFixReading(text) {
    return String(text || '').replace(
      // ⚠️ 词里可能夹着假名（「女の子」），所以词部分要允许假名，
      //    否则只会匹配到「子（このこ）」，整词查不到表。
      // 括号里必须是**纯假名**才不会误伤内容里的括号（如「（最高気温31.2度）」）
      /([぀-ゟ゠-ヿ一-鿿々〆ヵヶ]+)\s*[（(]\s*([ぁ-んァ-ヶー・]+)\s*[）)]/g,
      function (all, kanji, kana) {
        var r = readOf(kanji);
        return r ? (kanji + '（' + r + '）') : all;   // 查不到 → 保留模型的
      });
  }

  /** markdown 表格 → 墨水屏友好的行式
   *  实测 M3 的「逐词拆解」用的是表格：
   *    | 日语 | 中文意思 | 词性／语法功能 |
   *    |---|---|---|
   *    | 宮崎県 | 宫崎县 | 地名 |
   *  窄屏 + 黑白墨水屏上表格几乎没法看 → 转成
   *    宮崎県 → 宫崎县（地名）
   *  规则：第 1 列=词，第 2 列=释义，其余列用「·」接在括号里。 */
  function aiTable(rows) {
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var line = rows[i].replace(/^\s+|\s+$/g, '');
      if (!line) continue;
      var cells = line.replace(/^\|/, '').replace(/\|$/, '').split('|');
      // 分隔行 |---|---|
      if (cells.length && /^[\s\-:]+$/.test(cells[0])) continue;
      var c = [];
      for (var k = 0; k < cells.length; k++) c.push(cells[k].replace(/^\s+|\s+$/g, ''));
      if (!c[0]) continue;
      if (i === 0 && rows.length > 1) {
        // 第一行当表头 → 跳过（除非只有一行）
        var second = rows[1] || '';
        if (/^\s*\|?[\s\-:|]+\|/.test(second) || /^[\s\-:|]+\|/.test(second)) continue;
      }
      if (c.length === 1) { out.push(c[0]); continue; }
      var head = c[0], body = c[1], rest = c.slice(2).filter(function (x) { return x; });
      var line2 = head + ' → ' + body;
      if (rest.length) line2 += '（' + rest.join(' · ') + '）';
      out.push(line2);
    }
    return out;
  }

  /** 极简 markdown → HTML
   *  处理：**粗体**、行首 - / 数字、【重点】、``` 围栏的代码块、| 表格 |、纠正模型假名 */
  /* ══════════════════════════════════════════════════════════════
   * 句子结构剖析的专用渲染（2026-10-22 新答题模式）
   * 模型输出形如：
   *   A は 【B】 言っています            ← 整体大框架（要突出）
   *   A：気象庁は                        ← 成分原文
   *   A 说明：気象庁是话题，は 是话题标记助词…  ← 成分说明
   *   B 内部骨架：X に Y ように            ← 内部骨架
   *   X = 雨や風の災害
   *   X 说明：…
   *   に说明：…
   *   ように说明：…
   *   主句谓语：言っています
   *   说明：…
   * ══════════════════════════════════════════════════════════════ */
  /* 🔴 模型输出的垃圾清洗（2026-10-22，中转侧同款）
   *  ③-a 孤儿括号：模型吐的 `- 】：原句中的…`（孤立的右括号+冒号）
   *  ③-b 括号里是崩掉的假名：`緑茶（りょ在国际这里指"绿茶"）` → 整组括号剥掉
   *  保留： （げんき）（绿茶）（31.2度）
   *  剥除： （りょ在国际…）（りょ-cha tea）（English）← prompt 本来就说不用罗马音 */
  var ORPHAN_HEAD_RE = /^((\s*)(?:[-*\u2022]\s+)?)((?:[\u3011\]\)\uff09]|\u3010\u3011|\[\]|\u3014\u3015)+)\s*[:\uff1a]\s*/;
  function cleanOrphan(text) {
    return String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n').map(function (line) {
      var m = line.match(ORPHAN_HEAD_RE);
      if (!m) return line;
      return m[1] + m[2] + line.slice(m[0].length);
    }).join('\n');
  }
  var WORD_PAREN_RE = /([\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3005\u3006\u30F5\u30F6]+)\s*[\uff08(]([^\uff08()\uff09]{1,60})[\uff09)]/g;
  var GARBAGE_IN_PAREN_RE = /[A-Za-z"'`]/;
  function cleanBadParen(text) {
    return String(text == null ? '' : text).replace(WORD_PAREN_RE, function (m, w, inner) {
      if (GARBAGE_IN_PAREN_RE.test(inner)) return w;   /* 崩了 → 剥掉整组括号 */
      return m;
    });
  }

  /* ══════════════════════════════════════════════════════════════
   * 结构段的额外清洗（2026-10-22，与 peer 的 client v1.5 对齐）
   * 真接口实测：模型会先吐一版错的、再吐一版对的：
   *   C 内部骨架：X で Y ickle?                    ← 垃圾行
   *   C 内部骨架：今までで いちばん 遅い 真夏日 になりました   ← 紧跟着的正确行
   * 规则 A：内部骨架行正文里出现连续 ≥2 个 ASCII 字母 → 整行丢
   * 规则 B：同 tag 的内部骨架行只保留**最后一次**出现
   * ══════════════════════════════════════════════════════════════ */
  var STRUCT_TAG_RE = /^([A-Z]{1,3})\s*内部骨架\s*[：:](.*)$/;
  function stripStructNo(line) {
    return String(line == null ? '' : line)
      .replace(/^\s+|\s+$/g, '')
      .replace(/^\s*\d{1,2}\.\d{1,2}\s*/, '');
  }
  function cleanStructLines(lines) {
    var arr = (lines || []).map(function (l) { return l == null ? '' : String(l); });
    var i, b, m;
    for (i = 0; i < arr.length; i++) {                 /* 规则 A：英文垃圾行整行丢 */
      b = stripStructNo(arr[i]);
      m = b.match(STRUCT_TAG_RE);
      if (m && /[A-Za-z]{2,}/.test(m[2])) arr[i] = '';
    }
    var last = {};
    for (i = 0; i < arr.length; i++) {                 /* 规则 B：同 tag 只留最后一次 */
      b = stripStructNo(arr[i]);
      m = b.match(STRUCT_TAG_RE);
      if (m) last[m[1]] = i;
    }
    for (i = 0; i < arr.length; i++) {
      b = stripStructNo(arr[i]);
      m = b.match(STRUCT_TAG_RE);
      if (m && last[m[1]] !== i) arr[i] = '';
    }
    return arr;
  }

  function aiStruct(text) {
    var raw = String(text || '').trim();
    if (!raw) return '';
    raw = cleanBadParen(cleanOrphan(raw));
    var lines = cleanStructLines(raw.split(/\r?\n/));
    var out = '';
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i].replace(/^\s+|\s+$/g, '');
      if (!ln) { continue; }
      /* 🔴 剥掉小节编号：「3.1 整体大框架：…」「3.4 主句谓语：…」
       *   不剥的话这些行匹配不上任何规则 → 会掉进兜底，主句谓语块出不来。 */
      var body = ln.replace(/^\d{1,2}\.\d{1,2}\s*/, '');
      var esc = escapeHtml(body);
      // ① 整体大框架：含【】且不是「说明」行 → 高亮框
      if (/【[^】]+】/.test(body) && !/说明[：:]/.test(body)) {
        out += '<div class="ai-frame">' + esc + '</div>';
        continue;
      }
      // ①-b 小节标题行：「成分展开：」「成分内部骨架：」→ 小标题样式
      /* ⚠️ 这些是 3.x 的**小节名**，不是成分 → 不能当成分块渲染（否则多出空块）。 */
      var mSub = body.match(/^(成分展开|成分内部骨架|内部骨架|成分说明|骨架)\s*[：:]\s*(.*)$/);
      if (mSub) {
        out += '<div class="ai-sub">' + escapeHtml(mSub[1]) +
          (mSub[2] ? '：' + escapeHtml(mSub[2]) : '') + '</div>';
        continue;
      }
      // ② 成分标题行：「A：」「B 内部骨架：」→ 标签样式
      /* ⚠️ 前缀**不限于 A-Z** —— 模型可能写汉字/假名成分名（如「毎朝：」） */
      var m1 = body.match(/^([^：:]{1,10}?)\s*(内部骨架)?\s*[：:](.*)$/);
      if (m1 && !/^说明$/.test(m1[1].trim()) &&
          !/^(成分展开|成分内部骨架|内部骨架)$/.test(m1[1].trim())) {
        var tag = m1[1].trim() + (m1[2] ? ' 内部骨架' : '');
        out += '<div class="ai-comp"><span class="ai-tag">' + escapeHtml(tag) + '</span>' +
          (m1[3] ? escapeHtml(m1[3].replace(/^\s+/, '')) : '') + '</div>';
        continue;
      }
      // ③ 「X = 原文」→ 成分原文行
      /* ⚠️ 前缀**不限于 A-Z** —— 实测模型写「毎朝 = 毎朝」（汉字成分名）。 */
      var m2 = body.match(/^([^=：:]{1,10}?)\s*=\s*(.+)$/);
      if (m2) {
        out += '<div class="ai-eq"><span class="ai-tag">' + escapeHtml(m2[1].trim()) + '</span>' +
          '<span class="ai-eq-sign">=</span>' + escapeHtml(m2[2]) + '</div>';
        continue;
      }
      // ④ 「X 说明：…」→ 说明行（缩进 + 灰字）
      /* ⚠️ 前缀**不限于大写字母** —— 模型会写「に说明：」「ように说明：」
       *   （助词本身是假名），所以这里用「任意短前缀 + 说明」来匹配。 */
      var m3 = body.match(/^([^：:]{1,12}?)\s*说明[：:]\s*(.*)$/);
      if (m3) {
        var pre = m3[1].trim();
        out += '<div class="ai-note">' +
          (pre ? '<span class="ai-tag">' + escapeHtml(pre) + '</span>' : '') +
          '<span class="ai-note-l">说明</span>' + escapeHtml(m3[2]) + '</div>';
        continue;
      }
      // ⑤ 「主句谓语：…」/「说明：…」→ 谓语块
      var m4 = body.match(/^(主句谓语|谓语)\s*[：:]\s*(.*)$/);
      if (m4) {
        out += '<div class="ai-comp ai-predicate"><span class="ai-tag">' + escapeHtml(m4[1]) +
          '</span>' + escapeHtml(m4[2]) + '</div>';
        continue;
      }
      if (/^说明[：:]\s*(.*)$/.test(body)) {
        out += '<div class="ai-note"><span class="ai-note-l">说明</span>' +
          escapeHtml(body.replace(/^说明[：:]\s*/, '')) + '</div>';
        continue;
      }
      // ⑥ 兜底：普通行
      out += '<div class="ai-line">' + esc + '</div>';
    }
    return out;
  }

  function aiMd(s) {
    var raw = String(s || '').trim();
    if (!raw) return '';
    // ── 代码块（模型整句结构框架会用 ```text 围栏）──
    var blocks = [];
    raw = raw.replace(/```[a-zA-Z0-9]*\n([\s\S]*?)```/g, function (m, code) {
      blocks.push(code.replace(/\n+$/, ''));
      return '\u0000B' + (blocks.length - 1) + '\u0000';
    });
    // ── 表格 → 行式 ──
    raw = raw.replace(/(?:^\|.*\|$\n?)+/gm, function (m) {
      var rows = m.split('\n').filter(function (x) { return x.trim(); });
      var conv = aiTable(rows);
      return conv.length ? (conv.join('\n') + '\n') : '';
    });
    var t = escapeHtml(raw);
    t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    t = t.replace(/【重点】/g, '<span class="ai-key">重点</span>');
    t = t.replace(/^(\s*)[-*・]\s+/gm, '$1<span class="ai-bullet">・</span>');
    t = t.replace(/^(\s*)(\d+[.、)．])\s+/gm, '$1<span class="ai-num">$2</span> ');
    // 占位符还原成真正的代码块
    t = t.replace(/\u0000B(\d+)\u0000/g, function (m, i) {
      var code = blocks[+i] || '';
      var lines = code.split('\n').map(function (ln) {
        /* 树状缩进（│ ├ └ ─ 开头）→ 去掉树字符，改用 border-left 画竖线。
         * 原因：竖线是行内字符，窄屏折行后竖线不再贴最左边 → 层级感断掉；
         * 画成边框后，**折行时层级线一直在**。 */
        if (/^[\s\u2502\u251c\u2514]/.test(ln)) {
          var depth = (ln.match(/[\u2502]/g) || []).length;
          var rest = ln.replace(/^[\s\u2502\u251c\u2514\u2500]*/, '');
          return '<span class="ai-code-l ai-tree" style="padding-left:' +
            (depth * 12 + 12) + 'px">' + escapeHtml(rest) + '</span>';
        }
        return '<span class="ai-code-l">' + escapeHtml(ln).replace(/ /g, '&nbsp;') + '</span>';
      }).join('');
      return '<pre class="ai-code">' + lines + '</pre>';
    });
    // 🔴 最后纠正模型标的假名（在 escapeHtml 之后做，替换的是纯文本）
    t = aiFixReading(t);
    /* 逐行渲染：树状缩进行（│ ├ └ 开头）要用 border-left 画竖线，
     * 否则窄屏折行后竖线不贴最左边 → 层级感断掉。
     * 注意：真实输出的树形缩进**经常不在 ``` 代码块里**，
     * 而是在「整句结构框架」这类普通段落里 → 所以这里也要处理。 */
    var out = '';
    var ls = t.split('\n');
    for (var li = 0; li < ls.length; li++) {
      var ln = ls[li];
      if (!ln) { continue; }
      /* 🔴 树状判定必须「含树字符」，不能只看缩进 ——
       *    否则 markdown 列表的续行（缩进 2 空格）会被误判成树。 */
      if (/[\u2502\u251c\u2514]/.test(ln)) {
        var dep = (ln.match(/[\u2502]/g) || []).length;
        var rest2 = ln.replace(/^[\s\u2502\u251c\u2514\u2500]*/, '');
        out += '<div class="ai-line ai-tree" style="padding-left:' +
          (dep * 12 + 12) + 'px">' + rest2 + '</div>';
      } else {
        // 非树状行：行首空格转 &nbsp;（HTML 里空格会折叠，缩进会丢）
        out += '<div class="ai-line">' +
          ln.replace(/^(\s+)/, function (sp) { return sp.replace(/ /g, '&nbsp;'); }) +
          '</div>';
      }
    }
    return out;
  }

  /** 渲染 AI 区域。原文/中文翻译复用 App 自己的（不显示模型输出的括号假名 → 不出现两套注音） */
  function aiRender(text, c) {
    var p = aiSplit(text);
    var h = '';
    // 原文 → App 的全注音版
    h += '<div class="ai-sec"><button class="ai-h" data-ai-fold="1">原文</button>' +
      '<div class="ai-b"><div class="sp-orig">' + (c ? jaRich(c, 0, (c.ja || '').length) : '') + '</div></div></div>';
    // 中文翻译 → App 自己的
    h += '<div class="ai-sec"><button class="ai-h" data-ai-fold="1">中文翻译</button>' +
      '<div class="ai-b"><div class="sp-zh">' + escapeHtml(c && c.zh ? c.zh : '') + '</div></div></div>';
    /* 🔴 2026-10-22 新答题模式：
     *   「逐词拆解 / 语法说明 / 整句结构框架」三段 → 合并成「句子结构剖析」一段
     *   （逐词拆解本地词库已经有了，AI 重复做一遍只是浪费额度；
     *     AI 唯一不可替代的价值就是把句子**结构**讲透）。
     *   模型如果仍给旧格式（syn 为空但 word/gram 有内容）→ 回退显示旧三段，不丢内容。 */
    if (p.syn) {
      h += '<div class="ai-sec"><button class="ai-h" data-ai-fold="1">句子结构剖析</button>' +
        '<div class="ai-b">' + aiStruct(p.syn) + '</div></div>';
    } else {
      h += '<div class="ai-sec"><button class="ai-h" data-ai-fold="1">逐词拆解</button>' +
        '<div class="ai-b">' + (aiMd(p.word) || '<div class="ai-line ai-dim">（本次没有这一段）</div>') + '</div></div>';
      h += '<div class="ai-sec"><button class="ai-h" data-ai-fold="1">语法说明</button>' +
        '<div class="ai-b">' + (aiMd(p.gram) || '<div class="ai-line ai-dim">（本次没有这一段）</div>') + '</div></div>';
      h += '<div class="ai-sec"><button class="ai-h" data-ai-fold="1">整句结构框架</button>' +
        '<div class="ai-b">' + (aiMd(p.synOld) || '<div class="ai-line ai-dim">（本次没有这一段）</div>') + '</div></div>';
    }
    return h;
  }

  /** 老 WebView 兜底：textarea + execCommand('copy') */
  function aiLegacyCopy(text, cb) {
    var ta = null;
    try {
      ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      ta.style.top = '0';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try { ta.setSelectionRange(0, text.length); } catch (e2) { }
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      cb(!!ok);
    } catch (e) {
      if (ta && ta.parentNode) { try { ta.parentNode.removeChild(ta); } catch (e3) { } }
      cb(false);
    }
  }

  /** 剪贴板不可用时的兜底：给一个可全选复制的文本框 */
  function aiShowCopyBox(text, boxEl) {
    var box = boxEl || $('#aiBox');
    if (!box) return;
    box.innerHTML = '<div class="ai-line ai-err">剪貼板不可用，請長按下面的文字複製：</div>' +
      '<textarea class="ai-copy-box" id="aiCopyBox" readonly spellcheck="false"></textarea>';
    var ta = $('#aiCopyBox');
    if (ta) {
      ta.value = text;
      try { ta.focus(); ta.select(); ta.setSelectionRange(0, text.length); } catch (e) { }
    }
  }

  /** 写剪贴板。navigator.clipboard 优先，老 WebView 走 execCommand */
  function aiCopy(text, boxEl) {
    AI.lastText = text;
    var ok = function (done) {
      if (done) {
        AI.state = 'copied';
        if (boxEl) {
          boxEl.innerHTML = '<div class="ai-line ai-ok">已複製到剪貼板 ✓</div>' +
            '<div class="ai-line ai-dim">把它粘到 MiniMax（App / 網頁版 / 微信）裡，答案就出來了。' +
            '問的是同一句、同一份提示詞，輸出跟真接口完全一樣。</div>';
        }
        // 墨水屏刷新慢，toast 停久一点
        toast('已複製，粘貼到 MiniMax 即可', 2800);
      } else {
        AI.state = 'copy_fail';
        aiShowCopyBox(text, boxEl);
        toast('剪貼板不可用，請手動複製', 2800);
      }
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () { ok(true); },
          function () { aiLegacyCopy(text, ok); });
        return;
      }
    } catch (e) { /* 落到兜底 */ }
    aiLegacyCopy(text, ok);
  }

  /** 额度提示：GET {AI_ENDPOINT}/quota
   *  查不到 / 接口挂了 → **整行不显示**（不能因为查额度失败影响主功能） */
  function aiQuotaText(q) {
    /* 文案以「5 小时窗口」为主 —— 那才是用户体感最相关的（用完几小时就回满）；
     * 周窗口数字（41%）放进去反而让人紧张，只在明显偏低时才补一句。 */
    var parts = [];
    var ip = (typeof q.interval_percent === 'number') ? q.interval_percent : null;
    var wp = (typeof q.weekly_percent === 'number') ? q.weekly_percent : null;
    function humanReset(ms) {
      if (!(ms > 0)) return '';
      var h = ms / 3600000;
      return h >= 24 ? (Math.round(h / 24 * 10) / 10) + ' 天后回满'
                     : (Math.round(h * 10) / 10) + ' 小时后回满';
    }
    if (ip !== null) {
      parts.push(ip >= 50 ? 'AI 额度充足' : (ip >= 20 ? 'AI 额度偏紧' : 'AI 额度紧张'));
      var r1 = humanReset(q.interval_reset_in_ms);
      if (r1) parts.push(r1);
    }
    if (wp !== null && wp < 60) parts.push('本周剩 ' + Math.round(wp) + '%');
    return parts.join(' · ');
  }
  function aiShowQuota() {
    var el = $('#aiQuota');
    if (!el) return;
    var ep = String(CFG.AI_ENDPOINT || '').trim();
    if (!ep) return;
    try {
      var x = new XMLHttpRequest();
      x.open('GET', ep.replace(/\/+$/, '') + '/quota', true);
      x.timeout = 8000;
      x.onload = function () {
        if (x.status < 200 || x.status >= 300) return;
        var j = null;
        try { j = JSON.parse(x.responseText); } catch (e) { }
        if (!j || !j.ok || !j.quota) return;
        var t = aiQuotaText(j.quota);
        if (!t) return;
        el.textContent = t;
        el.classList.remove('hidden');
      };
      x.onerror = function () { };
      x.ontimeout = function () { };
      x.send();
    } catch (e) { }
  }

  /** 统一入口：讲整句 / 讲某个词
   *  - CFG.AI_ENDPOINT 为空 → **复制模式**（把问题拼好写进剪贴板）
   *  - CFG.AI_ENDPOINT 有值 → **真接口模式**（POST 给中转） */
  function aiAsk(scene, c, idx, word, boxEl) {
    var body = aiBuildBody(scene, c, idx, word);
    AI.lastBody = body;
    if (!AI.enabled()) {
      // ── 复制模式：立刻能用，不依赖任何后端 ──
      var text = aiBuildPrompt(body);
      AI.state = 'copying';
      if (boxEl) boxEl.innerHTML = '<div class="ai-line ai-loading">正在複製…</div>';
      aiCopy(text, boxEl);
      return;
    }
    // ── 真接口模式 ──
    AI.state = 'loading';
    if (boxEl) {
      boxEl.innerHTML = '<div class="ai-line ai-loading">思考中…</div>';
    }
    var done = false;
    var t0 = Date.now();
    var timer = setTimeout(function () {
      if (done) return;
      done = true; clearInterval(tick);
      AI.state = 'timeout';
      if (boxEl) boxEl.innerHTML = '<div class="ai-line ai-err">網絡失敗，請稍後重試</div>';
    }, AI_TIMEOUT);
    /* 每秒刷新「已用 N 秒」
     * 实测 M3 要 15~16 秒 —— 墨水屏刷新慢，用户干等会以为卡死了。
     * ⚠️ 绝不做流式（逐字刷新）→ 墨水屏会疯狂重绘、闪烁、耗电。 */
    var tick = setInterval(function () {
      if (done || !boxEl) return;
      var sec = Math.round((Date.now() - t0) / 1000);
      boxEl.innerHTML = '<div class="ai-line ai-loading">思考中… 已用 ' + sec + ' 秒</div>';
    }, 1000);
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', String(CFG.AI_ENDPOINT).trim(), true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      if (AI.token()) xhr.setRequestHeader('Authorization', 'Bearer ' + AI.token());
      xhr.timeout = AI_TIMEOUT;
      xhr.onload = function () {
        if (done) return;
        done = true; clearTimeout(timer); clearInterval(tick);
        var j = null;
        try { j = JSON.parse(xhr.responseText); } catch (e) { }
        // 🔴 状态码不是 2xx 时也要看 body 里的 error 字段
        //    （中转可能用 401/402/429 带着 error=unauthorized / quota_exceeded / too_many）
        if (xhr.status < 200 || xhr.status >= 300) {
          AI.state = 'error';
          if (boxEl) {
            boxEl.innerHTML = '<div class="ai-line ai-err">' +
              aiErrText((j && j.error) ? String(j.error) : '') + '</div>';
          }
          return;
        }
        if (!j || j.ok !== true || !j.text) {
          var code = (j && j.error) ? String(j.error) : '';
          AI.state = code || 'error';
          if (boxEl) {
            boxEl.innerHTML = '<div class="ai-line ai-err">' + aiErrText(code) + '</div>';
          }
          return;
        }
        AI.state = 'ok';
        if (boxEl) boxEl.innerHTML = aiRender(j.text, c);
      };
      xhr.onerror = function () {
        if (done) return;
        done = true; clearTimeout(timer); clearInterval(tick);
        AI.state = 'error';
        if (boxEl) boxEl.innerHTML = '<div class="ai-line ai-err">AI 暫時不可用</div>';
      };
      xhr.ontimeout = function () {
        if (done) return;
        done = true; clearTimeout(timer); clearInterval(tick);
        AI.state = 'timeout';
        if (boxEl) boxEl.innerHTML = '<div class="ai-line ai-err">網絡失敗，請稍後重試</div>';
      };
      xhr.send(JSON.stringify(body));
    } catch (e) {
      if (done) return;
      done = true; clearTimeout(timer);
      AI.state = 'error';
      if (boxEl) boxEl.innerHTML = '<div class="ai-line ai-err">AI 暫時不可用</div>';
    }
  }

  /* ---------- 弹窗 ---------- */
  function popShow(html) {
    var box = $('#popBody');
    if (!box) return;
    box.innerHTML = html;
    $('#pop').classList.remove('hidden');
  }
  function popHide() { $('#pop').classList.add('hidden'); }
  var popEl = $('#pop');
  if (popEl) {
    popEl.addEventListener('click', function (e) {
      if (e.target === popEl || e.target.closest('#popClose')) popHide();
    });
  }

  function exBlock(list) {
    if (!list || !list.length) return '';
    var h = '<div class="pop-sec">例句</div>';
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      h += '<div class="pop-ex" data-ep="' + e.ep + '" data-i="' + e.i + '">' +
        '<div class="ex-ja">' + escapeHtml(e.ja) + '</div>' +
        (e.zh ? '<div class="ex-zh">' + escapeHtml(e.zh) + '</div>' : '') +
        '<div class="ex-go">EP' + e.ep + ' · 跳过去 ›</div></div>';
    }
    return h;
  }

  function showWord(w) {
    var v = (window.__VOCAB__ || {})[w];
    if (!v) { toast('这个词暂时没有释义'); return; }
    var h = '<div class="pop-title">' +
      '<span class="pop-w">' + escapeHtml(w) + '</span>' +
      (v.r ? '<span class="pop-r">' + escapeHtml(v.r) + '</span>' : '') +
      '<span class="pop-pos">' + escapeHtml(v.p || '') + '</span>' +
      '<span class="pop-freq">出现 ' + (v.n || 0) + ' 次</span>' +
      '</div>';
    h += '<div class="pop-z">' + escapeHtml(v.z || '（释义待补）') + '</div>';
    if (v.u) h += '<div class="pop-u">' + escapeHtml(v.u) + '</div>';
    h += exBlock(v.ex);
    h += '<div class="pop-act"><button class="btn-primary" id="popAdd">加入生詞本</button>' +
      '<button class="tbtn" id="popClose2">关闭</button></div>';
    popShow(h);
    var b = $('#popAdd');
    if (b) b.addEventListener('click', function () { addWordToVocab(w); });
    var c2 = $('#popClose2');
    if (c2) c2.addEventListener('click', popHide);
  }

  function showPattern(pid) {
    var p = (window.__PATTERNS__ || {})[pid];
    if (!p) return;
    var h = '<div class="pop-title">' +
      '<span class="pop-w">' + escapeHtml(p.n) + '</span>' +
      '<span class="pop-freq">全片出现 ' + (p.k || 0) + ' 次</span>' +
      '</div>';
    h += '<div class="pop-row"><b>接续</b>' + escapeHtml(p.c || '') + '</div>';
    h += '<div class="pop-row"><b>含义</b>' + escapeHtml(p.m || '') + '</div>';
    if (p.f) h += '<div class="pop-row"><b>语感</b>' + escapeHtml(p.f) + '</div>';
    h += exBlock(p.ex);
    h += '<div class="pop-act"><button class="tbtn" id="popClose3">关闭</button></div>';
    popShow(h);
    var c3 = $('#popClose3');
    if (c3) c3.addEventListener('click', popHide);
  }

  function addWordToVocab(w) {
    var v = (window.__VOCAB__ || {})[w] || {};
    var item = { ja: w, zh: v.z || '', ro: v.r || '', at: Date.now() };
    var list = LS.get('ccs_vocab', []);
    for (var i = 0; i < list.length; i++) {
      if (list[i].ja === w) { toast('生詞本裡已經有了：' + w); return; }
    }
    list.unshift(item);
    LS.set('ccs_vocab', list);
    S.vocab = list;
    renderVocab();
    renderList();
    toast('已加入生詞本：' + w);
  }

  /* 点标注 → 弹窗（要在 cue 的点击事件之前拦下来，否则会跳播放） */
  var trBox = $('#transcript');
  if (trBox) {
    trBox.addEventListener('click', function (e) {
      var w = e.target.closest ? e.target.closest('.w') : null;
      if (w) {
        e.stopPropagation();
        var ww = decodeURIComponent(w.getAttribute('data-w') || '');
        speakJa(ww);
        // 就地面板开着且是同一句 → 在面板里展开；否则开词卡
        var host = e.target.closest('.cue');
        var hi = host ? parseInt(host.dataset.i, 10) : -1;
        var popOpen = !document.querySelector('#pop').classList.contains('hidden');
        if (popOpen && CUR.i === hi && CUR.c) {
          showWordDetail(CUR.c, -1, -1, ww);
        } else {
          showWord(ww);
        }
        return;
      }
      var g = e.target.closest ? e.target.closest('.pg') : null;
      if (g) {
        e.stopPropagation();
        showPattern(g.getAttribute('data-p'));
      }
    }, true);
  }
  var popBody = $('#popBody');
  if (popBody) {
    // 字号条：滑杆（拖动时实时生效）
    popBody.addEventListener('input', function (e) {
      var r = e.target.closest ? e.target.closest('#spPfs') : null;
      if (!r) return;
      S.settings.pfs = Math.max(14, Math.min(30, +r.value || 17));
      var v = $('#spPfsVal'); if (v) v.textContent = S.settings.pfs;
      LS.set('ccs_set', S.settings);
      applySettings();
    });
    popBody.addEventListener('click', function (e) {
      // AI 字号：小 / 標準 / 大 / 特大（独立于弹窗字号）
      var afs = e.target.closest ? e.target.closest('[data-ai-fs]') : null;
      if (afs) {
        e.stopPropagation();
        var av = +afs.getAttribute('data-ai-fs');
        LS.set('ccs_aifs', av);
        var box = $('#spAi'); if (box) box.style.fontSize = av + 'px';
        var allA = popBody.querySelectorAll('[data-ai-fs]');
        for (var qa = 0; qa < allA.length; qa++) {
          allA[qa].classList.toggle('on', +allA[qa].getAttribute('data-ai-fs') === av);
        }
        return;
      }
      // AI 分节折叠
      var fold = e.target.closest ? e.target.closest('[data-ai-fold]') : null;
      if (fold) {
        e.stopPropagation();
        var sec = fold.parentNode;
        var bod = sec.querySelector('.ai-b');
        if (bod) {
          var hid = bod.classList.toggle('hidden');
          fold.textContent = fold.textContent.replace(/[▲▼]\s*$/, '') + (hid ? ' ▼' : ' ▲');
        }
        return;
      }
      // 「問 AI：講這一句」/「複製提問」—— 两种模式走同一个 aiAsk
      var asb = e.target.closest ? e.target.closest('[data-act="ai-sentence"]') : null;
      if (asb) {
        e.stopPropagation();
        aiAsk('explain_sentence', CUR.c, CUR.i, '', $('#aiBox'));
        return;
      }
      // 「複製提問」—— 永远可用（不依赖后端）：把问题复制到剪贴板
      var acp = e.target.closest ? e.target.closest('[data-act="ai-copy"]') : null;
      if (acp) {
        e.stopPropagation();
        var body2 = aiBuildBody('explain_sentence', CUR.c, CUR.i, '');
        AI.lastBody = body2;
        var txt2 = aiBuildPrompt(body2);
        AI.state = 'copying';
        var bx = $('#aiBox');
        if (bx) bx.innerHTML = '<div class="ai-line ai-loading">正在複製…</div>';
        aiCopy(txt2, bx);
        return;
      }
      // 「讓 AI 講這個詞」
      var wab = e.target.closest ? e.target.closest('[data-wd-ai]') : null;
      if (wab) {
        e.stopPropagation();
        var aw = wab.getAttribute('data-wd-ai');
        var tgt = $('#aiBox');
        if (!tgt) return;
        // 词条解释区就地展开 AI 结果（弹窗内就地展开 = 唯一该就地展开的地方）
        aiAsk('explain_word', CUR.c, CUR.i, aw, tgt);
        var aiSec = $('#spAi');
        if (aiSec && aiSec.scrollIntoView) {
          aiSec.scrollIntoView({ block: 'nearest', behavior: 'auto' });
        }
        return;
      }
      // 「複製提問（講這個詞）」
      var wcp = e.target.closest ? e.target.closest('[data-wd-copy]') : null;
      if (wcp) {
        e.stopPropagation();
        var aw2 = wcp.getAttribute('data-wd-copy');
        var b3 = aiBuildBody('explain_word', CUR.c, CUR.i, aw2);
        AI.lastBody = b3;
        var t3 = aiBuildPrompt(b3);
        AI.state = 'copying';
        var bx3 = $('#aiBox');
        if (bx3) bx3.innerHTML = '<div class="ai-line ai-loading">正在複製…</div>';
        aiCopy(t3, bx3);
        var sec3 = $('#spAi');
        if (sec3 && sec3.scrollIntoView) {
          sec3.scrollIntoView({ block: 'nearest', behavior: 'auto' });
        }
        return;
      }
      // 🔴🔴 2026-10-25「複製整句」：把**该句完整日文**复制到剪贴板
      //   ⇒ 不含注音、不含中文（就是 c.ja）
      //   ⇒ 剪贴板走 aiCopy 那套**双路兜底**（clipboard API + textarea + execCommand）
      //      —— WebView 里 clipboard API 常常没权限，必须有兜底
      //   ⇒ 复制完给**可见**反馈：按钮短暂变「已複製」＋ toast（墨水屏刷新慢，toast 停 2800ms）
      var scp = e.target.closest ? e.target.closest('[data-act="sp-copy"]') : null;
      if (scp) {
        e.stopPropagation();
        var cj = (CUR && CUR.c) ? (CUR.c.ja || '') : '';
        if (!cj) { toast('沒有可複製的日文'); return; }
        aiCopy(cj, null);
        var oldTxt = scp.textContent;
        scp.textContent = '已複製';
        scp.classList.add('copied');
        setTimeout(function () {
          scp.textContent = oldTxt;
          scp.classList.remove('copied');
        }, 2800);
        return;
      }
      // 字号预设：小 / 標準 / 大 / 特大
      var pb = e.target.closest ? e.target.closest('[data-pfs]') : null;
      if (pb) {
        e.stopPropagation();
        S.settings.pfs = +pb.getAttribute('data-pfs');
        LS.set('ccs_set', S.settings);
        applySettings();
        var sb = $('#spPfs'); if (sb) sb.value = S.settings.pfs;
        var sv = $('#spPfsVal'); if (sv) sv.textContent = S.settings.pfs;
        var all = popBody.querySelectorAll('[data-pfs]');
        for (var q = 0; q < all.length; q++) {
          all[q].classList.toggle('on', +all[q].getAttribute('data-pfs') === S.settings.pfs);
        }
        return;
      }
      // 弹窗顶部按钮：跳到這句 / 收起 / 调字号
      var btn = e.target.closest ? e.target.closest('.sp-btn') : null;
      if (btn) {
        e.stopPropagation();
        var act = btn.getAttribute('data-act');
        if (act === 'close') { popHide(); return; }
        if (act === 'sp-size') {
          var bar = $('#spSizeBar');
          if (bar) bar.classList.toggle('hidden');
          return;
        }
        if (act === 'jump' && CUR.c) {
          popHide();
          P.currentTime = CUR.c.s / 1000 + 0.02;
          if (!S.playing) togglePlay(true);
        }
        return;
      }
      // 「★ 收藏这个词」
      var wsb = e.target.closest ? e.target.closest('[data-wd-save]') : null;
      if (wsb) {
        e.stopPropagation();
        var wname = wsb.getAttribute('data-wd-save');
        var winfo = wordInfo(wname);
        var epNow = S.ep || {};
        addWord(wname, winfo.z, winfo.r, epNow.id, epNow.no,
          CUR.c ? CUR.c.s / 1000 : 0);
        wsb.textContent = '✓ 已收藏';
        return;
      }
      // 整句结构里的词 → 在弹窗里就地展开解释
      var sw = e.target.closest ? e.target.closest('.sw') : null;
      if (sw && CUR.c) {
        e.stopPropagation();
        showWordDetail(CUR.c, parseInt(sw.getAttribute('data-s'), 10),
          parseInt(sw.getAttribute('data-e'), 10));
        return;
      }
      // 逐词拆解的词条 → 朗读 + 展开解释
      var wr = e.target.closest ? e.target.closest('.sp-w') : null;
      if (wr && CUR.c) {
        e.stopPropagation();
        var ws = parseInt(wr.getAttribute('data-s'), 10);
        var we = parseInt(wr.getAttribute('data-e'), 10);
        speakJa((CUR.c.ja || '').substr(ws, we - ws));
        showWordDetail(CUR.c, ws, we);
        return;
      }
      // 例句 → 跳过去
      var ex = e.target.closest ? e.target.closest('.sp-ex') : null;
      if (ex) {
        e.stopPropagation();
        var ep2 = parseInt(ex.getAttribute('data-ep'), 10);
        var idx2 = parseInt(ex.getAttribute('data-i'), 10);
        if (ep2) { popHide(); jumpToCue(ep2, idx2); }
        return;
      }
      var ex2 = e.target.closest ? e.target.closest('.pop-ex') : null;
      if (!ex2) return;
      var pid = ex2.getAttribute('data-p');
      if (pid) { showPattern(pid); return; }     // 句型卡片
      var ep = parseInt(ex2.getAttribute('data-ep'), 10);
      var idx = parseInt(ex2.getAttribute('data-i'), 10);
      if (!ep) return;
      popHide();
      jumpToCue(ep, idx);
    });
  }

  /** 跳到别的集数的某一句 —— 记住原来在哪，好让用户能跳回来 */
  function jumpToCue(epNo, cueIdx) {
    var id = 'ep' + pad2(epNo);
    if (!EPISODES.some(function (e) { return e.id === id; })) { toast('沒有這集'); return; }
    if (S.ep) {
      // 用播放时间反查当前句（不依赖 S.idx 是否已同步）
      var idx = S.idx >= 0 ? S.idx : cueIndexAt(P.currentTime);
      S.ret = { ep: S.ep.id, no: S.ep.no, i: idx, t: P.currentTime };
      renderRetBar();
    }
    S.pendingCue = cueIdx;
    openEpisode(id, 0);
  }

  /** 顶部「回到刚才那句」条 */
  function renderRetBar() {
    var bar = $('#retBar');
    if (!bar) return;
    if (!S.ret) { bar.classList.add('hidden'); return; }
    bar.classList.remove('hidden');
    bar.textContent = '← 回到第' + S.ret.no + '話 · 刚才那句';
  }
  function backToOrigin() {
    if (!S.ret) return;
    var r = S.ret;
    S.ret = null;
    renderRetBar();
    S.pendingCue = r.i;
    S.quiet = true;                 // 回到原句：别弹「繼續播放」提示
    openEpisode(r.ep, r.t || 0);
  }
  var retBar = $('#retBar');
  if (retBar) retBar.addEventListener('click', backToOrigin);


  /* ---------- 点整句：弹出详解弹窗 ---------- */
  /* 字幕流本身保持干净（不点不出现任何标签）；点哪句弹哪句。 */
  var CUR = { c: null, i: -1 };

  /** 词条信息（释义/读音/词性），带表层形→词典形的兜底 */
  function wordInfo(surf) {
    var VOC = window.__VOCAB__ || {}, LEX = window.__LEXICON__ || {};
    var key = surf;
    if (!VOC[key] && !LEX[key]) {
      var lx0 = LEX[surf] || {};
      var bf0 = lx0.bf || '';
      if (bf0 && (VOC[bf0] || LEX[bf0])) key = bf0;
    }
    var v = VOC[key] || {}, lx = LEX[key] || {};
    var SY = window.__SYNTAX__ || { p: {} };
    var pos = v.p || lx.p || '';
    return {
      key: key, z: v.z || '', u: v.u || '',
      r: v.r || lx.r || readOf(surf),
      p: SY.p[codeOfPos(pos)] || pos
    };
  }

  /** 这句里的形态链（話す → 話して → 話してみる → 話してみよう） */
  function chainList(c) {
    var CH = window.__CHAINS__ || {};
    var out = [];
    (c.ch || []).forEach(function (ch) {
      var t = CH[ch[2]];
      if (!t) return;
      out.push({
        type: ch[2], base: ch[3] || '',
        steps: ch[4] || [],
        desc: String(t[0] || '').replace('{orig}', ch[3] || ''),
        feel: t[1] || ''
      });
    });
    return out;
  }

  /** 详解弹窗的 HTML：原句 + 翻译 + ①逐词 ②语法 ③整句结构 */
  function studyHTML(c) {
    var SY = window.__SYNTAX__ || { r: {}, p: {} };
    var ja = c.ja || '';
    var h = '';

    // 头部：说话人 / 时间 / 操作
    var sp = ja.match(/^（[^）]{0,12}）\s*/);
    var who = sp ? sp[0].replace(/[（）\s]/g, '') : '';
    h += '<div class="sp-head">';
    if (who) h += '<span class="sp-who">' + escapeHtml(who) + '</span>';
    h += '<span class="sp-time">' + fmt(c.s / 1000) + '</span>';
    h += '<span class="sp-hint">点词＝朗读＋看解释</span>';
    h += '<button class="sp-btn" data-act="sp-size" title="調整彈窗字號">Aa</button>';
    // 🔴🔴 2026-10-25 补上「複製整句」（用户 10-24 亲定，规格里一直缺着）
    //   要复制的是**该句完整日文**（不含注音、不含中文）⇒ 就是 c.ja（纯文本）
    h += '<button class="sp-btn" data-act="sp-copy" title="把這句日文整句複製到剪貼板">複製整句</button>';
    h += '<button class="sp-btn" id="spDl" data-act="dl" title="把本集音頻存到手機">⬇ 存本集</button>';
    h += '<button class="sp-btn" data-act="jump">▶ 跳到這句</button>';
    h += '<button class="sp-btn" data-act="close">收起 ▲</button>';
    h += '</div>';
    // 字号条：点 Aa 展开，当场把弹窗里的字调大（老人/墨水屏特别需要）
    h += '<div class="sp-sizebar hidden" id="spSizeBar">' +
      '<span class="sp-sizebar-l">字號</span>' +
      '<input type="range" id="spPfs" min="14" max="30" value="' + (S.settings.pfs || 17) + '" step="1">' +
      '<span class="sp-sizebar-v" id="spPfsVal">' + (S.settings.pfs || 17) + '</span>' +
      '<button class="tbtn" data-pfs="14">小</button>' +
      '<button class="tbtn" data-pfs="17">標準</button>' +
      '<button class="tbtn" data-pfs="21">大</button>' +
      '<button class="tbtn" data-pfs="25">特大</button>' +
      '</div>';

    // 原文（整句注音）
    h += '<div class="sp-orig">' + jaRich(c, 0, ja.length) + '</div>';
    if (c.zh) h += '<div class="sp-zh">' + escapeHtml(c.zh) + '</div>';

    // ① 逐词拆解（内联词条流，尽量不占高度）
    if (c.t && c.t.length) {
      h += '<div class="sp-sec">① 逐词拆解</div><div class="sp-words">';
      var wi = 0;
      for (var j = 0; j < c.t.length; j++) {
        var tk = c.t[j];
        var w = ja.substr(tk[0], tk[1]);
        if (!w.trim()) continue;
        wi++;
        var rd = hasKanji(w) ? readOf(w) : '';
        var info = wordInfo(w);
        h += '<span class="sp-w" data-s="' + tk[0] + '" data-e="' + (tk[0] + tk[1]) + '">' +
          '<i class="sp-w-i">' + wi + '</i>' +
          '<b class="sp-w-j">' + escapeHtml(w) + '</b>' +
          (rd ? '<span class="sp-w-r">（' + escapeHtml(rd) + '）</span>' : '') +
          '<span class="sp-w-z">' + escapeHtml(info.z || '暂无释义') + '</span>' +
          '<span class="sp-w-s">🔊</span></span>';
      }
      h += '</div>';
      h += '<div class="sp-wd hidden" id="wordDetail"></div>';
    }

    // 🔴🔴 2026-10-25 **语法讲解新格式**（用户 10-24 亲定：只有这一种结构）
    //   ⇒ 有新格式数据就渲染它；**没有就回落旧三段式**
    //   （①逐词拆解照旧 —— 那是「点词展开释义」的入口，不能删）
    // 🔴 2026-10-25 **接线修正**（真 bug，之前的新格式永远渲染不出来）
    //   数据里的 blk 结构是 {frame, frameLabels, skeleton, blocks, zh, range, text}
    //   —— **没有 layers 字段** ⇒ 老判据 c.blk.layers 恒为 undefined
    //   ⇒ hasBlk 恒 false ⇒ 新格式永不触发 ⇒ 一直落回旧三段式
    //   ⇒ 判据放宽：只要有 frame（四类括号串）或 blocks（逐块拆解）就算有
    var hasBlk = !!(c.blk && ((c.blk.layers && c.blk.layers.length) ||
                              (c.blk.blocks && c.blk.blocks.length) ||
                              !!c.blk.frame));
    if (hasBlk) h += renderBlk(c);

    // ② 语法说明（旧格式：没有新格式数据时才用）
    var pats = [];
    (c.g || []).forEach(function (g) {
      var pp = (window.__PATTERNS__ || {})[g[2]];
      if (pp) pats.push(pp);
    });
    var chains = chainList(c);
    if (!hasBlk && (pats.length || chains.length)) {
      h += '<div class="sp-sec">② 语法说明</div><div class="sp-gram">';
      if (pats.length) {
        var p0 = pats[0];
        h += '<div class="sp-kv"><b>句型</b><span>' +
          pats.map(function (p) { return escapeHtml(p.n); }).join('、') + '</span></div>';
        if (p0.c) h += '<div class="sp-kv"><b>接续</b><span>' + escapeHtml(p0.c) + '</span></div>';
        if (p0.m) h += '<div class="sp-kv"><b>意思</b><span>' + escapeHtml(p0.m) + '</span></div>';
        if (p0.f) h += '<div class="sp-kv"><b>语感</b><span>' + escapeHtml(p0.f) + '</span></div>';
        if (p0.ex && p0.ex.length) {
          var ex0 = p0.ex[0];
          h += '<div class="sp-kv"><b>例句</b><span class="sp-ex" data-ep="' + ex0.ep + '" data-i="' + ex0.i + '">' +
            escapeHtml((ex0.ja || '').replace(/\n/g, ' ')) +
            (ex0.zh ? '<em>' + escapeHtml(ex0.zh) + '</em>' : '') + '</span></div>';
        }
      }
      chains.forEach(function (ch) {
        if (ch.steps.length > 1) {
          h += '<div class="sp-kv"><b>原形</b><span class="sp-chain">' +
            ch.steps.map(function (s) { return escapeHtml(s); }).join(' → ') + '</span></div>';
        }
        h += '<div class="sp-kv"><b>形态</b><span>' + escapeHtml(ch.desc) +
          (ch.feel ? '<em>' + escapeHtml(ch.feel) + '</em>' : '') + '</span></div>';
      });
      h += '</div>';
    }

    // ③ 整句结构（旧格式；新格式下由「二、逐块拆解」承担）
    if (!hasBlk && c.y && c.y.length) {
      h += '<div class="sp-sec">③ 整句结构</div><div class="sp-syn">';
      for (var i = 0; i < c.y.length; i++) {
        var sg = c.y[i];
        var role = SY.r[sg[2]] || { n: '其他' };
        var zt = (c.yb && c.yb[i]) ? c.yb[i] : '';
        h += '<div class="sp-syn-row">' +
          '<span class="sp-syn-r r' + sg[2] + '">' + role.n + '</span>' +
          '<span class="sp-syn-j">' + jaRich(c, sg[0], sg[0] + sg[1]) + '</span>' +
          '<span class="sp-syn-sep">｜</span>' +
          '<span class="sp-syn-z">' + escapeHtml(zt) + '</span></div>';
      }
      h += '</div>';
      h += '<div class="sp-legend">';
      for (var k in SY.r) h += '<span class="lg r' + k + '">' + SY.r[k].n + '</span>';
      h += '</div>';
    }

    // ④ AI 讲解
    //   复制模式（没配中转，默认）：按钮 =「複製提問」，点了把问题写进剪贴板
    //   真接口模式（配了中转）  ：按钮 =「問 AI：講這一句」，直接调接口
    var aiOn = AI.enabled();
    var aifs = LS.get('ccs_aifs', 18);
    h += '<div class="sp-sec">④ AI 講解</div>';
    h += '<div class="ai-quota hidden" id="aiQuota"></div>';
    h += '<div class="sp-ai" id="spAi" style="font-size:' + aifs + 'px">';
    h += '<div class="sp-ai-bar">';
    if (aiOn) {
      // 配了中转 → 两个入口都给：直接出结果 / 复制去 MiniMax
      h += '<button class="tbtn ai-ask" data-act="ai-sentence">問 AI：講這一句</button>';
      h += '<button class="tbtn ai-copy" data-act="ai-copy">複製提問</button>';
    } else {
      h += '<button class="tbtn ai-ask" data-act="ai-copy">複製提問</button>';
    }
    h += '<span class="sp-ai-fs"><span class="sp-ai-fs-l">AI 字號</span>' +
      '<button class="tbtn" data-ai-fs="15">小</button>' +
      '<button class="tbtn" data-ai-fs="18">標準</button>' +
      '<button class="tbtn" data-ai-fs="21">大</button>' +
      '<button class="tbtn" data-ai-fs="25">特大</button></span>';
    h += '</div>';
    h += '<div class="sp-ai-box" id="aiBox"><div class="ai-line ai-dim">' +
      (aiOn ? '（点「問 AI」直接出结果，或点「複製提問」把问题复制到剪贴板，粘到 MiniMax 也行）'
            : '（点「複製提問」，把问题复制到剪贴板，粘到 MiniMax 就有答案）') +
      '</div></div>';
    h += '</div>';
    return h;
  }

  /** 点一句 → 弹大窗 */

  /* ============ 🔴 语法讲解新格式的渲染（2026-10-25）============
     规格（用户 10-24 亲定）：
       一、大框架   四类括号 + 每层的语法角色标注
       二、逐块拆解 每块：成分分解（＋ 连接）+ 2~4 条说明 + 块的中文意思
       整句意思    完整中文翻译
     四类括号 = 四种语法层级：
       [ ]   连接词 / 独立成分（呼语、承接词、转折词）
       ［ ］  从句（条件、让步、修饰、宾语从句…）
       〈 〉  句体成分（主语、宾语、补语等句内成分）
       （ ）  谓语（动词/形容词 + 助动词，含疑问与终助词） */
  var BLK_CLASS = { '[]': 'b-conn', '［］': 'b-cls', '〈〉': 'b-body', '（）': 'b-pred' };
  /* 🔴 四类括号的中文标签（用户 10-24 亲定）
     [ ] 连接词/独立成分 ｜ ［ ］ 从句 ｜ 〈 〉 句体成分 ｜ （ ） 谓语 */
  var BLK_LABEL = { '[]': '连接词／独立成分', '［］': '从句', '〈〉': '句体成分', '（）': '谓语' };
  var BLK_ORD = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];

  /* 🔴 2026-10-25 把 blk.frame（四类括号串）切成 layers
     frame 形如：[国によると、] 〈去年、〉 〈自殺した人は〉 （でした。） ［前の年より］…
     ⚠️ 要按**配对**切，不能用简单正则 —— 有嵌套（〈…（…）…〉）时正则会切错
     ⇒ 用栈扫描：只在栈空→非空的那一刻记一个块，闭合时出栈
     ⇒ 括号外的残留文字（如果有）单独作为「独立成分」补一块，不丢信息 */
  function parseFrame(frame) {
    var PAIRS = { '[': ']', '［': '］', '〈': '〉', '（': '）' };
    var out = [], stack = [], rest = '';
    if (!frame) return out;
    for (var i = 0; i < frame.length; i++) {
      var ch = frame.charAt(i);
      if (PAIRS[ch]) {
        stack.push({ open: ch, text: ch });
        continue;
      }
      if (stack.length) {
        var top = stack[stack.length - 1];
        top.text += ch;
        if (ch === PAIRS[top.open]) {
          stack.pop();
          if (!stack.length) out.push({ type: top.open + PAIRS[top.open], text: top.text });
        }
        continue;
      }
      rest += ch;
    }
    if (rest.trim()) out.push({ type: '[]', text: rest.trim() });
    return out;
  }

  function renderBlk(c) {
    var b = c.blk || {};
    var h = '';
    /* ── 一、大框架 ── */
    h += '<div class="sp-sec">一、大框架</div>';
    h += '<div class="sp-frame">';
    var layers = (b.layers && b.layers.length) ? b.layers : parseFrame(b.frame);
    layers.forEach(function (L) {
      var ty = String(L.type || '[]').replace(/\s/g, '');
      var cls = BLK_CLASS[ty] || 'b-conn';
      var lbl = L.label || BLK_LABEL[ty] || '';
      h += '<div class="sp-frame-row">' +
        '<span class="sp-frame-t ' + cls + '">' + escapeHtml(L.text || '') + '</span>' +
        '<span class="sp-frame-l">' + escapeHtml(lbl) + '</span></div>';
    });
    h += '</div>';
    if (b.skeleton) {
      h += '<div class="sp-skel">' + escapeHtml(b.skeleton) + '</div>';
    }
    /* ── 二、逐块拆解 ── */
    h += '<div class="sp-sec">二、逐块拆解</div>';
    (b.blocks || []).forEach(function (bl, i) {
      var parts = (bl.parts || []).map(function (p) {
        return escapeHtml(p[0]) + '<em>' + escapeHtml(p[1] || '') + '</em>';
      }).join(' ＋ ');
      h += '<div class="sp-blk">';
      // 🔴 blocks[] 里常常没有 text 字段 ⇒ 用 parts 的原文拼出块文本
      var blText = bl.text || (bl.parts || []).map(function (p) { return p[0] || ''; }).join('');
      h += '<div class="sp-blk-h">第' + (BLK_ORD[i] || (i + 1)) + '块：' +
        escapeHtml(blText) + '</div>';
      if (parts) h += '<div class="sp-blk-p">' + parts + '</div>';
      (bl.notes || []).forEach(function (nt) {
        h += '<div class="sp-blk-n">· ' + escapeHtml(nt) + '</div>';
      });
      if (bl.zh) h += '<div class="sp-blk-z">意思：' + escapeHtml(bl.zh) + '</div>';
      h += '</div>';
    });
    /* ── 整句意思 ── */
    if (b.zh) h += '<div class="sp-blk-z sp-blk-all">整句意思：' + escapeHtml(b.zh) + '</div>';
    return h;
  }

  function openStudy(c, idx) {
    CUR.c = c; CUR.i = idx;
    // 词库延遲加載：沒就緒先等（第一次會有 3.8MB 的下載）
    if (!wordDataReadyNow()) {
      toast('正在載入詞庫…（首次約 3.8MB，之後就不用等了）');
      ensureWordData().then(function () { renderStudyPop(c, idx); });
      return;
    }
    renderStudyPop(c, idx);
  }

  // ⚠️ 別叫 renderStudy —— 下面 1500 行已經有一個 renderStudy()（學習頁渲染），
  //    函式宣告提升會互相覆蓋，彈窗就永遠打不開
  function renderStudyPop(c, idx) {
    popShow(studyHTML(c));
    aiShowQuota();          // 顶部额度提示（查不到就不显示）
    var db = $('#spDl');
    if (db) db.addEventListener('click', downloadCurrentEp);
    refreshDlBtn();
    var jb = $('#spJump');
    if (jb) jb.addEventListener('click', function () {
      popHide();
      P.currentTime = c.s / 1000 + 0.02;
      if (!S.playing) togglePlay(true);
    });
  }

  /* ---------- 学习页：生词表 + 句型表 ---------- */
  /* ============ 生词复习卡片 ============ */
  /* 队列：不会的排前面；答对的次数越多越靠后；没复习过的排最前 */
  function reviewQueue() {
    var list = S.vocab.slice();
    list.sort(function (a, b) {
      var sa = (a.w || 0) - (a.k || 0), sb = (b.w || 0) - (b.k || 0);
      if (sa !== sb) return sb - sa;
      return (a.r || 0) - (b.r || 0);
    });
    return list;
  }
  var RV = { on: false, q: [], i: 0, ok: 0, bad: 0, flipped: false };

  function rvStart() {
    if (!S.vocab.length) { toast('還沒有收藏 —— 在播放頁點台詞右邊的「＋」'); return; }
    RV.on = true; RV.q = reviewQueue(); RV.i = 0; RV.ok = 0; RV.bad = 0; RV.flipped = false;
    var w = $('#rvWrap'); if (w) w.classList.remove('hidden');
    var l = $('#studyList'); if (l) l.classList.add('hidden');
    // 复习时把搜索栏/提示行藏起来，屏幕全给卡片
    var vs = $('#viewStudy');
    if (vs) vs.classList.add('rv-mode');
    rvShow();
  }
  function rvEnd() {
    RV.on = false;
    var w = $('#rvWrap'); if (w) w.classList.add('hidden');
    var l = $('#studyList'); if (l) l.classList.remove('hidden');
    var vs = $('#viewStudy');
    if (vs) vs.classList.remove('rv-mode');
    renderStudy();
  }
  function rvShow() {
    var total = RV.q.length;
    var item = RV.q[RV.i];
    if (!item) {
      // 一轮结束
      var d = $('#rvDone'), t = $('#rvDoneT'), s2 = $('#rvDoneS');
      var f = $('#rvFront'), b = $('#rvBack');
      if (f) f.classList.add('hidden');
      if (b) b.classList.add('hidden');
      if (d) d.classList.remove('hidden');
      if (t) t.textContent = '這一輪複習完啦！';
      if (s2) s2.textContent = '認識 ' + RV.ok + ' 個 · 還不認識 ' + RV.bad + ' 個' +
        (RV.bad ? '（不認識的排到後面，下次再練）' : '（都記住了 👏）');
      var c = $('#rvCount'); if (c) c.textContent = total + ' / ' + total;
      var bar = $('#rvBar'); if (bar) bar.style.width = '100%';
      return;
    }
    var done = $('#rvDone'); if (done) done.classList.add('hidden');
    var f2 = $('#rvFront'), b2 = $('#rvBack');
    if (f2) f2.classList.remove('hidden');
    if (b2) b2.classList.add('hidden');
    RV.flipped = false;
    var ja = $('#rvJa'), rd = $('#rvRead');
    if (ja) ja.textContent = item.ja || '';
    if (rd) {
      var r = (window.__LEXICON__ || {})[item.ja];
      var ro = (item.ro || (r && r.r) || readOf(item.ja) || '');
      rd.textContent = ro ? ro : '';
    }
    var cnt = $('#rvCount'); if (cnt) cnt.textContent = (RV.i + 1) + ' / ' + total;
    var bar2 = $('#rvBar'); if (bar2) bar2.style.width = (RV.i / Math.max(1, total) * 100) + '%';
  }
  function rvFlip() {
    if (RV.flipped) return;
    var item = RV.q[RV.i];
    if (!item) return;
    RV.flipped = true;
    var f = $('#rvFront'), b = $('#rvBack');
    if (f) f.classList.add('hidden');
    if (b) b.classList.remove('hidden');
    var V = window.__VOCAB__ || {}, L = window.__LEXICON__ || {};
    var v = V[item.ja] || {}, lx = L[item.ja] || {};
    var zh = $('#rvZh');
    if (zh) zh.textContent = v.z || lx.z || item.zh || '（暂无释义）';
    var ex = $('#rvEx');
    if (ex) {
      var list = (v.ex && v.ex.length) ? v.ex : (lx.ex || []);
      ex.innerHTML = '';
      if (list.length) {
        var e0 = list[0];
        ex.innerHTML = '<div class="rv-ex-ja">' + escapeHtml((e0.ja || '').replace(/\n/g, ' ')) + '</div>' +
          (e0.zh ? '<div class="rv-ex-zh">' + escapeHtml(e0.zh) + '</div>' : '');
        ex.setAttribute('data-ep', e0.ep);
        ex.setAttribute('data-i', e0.i);
      } else {
        ex.innerHTML = '<div class="rv-ex-zh">' + escapeHtml(item.zh || '') + '</div>';
        ex.removeAttribute('data-ep');
      }
    }
  }
  function rvAnswer(ok) {
    var item = RV.q[RV.i];
    if (!item) return;
    if (ok) { item.k = (item.k || 0) + 1; RV.ok++; }
    else { item.w = (item.w || 0) + 1; RV.bad++; }
    item.r = Date.now();
    LS.set('ccs_vocab', S.vocab);
    if (!ok) {
      // 不认识的排到队尾，等下再遇到
      RV.q.push(RV.q.splice(RV.i, 1)[0]);
    } else {
      RV.i++;
    }
    rvShow();
  }

  function renderStudy() {
    var box = $('#studyList');
    if (!box) return;
    var tab = S.studyTab || 'w';
    var q = (S.studyQ || '').trim();
    box.innerHTML = '';
    var vh0 = $('#vocabHead'), vp0 = $('#vocabPanel');
    if (vh0) vh0.classList.remove('hidden');
    if (vp0) vp0.classList.remove('hidden');
    var frag = document.createDocumentFragment();
    if (tab === 'f') {
      // 我的收藏（生詞本）
      var rvWrap = $('#rvWrap');
      if (rvWrap) rvWrap.classList.add('hidden');
      var sl = $('#studyList'); if (sl) sl.classList.remove('hidden');
      var V0 = S.vocab;
      // 顶部：开始复习
      var head = document.createElement('div');
      head.className = 'rv-start-bar';
      head.innerHTML = '<span class="rv-start-t">我的收藏 <b>' + V0.length + '</b> 個詞</span>' +
        '<button class="rv-btn primary" id="rvStartBtn">開始復習</button>';
      frag.appendChild(head);
      var q0 = (S.studyQ || '').trim();
      var rows = V0.filter(function (v) {
        if (!q0) return true;
        return ((v.ja || '') + ' ' + (v.zh || '')).toLowerCase().indexOf(q0.toLowerCase()) >= 0;
      });
      rows = rows.slice().reverse();
      var sf = 0;
      for (var fi = 0; fi < rows.length; fi++) {
        var v2 = rows[fi];
        var e3 = document.createElement('div');
        e3.className = 'st-item';
        e3.innerHTML = '<div class="st-h"><span class="st-w">' + escapeHtml(v2.ja) + '</span>' +
          '<span class="st-n">第' + v2.ep + '話 · ' + fmt(v2.t) + '</span></div>' +
          '<div class="st-z">' + escapeHtml(v2.zh || '') + '</div>';
        e3.addEventListener('click', (function (vv) {
          return function () { openEpisode(vv.epId, vv.t); };
        })(v2));
        frag.appendChild(e3);
        sf++;
      }
      if (!sf) frag.appendChild(emptyNode(q0 ? '没有匹配的收藏' : '还没有收藏 —— 在播放页点台词右边的「＋」就能收藏'));
      var cntF = $('#studyCount');
      if (cntF) cntF.textContent = V0.length + ' 條收藏';
      var vh = $('#vocabHead'), vp = $('#vocabPanel');
      if (vh) vh.classList.add('hidden');
      if (vp) vp.classList.add('hidden');
      box.appendChild(frag);
      return;
    }
    if (tab === 'w') {
      var V = window.__VOCAB__ || {};
      var keys = Object.keys(V);
      // 精选（值得记的）在前；功能词/超高频词沉到后面，但仍然能搜到
      keys.sort(function (a, b) {
        var ca = V[a].c ? 1 : 0, cb = V[b].c ? 1 : 0;
        if (ca !== cb) return cb - ca;
        return (V[b].n || 0) - (V[a].n || 0);
      });
      var shown = 0;
      for (var i = 0; i < keys.length; i++) {
        var w = keys[i], v = V[w];
        if (q) {
          var hay = (w + ' ' + (v.r || '') + ' ' + (v.z || '') + ' ' + (v.u || '')).toLowerCase();
          if (hay.indexOf(q.toLowerCase()) < 0) continue;
        }
        var el = document.createElement('div');
        el.className = 'st-item';
        el.innerHTML = '<div class="st-h"><span class="st-w">' + escapeHtml(w) + '</span>' +
          (v.r ? '<span class="st-r">' + escapeHtml(v.r) + '</span>' : '') +
          '<span class="st-n">' + (v.n || 0) + '次</span></div>' +
          '<div class="st-z">' + escapeHtml(v.z || '') + '</div>' +
          (v.u ? '<div class="st-u">' + escapeHtml(v.u) + '</div>' : '');
        el.addEventListener('click', (function (ww) {
          return function () { showWord(ww); };
        })(w));
        frag.appendChild(el);
        shown++;
      }
      if (!shown) frag.appendChild(emptyNode('没有匹配的词'));
    } else {
      var PP = window.__PATTERNS__ || {};
      var pids = Object.keys(PP);
      pids.sort(function (a, b) { return (PP[b].k || 0) - (PP[a].k || 0); });
      var shown2 = 0;
      for (var j = 0; j < pids.length; j++) {
        var id = pids[j], pp = PP[id];
        if (q) {
          var hay2 = (pp.n + ' ' + (pp.c || '') + ' ' + (pp.m || '') + ' ' + (pp.f || '')).toLowerCase();
          if (hay2.indexOf(q.toLowerCase()) < 0) continue;
        }
        var e2 = document.createElement('div');
        e2.className = 'st-item st-pat';
        e2.innerHTML = '<div class="st-h"><span class="st-w">' + escapeHtml(pp.n) + '</span>' +
          '<span class="st-n">' + (pp.k || 0) + '次</span></div>' +
          '<div class="st-z">' + escapeHtml(pp.m || '') + '</div>' +
          '<div class="st-u">接续：' + escapeHtml(pp.c || '') + '</div>';
        e2.addEventListener('click', (function (ii) {
          return function () { showPattern(ii); };
        })(id));
        frag.appendChild(e2);
        shown2++;
      }
      if (!shown2) frag.appendChild(emptyNode('没有匹配的句型'));
    }
    box.appendChild(frag);
    var cnt = $('#studyCount');
    if (cnt) {
      var V2 = window.__VOCAB__ || {}, P2 = window.__PATTERNS__ || {};
      cnt.textContent = (tab === 'w' ? Object.keys(V2).length + ' 词' : Object.keys(P2).length + ' 句型');
    }
  }
  function emptyNode(txt) {
    var p = document.createElement('p');
    p.className = 'empty';
    p.textContent = txt;
    return p;
  }

  /* ============ 逐句对照表 ============ */
  var cueEls = [];
  function renderTranscript() {
    var box = $('#transcript');
    popHide();
    box.innerHTML = '';
    cueEls = [];
    var frag = document.createDocumentFragment();
    S.subs.forEach(function (c, i) {
      var el = document.createElement('div');
      el.className = 'cue';
      // 整句都是舞台指示（旁白）→ 标灰，不是台词
      if (c.ja && /^[（(][^）)]*[）)]$/.test(c.ja.trim())) el.className += ' stage';
      // 只有中文、没有日文原文（片头/片尾曲歌词，原字幕就没日文）→ 标出来，别让人误以为是日文
      if (!(c.ja || '').trim()) el.className += ' only-zh';
      el.dataset.i = i;
      el.innerHTML =
        '<span class="c-time">' + fmt(c.s / 1000) + '</span>' +
        '<div class="c-body">' +
        '<div class="c-ja">' + renderJa(c) + '</div>' +
        '<div class="c-zh">' + escapeHtml(c.zh) + '</div>' +
        '</div>' +
        '<button class="c-add" title="加入我的收藏">＋</button>';
      // 已收藏的台词，＋ 按钮显示成绿色
      if (S.vocab.some(function (v) { return v.epId === S.ep.id && v.s === c.s; })) {
        var addBtn0 = el.querySelector('.c-add');
        if (addBtn0) { addBtn0.classList.add('saved'); addBtn0.textContent = '✓'; }
      }
      el.addEventListener('click', function (e) {
        if (e.target.classList.contains('c-add')) { addVocab(c); return; }
        // 点整句 → 跳到这句（播放位置）+ 弹出详解弹窗
        seekToCue(c);
        openStudy(c, i);
      });
      el.querySelector('.c-add').addEventListener('click', function (e) {
        e.stopPropagation();
        addVocab(c);
      });
      frag.appendChild(el);
      cueEls.push(el);
    });
    box.appendChild(frag);
    applyFilter();
  }

  function applyFilter() {
    var q = S.filter.trim();
    var shown = 0;
    for (var i = 0; i < cueEls.length; i++) {
      var c = S.subs[i];
      var hit = !q || (c.ja && c.ja.indexOf(q) >= 0) || (c.zh && c.zh.indexOf(q) >= 0) ||
                (c.ro && c.ro.indexOf(q) >= 0);
      cueEls[i].style.display = hit ? '' : 'none';
      if (hit) shown++;
    }
    $('#transcript').querySelectorAll('.empty').forEach(function (n) { n.remove(); });
    if (!shown) {
      var p = document.createElement('p');
      p.className = 'empty';
      p.textContent = '沒有匹配的台詞';
      $('#transcript').appendChild(p);
    }
  }

  /* ============ 播放同步 ============ */
  function cueIndexAt(t) {
    var lo = 0, hi = S.subs.length - 1, ans = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (S.subs[mid].s <= t * 1000) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
  }

  var lastScroll = 0;
  var autoScrolling = false;
  /* ⚠️ 程序自己滚动也会触发 scroll 事件 —— 要是把它当成「用户滚过」，
     那个 2.5 秒守卫会每轮自我触发 → 自动滚动变成每 2.5 秒才跳一次，
     表现就是「字幕不跟着音频走」。所以要区分开。 */
  $('#transcript').addEventListener('scroll', function () {
    if (autoScrolling) return;
    lastScroll = Date.now();
  }, { passive: true });

  /* ⚠️ 自动滚动必须用「绝对目标 + instant」，不能用 smooth。
     原因：ticker 每 200ms 调一次，smooth 动画会被反复重启 → 永远到不了目标，
     表现就是「高亮行在变、画面不动」。
     behavior:'auto' 在 CSSOM 里是「听 CSS 的」，CSS 若有 scroll-behavior:smooth
     还是会平滑 → 这里显式用 'instant'。 */
  var lastAutoScroll = 0;
  function scrollCueTo(el, instant) {
    if (!el) return;
    var now = Date.now();
    if (!instant) {
      if (now - lastAutoScroll < 700) return;   // 别每个 tick 都重启一次
      lastAutoScroll = now;
    }
    try {
      autoScrolling = true;
      el.scrollIntoView({ block: 'center', behavior: instant ? 'instant' : 'auto' });
    } catch (e) {
      try { el.scrollIntoView(); } catch (e2) { }
    }
    setTimeout(function () { autoScrolling = false; }, 350);
  }

  function syncCue() {
    if (!S.subs.length) return;
    // 🔴 2026-10-24：阅读模式（普通版关掉「音字同步」）⇒ **不高亮、不自动滚**
    //   —— 但**不另写一套同步逻辑**，还是走下面这段（同一个 cueIndexAt / scrollCueTo）
    if (!syncEnabled()) {
      if (S.idx !== -1) {
        S.idx = -1;
        for (var k0 = 0; k0 < cueEls.length; k0++) cueEls[k0].classList.remove('on');
      }
      return;
    }
    var t = P.currentTime;
    var i = cueIndexAt(t);
    if (i === S.idx) return;
    S.idx = i;
    var c = i >= 0 ? S.subs[i] : null;
    for (var k = 0; k < cueEls.length; k++) cueEls[k].classList.toggle('on', k === i);
    if (S.settings.auto && Date.now() - lastScroll > 2500 && i >= 0 && cueEls[i]) {
      scrollCueTo(cueEls[i], false);
    }
  }

  function onTimeUpdate() {
    updateTime();
    syncCue();
    // 单句循环
    if (S.loop && S.idx >= 0 && S.subs[S.idx]) {
      var c = S.subs[S.idx];
      if (P.currentTime * 1000 > c.e + 60) {
        P.currentTime = c.s / 1000;
      }
    }
  }

  /* ⚠️ 拖动进度条时不能被 ticker 抢走 value。
     updateTime() 每 200ms 跑一次，如果在用户手指还按着的时候写 value，
     滑块会被一次次拉回播放位置 → 表现为「进度条拉动不灵」。 */
  var isSeeking = false;

  /* 🔴 墨水屏防闪（2026-10-22）
   *   电子墨水屏**每写一次 DOM 就闪一次**，值没变也闪。
   *   → ① 一律「值没变就不写」；
   *   → ② 高对比模式（html.hc）= 墨水屏用户的明确选择 → 时间标签 5 秒一跳、进度条 500ms 一跳；
   *   → ③ 彩屏主题保持每拍更新，但同样走「值没变不写」（纯优化，无副作用）。 */
  function isEinkUI() {
    try { return document.documentElement.classList.contains('hc'); } catch (e) { return false; }
  }
  function setTextIfChanged(el, v) {
    if (el && el.textContent !== v) { el.textContent = v; return true; }
    return false;
  }
  var _lastSeekV = -1, _lastTCurAt = 0;
  function updateTime(force) {
    var d = P.duration;
    if (!isFinite(d) || d <= 0) d = (S.ep && S.ep.duration) || 0;
    var eink = isEinkUI();
    var now = Date.now();
    // 时间标签：墨水屏 5 秒一跳；彩屏每拍写（值每秒才变一次，写了也不闪）
    if (force || !eink || now - _lastTCurAt > 5000) {
      _lastTCurAt = now;
      setTextIfChanged($('#tCur'), fmt(P.currentTime));
    }
    setTextIfChanged($('#tDur'), fmt(d));
    if (!isSeeking) {
      var v = d ? Math.round((P.currentTime / d) * 10000) : 0;
      // 进度条：墨水屏 500ms 一跳；彩屏保持实时
      if (force || !eink || now - (_lastTCurAt - 4500) > 500) {
        if (v !== _lastSeekV) { _lastSeekV = v; $('#seek').value = v; }
      }
    }
  }

  (function bindSeek() {
    var el = $('#seek');
    if (!el) return;
    function down() { isSeeking = true; }
    function up() { isSeeking = false; }
    el.addEventListener('pointerdown', down);
    el.addEventListener('touchstart', down, { passive: true });
    el.addEventListener('pointerup', up);
    el.addEventListener('touchend', up, { passive: true });
    el.addEventListener('touchcancel', up, { passive: true });
    window.addEventListener('pointerup', up);
    el.addEventListener('input', function () {
      var d = P.duration;
      if (!isFinite(d) || d <= 0) return;
      P.currentTime = (this.value / 10000) * d;
      updateTime(true);
    });
    // 拖动中实时预览，松手才真正 seek
    el.addEventListener('change', function () {
      var d = P.duration;
      if (!isFinite(d) || d <= 0) return;
      P.currentTime = (this.value / 10000) * d;
      isSeeking = false;
      updateTime(true);
    });
  })();

  function bindPlayerEvents() {
    P.on('play', function () {
      /* 🔴 只有「**真的在播**」才取消看门狗（2026-10-22）
       *   'play' 事件现在由「用户意图」触发（准备期意图就是 true），
       *   如果无条件清看门狗 → 原生迟迟不出声时**永远不会兜底切 WebView**。
       *   ⇒ 判断条件改成「实际在播」。 */
      var actually = P.impl ? !!P.impl.actualPlaying : false;
      if (actually && nativeWatch) { clearTimeout(nativeWatch); nativeWatch = null; }
      S.playing = true;
      $('#btnPlay').textContent = '❚❚';
      // 我们的音频真的响起来了 → 接管，别的音源全停
      claimAudio(P.mode === 'native' ? 'native' : 'ours');
    });
    P.on('pause', function () { S.playing = false; $('#btnPlay').textContent = '▶'; });
    P.on('error', onAudioError);
    P.on('timeupdate', onTimeUpdate);
    P.on('loadedmetadata', function () {
      if (S.ep) {
        var p = S.prog[S.ep.id] || {};
        if (!p.t && P.currentTime < 1) {
          var st = S.prog[S.ep.id] && S.prog[S.ep.id].t;
          if (st) P.currentTime = st;
        }
        updateTime(true);
      }
    });
    P.on('ended', function () {
      if (!S.ep) return;
      var idx = EPISODES.map(function (e) { return e.id; }).indexOf(S.ep.id);
      if (idx >= 0 && idx + 1 < EPISODES.length) {
        var next = EPISODES[idx + 1];
        S.prog[S.ep.id] = { t: S.ep.duration, updated: Date.now() };
        LS.set('ccs_prog', S.prog);
        toast('本集結束，播放下一集');
        openEpisode(next.id, 0);
        setTimeout(function () { P.play(); }, 400);
      } else {
        toast('全部看完啦！');
      }
    });
  }

  function togglePlay(force) {
    if (force === true) { P.play(); return; }
    if (P.paused) { P.play(); } else { P.pause(); }
  }

  /* ============ 控制条 ============ */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('[data-act]') : null;
    if (!btn) return;
    var act = btn.dataset.act;
    if (act === 'prev10') { P.currentTime = Math.max(0, P.currentTime - 10); }
    else if (act === 'next10') { P.currentTime = Math.min(P.duration || 1e9, P.currentTime + 10); }
    else if (act === 'prevLine') { jumpCue(-1); }
    else if (act === 'nextLine') { jumpCue(1); }
    else if (act === 'mode') { cycleMode(btn); }
    else if (act === 'ro') { toggleRo(); }
    else if (act === 'speed') { cycleSpeed(btn); }
    else if (act === 'loop') { S.loop = !S.loop; btn.classList.toggle('on', S.loop); toast(S.loop ? '單句循環：開' : '單句循環：關'); }
    else if (act === 'addWord') { addVocab(S.idx >= 0 ? S.subs[S.idx] : null); }
    else if (act === 'hide') { cycleMode(btn); }
  });

  $('#btnPlay').addEventListener('click', function () { togglePlay(); });

  function jumpCue(dir) {
    if (!S.subs.length) return;
    var t = P.currentTime * 1000;
    var target = -1;
    if (dir < 0) {
      for (var i = S.subs.length - 1; i >= 0; i--) {
        if (S.subs[i].s < t - 300) { target = i; break; }
      }
      if (target < 0) target = 0;
    } else {
      for (var j = 0; j < S.subs.length; j++) {
        if (S.subs[j].s > t + 100) { target = j; break; }
      }
      if (target < 0) target = S.subs.length - 1;
    }
    P.currentTime = S.subs[target].s / 1000 + 0.02;
    S.idx = -1;
    syncCue();
  }

  var MODES = [
    { id: 'both', label: '雙語' },
    { id: 'ja', label: '只看日文' },
    { id: 'zh', label: '只看中文' },
    { id: 'none', label: '隱藏字幕' }
  ];
  function cycleMode(btn) {
    var i = 0;
    for (var k = 0; k < MODES.length; k++) if (MODES[k].id === S.mode) i = k;
    i = (i + 1) % MODES.length;
    S.mode = MODES[i].id;
    if (btn) {
      btn.textContent = MODES[i].label;
      btn.classList.toggle('on', S.mode !== 'both');
    }
    applyMode();
    if (S.mode === 'none') toast('聽力模式：點某一句可偷看');
    if (S.mode === 'zh') toast('中文模式：先自己唸，再看日文');
  }
  /** 字幕显示模式作用在整条字幕流上 */
  function applyMode() {
    var r = document.documentElement;
    r.classList.toggle('mode-ja', S.mode === 'ja');
    r.classList.toggle('mode-zh', S.mode === 'zh');
    r.classList.toggle('mode-none', S.mode === 'none');
  }

  /* ============ 跳到某一句（点句行为） ============ */
  function seekToCue(c) {
    if (!c) return;
    P.currentTime = c.s / 1000 + 0.02;
    S.idx = -1;
    syncCue();
    updateTime(true);
    // 跳过去之后把这一行滚到中间（老人/长字幕时不至于找不到）
    var idx = S.subs.indexOf(c);
    if (idx >= 0 && cueEls[idx]) {
      scrollCueTo(cueEls[idx], true);   // 用户点了 → 立即到位，别用 smooth
    }
  }

  /* ============ 字号面板（顶栏 Aa） ============ */
  function showFontPanel() {
    var PRESETS = [
      { v: 24, n: '標準' }, { v: 28, n: '大' }, { v: 32, n: '特大' }, { v: 36, n: '超大' }, { v: 42, n: '最大' }
    ];
    var h = '<div class="pop-title"><span class="pop-w">字體大小</span></div>';
    h += '<div class="pop-row fp-row"><b>字幕</b>' +
      '<input id="fpRange" type="range" min="18" max="46" value="' + S.settings.fs + '" step="1">' +
      '<span id="fpVal" class="fp-val">' + S.settings.fs + '</span></div>';
    h += '<div class="pop-row fp-row"><b>彈窗</b>' +
      '<input id="fpPRange" type="range" min="14" max="30" value="' + (S.settings.pfs || 17) + '" step="1">' +
      '<span id="fpPVal" class="fp-val">' + (S.settings.pfs || 17) + '</span></div>';
    h += '<div class="fp-presets">';
    for (var i = 0; i < PRESETS.length; i++) {
      h += '<button class="tbtn fp-p' + (S.settings.fs === PRESETS[i].v ? ' on' : '') +
        '" data-fs="' + PRESETS[i].v + '">' + PRESETS[i].n + '</button>';
    }
    h += '</div>';
    h += '<div class="pop-row fp-row"><b>主題</b>' +
      '<span id="fpThemes" class="fp-themes"></span></div>';
    h += '<div class="pop-row fp-row"><b>行距</b>' +
      '<label class="fp-sw"><input type="checkbox" id="fpSpacious"' + (S.settings.spacious ? ' checked' : '') +
      '> 寬鬆行距</label></div>';
    h += '<div class="pop-act"><button class="btn-primary" id="fpDone">完成</button></div>';
    popShow(h);

    var rg = $('#fpRange'), vl = $('#fpVal');
    if (rg) rg.addEventListener('input', function () {
      S.settings.fs = +this.value;
      if (vl) vl.textContent = this.value;
      LS.set('ccs_set', S.settings);
      applySettings();
    });
    var prg = $('#fpPRange'), pvl = $('#fpPVal');
    if (prg) prg.addEventListener('input', function () {
      S.settings.pfs = +this.value;
      if (pvl) pvl.textContent = this.value;
      LS.set('ccs_set', S.settings);
      applySettings();
      var sb = $('#spPfs'); if (sb) sb.value = S.settings.pfs;
      var sv = $('#spPfsVal'); if (sv) sv.textContent = S.settings.pfs;
    });
    var ps = document.querySelectorAll('#popBody .fp-p');
    for (var k = 0; k < ps.length; k++) {
      ps[k].addEventListener('click', function () {
        S.settings.fs = +this.getAttribute('data-fs');
        LS.set('ccs_set', S.settings);
        applySettings();
        if (rg) rg.value = S.settings.fs;
        if (vl) vl.textContent = S.settings.fs;
        for (var m = 0; m < ps.length; m++) ps[m].classList.toggle('on', ps[m] === this);
      });
    }
    // 主题快捷：一排小色块，点一下就换
    var ftb = $('#fpThemes');
    if (ftb) {
      var hh = '';
      for (var qi = 0; qi < THEMES.length; qi++) {
        var qt = THEMES[qi];
        hh += '<button class="fp-theme' + (S.settings.palette === qt.id ? ' on' : '') +
          '" data-theme-id="' + qt.id + '" title="' + qt.n + '" style="background:' +
          qt.sw[0] + ';color:' + qt.sw[1] + '"><i style="background:' + qt.sw[1] + '"></i></button>';
      }
      ftb.innerHTML = hh;
      ftb.addEventListener('click', function (e) {
        var b = e.target.closest ? e.target.closest('[data-theme-id]') : null;
        if (!b) return;
        S.settings.palette = b.getAttribute('data-theme-id');
        LS.set('ccs_set', S.settings);
        applySettings();
        var all = ftb.querySelectorAll('[data-theme-id]');
        for (var i2 = 0; i2 < all.length; i2++) {
          all[i2].classList.toggle('on', all[i2].getAttribute('data-theme-id') === S.settings.palette);
        }
        toast('主題：' + themeById(S.settings.palette).n);
      });
    }    var sp = $('#fpSpacious');
    if (sp) sp.addEventListener('change', function () {
      S.settings.spacious = this.checked;
      LS.set('ccs_set', S.settings);
      applySettings();
    });
    var dn = $('#fpDone');
    if (dn) dn.addEventListener('click', popHide);
  }

  /* ============ 罗马字开关 ============ */
  function applyRo() {
    document.documentElement.classList.toggle('no-ro', !S.settings.ro);
    var b = $('#btnRo');
    if (b) b.classList.toggle('on', !!S.settings.ro);
  }
  function toggleRo() {
    S.settings.ro = !S.settings.ro;
    LS.set('ccs_set', S.settings);
    applyRo();
    toast(S.settings.ro ? '罗马字：開' : '罗马字：關');
  }

  var SPEEDS = [0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
  function cycleSpeed(btn) {
    var i = SPEEDS.indexOf(S.speed);
    if (i < 0) i = 3;
    i = (i + 1) % SPEEDS.length;
    S.speed = SPEEDS[i];
    P.playbackRate = S.speed;
    btn.textContent = S.speed.toFixed(2).replace(/0$/, '').replace(/\.$/, '.0') + '×';
  }

  // 隐藏字幕模式下，点一下某句临时显示（偷看）
  var peekTimer = null;
  function peekCue(el) {
    if (!el) return;
    el.classList.add('peek');
    if (peekTimer) clearTimeout(peekTimer);
    peekTimer = setTimeout(function () { el.classList.remove('peek'); }, 2200);
  }

  $('#search').addEventListener('input', function () {
    S.filter = this.value;
    applyFilter();
  });
  var btnSearch = $('#btnSearch'), btnSearchClose = $('#btnSearchClose');
  if (btnSearch) btnSearch.addEventListener('click', function () {
    var sb = $('#searchBar');
    if (!sb) return;
    sb.classList.remove('hidden');
    var i = $('#search');
    if (i) { i.focus(); i.select(); }
  });
  if (btnSearchClose) btnSearchClose.addEventListener('click', function () {
    var sb = $('#searchBar');
    if (sb) sb.classList.add('hidden');
    var i = $('#search');
    if (i) { i.value = ''; S.filter = ''; applyFilter(); i.blur(); }
  });
  var btnAa = $('#btnAa');
  if (btnAa) btnAa.addEventListener('click', showFontPanel);

  /* ============ 进度保存 ============ */
  setInterval(function () {
    if (!S.ep || !P.currentTime) return;
    if (Math.abs(P.currentTime - (S.prog[S.ep.id] || {}).t) < 1) return;
    S.prog[S.ep.id] = { t: Math.floor(P.currentTime), updated: Date.now() };
    LS.set('ccs_prog', S.prog);
  }, 3000);
  window.addEventListener('pagehide', function () {
    if (!S.ep) return;
    S.prog[S.ep.id] = { t: Math.floor(P.currentTime), updated: Date.now() };
    LS.set('ccs_prog', S.prog);
  });

  /* ============ 设置 ============ */
  migrateTheme();
  /* ============ 主题（明暗 × 配色） ============
   * data-theme  = light | dark   → 决定所有「深色系」专有样式（顶栏、toast、当前句…）
   * data-palette= sakura/night/paper/soft/bw/ink/green → 只覆盖配色变量
   * 墨水屏只有黑白灰：彩色主题在屏上会糊成灰，所以每个方案都标了实际效果。
   */
  var THEMES = [
    { id: 'sakura', n: '櫻花粉', dark: false, sw: ['#fdf5fb', '#e8619c', '#b23a48'],
      d: '亮環境｜彩屏是櫻花粉，墨水屏呈淺灰' },
    { id: 'night', n: '夜空紫', dark: true, sw: ['#17122a', '#ff8ab8', '#f2edfb'],
      d: '暗環境｜彩屏是夜空紫，墨水屏呈深灰' },
    { id: 'paper', n: '米黃', dark: false, sw: ['#f5eddb', '#b08a5a', '#453a2b'],
      d: '暖色護眼｜彩屏是米黃，墨水屏呈淺灰底' },
    { id: 'soft', n: '柔和灰', dark: false, sw: ['#e9e9e9', '#8a8a8a', '#3c3c3c'],
      d: '低對比｜長時間看不累，墨水屏是淺灰底深灰字' },
    { id: 'bw', n: '純黑白', dark: false, sw: ['#ffffff', '#000000', '#000000'],
      d: '最高對比｜墨水屏上最清楚（白底黑字）' },
    { id: 'ink', n: '黑底白字', dark: true, sw: ['#000000', '#ffffff', '#ffffff'],
      d: '反白｜暗環境最舒服，墨水屏上還省電' },
    { id: 'green', n: '綠字終端', dark: true, sw: ['#0e1710', '#86e08a', '#86e08a'],
      d: '老式 CRT 綠｜懷舊護眼' }
  ];
  function themeById(id) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return THEMES[i];
    return THEMES[0];
  }
  /** 旧设置迁移：theme=dark/high-contrast → 新配色 id */
  function migrateTheme() {
    if (S.settings.palette) return;
    var old = S.settings.theme;
    var wasHc = !!S.settings.hc;
    if (wasHc) S.settings.palette = (old === 'dark') ? 'ink' : 'bw';
    else S.settings.palette = (old === 'dark') ? 'night' : 'sakura';
    S.settings.hc = false;          // 旧的高对比已折进新配色，不再叠加
    LS.set('ccs_set', S.settings);
  }
  function applyTheme() {
    var t = themeById(S.settings.palette);
    document.documentElement.setAttribute('data-theme', t.dark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-palette', t.id);
  }
  function renderThemeGrid() {
    var g = $('#themeGrid');
    if (!g) return;
    var h = '';
    for (var i = 0; i < THEMES.length; i++) {
      var t = THEMES[i];
      var on = (S.settings.palette === t.id) ? ' on' : '';
      h += '<button class="theme-opt' + on + '" data-theme-id="' + t.id + '">' +
        '<span class="theme-sw" style="background:' + t.sw[0] + ';color:' + t.sw[1] + '">' +
        '<i style="background:' + t.sw[1] + '"></i>' +
        '<i style="background:' + t.sw[2] + '"></i></span>' +
        '<span>' + t.n + '</span></button>';
    }
    g.innerHTML = h;
    var t0 = themeById(S.settings.palette);
    var note = $('#themeNote');
    if (note) note.textContent = '目前：' + t0.d;
    var qt = $('#qTheme');
    if (qt) qt.textContent = t0.n;
  }

  function applySettings() {
    document.documentElement.style.setProperty('--fs', (S.settings.fs || 30) + 'px');
    document.documentElement.style.setProperty('--pfs', (S.settings.pfs || 17) + 'px');
    applyTheme();
    document.documentElement.classList.toggle('hc', !!S.settings.hc);
    document.documentElement.classList.toggle('spacious', !!S.settings.spacious);
    var af = $('#autoScroll'); if (af) af.checked = !!S.settings.auto;
    var ss = $('#spSync'); if (ss) ss.checked = !!S.settings.sp;
    var fv = $('#fontSize'); if (fv) fv.value = S.settings.fs;
    var fvv = $('#fontSizeVal'); if (fvv) fvv.textContent = S.settings.fs;
    var pv = $('#popFontSize'); if (pv) pv.value = S.settings.pfs || 17;
    var pvv = $('#popFontSizeVal'); if (pvv) pvv.textContent = S.settings.pfs || 17;
    renderThemeGrid();
    var spb = $('#btnSpacious'); if (spb) spb.textContent = S.settings.spacious ? '寬鬆：開' : '寬鬆：關';
    var tb = $('#btnTags'); if (tb) tb.textContent = S.settings.tags ? '開' : '關';
    var ab = $('#btnAudioMode');
    if (ab) {
      var am = S.settings.audio || 'auto';
      ab.textContent = am === 'auto' ? '自動' : (am === 'native' ? '原生' : '網頁');
    }
    applyRo();
    applyMode();
  }
  $('#fontSize').addEventListener('input', function () {
    S.settings.fs = +this.value; $('#fontSizeVal').textContent = this.value;
    LS.set('ccs_set', S.settings); applySettings();
  });
  var popFontSize = $('#popFontSize');
  if (popFontSize) popFontSize.addEventListener('input', function () {
    S.settings.pfs = +this.value;
    var v = $('#popFontSizeVal'); if (v) v.textContent = this.value;
    LS.set('ccs_set', S.settings); applySettings();
  });
  $('#autoScroll').addEventListener('change', function () {
    S.settings.auto = this.checked; LS.set('ccs_set', S.settings);
  });
  // 🔴🔴 2026-10-24 普通版「音字同步」开关
  //   开 = 跟音（sp 轨 = 电视播报稿，高亮 + 自动滚）
  //   关 = 阅读（web 轨 = 报纸原文，不高亮、不自动滚）
  //   ⇒ **立即生效**（不用重进播放页）：重算字幕流 + 重画 + 刷新模式指示
  var spSync = $('#spSync');
  if (spSync) spSync.addEventListener('change', function () {
    S.settings.sp = this.checked;
    LS.set('ccs_set', S.settings);
    if (S.ep) {
      S.subs = cuesOf(S.ep);
      S.idx = -1;
      renderTranscript();
      refreshVerBar(S.ep);
      syncCue();
    }
    toast(S.settings.sp ? '普通版：高亮同步（字幕不變）' : '普通版：靜默跟讀（字幕不變）');
  });
  function setFs(d) {
    var v = Math.max(18, Math.min(46, (S.settings.fs || 30) + d));
    if (v === S.settings.fs) return;
    S.settings.fs = v;
    LS.set('ccs_set', S.settings);
    applySettings();
    toast('字體 ' + v + 'px');
  }
  var btnFsUp = $('#btnFsUp'), btnFsDown = $('#btnFsDown');
  if (btnFsUp) btnFsUp.addEventListener('click', function () { setFs(2); });
  if (btnFsDown) btnFsDown.addEventListener('click', function () { setFs(-2); });
  var btnAudioMode = $('#btnAudioMode');
  if (btnAudioMode) btnAudioMode.addEventListener('click', function () {
    var order = ['auto', 'web', 'native'];
    var cur = S.settings.audio || 'auto';
    var next = order[(order.indexOf(cur) + 1) % order.length];
    S.settings.audio = next;
    LS.set('ccs_set', S.settings);
    btnAudioMode.textContent = next === 'auto' ? '自動' : (next === 'native' ? '原生' : '網頁');
    P.init();
    if (S.ep) { attachAudio(S.ep, P.currentTime); renderTranscript(); }
    toast('播放方式：' + (next === 'auto' ? '自動（原生優先，出聲自動切網頁）'
      : (next === 'native' ? '強制原生（可後台播放）' : '強制網頁（穩定，但切後台可能停）')));
  });

  var btnTags = $('#btnTags');
  if (btnTags) btnTags.addEventListener('click', function () {
    S.settings.tags = !S.settings.tags;
    LS.set('ccs_set', S.settings);
    btnTags.textContent = S.settings.tags ? '開' : '關';
    renderTranscript();
    toast(S.settings.tags ? '已開啟行內標記' : '已關閉行內標記（字幕更乾淨）');
  });

  var btnSpacious = $('#btnSpacious');
  if (btnSpacious) btnSpacious.addEventListener('click', function () {
    S.settings.spacious = !S.settings.spacious;
    LS.set('ccs_set', S.settings); applySettings();
    toast(S.settings.spacious ? '寬鬆行距：開' : '寬鬆行距：關');
  });
  /* 旧的單一主題按鈕已由 #themeGrid 取代（#btnTheme 從 HTML 裡也刪了） */
  $('#btnClear').addEventListener('click', function () {
    S.prog = {}; LS.set('ccs_prog', {}); renderList(); toast('已清除觀看記錄');
  });
  /* 首页快捷入口 */
  var btnStudy0 = $('#btnStudy');
  if (btnStudy0) btnStudy0.addEventListener('click', function () {
    showView('study');
    setStudyTab('f');
  });
  var btnEpSearchClose = $('#btnEpSearchClose');
  if (btnEpSearchClose) btnEpSearchClose.addEventListener('click', function () {
    var sb = $('#epSearchBar');
    if (sb) sb.classList.add('hidden');
    var i = $('#epSearch');
    if (i) { i.value = ''; S.epQuery = ''; renderList(); i.blur(); }
  });
  var epSearch = $('#epSearch');
  if (epSearch) epSearch.addEventListener('input', function () {
    S.epQuery = this.value;
    renderList();
  });

  function openSheet() {
    $('#sheet').classList.remove('hidden');
    $('#sheetMask').classList.remove('hidden');
    if (typeof refreshTtsUI === 'function') refreshTtsUI();
  }
  $('#btnMenu').addEventListener('click', openSheet);
  // 首页「🎨 主題」快捷入口：直接开设置面板（主题就在第一行）
  var btnThemeQuick = $('#btnThemeQuick');
  if (btnThemeQuick) btnThemeQuick.addEventListener('click', function () {
    openSheet();
    var g = $('#themeGrid');
    if (g && g.scrollIntoView) g.scrollIntoView({ block: 'center' });
  });
  function closeSheet() {
    $('#sheet').classList.add('hidden');
    $('#sheetMask').classList.add('hidden');
  }
  $('#btnSheetClose').addEventListener('click', closeSheet);
  $('#sheetMask').addEventListener('click', closeSheet);
  $('#btnBack').addEventListener('click', function () { showView('home'); });
  $('#btnContinue').addEventListener('click', function () {
    var lw = lastWatched();
    if (!lw) { toast('還沒有觀看記錄，先選一集吧'); return; }
    openEpisode(lw);
  });

  /* 复习卡片事件 */
  var rvCard = $('#rvCard');
  if (rvCard) rvCard.addEventListener('click', function (e) {
    if (e.target.closest('.rv-btn')) return;
    rvFlip();
  });
  var rvSay = $('#rvSay');
  if (rvSay) rvSay.addEventListener('click', function (e) {
    e.stopPropagation();
    var it = RV.q[RV.i];
    if (it) speakJa(it.ja);
  });
  var rvGood = $('#rvGood');
  if (rvGood) rvGood.addEventListener('click', function (e) { e.stopPropagation(); rvAnswer(true); });
  var rvBad = $('#rvBad');
  if (rvBad) rvBad.addEventListener('click', function (e) { e.stopPropagation(); rvAnswer(false); });
  var rvExit = $('#rvExit');
  if (rvExit) rvExit.addEventListener('click', function (e) { e.stopPropagation(); rvEnd(); });
  var rvAgain = $('#rvAgain');
  if (rvAgain) rvAgain.addEventListener('click', function (e) { e.stopPropagation(); rvStart(); });
  document.addEventListener('click', function (e) {
    var st = e.target.closest ? e.target.closest('#rvStartBtn') : null;
    if (st) { e.stopPropagation(); rvStart(); }
    var ex2 = e.target.closest ? e.target.closest('.rv-ex') : null;
    if (ex2 && ex2.getAttribute('data-ep')) {
      var ep = parseInt(ex2.getAttribute('data-ep'), 10);
      var ix = parseInt(ex2.getAttribute('data-i'), 10);
      if (ep) { rvEnd(); jumpToCue(ep, ix); }
    }
  });

  /* 播放页 / 菜单里直达「我的收藏」 */
  var btnMyVocab = $('#btnMyVocab');
  if (btnMyVocab) btnMyVocab.addEventListener('click', function () {
    closeSheet();
    showView('study');
    setStudyTab('f');
  });

  /* 主题网格：点一下就换 */
  var themeGrid = $('#themeGrid');
  if (themeGrid) themeGrid.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-theme-id]') : null;
    if (!b) return;
    S.settings.palette = b.getAttribute('data-theme-id');
    LS.set('ccs_set', S.settings);
    applySettings();
    var t = themeById(S.settings.palette);
    toast('主題：' + t.n);
  });

  /* ============ 音頻緩存 ============
   * 網頁版：Service Worker 把音頻存進 Cache Storage（重複聽不再下 12MB）
   * APK   ：原生層把音頻落盤到 App 私有目錄（離線也能聽）
   * 兩邊都能「全部刪除」，一次清空不佔手機空間。
   */
  function inApk() {
    return !!(window.Android && typeof window.Android.downloadedList === 'function');
  }

  /** 網頁版：列出 SW 裡緩存的音頻條目 */
  function swAudioKeys() {
    if (!window.caches) return Promise.resolve([]);
    return caches.keys().then(function (ks) {
      var jobs = ks.map(function (k) {
        return caches.open(k).then(function (c) { return c.keys(); }).catch(function () { return []; });
      });
      return Promise.all(jobs).then(function (arrs) {
        var out = [];
        arrs.forEach(function (a) {
          a.forEach(function (key) {
            try {
              if (/\.(m4a|mp3|aac)$/i.test(new URL(key.url).pathname)) out.push(key);
            } catch (e) { }
          });
        });
        return out;
      });
    }).catch(function () { return []; });
  }

  /** 逐個讀大小（串行，一次只留一個 Blob，避免記憶體峰值） */
  function sumCacheSizes(keys) {
    var total = 0, i = 0;
    function next() {
      if (i >= keys.length) return Promise.resolve(total);
      var key = keys[i++];
      return caches.match(key).then(function (r) {
        if (!r) return 0;
        return r.blob().then(function (b) { total += b.size; return 0; });
      }).catch(function () { return 0; }).then(next);
    }
    return next();
  }

  function fmtCache(n, bytes) {
    if (!n) return '未緩存';
    return n + ' 集 · ' + (bytes / 1048576).toFixed(1) + ' MB';
  }

  function refreshAudioCacheUI() {
    var el = $('#audioCacheVal');
    if (!el) return;
    if (inApk()) {
      try {
        var info = JSON.parse(window.Android.downloadedList() || '{}');
        el.textContent = fmtCache(info.count || 0, info.bytes || 0);
      } catch (e) { el.textContent = '—'; }
      refreshDlBtn();
      return;
    }
    swAudioKeys().then(function (keys) {
      if (!keys.length) { el.textContent = fmtCache(0, 0); return; }
      return sumCacheSizes(keys).then(function (b) { el.textContent = fmtCache(keys.length, b); });
    });
  }

  var btnClearAudio = $('#btnClearAudio');
  if (btnClearAudio) btnClearAudio.addEventListener('click', function () {
    if (inApk()) {
      if (!window.confirm('確定刪除所有已下載的音頻？\n（會釋放手機空間，已聽過的集需要重新下載）')) return;
      try {
        var r = JSON.parse(window.Android.clearDownloads() || '{}');
        toast(r.removed ? ('已刪除 ' + r.removed + ' 個音頻') : '沒有已下載的音頻');
      } catch (e) { toast('刪除失敗'); }
      refreshAudioCacheUI();
      return;
    }
    if (!window.caches) { toast('這個瀏覽器不支援離線緩存'); return; }
    if (!window.confirm('確定刪除所有已緩存的音頻？\n（會釋放空間，已聽過的集需要重新下載）')) return;
    var removed = 0;
    caches.keys().then(function (ks) {
      var jobs = [];
      ks.forEach(function (k) {
        jobs.push(caches.open(k).then(function (c) {
          return c.keys().then(function (keys) {
            keys.forEach(function (key) {
              if (/\/media\/ep\d+\.m4a$/.test(new URL(key.url).pathname)) {
                removed++;
                c.delete(key);
              }
            });
          });
        }));
      });
      return Promise.all(jobs);
    }).then(function () {
      toast(removed ? ('已刪除 ' + removed + ' 個緩存的音頻') : '沒有緩存的音頻');
      refreshAudioCacheUI();
    }).catch(function () { toast('刪除失敗'); });
  });
  refreshAudioCacheUI();

  /* ---------- 底欄 ⬇：把本集音頻存到手機 ---------- */
  function currentEpId() {
    var ep = S.ep;
    if (!ep) return '';
    // ⚠️ 用 ep.id（"ep01"），不是 ep.epId（那是 B 站 aid 86200）
    //    原生層按 /media/epNN.m4a 找本地緩存，兩邊必須是同一個號
    return String(ep.id || ('ep' + (ep.no < 10 ? '0' : '') + ep.no));
  }
  function epDownloaded(epId) {
    if (!inApk()) return false;
    try {
      var info = JSON.parse(window.Android.downloadedList() || '{}');
      var items = info.items || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].id === epId) return true;
      }
    } catch (e) { }
    return false;
  }
  function refreshDlBtn() {
    var b = $('#btnDl');
    var epId = currentEpId();
    var done = epId ? epDownloaded(epId) : false;
    if (b) {
      b.classList.toggle('done', done);
      b.textContent = done ? '✓ 已存' : '⬇ 下載';
      b.title = done ? '本集已下載到手機' : '把本集音頻存到手機';
    }
    // 彈窗裡那顆也同步（「⬇ 存本集」）
    var d2 = $('#spDl');
    if (d2) {
      d2.classList.toggle('done', done);
      d2.textContent = done ? '✓ 已存' : '⬇ 存本集';
    }
  }
  var dlTimer = null;

  /** 把當前這集音頻存到手機（底欄 ⬇ 和彈窗「⬇ 存本集」共用） */
  function downloadCurrentEp() {
    var ep = S.ep;
    if (!ep) { toast('先選一集'); return; }
    var epId = currentEpId();
    var urls = cdnCandidates(ep);
    if (!urls.length) { toast('沒有可用的音頻地址'); return; }
    var btnDl = $('#btnDl');

    if (inApk()) {
      if (epDownloaded(epId)) { toast('第' + ep.no + '話已經在手機裡了'); return; }
      window.Android.downloadAudio(absUrl(urls[0]), epId);
      toast('正在下載第' + ep.no + '話…');
      if (dlTimer) clearInterval(dlTimer);
      dlTimer = setInterval(function () {
        var st = {};
        try { st = JSON.parse(window.Android.downloadStatus() || '{}'); } catch (e) { }
        if (st.state === 'done') {
          clearInterval(dlTimer); dlTimer = null;
          toast('第' + ep.no + '話已下載到手機');
          refreshAudioCacheUI(); refreshDlBtn();
        } else if (st.state === 'error') {
          clearInterval(dlTimer); dlTimer = null;
          toast('下載失敗：' + (st.error || '未知錯誤'));
          refreshDlBtn();
        } else if (st.state === 'downloading' && st.progress) {
          var pct = Math.round(st.progress * 100) + '%';
          if (btnDl) btnDl.textContent = pct;
          var d2 = $('#spDl');
          if (d2) d2.textContent = pct;
        }
      }, 600);
      return;
    }

    // 網頁版：完整拉一遍 → Service Worker 順手存進 Cache Storage
    if (!window.caches) { toast('這個瀏覽器不支援離線緩存'); return; }
    toast('正在緩存第' + ep.no + '話…');
    fetch(urls[0]).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    }).then(function (b) {
      toast('已緩存 ' + (b.size / 1048576).toFixed(1) + ' MB');
      refreshAudioCacheUI();
      refreshDlBtn();
    }).catch(function (e) {
      toast('緩存失敗：' + String((e && e.message) || e));
      refreshDlBtn();
    });
  }

  var btnDl = $('#btnDl');
  if (btnDl) btnDl.addEventListener('click', downloadCurrentEp);

  /* ============ 日語語音包狀態（點詞朗讀靠它） ============ */
  function refreshTtsUI() {
    var el = $('#ttsStatus');
    if (!el) return;
    var st = ttsState();
    var vs = jaVoices();
    el.textContent = st === 'ok' ? ('已就緒（' + vs.length + ' 個日語語音）')
      : (st === 'no-api' ? '系統不支援' : '未安裝日語語音包');
    el.style.color = st === 'ok' ? 'var(--accent-2)' : 'var(--accent)';
  }
  var btnTtsGuide = $('#btnTtsGuide');
  if (btnTtsGuide) btnTtsGuide.addEventListener('click', function () { showTtsGuide('manual'); });
  if (window.speechSynthesis && window.speechSynthesis.addEventListener) {
    window.speechSynthesis.addEventListener('voiceschanged', refreshTtsUI);
  }
  refreshTtsUI();

  /* ============ 資源來源（APK：內置 / 線上 切換） ============ */
  function isLocalSource() {
    // ⚠️ 127.0.0.1 是 APK 的本機代理（伺服「線上」資源），不是內置 —— 別混為一談
    return location.protocol === 'file:'
      || location.hostname === 'appassets.androidplatform.net';
  }
  function refreshSrcButtons() {
    var local = isLocalSource();
    var a = $('#btnSrcLocal');
    if (a) {
      a.classList.toggle('on', local);
      a.textContent = local ? '已用內置' : '切到內置';
    }
  }
  var btnSrcLocal = $('#btnSrcLocal');
  if (btnSrcLocal) btnSrcLocal.addEventListener('click', function () {
    if (isLocalSource()) { toast('已在用內置資源'); return; }
    if (window.Android && typeof window.Android.loadOffline === 'function') {
      window.Android.loadOffline();
      toast('切換到內置資源');
    } else {
      toast('網頁版請直接刷新頁面');
    }
  });

  /* ============ 網站地址（換站 · 自動搬遷收藏） ============ */
  (function bindSiteAddr() {
    var inp = $('#inpSiteAddr');
    var btn = $('#btnSetSite');
    if (!inp || !btn) return;
    var isApp = !!(window.Android && typeof window.Android.getSite === 'function');
    if (isApp) {
      try { inp.value = window.Android.getSite() || location.origin + '/'; } catch (e) { }
    } else {
      inp.value = location.origin + '/';
      inp.readOnly = true;
      btn.textContent = '網頁版固定';
    }
    btn.addEventListener('click', function () {
      var v = (inp.value || '').trim();
      if (!v) { toast('請填網址'); return; }
      if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
      if (isApp) {
        try {
          window.Android.setSite(v);
          toast('正在換站，收藏會一起搬過去…');
          setTimeout(function () { location.reload(); }, 2600);
        } catch (e) { toast('換站失敗：' + e); }
      } else {
        location.href = v;
      }
    });
  })();

  /* ============ 版本切换条 ============ */
  Array.prototype.forEach.call(document.querySelectorAll('#verBar .vb'), function (b) {
    b.addEventListener('click', function () { setVersion(b.getAttribute('data-ver')); });
  });

  /* ============ 启动 ============ */
  P.init();
  bindPlayerEvents();
  applySettings();
  renderList();
  renderVocab();
  $('#epCount').textContent = EPISODES.length + ' 期';
  // 学习页
  var btnStudy = $('#btnStudy');
  if (btnStudy && !btnStudy0) btnStudy.addEventListener('click', function () { showView('study'); });
  function setStudyTab(tab) {
    S.studyTab = tab;
    Array.prototype.forEach.call(document.querySelectorAll('#studyTabs .tab'), function (x) {
      x.classList.toggle('on', x.getAttribute('data-tab') === tab);
    });
    renderStudy();
  }
  Array.prototype.forEach.call(document.querySelectorAll('#studyTabs .tab'), function (t) {
    t.addEventListener('click', function () {
      S.studyTab = t.getAttribute('data-tab');
      Array.prototype.forEach.call(document.querySelectorAll('#studyTabs .tab'), function (x) {
        x.classList.toggle('on', x === t);
      });
      renderStudy();
    });
  });
  var studySearch = $('#studySearch');
  if (studySearch) studySearch.addEventListener('input', function () {
    S.studyQ = this.value;
    renderStudy();
  });
  renderStudy();
  var mode = isLocalSource() ? '離線內建' : '線上版';
  $('#sheetNote').textContent = '魔卡少女樱／カードキャプターさくら（1998）' +
    '　日語原聲 + 簡體中文對照　·　' + mode + '　·　' + EPISODES.length + ' 集';
  $('#btnContinue').disabled = !lastWatched();
  refreshSrcButtons();
  // 已经装成 App 了就不用再显示下载入口
  var rowApk = $('#rowApk');
  if (rowApk && window.Android) rowApk.classList.add('hidden');

  // 深链：?ep=ep01 直接打开某集（方便分享/书签某一集）
  try {
    var qep = new URLSearchParams(location.search).get('ep');
    if (qep && EPISODES.some(function (e) { return e.id === qep; })) {
      openEpisode(qep, 0);
    }
  } catch (e) { /* 忽略 */ }

  // 离线版：禁止双指缩放，手感更接近原生 App
  document.addEventListener('gesturestart', function (e) { e.preventDefault(); }, { passive: false });

  // 网页版才注册 Service Worker（离线 APK 全部资源已内置）
  // APK 里不注册：WebView 的缓存跨版本升级保留，旧 SW 会一直喂旧 app.js
  if ('serviceWorker' in navigator && location.protocol === 'https:' &&
      location.hostname !== 'appassets.androidplatform.net' && !window.Android) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('sw.js?v=' + APP_VER).catch(function () {});
    });
  }

  // Android 返回键：从播放页退回剧集列表，没得退才退出 App
  window.__ccsHandleBack = function () {
    var player = document.querySelector('#viewPlayer');
    if (player && !player.classList.contains('hidden')) {
      showView('home');
      return true;
    }
    return false;
  };
})();
