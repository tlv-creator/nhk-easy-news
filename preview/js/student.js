/* =====================================================================
   四级精读日报 · 学生端
   ---------------------------------------------------------------------
   四块内容：登录 / 今日短文+生词 / 翻译练习（4 小节）/ 理解题
   交互沿用老师已认可的「四级翻译讲练单」：
     采分表达卡（点开看说明）→ 逐句自译（词数+进度条）→
     采分点自查（命中/漏掉）→ 逐句对照讲评（点开看参考译文）
   🔴 所有答案默认隐藏，点开才注入 DOM（页面加载后的可见文本里没有答案）
   ===================================================================== */
(function (global) {
  'use strict';

  var SKEY = 'cet4.session';

  /* =====================================================================
     一、纯函数区（自检脚本 node 直接调用，不碰 DOM）
     ===================================================================== */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function stripTags(s) { return String(s || '').replace(/<[^>]+>/g, ''); }
  function wordCount(s) {
    return (String(s || '').trim().match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
  }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  // 进度文案：句数随模式变（2 句速练 / 5 句全练），所以不能写死
  function progressLabel(n, total) {
    total = total || 5;
    if (n <= 0) return '尚未开始自译 · 先做译前准备';
    if (n >= total) return total + ' 句全部完成 · 去交卷看档位';
    return '完成 ' + n + ' 句 · 还剩 ' + (total - n) + ' 句';
  }

  // 覆盖率评语（三档）
  function commentOf(pct) {
    if (pct >= 80) return '采分点覆盖良好。剩下的时间请检查单复数、冠词和时态一致性——这些是隐性扣分点。';
    if (pct >= 50) return '覆盖了一半左右。展开「逐句对照讲评」逐条核对标了「漏掉」的表达，再重写一遍。';
    return '采分点覆盖偏低。先别急着改稿——回到「采分表达表」把表达看熟，再重写。';
  }

  // 采分点自查：用 exprs[i].re 逐条匹配 5 句拼起来的文本
  function checkPoints(exprs, blob) {
    exprs = exprs || [];
    var text = String(blob || '');
    var items = [], hits = 0;
    for (var i = 0; i < exprs.length; i++) {
      var ok = false;
      try { ok = new RegExp(exprs[i].re, 'i').test(text); } catch (e) { ok = false; }
      if (ok) hits++;
      items.push({ i: i, no: i + 1, cn: exprs[i].cn, en: exprs[i].en, ok: ok });
    }
    var total = exprs.length;
    var pct = total ? Math.round(hits / total * 100) : 0;
    return { hits: hits, total: total, pct: pct, items: items, comment: commentOf(pct) };
  }

  /* ---------------------------------------------------------------------
     练习状态机
     🔴 2026-10-24 改造：
       · 句数不再写死 5 —— 「课堂速练 2 句」和「考场全练 5 句」共用同一份草稿
       · 草稿按**句号**存（store），切模式不丢稿（学生切去全练再切回来不用重写）
       · 打分交给 CET4Score.score()（机器初判 = 信息完整度 + 档位区间）
     --------------------------------------------------------------------- */
  function createPractice(opts) {
    opts = opts || {};
    var p = {
      exprs: opts.exprs || [],
      sentences: opts.sentences || [],
      sentNos: opts.sentNos || (opts.sentences || []).map(function (s, i) { return s.no || (i + 1); }),
      store: opts.store || {},          // {句号: 文本} 跨模式共用
      checkedSig: null,
      checked: null,
      signature: function () {
        var out = [], i;
        for (i = 0; i < this.sentNos.length; i++) out.push(String(this.store[this.sentNos[i]] || ''));
        return out.join('\n---\n');
      },
      setDraft: function (no, text) {
        var before = this.signature();
        this.store[no] = text == null ? '' : String(text);
        var after = this.signature();
        var invalidated = false;
        if (this.checkedSig !== null && before !== after) {
          this.checkedSig = null;
          this.checked = null;
          invalidated = true;
        }
        return { changed: before !== after, invalidated: invalidated };
      },
      filledCount: function () {
        var n = 0, i;
        for (i = 0; i < this.sentNos.length; i++) {
          if (wordCount(this.store[this.sentNos[i]]) > 0) n++;
        }
        return n;
      },
      blob: function () {
        var out = [], i;
        for (i = 0; i < this.sentNos.length; i++) out.push(String(this.store[this.sentNos[i]] || ''));
        return out.join(' ');
      },
      check: function () {
        var res = global.CET4Score
          ? global.CET4Score.score({ sentences: this.sentences, exprs: this.exprs, drafts: this.store })
          : checkPoints(this.exprs, this.blob());
        this.checked = res;
        this.checkedSig = this.signature();
        return res;
      },
      isStale: function () {
        return this.checkedSig !== null && this.checkedSig !== this.signature();
      }
    };
    return p;
  }

  /* ---------- 各小节的 HTML 片段（答案一律不出现，除了显式 reveal） ---------- */

  // 采分表达卡：默认显示 cn + en，note（用法说明）不出现
  function exprCardHTML(expr, i, open) {
    return '<button type="button" class="exp" data-i="' + i + '" aria-expanded="' + (open ? 'true' : 'false') + '">'
      + '<span class="flag' + (open ? ' on' : '') + '">' + (open ? '已展开' : '用法') + '</span>'
      + '<span class="cn">' + esc(expr.cn) + '</span>'
      + '<span class="en">' + esc(expr.en) + '</span>'
      + (open ? '<span class="note"><b>用法说明：</b>' + esc(expr.note) + '</span>' : '')
      + '</button>';
  }
  function exprNoteHTML(expr) {
    return '<span class="note"><b>用法说明：</b>' + esc(expr.note) + '</span>';
  }

  // 逐句自译：中文原句 + 三步提示（默认收起，点开才注入）
  function hintsHTML(h) {
    h = h || {};
    var kws = (h.kw || []).map(function (k) { return '<span class="kwchip">' + esc(k) + '</span>'; }).join('');
    return '<div class="step"><span class="sn">1</span><span><b>关键短语：</b>' + (kws || '—') + '</span></div>'
      + '<div class="step"><span class="sn">2</span><span><b>句式骨架：</b><span class="struct">' + esc(h.struct || '') + '</span></span></div>'
      + '<div class="step"><span class="sn">3</span><span><b>本句目标：</b>' + esc(h.goal || '') + '</span></div>';
  }
  function draftBlockHTML(sent, n) {
    return '<div class="draft">'
      + '<div class="draft-h"><b>第 ' + n + ' 句</b>'
      + '<span class="hint">' + sent.cn + '</span>'
      + '<span class="cnt" id="cnt' + n + '">0 词</span></div>'
      + '<div class="draft-steps" id="steps' + n + '"></div>'
      + '<textarea id="ta' + n + '" rows="4" placeholder="在这里写下你的译文…" aria-label="第 ' + n + ' 句译文"></textarea>'
      + '<div class="draft-foot">'
      + '<button type="button" class="sm ghost" id="hintBtn' + n + '">看三步提示</button>'
      + '<span class="draft-note" id="note' + n + '">提示：' + esc((sent.hints && sent.hints.goal) || '') + '</span>'
      + '</div></div>';
  }

  // 逐句对照讲评：默认只给中文原句；参考译文 / 采分点拆解点开才注入
  function segItemHTML(sent) {
    return '<div class="seg" data-n="' + sent.no + '" role="button" tabindex="0">'
      + '<div class="seg-top"><span class="seg-no">' + sent.no + '</span>'
      + '<span class="seg-cn">' + sent.cn + '</span>'
      + '<span class="seg-arrow">展开</span></div>'
      + '<div class="seg-body"></div></div>';
  }
  function segRevealHTML(sent) {
    var pts = (sent.pts || []).map(function (p, i) {
      return '<div class="pt"><span class="n">' + (i + 1) + '</span><div class="d">' + p.t + '</div></div>';
    }).join('');
    return '<div class="ref"><div class="lbl">参考译文</div><p>' + esc(sent.ref) + '</p></div>'
      + '<div class="tp"><div class="tp-h">采分点拆解 · ' + (sent.pts || []).length + ' 个</div>' + pts + '</div>';
  }

  // 生词卡：默认只有单词 + 词性
  function vocabCardHTML(v, i, open) {
    // 🔴 四级词汇标签：core/secondary = 四级；beyond = 超纲（大纲里没有）
    var lv = v.cet4 || '';
    var lvHtml = lv ? ('<span class="lv' + (lv === 'beyond' ? ' lv2' : '') + '">'
      + (lv === 'beyond' ? '超纲' : '四级') + '</span>') : '';
    return '<button type="button" class="vc' + (open ? ' open' : '') + '" data-i="' + i + '" aria-expanded="' + (open ? 'true' : 'false') + '">'
      + '<span class="w">' + esc(v.w) + '</span><span class="p">' + esc(v.pos) + '</span>' + lvHtml
      + (open ? '<span class="d"><span class="cn">' + esc(v.cn) + '</span>'
        + '<span class="ex">' + esc(v.ex) + '</span>'
        + (v.note ? '<span class="note">' + esc(v.note) + '</span>' : '') + '</span>' : '')
      + '</button>';
  }

  // 短文：英文在前，中文对照默认收起
  function articleHTML(article) {
    var en = String(article.en || '').split(/\n{2,}/).map(function (p) {
      return '<p>' + esc(p.trim()) + '</p>';
    }).join('');
    var zh = String(article.zh || '').split(/<br\s*\/?>\s*<br\s*\/?>|<br\s*\/?>/i).map(function (p) {
      return '<p>' + p + '</p>';
    }).join('');
    return { en: en, zh: zh };
  }

  // 采分点标记的三态：命中（反白）/ 漏掉（虚线框）/ 未定
  function kwClassOf(res, i) {
    if (res && res.items && res.items[i]) return res.items[i].ok ? 'hit' : 'miss';
    return '';
  }
  // 「展开全部 N 个采分表达 / 收起全部 N 个说明」
  function expandAllLabel(exprs, openMap) {
    var n = (exprs || []).length;
    var allOpen = n > 0, i;
    for (i = 0; i < n; i++) if (!openMap || !openMap[i]) { allOpen = false; break; }
    return allOpen ? ('收起全部 ' + n + ' 个说明') : ('展开全部 ' + n + ' 个采分表达');
  }

  global.CET4UI = {
    esc: esc, stripTags: stripTags, wordCount: wordCount,
    progressLabel: progressLabel, commentOf: commentOf,
    checkPoints: checkPoints, createPractice: createPractice,
    exprCardHTML: exprCardHTML, exprNoteHTML: exprNoteHTML,
    hintsHTML: hintsHTML, draftBlockHTML: draftBlockHTML,
    segItemHTML: segItemHTML, segRevealHTML: segRevealHTML,
    vocabCardHTML: vocabCardHTML, articleHTML: articleHTML,
    kwClassOf: kwClassOf, expandAllLabel: expandAllLabel
  };

  /* =====================================================================
     二、DOM 部分
     ===================================================================== */
  if (!global.document || !global.document.getElementById) return;

  /* 🔴 找不到元素时返回一个**哑对象**而不是 null：
     真实事故：student.js 引用了 3 个页面里不存在的 id（issueTopic 等）
     ⇒ renderIssue() 第一行就 TypeError ⇒ 整个内容区全空（学生端直接不能用）
     ⇒ 哑对象让渲染继续跑完，同时把问题记到 console，闸门会另外拦。 */
  var $ = function (id) {
    var el = document.getElementById(id);
    if (!el) { try { console.warn('[cet4] 页面缺少元素 #' + id); } catch (e) { } }
    return el || { textContent: '', innerHTML: '', value: '', classList: { add: function () { }, remove: function () { }, toggle: function () { }, contains: function () { return false; } }, hidden: true, style: {}, setAttribute: function () { }, getAttribute: function () { return null; }, appendChild: function () { }, addEventListener: function () { }, querySelector: function () { return null; }, querySelectorAll: function () { return []; }, focus: function () { }, scrollIntoView: function () { }, dataset: {} };
  };
  var me = null;              // {token,name,student_id,class_key,class_name}
  var issue = null;           // 当前这一期
  var practice = null;        // createPractice(exprs)
  var vocabOpen = {};         // {index:true}
  var beyondOpen = false;     // 超纲词区是否展开
  var exprOpen = {};          // {index:true}
  var segOpen = {};           // {no:true}
  var manualKw = {};          // 学生自己点的采分点标记
  var browse = { last: 0, sec: 0, sent: 0 };

  /* =====================================================================
     三、翻译练习模式（2026-10-24 新增）
     ---------------------------------------------------------------------
     · quick = 课堂速练（本期指定的 2 句）→ 课上 10 分钟能做完
     · full  = 考场全练（全部 5 句）→ 完整 15 分题节奏
     🔴 草稿按**句号**存在 draftStore 里，切模式不丢稿。
     ===================================================================== */
  var transMode = 'quick';
  var draftStore = {};        // {句号: 文本} 跨模式共用
  var LIMIT_SEC = 600;        // 限时 10 分钟
  var timerLeft = LIMIT_SEC;
  var timerPaused = false;
  var timerHandle = null;

  function allSentences() { return (issue && issue.translation && issue.translation.sentences) || []; }
  function modeSentences() {
    var ss = allSentences();
    if (transMode === 'full') return ss;
    var pick = (issue && issue.translation && issue.translation.classroom) || [];
    var map = {}, out = [], i;
    for (i = 0; i < ss.length; i++) map[ss[i].no] = ss[i];
    for (i = 0; i < pick.length; i++) if (map[pick[i]]) out.push(map[pick[i]]);
    if (!out.length) out = ss.slice(0, 2);
    return out;
  }
  function modeExprs() {
    var nos = modeSentences().map(function (s) { return s.no; });
    return ((issue && issue.translation && issue.translation.exprs) || []).filter(function (e) {
      return e.sno == null || e.sno === 0 || nos.indexOf(e.sno) >= 0;
    });
  }
  function rebuildPractice() {
    var sents = modeSentences();
    practice = CET4UI.createPractice({
      exprs: modeExprs(), sentences: sents, store: draftStore
    });
    manualKw = {};
    exprOpen = {};
    segOpen = {};
    $('expCount').textContent = modeExprs().length;
    $('draftCount').textContent = '本次练习 ' + sents.length + ' 句 · 共 ' + modeExprs().length + ' 个采分表达';
    $('pgTotal').textContent = '/ ' + sents.length + ' 句';
    var note = $('modeNote');
    if (note) {
      note.textContent = transMode === 'quick'
        ? '课上 10 分钟能做完的一组（' + sents.length + ' 句）'
        : '完整 15 分题节奏（' + sents.length + ' 句，约 20 分钟）';
    }
    renderExprs();
    renderDrafts();
    renderKws();
    renderSegs();
    updateProgress();
    invalidateCheck(true);
  }
  function switchMode(m) {
    if (m === transMode) return;
    transMode = m;
    var btns = document.querySelectorAll('#modeSwitch .mbtn');
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle('on', btns[i].getAttribute('data-mode') === m);
    }
    rebuildPractice();
    toast(m === 'quick' ? '已切到课堂速练（2 句）' : '已切到考场全练（5 句）');
  }

  /* ---------------- 限时 ---------------- */
  function fmtClock(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function paintTimer() {
    var el = $('timerLeft');
    if (!el) return;
    el.textContent = timerLeft <= 0 ? '00:00 · 时间到' : fmtClock(timerLeft);
    el.classList.toggle('over', timerLeft <= 0);
  }
  function tickTimer() {
    if (timerPaused) return;
    if (timerLeft > 0) {
      timerLeft--;
      if (timerLeft === 0) toast('时间到 —— 先交卷看档位，别硬改');
    }
    paintTimer();
  }
  function startTimerLoop() {
    if (timerHandle) clearInterval(timerHandle);
    timerHandle = setInterval(tickTimer, 1000);
    paintTimer();
  }
  function resetTimer() {
    timerLeft = LIMIT_SEC;
    timerPaused = false;
    var b = $('btnTimer');
    if (b) b.textContent = '暂停';
    paintTimer();
  }

  function toast(msg) {
    var t = $('toast');
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(t._tm);
    t._tm = setTimeout(function () { t.hidden = true; }, 2600);
  }
  function setMsg(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'msg' + (kind ? ' ' + kind : '');
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    if (CET4.isDemo()) $('demoBanner').hidden = false;
    CET4.prefs.applyAll();
    // 🔴 预取四级词库：不然学生第一次交卷会看到「不完整的语法结果」（词库还在路上）
    loadLexicon();
    bindPrefs();
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(SKEY) || 'null'); } catch (e) { saved = null; }
    if (saved && saved.token) {
      me = saved;
      showHome();
      loadIssue();
    } else {
      showLogin();
    }
    startTimer();
  }

  function showLogin() {
    $('viewLogin').hidden = false;
    $('viewHome').hidden = true;
    $('progress').hidden = true;
    $('btnLogout').hidden = true;
    $('who').textContent = '';
  }
  function showHome() {
    $('viewLogin').hidden = true;
    $('viewHome').hidden = false;
    $('progress').hidden = false;
    $('btnLogout').hidden = false;
    var w = $('who');
    w.textContent = '';
    var b = document.createElement('b');
    b.textContent = me.name || '';
    w.appendChild(b);
    // 🔴 自测号：显示原始学号，别把内部前缀露给用户
    w.appendChild(document.createTextNode(' ' + (me.display_id || me.student_id || '') +
      (me.is_test ? '（自测）' : '') + ' · ' + (me.class_name || '')));
  }
  function logout() {
    me = null;
    try { localStorage.removeItem(SKEY); } catch (e) { /* ignore */ }
    showLogin();
  }

  /* ---------------- 登录 ---------------- */
  function doLogin() {
    var sid = $('sid').value.trim();
    var nm = $('sname').value.trim();
    if (!sid || !nm) { setMsg($('loginMsg'), '请把学号和姓名都填上', 'err'); return; }
    $('btnLogin').disabled = true;
    setMsg($('loginMsg'), '正在核对名册…');
    CET4.studentLogin(sid, nm).then(function (r) {
      $('btnLogin').disabled = false;
      if (!r || !r.ok) { setMsg($('loginMsg'), (r && r.msg) || '登录失败', 'err'); return; }
      me = {
        token: r.token, name: r.name, student_id: r.student_id,
        class_key: r.class_key, class_name: r.class_name,
        is_test: !!r.is_test, display_id: r.display_id || r.student_id
      };
      try { localStorage.setItem(SKEY, JSON.stringify(me)); } catch (e) { /* ignore */ }
      setMsg($('loginMsg'), '');
      showHome();
      loadIssue();
    }).catch(function (e) {
      $('btnLogin').disabled = false;
      setMsg($('loginMsg'), (e && e.message) || '登录失败', 'err');
    });
  }

  /* ---------------- 取内容 ---------------- */
  function loadIssue() {
    setMsg($('loadMsg'), '正在取今天的内容…');
    var date = null;
    CET4.getIndex().then(function (idx) {
      date = (idx && idx.latest) || null;
      return CET4.getIssue(date);
    }).catch(function () {
      return CET4.getIssue(null);
    }).then(function (iss) {
      setMsg($('loadMsg'), '');
      if (!iss) throw new Error('没有取到内容');
      issue = iss;
      renderIssue();
    }).catch(function (e) {
      setMsg($('loadMsg'), (e && e.message) || '内容加载失败，请检查网络后重试', 'err');
    });
  }

  function renderIssue() {
    $('issueTopic').textContent = issue.topic || '';
    $('issueTitle').textContent = issue.title || '';
    $('issueSub').textContent = issue.subtitle || '';
    $('issueMeta').textContent = '第 ' + issue.seq + ' 期 · ' + issue.date +
      (issue.source && issue.source.name ? ' · 来源：' + issue.source.name : '');
    var src = $('issueSrc');
    if (issue.source && issue.source.url) {
      src.href = issue.source.url;
      src.hidden = false;
    } else { src.hidden = true; }

    // 短文：英文在前，中文对照默认收起（点开才注入 DOM）
    var a = CET4UI.articleHTML(issue.article);
    $('artEn').innerHTML = a.en;
    $('artZh').innerHTML = '';
    $('artZh').hidden = true;
    $('btnZh').textContent = '显示中文对照';

    // 生词
    try { vocabOpen = JSON.parse(localStorage.getItem('cet4.vocab.' + issue.date) || '{}'); } catch (e) { vocabOpen = {}; }
    renderVocab();

    // 翻译（模式 + 句子 + 采分点 + 打分器一次装配好）
    transMode = 'quick';
    draftStore = {};
    resetTimer();
    var mbtns = document.querySelectorAll('#modeSwitch .mbtn');
    for (var mi = 0; mi < mbtns.length; mi++) {
      mbtns[mi].classList.toggle('on', mbtns[mi].getAttribute('data-mode') === 'quick');
    }
    $('transTitle').textContent = issue.translation.title || '翻译练习';
    $('transBrief').textContent = issue.translation.brief || '';
    rebuildPractice();
    startTimerLoop();
  }

  /* ---------------- 生词 ---------------- */
  function renderVocab() {
    var box = $('vocabGrid');
    box.textContent = '';
    var all = issue.vocab || [];
    // 🔴 四级词在前，超纲词折叠在后面（用户要求「生词给四级词汇」；
    //    超纲词不是错，只是「不在大纲里」⇒ 折起来，不混进主列表）
    var core = [], beyond = [];
    all.forEach(function (v, i) { (v.cet4 === 'beyond' ? beyond : core).push({ v: v, i: i }); });
    var beyondBtn = $('btnBeyondVocab');
    var beyondWrap = $('beyondWrap');

    function card(v, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'vc' + (vocabOpen[i] ? ' open' : '');
      b.setAttribute('data-i', i);
      b.setAttribute('aria-expanded', vocabOpen[i] ? 'true' : 'false');
      b.innerHTML = CET4UI.vocabCardHTML(v, i, !!vocabOpen[i]);
      b.addEventListener('click', function () {
        vocabOpen[i] = !vocabOpen[i];
        try { localStorage.setItem('cet4.vocab.' + issue.date, JSON.stringify(vocabOpen)); } catch (e) { }
        renderVocab();
      });
      return b;
    }

    core.forEach(function (o) { box.appendChild(card(o.v, o.i)); });

    if (beyond.length) {
      if (beyondBtn) {
        beyondBtn.textContent = (beyondOpen ? '收起超纲词' : '另有 ' + beyond.length + ' 个超纲词（点开看）');
        beyondBtn.hidden = false;
      }
      if (beyondWrap) {
        beyondWrap.textContent = '';
        beyond.forEach(function (o) { beyondWrap.appendChild(card(o.v, o.i)); });
        beyondWrap.hidden = !beyondOpen;
      }
    } else if (beyondBtn) {
      beyondBtn.hidden = true;
    }
  }

  /* ---------------- 采分表达表 ---------------- */
  function renderExprs() {
    var box = $('expGrid');
    box.textContent = '';
    var exprs = modeExprs();
    exprs.forEach(function (e, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'exp';
      b.setAttribute('data-i', i);
      b.innerHTML = CET4UI.exprCardHTML(e, i, !!exprOpen[i]);
      b.addEventListener('click', function () {
        exprOpen[i] = !exprOpen[i];
        renderExprs();
        updateExpandAllLabel();
      });
      box.appendChild(b);
    });
    updateExpandAllLabel();
  }
  function updateExpandAllLabel() {
    $('btnExpandAll').textContent = CET4UI.expandAllLabel(modeExprs(), exprOpen);
  }

  /* ---------------- 逐句自译 ---------------- */
  function renderDrafts() {
    var box = $('draftWrap');
    box.textContent = '';
    modeSentences().forEach(function (s, i) {
      var n = s.no || (i + 1);
      var d = document.createElement('div');
      d.innerHTML = CET4UI.draftBlockHTML(s, n);
      box.appendChild(d.firstChild);
      var ta = $('ta' + n);
      ta.value = draftStore[n] || '';
      ta.addEventListener('input', function () { onType(n, ta.value); });
      $('hintBtn' + n).addEventListener('click', function () {
        var st = $('steps' + n);
        var open = st.classList.toggle('open');
        if (open) st.innerHTML = CET4UI.hintsHTML(s.hints);
        else st.innerHTML = '';
        $('hintBtn' + n).textContent = open ? '收起三步提示' : '看三步提示';
      });
      $('cnt' + n).textContent = CET4UI.wordCount(ta.value) + ' 词';
    });
  }
  function onType(n, text) {
    var r = practice.setDraft(n, text);
    $('cnt' + n).textContent = CET4UI.wordCount(text) + ' 词';
    updateProgress();
    if (r.invalidated) invalidateCheck();
    else if (practice.isStale()) invalidateCheck();
  }
  function updateProgress() {
    if (!practice) return;
    var total = practice.sentNos.length || 1;
    var done = practice.filledCount();
    $('pgCur').textContent = done;
    $('pgFill').style.width = (done / total * 100) + '%';
    $('pgLabel').textContent = CET4UI.progressLabel(done, total);
  }

  /* ---------------- 采分点自查 ---------------- */
  function renderKws() {
    var box = $('kwGrid');
    box.textContent = '';
    modeExprs().forEach(function (e, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'kw';
      b.setAttribute('data-i', i);
      b.innerHTML = '<span class="k">' + (i + 1) + '</span><span class="w">' + CET4UI.esc(e.cn) + '</span><span class="s"></span>';
      b.addEventListener('click', function () { toggleManualKw(i); });
      box.appendChild(b);
    });
    paintKw();
  }
  function kwClass(i) {
    var res = practice.checked;
    var c = CET4UI.kwClassOf(res, i);
    if (c) return c;
    if (manualKw[i]) return 'hit';
    return '';
  }
  function paintKw() {
    var items = $('kwGrid').children;
    for (var i = 0; i < items.length; i++) {
      var cls = kwClass(i);
      items[i].className = 'kw' + (cls ? ' ' + cls : '');
      var s = items[i].querySelector('.s');
      if (s) s.textContent = cls === 'hit' ? '✓ 命中' : (cls === 'miss' ? '✗ 漏掉' : '');
    }
  }
  function toggleManualKw(i) {
    if (practice.checked) { toast('已经检查过了，改稿后可重新检查'); return; }
    manualKw[i] = !manualKw[i];
    paintKw();
  }
  function invalidateCheck(silent) {
    manualKw = {};
    if (practice) { practice.checkedSig = null; practice.checked = null; }
    paintKw();
    $('result').classList.remove('on');
    $('resBand').textContent = '—';
    $('resComp').textContent = '';
    $('resTxt').textContent = '';
    $('resStd').textContent = '';
    $('resList').innerHTML = '';
    $('aiOut').innerHTML = '';
    if (!silent) setMsg($('checkMsg'), '内容已改，请重新交卷', 'warn');
  }

  /* ---------------------------------------------------------------------
     交卷：机器初判
     🔴 这里报的是「信息完整度」和**档位区间**，不是最终四级分数 ——
        语言错误（语法 / 通顺）机器看不见，所以必须诚实标注。
     --------------------------------------------------------------------- */
  /* ---------------- 语法检查（本地规则引擎，零成本、离线可用） ----------------
     · 词库（四级大纲带词性）**按需加载** ⇒ 不占首屏流量
     · 拿不到词库也能跑（只用不依赖词库的那几条规则） */
  var lexPromise = null;
  function loadLexicon() {
    if (lexPromise) return lexPromise;
    lexLoading = true;
    lexPromise = fetch('data/cet4_lexicon.json').then(function (r) { return r.json(); })
      .then(function (lex) {
        if (global.CET4Grammar) global.CET4Grammar.setLexicon(lex);
        lexLoading = false;
        return lex;
      }).catch(function () { lexLoading = false; return null; });
    return lexPromise;
  }

  /** 逐句跑语法检查 ⇒ {errors, types, typeList} */
  function checkGrammar() {
    var G = global.CET4Grammar;
    if (!G || !practice) return null;
    var nos = practice.sentNos || [];
    var all = [], types = {};
    for (var i = 0; i < nos.length; i++) {
      var text = String(practice.store[nos[i]] || '');
      if (!text.trim()) continue;
      var r = G.check(text);
      for (var j = 0; j < r.errors.length; j++) {
        var e = r.errors[j];
        e.no = nos[i];
        all.push(e);
      }
      for (var t in r.types) { if (r.types.hasOwnProperty(t)) types[t] = (types[t] || 0) + r.types[t]; }
    }
    var typeList = Object.keys(types).sort(function (a, b) { return types[b] - types[a]; });
    return { errors: all, types: types, typeList: typeList, total: all.length };
  }

  var lexLoading = false;
  function renderGrammar(g) {
    var box = $('resGram');
    if (!box) return;
    if (!g) { box.innerHTML = ''; box.hidden = true; return; }
    box.hidden = false;
    if (lexLoading) {
      box.innerHTML = '<div class="res-note-i">正在加载更细的检查（四级词库还在路上）…</div>';
      return;
    }
    if (!g.total) {
      box.innerHTML = '<div class="res-note-i ok">✓ 没查到常见语法问题（只查了机器能查的那几类，仍以参考译文为准）</div>';
      return;
    }
    var head = g.typeList.map(function (t) {
      return '<span class="gtag">' + CET4UI.esc(t) + ' × ' + g.types[t] + '</span>';
    }).join('');
    var list = g.errors.slice(0, 12).map(function (e) {
      return '<div class="res-i no"><span>' + CET4UI.esc(e.type) + '</span>'
        + '<span>第 ' + e.no + ' 句：' + CET4UI.esc(e.msg)
        + (e.fix ? '　<b>改法：</b>' + CET4UI.esc(e.fix) : '') + '</span></div>';
    }).join('');
    var more = g.errors.length > 12 ? '<div class="res-note-i">…… 还有 ' + (g.errors.length - 12) + ' 处没列出来</div>' : '';
    box.innerHTML = '<div class="gram-h">语法问题（机器查的常见错误，可能有误报）</div>'
      + '<div class="gram-tags">' + head + '</div>' + list + more
      + '<div class="res-note-i">这些是<b>常见错误类型</b>的提示，不是判卷；最终以参考译文和老师的判断为准。</div>';
  }

  function doCheck() {
    var res = practice.check();
    paintKw();
    $('result').classList.add('on');
    var band = res.band || {};
    $('resBand').textContent = CET4Score.bandText(band);
    $('resComp').innerHTML = '信息完整度 <b>' + res.completeness + '%</b>'
      + ' · 采分表达命中 <b>' + res.hits + ' / ' + res.total + '</b>';
    $('resTxt').textContent = CET4UI.commentOf(res.pct);
    $('resStd').innerHTML = '<b>这一档的意思：</b>' + CET4Score.esc(band.std || '')
      + '<br><span class="muted">机器只判得出「意思传达到没有」。语法和通顺要靠 AI 批改或老师看。</span>';

    var per = (res.perSentence || []).map(function (p) {
      return '<div class="res-i ' + (p.empty ? 'no' : (p.short ? 'warn' : 'ok')) + '">'
        + '<span>' + (p.empty ? '✗ 没写' : (p.short ? '! 偏短' : '✓ 已写')) + '</span>'
        + '<span>第 ' + p.no + ' 句 · ' + p.words + ' 词'
        + (p.refWords ? '（参考 ' + p.refWords + ' 词）' : '') + '</span></div>';
    }).join('');
    var miss = (res.missing || []).map(function (m) {
      return '<div class="res-i no"><span>✗ 漏掉</span><span>' + m.no + ' ' + CET4UI.esc(m.cn) + '</span></div>';
    }).join('');
    var notes = (res.notes || []).map(function (n) {
      return '<div class="res-note-i ' + n.level + '">' + CET4UI.esc(n.text) + '</div>';
    }).join('');
    $('resList').innerHTML = per + miss + notes;

    setMsg($('checkMsg'), '机器初判完成：信息完整度 ' + res.completeness + '%（' + CET4Score.bandText(band) + '）', 'ok');
    // 语法检查：先出结果（不依赖词库的那部分），词库到了再补更细的
    renderGrammar(checkGrammar());
    loadLexicon().then(function () {
      if (practice && practice.checked) renderGrammar(checkGrammar());
    });
    reportTranslation(res);
    toast('初判完成：信息完整度 ' + res.completeness + '%');
  }

  /* ---------------- 逐句对照讲评 ---------------- */
  function renderSegs() {
    var box = $('segGrid');
    box.textContent = '';
    modeSentences().forEach(function (s) {
      var d = document.createElement('div');
      d.innerHTML = CET4UI.segItemHTML(s);
      var el = d.firstChild;
      function toggle() {
        var no = s.no;
        if (segOpen[no]) {
          segOpen[no] = false;
          el.classList.remove('open');
          el.querySelector('.seg-body').innerHTML = '';
          el.querySelector('.seg-arrow').textContent = '展开';
        } else {
          segOpen[no] = true;
          el.classList.add('open');
          el.querySelector('.seg-body').innerHTML = CET4UI.segRevealHTML(s);
          el.querySelector('.seg-arrow').textContent = '收起';
        }
      }
      el.addEventListener('click', toggle);
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
      box.appendChild(el);
    });
  }

  /* ---------------- 中式英语坑 ---------------- */
  /* ---------------- 记录上报 ---------------- */
  function reportTranslation(res) {
    if (!me || !issue) return;
    // 🔴 语法错误类型 —— 老师端靠它统计「全班最常见的错误类型」
    var g = checkGrammar();
    CET4.addLog({
      class_name: me.class_name, student_id: me.student_id,
      duration_sec: 0,
      action: CET4.logTrans(
        issue.date, res.hits, res.total,
        res.completeness,
        res.band ? ('第' + res.band.no + '档') : '',
        // 🔴 漏掉的是哪几个采分表达 —— 老师端靠它算「全班最弱的采分点」
        (res.missing || []).map(function (m) { return m.cn; }),
        // 🔴 语法错误类型（老师端靠它算「全班最常见的错误类型」）
        g ? g.typeList : []
      )
    }).then(function (r) {
      if (r && r.ok === false && r.queued) toast('已存到本机，联网后自动补记');
    });
  }
  function flushBrowse(keepalive) {
    if (!me || !issue) return;
    var delta = browse.sec - browse.sent;
    if (delta < 5) return;
    var send = delta;
    CET4.addLog({
      class_name: me.class_name, student_id: me.student_id,
      duration_sec: send,
      action: CET4.logBrowse(issue.date, send),
      timestamp: CET4.tsString(CET4.todayStr(), CET4.nowTime())
    }).then(function (r) {
      if (r && r.ok) browse.sent += send;
      else if (r && r.queued) browse.sent += send;
    }).catch(function () { /* 失败不影响做题 */ });
  }

  // 有效操作时长：30 秒内有交互才计
  function startTimer() {
    setInterval(function () {
      if (!me) return;
      var now = Date.now();
      if (browse.last && now - browse.last <= 30000) {
        browse.sec++;
        if (browse.sec % 15 === 0) $('studyTime').textContent = '本次有效阅读约 ' + browse.sec + ' 秒';
      }
    }, 1000);
    ['click', 'keydown', 'input', 'scroll', 'touchstart'].forEach(function (ev) {
      document.addEventListener(ev, function () { browse.last = Date.now(); }, { passive: true });
    });
    window.addEventListener('pagehide', function () { flushBrowse(true); });
    window.addEventListener('beforeunload', function () { flushBrowse(true); });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') flushBrowse(true);
    });
    // 断网恢复 → 补发本地队列
    window.addEventListener('online', function () { CET4.flushQueue(); });
    setInterval(function () { CET4.flushQueue(); }, 30000);
  }

  /* ---------------- 字号 / 高对比 ---------------- */
  function bindPrefs() {
    var panel = $('prefPanel');
    $('btnAa').addEventListener('click', function () { panel.hidden = !panel.hidden; });
    $('btnHc').addEventListener('click', function () {
      CET4.prefs.setHc(!CET4.prefs.hcOn());
      paintHc();
    });
    $('zoom').addEventListener('input', function () {
      var z = CET4.prefs.setZoom(parseInt(this.value, 10));
      $('zoomVal').textContent = z + '%';
      syncTopH();
    });
    $('btnZoomIn').addEventListener('click', function () {
      var z = CET4.prefs.setZoom(CET4.prefs.zoom() + 10);
      $('zoom').value = z; $('zoomVal').textContent = z + '%'; syncTopH();
    });
    $('btnZoomOut').addEventListener('click', function () {
      var z = CET4.prefs.setZoom(CET4.prefs.zoom() - 10);
      $('zoom').value = z; $('zoomVal').textContent = z + '%'; syncTopH();
    });
    paintHc();
    $('zoom').value = CET4.prefs.zoom();
    $('zoomVal').textContent = CET4.prefs.zoom() + '%';
  }
  function paintHc() {
    var on = CET4.prefs.hcOn();
    var b = $('btnHc');
    b.textContent = on ? '高对比：开' : '高对比：关';
    b.classList.toggle('on', on);
  }
  function syncTopH() {
    var tb = document.querySelector('.topbar');
    if (!tb) return;
    document.documentElement.style.setProperty('--top-h', tb.offsetHeight + 'px');
  }

  /* ---------------- 事件绑定 ---------------- */
  $('btnLogin').addEventListener('click', doLogin);
  var btnBeyond = $('btnBeyondVocab');
  if (btnBeyond) {
    btnBeyond.addEventListener('click', function () {
      beyondOpen = !beyondOpen;
      renderVocab();
    });
  }
  // 🔴 自测号一键进入（老师 / 教研 / 设备调试用）
  //    不占名册、记录不进老师看板统计 ⇒ 自测多少次都不污染班级数据
  var btnTest = $('btnTestLogin');
  if (btnTest) {
    btnTest.addEventListener('click', function () {
      $('sid').value = CET4.testSid || '9999999999';
      $('sname').value = CET4.testName || '测试同学';
      doLogin();
    });
  }
  $('sid').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
  $('sname').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
  $('btnLogout').addEventListener('click', function () {
    if (confirm('确定退出登录吗？')) logout();
  });
  $('btnZh').addEventListener('click', function () {
    var box = $('artZh');
    var open = box.hidden;
    if (open) box.innerHTML = CET4UI.articleHTML(issue.article).zh;
    box.hidden = !open;
    $('btnZh').textContent = open ? '收起中文对照' : '显示中文对照';
  });
  $('btnReadDone').addEventListener('click', function () {
    flushBrowse(false);
    var t = document.getElementById('secTrans');
    if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    toast('好，开始翻译练习');
  });
  $('btnExpandAll').addEventListener('click', function () {
    var exprs = modeExprs();
    var allOpen = exprs.length > 0;
    for (var i = 0; i < exprs.length; i++) if (!exprOpen[i]) { allOpen = false; break; }
    for (var j = 0; j < exprs.length; j++) exprOpen[j] = !allOpen;
    renderExprs();
  });
  $('btnCheck').addEventListener('click', doCheck);
  $('btnReset').addEventListener('click', resetDrafts);
  $('btnResetTop').addEventListener('click', resetDrafts);
  function resetDrafts() {
    var nos = (practice && practice.sentNos) || [];
    for (var i = 0; i < nos.length; i++) {
      var n = nos[i];
      draftStore[n] = '';
      var ta = $('ta' + n);
      if (ta) ta.value = '';
      var c = $('cnt' + n);
      if (c) c.textContent = '0 词';
    }
    manualKw = {};
    if (practice) { practice.checkedSig = null; practice.checked = null; }
    paintKw();
    $('result').classList.remove('on');
    updateProgress();
    setMsg($('checkMsg'), '');
    toast('已清空，可重新开始');
  }

  /* ---------------- 模式切换 / 限时 / AI 批改 ---------------- */
  var modeBtns = document.querySelectorAll('#modeSwitch .mbtn');
  for (var mb = 0; mb < modeBtns.length; mb++) {
    (function (btn) {
      btn.addEventListener('click', function () { switchMode(btn.getAttribute('data-mode')); });
    })(modeBtns[mb]);
  }
  $('btnTimer').addEventListener('click', function () {
    timerPaused = !timerPaused;
    this.textContent = timerPaused ? '继续' : '暂停';
    paintTimer();
  });
  $('btnTimerReset').addEventListener('click', function () { resetTimer(); toast('已重新计时'); });
  $('btnAI').addEventListener('click', function () {
    var out = $('aiOut');
    out.innerHTML = '<div class="ai-pending">AI 批改还没接上这一站。'
      + '<br><span class="muted">接上之后：一次调用约 2000 token ≈ ¥0.0002，按需点，不点不花钱。</span></div>';
  });
  window.addEventListener('resize', syncTopH);
  window.addEventListener('load', syncTopH);
  boot();
})(typeof window !== 'undefined' ? window : globalThis);
