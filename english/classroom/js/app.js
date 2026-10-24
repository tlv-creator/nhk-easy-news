/* =====================================================================
   课堂投影页 · 主体
   ---------------------------------------------------------------------
   老师要的是「投上去就能讲」⇒ 屏幕上只留**实在的东西**：
     1) 课文（英文，可一键切中文对照）
     2) 重点单词（词 + 释义 + 例句，一行一个，看得清）
     3) 翻译练习 + 参考答案 + 采分点讲解（同一屏，讲评时直接翻）
   ⇒ 刻意不做：封面大屏、考点卡片网格、提示 chip、遮罩块、学生书写区
     （那些是"好看"，不是"能上课"）

   投影场景的硬约束：
     · 教室后排要能看清 ⇒ 正文 34px 起
     · 答案默认**盖住** ⇒ 讲完再揭开（一个键）
     · 键盘翻屏 ⇒ 空格/→ 下一步，← 上一步，数字键跳屏
   ===================================================================== */
'use strict';

(function (global) {
  'use strict';

  var S = {
    screens: ['article', 'vocab', 'trans'],
    cur: 0,
    issue: null,
    index: null,
    date: '',
    showZh: false,        /* 课文中文对照：默认关（先读英文） */
    revealAnswer: false,  /* 参考译文：默认盖住 */
    practiceMode: 2,      /* 课堂速练：2 句 / 全部 */
    fontScale: 1,
    err: '',
    loading: true
  };

  /* ---------------- 工具 ---------------- */
  function $(s) { return document.querySelector(s); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  /* 🔴 内容里带 <b> <code>（采分点高亮）⇒ 白名单式放行 */
  function safeHtml(s) {
    var t = String(s == null ? '' : s);
    t = t.replace(/&(?!#?\w+;)/g, '&amp;');
    t = t.replace(/<(script|style|iframe|object|embed)[\s\S]*?<\/\1>/gi, '');
    t = t.replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '');
    t = t.replace(/javascript:/gi, '');
    return t;
  }
  function articleParas() {
    var a = (S.issue && S.issue.article) || {};
    return String(a.en || '').split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean);
  }
  function zhParas() {
    var a = (S.issue && S.issue.article) || {};
    return String(a.zh || '').split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean);
  }
  function vocabList() { return (S.issue && S.issue.vocab) || []; }
  function transData() { return (S.issue && S.issue.translation) || {}; }

  /* 🔴 课堂速练句：translation.classroom = [起始句号, 结束句号] */
  function sentences() {
    var t = transData();
    var ss = t.sentences || [];
    var cls = t.classroom;
    if (Array.isArray(cls) && cls.length === 2 && S.practiceMode === 2) {
      var picked = ss.filter(function (x) { return x.no >= cls[0] && x.no <= cls[1]; });
      if (picked.length) return picked;
    }
    return ss;
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    var root = $('#stage');
    if (!root) return;
    /* 🔴 --fs 必须设在 **body** 上：html/body 的 font-size 用它算，
       设在 #stage 上只影响它自己的子树 ⇒ 标题/控制条不跟着变（实测字号按钮无效） */
    document.body.style.setProperty('--fs', S.fontScale);
    if (S.loading) { root.innerHTML = '<div class="loading">正在取内容…</div>'; return; }
    if (S.err) {
      root.innerHTML = '<div class="err"><h2>内容没取到</h2><p>' + esc(S.err) + '</p></div>';
      return;
    }
    var html = '';
    if (S.cur === 0) html = screenArticle();
    else if (S.cur === 1) html = screenVocab();
    else html = screenTrans();
    root.innerHTML = html;
    renderChrome();
  }

  /* ---- 底部控制条 ---- */
  function renderChrome() {
    var c = $('#chrome');
    if (!c) return;
    var labels = ['课文', '单词', '翻译'];
    var html = '<div class="steps">';
    labels.forEach(function (s, i) {
      html += '<span class="step' + (i === S.cur ? ' on' : '') + '">' + (i + 1) + ' ' + s + '</span>';
    });
    html += '</div><div class="acts">';
    if (S.cur > 0) html += '<button class="btn" id="navPrev">←</button>';
    if (S.cur < S.screens.length - 1) html += '<button class="btn primary" id="navNext">下一步 →</button>';
    if (S.cur === 0) html += '<button class="btn" id="btnZh">' + (S.showZh ? '隐藏中文' : '显示中文') + '</button>';
    if (S.cur === 2) {
      html += '<button class="btn" id="btnReveal">' + (S.revealAnswer ? '盖住答案' : '显示答案') + '</button>';
      html += '<button class="btn" id="btnMode">' + (S.practiceMode === 2 ? '2 句' : '全部') + '</button>';
    }
    html += '<button class="btn" id="btnFont">字号</button>';
    html += '</div>';
    c.innerHTML = html;

    var p = $('#navPrev'); if (p) p.onclick = function () { go(S.cur - 1); };
    var n = $('#navNext'); if (n) n.onclick = function () { go(S.cur + 1); };
    var z = $('#btnZh'); if (z) z.onclick = function () { S.showZh = !S.showZh; render(); };
    var r = $('#btnReveal'); if (r) r.onclick = function () { S.revealAnswer = !S.revealAnswer; render(); };
    var m = $('#btnMode');
    if (m) m.onclick = function () {
      S.practiceMode = S.practiceMode === 2 ? 99 : 2;
      S.revealAnswer = false;
      render();
    };
    var f = $('#btnFont');
    if (f) f.onclick = function () {
      var steps = [0.85, 1, 1.15, 1.3, 1.5];
      var i = steps.indexOf(S.fontScale);
      if (i < 0) i = 1;
      S.fontScale = steps[(i + 1) % steps.length];
      render();
    };
  }

  /* ---------------- 屏 1：课文 ---------------- */
  function screenArticle() {
    var s = S.issue || {};
    var paras = articleParas();
    var zh = zhParas();

    var html = '<div class="screen">' +
      '<div class="head"><h2>' + esc(s.title || '') + '</h2>' +
      '<div class="head-meta">' + esc(s.date || S.date) + '</div></div>' +
      '<div class="article">';
    paras.forEach(function (p, i) {
      html += '<p class="para">' + safeHtml(p) + '</p>';
      if (S.showZh && zh[i]) html += '<p class="para zh">' + safeHtml(zh[i]) + '</p>';
    });
    html += '</div>';
    return html + '</div>';
  }

  /* ---------------- 屏 2：重点单词 ---------------- */
  function screenVocab() {
    var vs = vocabList();
    var html = '<div class="screen">' +
      '<div class="head"><h2>重点单词</h2></div>' +
      '<table class="vt"><tbody>';
    vs.forEach(function (v) {
      html += '<tr>' +
        '<td class="vw">' + esc(v.w) + '</td>' +
        '<td class="vpos">' + esc(v.pos || '') + '</td>' +
        '<td class="vcn">' + esc(v.cn || '') + '</td>' +
        '<td class="vex">' + (v.ex ? safeHtml(v.ex) : '') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  /* ---------------- 屏 3：翻译练习 + 答案 ---------------- */
  function screenTrans() {
    var t = transData();
    var ss = sentences();
    var html = '<div class="screen">' +
      '<div class="head"><h2>翻译练习</h2></div>';

    ss.forEach(function (s) {
      html += '<div class="q">' +
        '<div class="qcn">' + s.no + '. ' + safeHtml(s.cn) + '</div>';
      if (s.hints && s.hints.kw && s.hints.kw.length) {
        html += '<div class="qhint">提示：' + s.hints.kw.map(function (k) { return esc(k); }).join(' / ') +
          (s.hints.struct ? '　结构：' + esc(s.hints.struct) : '') + '</div>';
      }
      if (S.revealAnswer) {
        html += '<div class="qref">' + safeHtml(s.ref) + '</div>';
        if (s.pts && s.pts.length) {
          html += '<ul class="qpts">';
          s.pts.forEach(function (p) { html += '<li>' + safeHtml(p.t) + '</li>'; });
          html += '</ul>';
        }
      }
      html += '</div>';
    });
    return html + '</div>';
  }

  /* ---------------- 翻屏 ---------------- */
  function go(i) {
    if (i < 0) i = 0;
    if (i >= S.screens.length) i = S.screens.length - 1;
    S.cur = i;
    /* 🔴 离开翻译屏就收起答案（回到练习时不该还露着答案） */
    if (i !== 2) S.revealAnswer = false;
    render();
    document.body.scrollTop = 0;
  }

  /* ---------------- 键盘 ---------------- */
  function onKey(ev) {
    if (ev.target && /input|textarea/i.test(ev.target.tagName)) return;
    var k = ev.key;
    if (k === ' ' || k === 'ArrowRight' || k === 'PageDown') { ev.preventDefault(); go(S.cur + 1); }
    else if (k === 'ArrowLeft' || k === 'PageUp') { ev.preventDefault(); go(S.cur - 1); }
    else if (k === 'a' || k === 'A') { var b = $('#btnReveal'); if (b) b.click(); }
    else if (k === 'z' || k === 'Z') { var z = $('#btnZh'); if (z) z.click(); }
    else if (k >= '1' && k <= '3') { go(parseInt(k, 10) - 1); }
    else if (k === 'f' || k === 'F') { var f = $('#btnFont'); if (f) f.click(); }
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    CLS.getIndex().then(function (idx) {
      S.index = idx;
      var today = CLS.todayStr();
      var issues = idx.issues || [];
      var hasToday = issues.some(function (x) { return x.date === today; });
      var pick = today;
      if (!hasToday && issues.length) {
        pick = issues.map(function (x) { return x.date; }).sort().pop();
      }
      /* 🔴 支持 ?date=YYYY-MM-DD 手动指定（老师要上某一期 / 调试都靠它） */
      var qd = (location.search.match(/[?&]date=(\d{4}-\d{2}-\d{2})/) || [])[1];
      if (qd) pick = CLS.normDate(qd);
      S.date = pick;
      return CLS.getIssue(pick);
    }).then(function (issue) {
      S.issue = issue;
      S.loading = false;
      render();
    }).catch(function (e) {
      S.loading = false;
      S.err = (e && e.message) ? e.message : String(e);
      render();
    });
  }

  document.addEventListener('keydown', onKey);
  window.addEventListener('DOMContentLoaded', boot);

  global.CLSApp = { S: S, go: go, render: render, sentences: sentences, vocabList: vocabList, articleParas: articleParas };
})(typeof window !== 'undefined' ? window : this);
