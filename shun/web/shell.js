/* ============================================================================
 * Hub Shell —— 四个日语精读项目共用的界面外壳（主题 + 字号 + 设置浮标）
 * ----------------------------------------------------------------------------
 * 模板来源：魔卡少女樱 v2.10
 *   · 7 套主题（明暗 × 配色 两层）：sakura/night/paper/soft/bw/ink/green
 *   · 字号 14~30，独立于字幕与弹窗两处
 *
 * 零侵入设计：不改动各项目自己的 DOM 结构，只在页面右下角加一个「設定」浮标，
 * 里面放主题网格 + 字号滑杆。主题/字号通过 CSS 变量作用于全页。
 *
 * 各项目怎么接：
 *   1) index.html 里 <link rel="stylesheet" href="../shell/shell.css">（自己 style.css 之后）
 *      + <script src="../shell/shell.js"></script>（自己 app.js 之前）
 *   2) <html data-shell-project="wakakusa">  ← 用来隔离各自的设置存储
 *   3) style.css 里把硬编码颜色换成 var(--sh-*) → 自动跟随主题
 *
 * 暴露的 API（项目可以调）：
 *   HubShell.setFs(n) / getFs() / setPalette(id) / getPalette() / toast(msg)
 * ========================================================================== */
(function () {
  'use strict';

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

  var doc = document;
  var root = doc.documentElement;

  /* ---- 项目标识：用于隔离各自的设置 ---- */
  var proj = root.getAttribute('data-shell-project') || (function () {
    var m = location.pathname.match(/\/([a-z]+)\/(?:index\.html)?$/i);
    return m ? m[1] : 'app';
  })();
  // 全局共享：一个人在任何一个 App 里选了「純黑白」，四个 App + 总目录都跟着变
  // （老花眼 + 墨水屏用户不可能愿意每个 App 单独调一次主题）
  var KEY = 'hubshell';

  var S = { palette: 'sakura', fs: 30, pfs: 17 };   // 规格书 §4.2：字幕正文默认 30px

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      if (raw) {
        var o = JSON.parse(raw);
        if (o && o.palette) S.palette = o.palette;
        if (o && o.fs) S.fs = o.fs;
        if (o && o.pfs) S.pfs = o.pfs;
      }
    } catch (e) { }
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { }
  }
  function themeById(id) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return THEMES[i];
    return THEMES[0];
  }

  /* ---- 应用主题 ---- */
  function applyTheme() {
    var t = themeById(S.palette);
    root.setAttribute('data-theme', t.dark ? 'dark' : 'light');
    root.setAttribute('data-palette', t.id);
    root.classList.toggle('sh-dark', !!t.dark);
    var n = doc.getElementById('shThemeNote');
    if (n) n.textContent = '目前：' + t.d;
    var q = doc.getElementById('shThemeName');
    if (q) q.textContent = t.n;
    var opts = doc.querySelectorAll('#shThemeGrid .sh-theme-opt');
    for (var i = 0; i < opts.length; i++) {
      opts[i].classList.toggle('on', opts[i].getAttribute('data-pid') === S.palette);
    }
  }

  /* ---- 应用字号 ---- */
  function applyFs() {
    root.style.setProperty('--sh-fs', S.fs + 'px');
    if (!HIDE_FS) root.style.setProperty('--fs', S.fs + 'px');
    root.style.setProperty('--sh-pfs', S.pfs + 'px');
    root.style.setProperty('--pfs', S.pfs + 'px');
    var r = doc.getElementById('shFsRange');
    if (r) r.value = S.fs;
    var v = doc.getElementById('shFsVal');
    if (v) v.textContent = S.fs;
    var pr = doc.getElementById('shPfsRange');
    if (pr) pr.value = S.pfs;
    var pv = doc.getElementById('shPfsVal');
    if (pv) pv.textContent = S.pfs;
  }

  // 总目录这类页面有自己的字号尺度（data-shell-hide-fs），shell 不接管
  var HIDE_FS = root.hasAttribute('data-shell-hide-fs');
  function setFs(n) {
    n = Math.max(14, Math.min(40, Math.round(n)));
    S.fs = n; save(); applyFs();
    // 通知项目（若项目自己管字号，让它也更新显示）
    try { if (typeof window.onHubShellFs === 'function') window.onHubShellFs(n); } catch (e) { }
  }
  /* 弹窗字号是**独立**的一档（讲解窗要能单独放大） */
  function setPfs(n) {
    n = Math.max(14, Math.min(30, Math.round(n)));
    S.pfs = n; save(); applyFs();
    try { if (typeof window.onHubShellPfs === 'function') window.onHubShellPfs(n); } catch (e) { }
  }
  function setPalette(id) {
    S.palette = id; save(); applyTheme();
    toast('主题：' + themeById(id).n);
  }

  /* ---- toast ---- */
  var toastEl = null, toastTimer = null;
  function toast(msg) {
    if (!toastEl) {
      toastEl = doc.createElement('div');
      toastEl.className = 'sh-toast';
      doc.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add('on');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('on'); }, 1900);
  }

  /* ---- 注入设置浮标 + 面板 ---- */
  var FAB_ID = 'shFab', SHEET_ID = 'shSheet';
  function buildUI() {
    if (!doc.body) return;
    if (!doc.getElementById(SHEET_ID)) {
      var sheet = doc.createElement('div');
      sheet.id = SHEET_ID;
      sheet.className = 'sh-sheet';
      sheet.setAttribute('hidden', '');
      sheet.innerHTML =
        '<button class="sh-close" id="shClose" aria-label="关闭">✕</button>' +
        '<h3>設定</h3>' +
        '<div class="sh-sheet-row"><span>主題</span>' +
        '<span class="sh-tbtn on" id="shThemeName" style="margin-left:auto">櫻花粉</span></div>' +
        '<div class="sh-theme-grid" id="shThemeGrid"></div>' +
        '<div class="sh-sheet-note" id="shThemeNote"></div>' +
        (HIDE_FS ? '' :
        '<div class="sh-sheet-row" style="margin-top:10px"><span>字幕字號</span>' +
        '<button class="sh-tbtn" id="shFsMinus">A−</button>' +
        '<input class="sh-range" id="shFsRange" type="range" min="14" max="40" step="1">' +
        '<button class="sh-tbtn" id="shFsPlus">A＋</button></div>') +
        (HIDE_FS ? '' :
        '<div class="sh-sheet-row"><span>彈窗字號</span>' +
        '<button class="sh-tbtn" id="shPfsMinus">A−</button>' +
        '<input class="sh-range" id="shPfsRange" type="range" min="14" max="30" step="1">' +
        '<button class="sh-tbtn" id="shPfsPlus">A＋</button></div>') +
        '<div class="sh-sheet-note">墨水屏用「純黑白」或「黑底白字」最清楚。</div>';
      doc.body.appendChild(sheet);

      var g = doc.getElementById('shThemeGrid');
      var h = '';
      for (var i = 0; i < THEMES.length; i++) {
        var t = THEMES[i];
        h += '<button class="sh-theme-opt" data-pid="' + t.id + '">' +
          '<span class="sh-theme-sw" style="background:' + t.sw[0] + ';color:' + t.sw[1] + '">' +
          '<i style="background:' + t.sw[1] + '"></i>' +
          '<i style="background:' + t.sw[2] + '"></i></span>' +
          '<span>' + t.n + '</span></button>';
      }
      g.innerHTML = h;
      g.addEventListener('click', function (e) {
        var b = e.target.closest ? e.target.closest('[data-pid]') : null;
        if (b) setPalette(b.getAttribute('data-pid'));
      });
      doc.getElementById('shClose').onclick = closeSheet;
      var fr = doc.getElementById('shFsRange');
      if (fr) fr.oninput = function () { setFs(+this.value); };
      var fm = doc.getElementById('shFsMinus');
      if (fm) fm.onclick = function () { setFs(S.fs - 1); };
      var fp = doc.getElementById('shFsPlus');
      if (fp) fp.onclick = function () { setFs(S.fs + 1); };
      var pfsR = doc.getElementById('shPfsRange');
      if (pfsR) pfsR.oninput = function () {
        setPfs(+this.value);
      };
      var pfsM = doc.getElementById('shPfsMinus');
      if (pfsM) pfsM.onclick = function () {
        setPfs(S.pfs - 1);
      };
      var pfsP = doc.getElementById('shPfsPlus');
      if (pfsP) pfsP.onclick = function () {
        setPfs(S.pfs + 1);
      };
    }
    if (!doc.getElementById(FAB_ID)) {
      var fab = doc.createElement('button');
      fab.id = FAB_ID;
      fab.className = 'sh-fab';
      fab.textContent = '設定';
      fab.setAttribute('aria-label', '設定（主題 / 字號）');
      fab.onclick = function () {
        var s = doc.getElementById(SHEET_ID);
        if (s.hasAttribute('hidden')) { s.removeAttribute('hidden'); }
        else { s.setAttribute('hidden', ''); }
      };
      doc.body.appendChild(fab);
    }
  }
  function closeSheet() {
    var s = doc.getElementById(SHEET_ID);
    if (s) s.setAttribute('hidden', '');
  }

  /* ---- 接管项目自己的字号控件 ----
   * 各项目的字号实现不一样：有的用 range，有的用 [data-fs="2"] 按钮，有的用 A−/A＋。
   * 这里做通用接管：谁改了字号，shell 都知道并同步。 */
  function hookFontControls() {
    if (HIDE_FS) return;
    // range 输入（**排除自己面板里的滑杆** —— 那是弹窗字号，不是字幕字号）
    var ranges = doc.querySelectorAll('input[type=range]');
    for (var i = 0; i < ranges.length; i++) {
      (function (el) {
        if (el.__shHooked) return;
        if (el.closest && el.closest('#shSheet')) return;   // ← 修：shell 自己的弹窗字号滑杆
        // 项目自己管字号的滑杆：data-shell-pfs（弹窗字号）/ data-shell-nofs（根本不是字号，比如进度条）
        if (el.hasAttribute('data-shell-pfs') || el.hasAttribute('data-shell-nofs')) return;
        el.__shHooked = true;
        var mn = +el.min || 14, mx = +el.max || 30;
        if (el.value) { S.fs = Math.max(mn, Math.min(mx, +el.value)); }
        el.addEventListener('input', function () {
          S.fs = Math.max(mn, Math.min(mx, +el.value)); save(); applyFs();
        });
      })(ranges[i]);
    }
    // [data-fs="2"] / [data-fs="-2"] 这样的加减按钮
    var btns = doc.querySelectorAll('[data-fs]');
    for (var j = 0; j < btns.length; j++) {
      (function (b) {
        if (b.__shHooked) return;
        b.__shHooked = true;
        b.addEventListener('click', function () {
          setFs(S.fs + (+b.getAttribute('data-fs') || 0));
        });
      })(btns[j]);
    }
  }

  /* ---- 启动 ---- */
  function boot() {
    load();
    applyTheme();
    applyFs();
    buildUI();
    hookFontControls();
    // 主题/字号变化时，让项目自己的控件也跟着（项目可能重绘 DOM）
    setInterval(hookFontControls, 1500);
  }

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.HubShell = {
    setFs: setFs, getFs: function () { return S.fs; },
    setPfs: setPfs, getPfs: function () { return S.pfs; },
    setPalette: setPalette, getPalette: function () { return S.palette; },
    toast: toast, THEMES: THEMES, closeSheet: closeSheet
  };
})();
