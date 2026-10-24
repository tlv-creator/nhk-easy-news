/*!
 * Shun · AI 讲解（接入 ai-client.js v1.2 + 中转）
 *
 * 设计：
 *   · 第 5 段「⑤ AI 讲解（实时）」默认**收起**，点标题才展开
 *   · 展开后**不自动发请求** —— 用户点「問 AI」才发（省额度、不让人干等）
 *   · 额度小字：拿不到就整行隐藏（:empty 高度为 0，不占地方）
 *   · 失败自动降级成「複製提問」（把 system prompt + 上下文写进剪贴板）
 *   · **逐字注音完全不依赖模型**：渲染层用 App 自己的 ruby 表
 */
(function () {
  'use strict';

  var AI_ENDPOINT = 'https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai';
  var busy = false;
  var quotaTimer = null;

  function el(id) { return document.getElementById(id); }

  /* 🔴 toast 必须自己实现 —— app.js / shell.js 的 toast 都在各自的 IIFE 里，
     外部拿不到（直接调会 ReferenceError: toast is not defined）。 */
  var toastEl = null, toastTimer = null;
  function toast(msg, ms) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'shun-toast';
      toastEl.setAttribute('role', 'status');
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.style.display = 'block';
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      if (toastEl) toastEl.style.display = 'none';
    }, ms || 2200);
  }

  /* ---------- 初始化 ---------- */
  function init() {
    if (!window.AIC) return;
    window.AIC.config({
      endpoint: AI_ENDPOINT,
      timeout: 75000,           /* 中转上游 60s + 15s 余量 */
      readingLookup: function (w) { return window.ShunAI ? window.ShunAI.readingLookup(w) : ''; }
    });
    /* 额度小字：后台取一次，失败静默 */
    setTimeout(loadQuota, 600);
  }

  /* ---------- 额度 ---------- */
  function loadQuota() {
    if (!window.AIC || !window.AIC.quota) return;
    var box = el('exAiQuota');
    if (!box) return;
    try {
      window.AIC.quota().then(function (q) {
        var t = window.AIC.quotaText ? window.AIC.quotaText(q) : '';
        box.textContent = t || '';       /* 空 → :empty 整行高度 0 */
      }).catch(function () { box.textContent = ''; });
    } catch (e) { box.textContent = ''; }
  }

  /* ---------- 取当前句的上下文 ---------- */
  function currentCtx() {
    /* 🔴 不能用 window.S —— app.js 的 S 是 IIFE 局部变量，外部拿不到。
       走 app.js 暴露的受控只读接口 __shunCtx()。 */
    if (!window.__shunCtx) return null;
    var c = window.__shunCtx();
    if (!c) return null;
    var ja = window.ShunAI ? window.ShunAI.cleanLine(c.ja) : (c.ja || '');
    var ctx = window.ShunAI
      ? window.ShunAI.buildCtx({ lines: c.lines, index: c.index })
      : { prev: [], next: [], refs: [] };
    return {
      L: c.line,
      ja: ja,
      zh: c.zh || '',
      ep: c.epTitle || '',
      epNo: c.epNo || '',
      prev: ctx.prev, next: ctx.next, refs: ctx.refs
    };
  }

  /* ---------- 问 AI ---------- */
  function ask() {
    if (busy) return;
    var body = el('exAiBody');
    var btn = document.querySelector('[data-act="ai-ask"]');
    if (!body) return;
    var c = currentCtx();
    if (!c) { body.textContent = '这一行没有内容'; return; }

    busy = true;
    if (btn) btn.disabled = true;
    body.innerHTML = '<div class="ex-ai-wait">思考中… 已用 0 秒</div>';

    var payload = {
      scene: 'explain_sentence',
      title: 'Japanese with Shun',
      episode: c.epNo ? ('第 ' + c.epNo + ' 集') : c.ep,
      ja: c.ja,
      zh: c.zh,
      prev: c.prev,
      next: c.next,
      word: '',
      refs: c.refs
    };

    var opts = {
      onTick: function (sec) {
        var w = body.querySelector('.ex-ai-wait');
        if (w) w.textContent = '思考中… 已用 ' + sec + ' 秒';
      }
    };

    try {
      window.AIC.ask(payload, opts).then(function (text) {
        busy = false;
        if (btn) btn.disabled = false;
        var html = window.AIC.md(text);
        /* 🔴 逐字注音不依赖模型 —— 主渲染只显示 App 自己的 ruby。
           这里对模型输出里的假名做查表纠正（查不到就保留模型的）。 */
        try { html = window.AIC.fixReading(html); } catch (e) { }
        body.innerHTML = cleanAiHtml(html);
      }).catch(function (err) {
        busy = false;
        if (btn) btn.disabled = false;
        var msg = (err && (err.text || err.message)) || 'AI 暫時不可用';
        body.innerHTML = '<div class="ex-ai-err">' + escapeHtml(msg)
          + '<br><button class="mini-btn" data-act="ai-copy">複製提問（貼到 MiniMax 也一樣）</button></div>';
      });
    } catch (e) {
      busy = false;
      if (btn) btn.disabled = false;
      body.innerHTML = '<div class="ex-ai-err">AI 暫時不可用，可改用「複製提問」</div>';
    }
  }

  /* ---------- 复制提问（零后端降级）---------- */
  function copyAsk() {
    var c = currentCtx();
    if (!c) return;
    var payload = {
      scene: 'explain_sentence',
      title: 'Japanese with Shun',
      episode: c.epNo ? ('第 ' + c.epNo + ' 集') : c.ep,
      ja: c.ja, zh: c.zh,
      prev: c.prev, next: c.next, word: '', refs: c.refs
    };
    var text = window.AIC && window.AIC.buildPrompt ? window.AIC.buildPrompt(payload) : '';
    if (!text) { toast('複製失敗'); return; }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { toast('已複製，粘到 MiniMax 就能得到講解', 2800); },
          function () { fallbackCopy(text); });
      } else {
        fallbackCopy(text);
      }
    } catch (e) { fallbackCopy(text); }
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { }
    document.body.removeChild(ta);
    if (ok) toast('已複製，粘到 MiniMax 就能得到講解', 2800);
    else showCopyBox(text);
  }

  function showCopyBox(text) {
    var body = el('exAiBody');
    if (!body) return;
    body.innerHTML = '<div class="ex-ai-err">請長按下面文字全選複製：</div>'
      + '<textarea class="ex-ai-copy" readonly>' + escapeHtml(text) + '</textarea>';
  }

  /* ---------- 模型输出清洗（Shun 侧兜底，不改共享的 ai-client.js）---------- */
  /* 模型偶尔会吐两类垃圾：
   *   ① 孤立的方括号残片 `- 】：原句中的…` → **上游 ai-client.js 的 cleanOrphan 已处理**
   *   ② 幻觉英文 `- て：…此处连用"cling"式判断…` → **只在 Shun 侧处理**
   *      （按「方案 C」：共享层不碰，避免误伤 "tea"/"OK"/"NFC" 这类正当单词）
   */
  function cleanAiHtml(html) {
    var s = String(html || '');
    /* ① 孤儿括号残片 → **已上游处理**（ai-client.js v1.2 的 cleanOrphan），
     *    这里不再重复做（避免两层规则互相干扰）。 */
    /* ② 明显的幻觉英文单词（不在日语句子里、也不在常见词表里） */
    /* ② 幻觉英文单词（被引号包着的纯 ASCII 词，且不在日语术语允许表里）
     *    模型会写「此处连用"cling"式判断」这种鬼话 → 用「」括起来降级成「英文术语」 */
    var OK = {
      wa: 1, wo: 1, ka: 1, te: 1, to: 1, no: 1, ga: 1, ni: 1, de: 1,
      he: 1, ji: 1, ko: 1, ki: 1, mo: 1, na: 1, ra: 1, ri: 1, ru: 1,
      ma: 1, mi: 1, mu: 1, me: 1, ne: 1, ho: 1, se: 1, su: 1, so: 1,
      genki: 1, kana: 1, romaji: 1, hiragana: 1, katakana: 1
    };
    s = s.replace(/([\u300C\u300D\u300E\u300F\u201C\u201D\"])\s*([A-Za-z]{2,})\s*([\u300C\u300D\u300E\u300F\u201C\u201D\"])/g,
      function (m0, q1, w, q2) {
        if (OK[w.toLowerCase()] !== undefined) return m0;
        return q1 + '「' + w + '」' + q2;
      });
    return s;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>')
      .replace(/"/g, '"').replace(/'/g, '&#39;');
  }

  /* ---------- 展开 / 收起 ---------- */
  function toggleAiSec() {
    var box = el('exAiBox');
    var sec = el('exAiSec');
    if (!box) return;
    var open = box.classList.toggle('open');
    if (sec) sec.classList.toggle('open', open);
    if (open) {
      loadQuota();
      if (quotaTimer) clearTimeout(quotaTimer);
      quotaTimer = setTimeout(loadQuota, 60000);   /* 一分钟后刷新一次额度 */
    }
  }

  /* ---------- 暴露给 app.js 的事件委托 ---------- */
  window.ShunAIActions = {
    toggle: toggleAiSec,
    ask: ask,
    copy: copyAsk,
    init: init
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
