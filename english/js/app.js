/* =====================================================================
   统一教师看板 · 主逻辑
   ---------------------------------------------------------------------
   老师的真实工作流：先看**一个学生**今天/这周做了什么，再看**全班**。
   ⇒ 主视图 = 「学生维度」一行一个学生，四类数据并排；点开看明细。
   ⇒ 次视图 = 「班级维度」按班汇总。
   ===================================================================== */
'use strict';

(function (global) {
  'use strict';

  var TH = global.TH;
  var P = TH.parse;

  var S = {
    authed: false,
    teacherId: '',
    loading: false,
    view: 'student',      /* student | class */
    classFilter: '',
    unitFilter: '',
    q: '',
    roster: [],
    vocabLogs: [],
    listenLogs: [],
    cet4Logs: [],
    cet4Days: [],
    byStudent: {},
    lastLoad: ''
  };

  /* ---------------- 加载全部数据 ---------------- */
  function loadAll(onProgress) {
    S.loading = true;
    render();
    var step = function (msg) { if (onProgress) onProgress(msg); };
    return Promise.all([
      TH.loadRoster().then(function (r) { S.roster = r; step('名册 ' + r.length + ' 人'); }),
      TH.loadVocabLogs().then(function (r) { S.vocabLogs = r; step('单词日志 ' + r.length + ' 条'); }),
      TH.loadListenLogs().then(function (r) { S.listenLogs = r; step('听力记录 ' + r.length + ' 条'); }),
      TH.loadCet4Logs().then(function (r) { S.cet4Logs = r; step('四级记录 ' + r.length + ' 条'); }),
      TH.loadCet4Days().then(function (r) { S.cet4Days = r; step('四级期数 ' + r.length + ' 期'); })
    ]).then(function () {
      buildIndex();
      S.loading = false;
      S.lastLoad = new Date().toLocaleString();
      render();
    }).catch(function (e) {
      S.loading = false;
      S.err = (e && e.message) ? e.message : String(e);
      render();
    });
  }

  /* ---------------- 建立「按学号」的索引 ---------------- */
  function buildIndex() {
    var idx = {};

    function slot(sid) {
      if (!idx[sid]) {
        idx[sid] = {
          sid: sid, name: '', cls: '', className: '',
          rosterCls: '', rosterClsName: '', inRoster: false,
          /* 单词 */
          wSec: 0, wPairs: 0, wClicks: 0, wSegs: 0, wUnits: {}, wPages: {}, wLast: '',
          wActiveDays: 0, wActivityCount: 0,
          /* 听力 */
          lCnt: 0, lMin: 0, lDays: 0, lCats: {}, lLast: '', lRows: [],
          /* 四级（阅读+翻译+理解题） */
          cBrowse: 0, cTrans: 0, cHit: 0, cDen: 0, cQuiz: 0, cOk: 0, cQDen: 0,
          cDays: 0, cIssues: {}, cLast: ''
        };
      }
      return idx[sid];
    }

    /* 名册：提供**姓名**（唯一权威）+ 在册标记
       🔴 班级**不**在这里定死 —— 名册的 5 个班（JR1/CW3/WL_CW/XQ1/CWZT1/WL1）
          与日志实际用的 class_key（JR1/CW3/WL_CW/XQ1/LISTEN/CET4）**不是一套**
          （例：某位同学名册是 CWZT1，但她的词汇日志写在 WL_CW「物流财务合上课班」）
          ⇒ 分类必须以**日志自己的 class_key/class_name** 为准（= 记录发生时的真实归属）
          ⇒ 名册只负责「这个人叫什么、是不是在册」 */
    S.roster.forEach(function (r) {
      var it = slot(String(r.id));
      it.name = r.name || '';
      it.rosterCls = r.class_key || '';
      it.rosterClsName = r.class_name || (TH.CLASS_NAME[r.class_key] || r.class_key || '');
      it.inRoster = true;
    });

    /* 单词：平时练习 */
    var practice = P.parsePracticeLogs(S.vocabLogs, S.unitFilter);
    practice.forEach(function (r) {
      var it = slot(r.sid);
      /* 🔴 班级以**日志自己**的 class_key/class_name 为准 */
      /* 🔴 班级以日志自己的 **class_name** 为准（不是 class_key！）
         class_key 是**分站标识**：JR1/CW3/WL_CW/XQ1（词汇）/ LISTEN（听力）/ CET4（四级）
         ⇒ 拿 class_key 当班号，听力会归成一个叫「LISTEN」的班
         ⇒ 真正的班在 class_name（'26财务管理3班' / '26财务管理（中本贯通）1班' …） */
      if (r.clsName) { it.className = r.clsName; it.cls = r.clsName; }
      it.wSec += r.sec; it.wPairs += r.pairs; it.wClicks += r.clicks; it.wSegs += r.segs;
      for (var u in r.units) it.wUnits[u] = (it.wUnits[u] || 0) + r.units[u];
      for (var p in r.pages) it.wPages[p] = (it.wPages[p] || 0) + r.pages[p];
      if (!it.wLast || tsNum(r.last) > tsNum(it.wLast)) it.wLast = r.last;
    });
    /* 单词：活跃天数 / 行为条数（从原始日志算，口径与老师端一致） */
    var actDays = {};
    S.vocabLogs.forEach(function (l) {
      if (TH.isTestId(l.student_id)) return;
      if (!P.isVocabActivity(l.action || '')) return;
      var it = slot(String(l.student_id));
      it.wActivityCount++;
      var d = String(l.timestamp || '').slice(0, 10).replace(/\//g, '-');
      if (!actDays[l.student_id]) actDays[l.student_id] = {};
      actDays[l.student_id][d] = 1;
    });
    for (var sid in actDays) {
      if (idx[sid]) idx[sid].wActiveDays = Object.keys(actDays[sid]).length;
    }

    /* 听力 */
    var listen = P.parseListenLogs(S.listenLogs);
    listen.forEach(function (r) {
      var it = slot(r.sid);
      /* 🔴 班级以日志自己的 class_key/class_name 为准 */
      /* 🔴 班级以日志自己的 **class_name** 为准（不是 class_key！）
         class_key 是**分站标识**：JR1/CW3/WL_CW/XQ1（词汇）/ LISTEN（听力）/ CET4（四级）
         ⇒ 拿 class_key 当班号，听力会归成一个叫「LISTEN」的班
         ⇒ 真正的班在 class_name（'26财务管理3班' / '26财务管理（中本贯通）1班' …） */
      if (r.clsName) { it.className = r.clsName; it.cls = r.clsName; }
      it.lCnt += r.cnt; it.lMin += r.minutes; it.lDays = Object.keys(r.days).length;
      for (var c in r.cats) it.lCats[c] = (it.lCats[c] || 0) + r.cats[c];
      it.lRows = r.rows;
      if (!it.lLast || tsNum(r.last) > tsNum(it.lLast)) it.lLast = r.last;
    });

    /* 四级（阅读 + 翻译 + 理解题） */
    var cet = P.parseCet4Logs(S.cet4Logs);
    cet.forEach(function (r) {
      var it = slot(r.sid);
      /* 🔴 班级以日志自己的 **class_name** 为准（不是 class_key！）
         class_key 是**分站标识**：JR1/CW3/WL_CW/XQ1（词汇）/ LISTEN（听力）/ CET4（四级）
         ⇒ 拿 class_key 当班号，听力会归成一个叫「LISTEN」的班
         ⇒ 真正的班在 class_name（'26财务管理3班' / '26财务管理（中本贯通）1班' …） */
      if (r.clsName) { it.className = r.clsName; it.cls = r.clsName; }
      it.cBrowse += r.browse_sec;
      it.cTrans += r.trans_n; it.cHit += r.trans_hit; it.cDen += r.trans_den;
      it.cQuiz += r.quiz_n; it.cOk += r.quiz_ok; it.cQDen += r.quiz_den;
      it.cDays = Object.keys(r.days).length;
      it.cIssues = r.issues;
      if (!it.cLast || tsNum(r.last) > tsNum(it.cLast)) it.cLast = r.last;
    });

    S.byStudent = idx;
  }

  /* ---------------- 工具 ---------------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function $(s) { return document.querySelector(s); }
  function list() {
    var arr = Object.keys(S.byStudent).map(function (k) { return S.byStudent[k]; });
    /* 名册里的人即使没记录也要出现（老师要知道「谁还没动」） */
    return arr;
  }
  function hasAny(it) {
    return it.wSegs > 0 || it.lCnt > 0 || it.cTrans > 0 || it.cQuiz > 0 || it.cBrowse > 0;
  }
  function matchQ(it, q) {
    if (!q) return true;
    q = String(q).trim().toLowerCase();
    if (!q) return true;
    return String(it.sid).indexOf(q) >= 0
      || String(it.name).toLowerCase().indexOf(q) >= 0
      || String(it.className).indexOf(q) >= 0;
  }
  function lastOf(it) {
    var t = '';
    [it.wLast, it.lLast, it.cLast].forEach(function (x) { if (x && String(x) > String(t)) t = x; });
    /* 🔴 库里 timestamp 是 '2026/09/28 13:22:00' 形态
       ⇒ 统一成 '2026-09-28 13:22'（不依赖浏览器 locale，
          免得渲染成 '2026-9-28 1:22:00 PM'，跟其它站口径不一致） */
    return fmtTs(t);
  }

  /* 时间戳统一格式化 */
  function fmtTs(ts) {
    if (!ts) return '';
    var s = String(ts).trim();
    var m = /(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?/.exec(s);
    if (!m) return s;
    var d = m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
    if (m[4] == null) return d;
    return d + ' ' + ('0' + m[4]).slice(-2) + ':' + m[5];
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    var root = $('#root');
    if (!root) return;
    if (!S.authed) { root.innerHTML = renderGate(); bindGate(); return; }
    root.innerHTML = renderDash();
    bindDash();
  }

  function renderGate() {
    return '<div class="gate">' +
      '<div class="logo">📊</div>' +
      '<h2>教师统一看板</h2>' +
      '<p class="sub">单词 · 听力 · 阅读翻译 —— 一页看全</p>' +
      '<div class="card"><div class="card-b">' +
      '<label class="hint">工号</label>' +
      '<input class="inp" id="tId" placeholder="例如 2013" autocomplete="username">' +
      '<label class="hint" style="margin-top:12px;">密码</label>' +
      '<input class="inp" id="tPass" type="password" placeholder="教师密码" autocomplete="current-password">' +
      '<button class="btn wide" id="tGo">进入看板</button>' +
      '<div class="hint" style="margin-top:14px;line-height:1.75">' +
      '🔒 口令校验在**云端**完成，浏览器里不存明文、也不存摘要。<br>' +
      '数据只读：本页面**不写库**、不删任何记录。' +
      '</div></div></div></div>';
  }

  function renderDash() {
    var today = P.todayStr();
    var all = list();
    var active = all.filter(hasAny);
    var todayActive = all.filter(function (it) {
      return lastOf(it).slice(0, 10) === today;
    });

    var html = '';
    html += '<div class="top">' +
      '<div class="top-l"><h1>教师统一看板</h1>' +
      '<p class="sub">单词 · 听力 · 阅读翻译　|　' +
      '<span class="pill">' + (S.roster.length) + ' 人在册</span>' +
      '<span class="pill">' + active.length + ' 人有记录</span>' +
      '<span class="pill">' + todayActive.length + ' 人今天动过</span>' +
      '</p></div>' +
      '<div class="top-r">' +
      '<button class="btn sm" id="btn-reload">重新加载</button>' +
      '<button class="btn sm ghost" id="btn-export">导出 CSV</button>' +
      '<button class="btn sm ghost" id="btn-out">退出</button>' +
      '</div></div>';

    if (S.err) {
      html += '<div class="err">读取失败：' + esc(S.err) + '　（重试一次；若仍失败，多半是网络或权限问题）</div>';
    }
    if (S.loading) {
      html += '<div class="loading">正在拉取全部数据…（首次约 3–6 秒，2488 条日志要分页取）</div>';
      return html;
    }

    /* 视图切换 */
    html += '<div class="tabs">' +
      '<button class="tab' + (S.view === 'student' ? ' on' : '') + '" data-view="student">按学生</button>' +
      '<button class="tab' + (S.view === 'class' ? ' on' : '') + '" data-view="class">按班级</button>' +
      '</div>';

    /* 筛选条 */
    html += '<div class="filters">' +
      '<input class="inp" id="fQ" placeholder="搜学号 / 姓名 / 班级" value="' + esc(S.q) + '">' +
      '<select class="inp" id="fClass"><option value="">全部班级</option>' +
      classOptions() + '</select>' +
      (S.view === 'student' ?
        '<select class="inp" id="fUnit"><option value="">全部课次</option>' +
        '<option value="1"' + (S.unitFilter === '1' ? ' selected' : '') + '>第一课</option>' +
        '<option value="2"' + (S.unitFilter === '2' ? ' selected' : '') + '>第二课</option>' +
        '<option value="3"' + (S.unitFilter === '3' ? ' selected' : '') + '>第三课</option>' +
        '</select>' : '') +
      '<span class="count">' + (S.view === 'student' ? '学生 ' + filtered().length + ' 人' : '班级 ' + classRows().length + ' 个') + '</span>' +
      '</div>';

    if (S.view === 'student') html += renderStudentTable();
    else html += renderClassTable();

    html += '<div class="foot">数据来源：同一个 Supabase 库（词汇站 / 听力站 / 四级日报站共写一张表，靠 class_key 区分）。' +
      '本次读取：' + (S.lastLoad || '—') + '　|　自测号不计入任何统计。</div>';
    return html;
  }

  function classOptions() {
    var seen = {}, out = '';
    S.roster.forEach(function (r) {
      if (!r.class_key || seen[r.class_key]) return;
      seen[r.class_key] = 1;
      var n = S.roster.filter(function (x) { return x.class_key === r.class_key; }).length;
      out += '<option value="' + esc(r.class_key) + '"' + (S.classFilter === r.class_key ? ' selected' : '') +
        '>' + esc(r.class_name || (TH.CLASS_NAME[r.class_key] || r.class_key)) + '（' + n + ' 人）</option>';
    });
    return out;
  }

  function filtered() {
    return list().filter(function (it) {
      if (S.classFilter && it.cls !== S.classFilter) return false;
      return matchQ(it, S.q);
    });
  }

  /* ---------------- 学生视图 ---------------- */
  function renderStudentTable() {
    var rows = filtered().sort(function (a, b) {
      var ka = hasAny(a) ? 1 : 0, kb = hasAny(b) ? 1 : 0;
      if (ka !== kb) return kb - ka;                    /* 有记录的排前面 */
      return String(lastOf(b)).localeCompare(String(lastOf(a)));
    });
    if (!rows.length) return '<div class="empty">没有匹配的学生</div>';

    var tW = 0, tL = 0, tT = 0, tQ = 0, tB = 0;
    var html = '<div class="tb-wrap"><table class="tb"><thead><tr>' +
      '<th>学号</th><th>姓名</th><th>班级</th>' +
      '<th class="num">单词<br><span class="u">有效时长</span></th>' +
      '<th class="num">单词<br><span class="u">配对/段</span></th>' +
      '<th class="num">听力<br><span class="u">次数/分钟</span></th>' +
      '<th class="num">阅读<br><span class="u">浏览秒</span></th>' +
      '<th class="num">翻译<br><span class="u">命中/总数</span></th>' +
      '<th class="num">理解题<br><span class="u">答对/总数</span></th>' +
      '<th>最近</th><th></th></tr></thead><tbody>';

    rows.forEach(function (it) {
      tW += it.wSec; tL += it.lMin; tT += it.cTrans; tQ += it.cQuiz; tB += it.cBrowse;
      var units = Object.keys(it.wUnits).sort().map(P.unitLabel).join('+');
      html += '<tr' + (hasAny(it) ? '' : ' class="idle"') + '>' +
        '<td class="sid">' + esc(TH.displayIdOf(it.sid)) + '</td>' +
        '<td class="nm">' + esc(it.name || '—') + '</td>' +
        '<td class="cls sm">' + esc(it.className || '—') + '</td>' +
        '<td class="num">' + (it.wSec ? P.fmtSec(it.wSec) : '—') + '</td>' +
        '<td class="num">' + (it.wSegs ? (it.wPairs + '对 / ' + it.wSegs + '段' + (units ? '<br><span class="u">' + esc(units) + '</span>' : '')) : '—') + '</td>' +
        '<td class="num">' + (it.lCnt ? (it.lCnt + '次 / ' + it.lMin + '分') : '—') + '</td>' +
        '<td class="num">' + (it.cBrowse ? (it.cBrowse + '秒') : '—') + '</td>' +
        '<td class="num">' + (it.cTrans ? (it.cHit + '/' + it.cDen + '<br><span class="u">' + P.pct(it.cHit, it.cDen) + '</span>') : '—') + '</td>' +
        '<td class="num">' + (it.cQuiz ? (it.cOk + '/' + it.cQDen + '<br><span class="u">' + P.pct(it.cOk, it.cQDen) + '</span>') : '—') + '</td>' +
        '<td class="dt sm">' + esc(lastOf(it) || '—') + '</td>' +
        '<td><button class="mini" data-sid="' + esc(it.sid) + '">明细</button></td>' +
        '</tr>';
    });

    html += '</tbody><tfoot><tr class="sum">' +
      '<td colspan="3">合计（' + rows.length + ' 人）</td>' +
      '<td class="num">' + P.fmtSec(tW) + '</td><td class="num">—</td>' +
      '<td class="num">' + tL + '分</td>' +
      '<td class="num">' + tB + '秒</td>' +
      '<td class="num">' + tT + '次</td>' +
      '<td class="num">' + tQ + '次</td><td colspan="2"></td></tr></tfoot></table></div>';
    return html;
  }

  /* ---------------- 班级视图 ---------------- */
  function classRows() {
    var byCls = {};
    list().forEach(function (it) {
      var k = it.cls || '(未分班)';
      if (!byCls[k]) {
        byCls[k] = {
          key: k, name: it.className || (TH.CLASS_NAME[k] || k),
          roster: 0, active: 0, wSec: 0, wSegs: 0, lCnt: 0, lMin: 0,
          cBrowse: 0, cTrans: 0, cHit: 0, cDen: 0, cQuiz: 0, cOk: 0, cQDen: 0
        };
      }
      var r = byCls[k];
      /* 在册 = 名册里**这个班**有人（按班名匹配：名册的 class_name） */
      if (it.inRoster && (it.rosterClsName === k || it.rosterCls === k)) r.roster++;
      if (hasAny(it)) r.active++;
      r.wSec += it.wSec; r.wSegs += it.wSegs;
      r.lCnt += it.lCnt; r.lMin += it.lMin;
      r.cBrowse += it.cBrowse;
      r.cTrans += it.cTrans; r.cHit += it.cHit; r.cDen += it.cDen;
      r.cQuiz += it.cQuiz; r.cOk += it.cOk; r.cQDen += it.cQDen;
    });
    return Object.keys(byCls).map(function (k) { return byCls[k]; })
      .filter(function (r) { return !S.classFilter || r.key === S.classFilter; })
      .sort(function (a, b) { return b.active - a.active; });
  }

  function renderClassTable() {
    var rows = classRows();
    if (!rows.length) return '<div class="empty">没有匹配的班级</div>';
    var html = '<div class="tb-wrap"><table class="tb"><thead><tr>' +
      '<th>班级</th><th class="num">在册</th><th class="num">有记录</th>' +
      '<th class="num">单词时长</th><th class="num">单词段数</th>' +
      '<th class="num">听力次数</th><th class="num">听力分钟</th>' +
      '<th class="num">阅读秒</th>' +
      '<th class="num">翻译命中</th><th class="num">翻译正确率</th>' +
      '<th class="num">理解题正确率</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      html += '<tr>' +
        /* 🔴 班名下面那行：只有当它和班名**不同**时才显示（否则重复两遍） */
        '<td>' + esc(r.name) +
        (r.key && r.key !== r.name ? '<br><span class="u">' + esc(r.key) + '</span>' : '') + '</td>' +
        '<td class="num">' + r.roster + '</td>' +
        '<td class="num">' + r.active + '</td>' +
        '<td class="num">' + P.fmtSec(r.wSec) + '</td>' +
        '<td class="num">' + r.wSegs + '</td>' +
        '<td class="num">' + r.lCnt + '</td>' +
        '<td class="num">' + r.lMin + '</td>' +
        '<td class="num">' + r.cBrowse + '</td>' +
        '<td class="num">' + r.cHit + '/' + r.cDen + '</td>' +
        '<td class="num">' + (P.pct(r.cHit, r.cDen) || '—') + '</td>' +
        '<td class="num">' + (P.pct(r.cOk, r.cQDen) || '—') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  /* ---------------- 明细弹窗 ---------------- */
  function showDetail(sid) {
    var it = S.byStudent[sid];
    if (!it) return;
    var html = '<div class="pop"><div class="pop-card">' +
      '<h3>' + esc(TH.displayIdOf(it.sid)) + '　' + esc(it.name || '') + '</h3>' +
      '<p class="sub">' + esc(it.className || '未分班') + '</p>';

    html += '<h4>单词（词汇学习站）</h4>';
    if (it.wSegs) {
      html += '<p>有效时长 <b>' + P.fmtSec(it.wSec) + '</b>　配对 <b>' + it.wPairs + '</b> 对　' +
        '点击 <b>' + it.wClicks + '</b>　练习段 <b>' + it.wSegs + '</b>　活跃天数 <b>' + it.wActiveDays + '</b></p>';
      var units = Object.keys(it.wUnits).sort().map(P.unitLabel).join(' + ');
      var pages = Object.keys(it.wPages).map(function (k) { return k.replace('-', '·P'); }).join('　');
      html += '<p class="u">课次：' + esc(units || '—') + '　练过的页：' + esc(pages || '—') + '</p>';
    } else { html += '<p class="u">没有平时练习记录</p>'; }

    html += '<h4>听力（每日听力记录站）</h4>';
    if (it.lCnt) {
      html += '<p>记录 <b>' + it.lCnt + '</b> 条　累计 <b>' + it.lMin + '</b> 分钟　天数 <b>' + it.lDays + '</b></p>';
      var cats = Object.keys(it.lCats).map(function (c) { return esc(c) + '×' + it.lCats[c]; }).join('　');
      html += '<p class="u">类别：' + (cats || '—') + '</p>';
      html += '<table class="tb sm"><thead><tr><th>日期</th><th>时间</th><th>类别</th><th>内容</th><th>分钟</th></tr></thead><tbody>';
      it.lRows.slice().sort(function (a, b) { return String(b.ts).localeCompare(String(a.ts)); })
        .forEach(function (r) {
          html += '<tr><td class="sm">' + esc(fmtTs(r.ts).slice(0, 10)) + '</td>' +
            '<td class="sm">' + esc(fmtTs(r.ts).slice(11)) + '</td>' +
            '<td class="sm">' + esc(r.cat || '—') + '</td>' +
            '<td>' + esc(r.content || '') + (r.note ? ' <span class="u">备注:' + esc(r.note) + '</span>' : '') + '</td>' +
            '<td class="num">' + r.min + '</td></tr>';
        });
      html += '</tbody></table>';
    } else { html += '<p class="u">没有听力记录</p>'; }

    html += '<h4>阅读 + 翻译（四级精读日报）</h4>';
    if (it.cBrowse || it.cTrans || it.cQuiz) {
      html += '<p>阅读浏览 <b>' + it.cBrowse + '</b> 秒　' +
        '翻译练习 <b>' + it.cTrans + '</b> 次（命中 <b>' + it.cHit + '/' + it.cDen + '</b>）　' +
        '理解题 <b>' + it.cQuiz + '</b> 次（答对 <b>' + it.cOk + '/' + it.cQDen + '</b>）</p>';
      var iss = Object.keys(it.cIssues || {}).sort();
      if (iss.length) {
        html += '<p class="u">做过：' + esc(iss.join('、')) + '</p>';
      }
    } else { html += '<p class="u">没有四级记录</p>'; }

    html += '<div class="pop-foot"><button class="btn sm" id="pop-close">关闭</button></div>';
    html += '</div></div>';

    var host = $('#modal');
    host.innerHTML = html;
    var c = document.getElementById('pop-close');
    if (c) c.onclick = function () { host.innerHTML = ''; };
  }

  /* ---------------- 导出 CSV ---------------- */
  function exportCSV() {
    var rows = filtered().sort(function (a, b) { return String(a.sid).localeCompare(String(b.sid)); });
    if (!rows.length) { alert('没有可导出的数据'); return; }
    var csv = '﻿学号,姓名,班级,单词有效时长(秒),单词配对(对),单词点击,单词练习段,单词活跃天数,' +
      '听力条数,听力分钟,听力天数,阅读浏览(秒),翻译次数,翻译命中,翻译总数,翻译正确率,' +
      '理解题次数,理解题答对,理解题总数,理解题正确率,最近活动\n';
    rows.forEach(function (it) {
      csv += ['"' + String(it.sid).replace(/"/g, '""') + '"',
        '"' + String(it.name || '').replace(/"/g, '""') + '"',
        '"' + String(it.className || '').replace(/"/g, '""') + '"',
        it.wSec, it.wPairs, it.wClicks, it.wSegs, it.wActiveDays,
        it.lCnt, it.lMin, it.lDays,
        it.cBrowse,
        it.cTrans, it.cHit, it.cDen, P.pct(it.cHit, it.cDen) || '',
        it.cQuiz, it.cOk, it.cQDen, P.pct(it.cOk, it.cQDen) || '',
        '"' + lastOf(it) + '"'
      ].join(',') + '\n';
    });
    csv += '\n说明,自测号（2601 / 9999999999 / __test__ 前缀）不计入任何统计\n';
    csv += '说明,四级日报站刚上线，四级列为 0 表示学生还没做过，不表示没数据\n';
    var a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = '教师统一看板_' + P.todayStr() + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  /* ---------------- 事件绑定 ---------------- */
  function bindGate() {
    var go = document.getElementById('tGo');
    if (!go) return;
    function submit() {
      var id = (document.getElementById('tId') || {}).value || '';
      var pw = (document.getElementById('tPass') || {}).value || '';
      if (!id || !pw) { alert('请填工号和密码'); return; }
      go.disabled = true; go.innerText = '校验中…';
      TH.teacherLogin(id.trim(), pw).then(function () {
        S.authed = true; S.teacherId = id.trim();
        var root = $('#root');
        root.innerHTML = '<div class="loading">校验通过，正在拉取数据…</div>';
        return loadAll(function (m) { var e = document.getElementById('loadmsg'); if (e) e.textContent = m; });
      }).catch(function (e) {
        go.disabled = false; go.innerText = '进入看板';
        alert('登录失败：' + ((e && e.message === 'unauthorized') ? '工号或密码不对' : (e && e.message) || '网络错误'));
      });
    }
    go.onclick = submit;
    var pwEl = document.getElementById('tPass');
    if (pwEl) pwEl.onkeydown = function (ev) { if (ev.key === 'Enter') submit(); };
  }

  function bindDash() {
    document.querySelectorAll('.tab').forEach(function (b) {
      b.onclick = function () { S.view = b.getAttribute('data-view'); render(); };
    });
    var q = document.getElementById('fQ');
    if (q) {
      q.oninput = function () {
        S.q = q.value;
        var pos = q.selectionStart;
        render();
        var nq = document.getElementById('fQ');
        if (nq) { nq.focus(); try { nq.setSelectionRange(pos, pos); } catch (e) { } }
      };
    }
    var fc = document.getElementById('fClass');
    if (fc) fc.onchange = function () { S.classFilter = fc.value; buildIndex(); render(); };
    var fu = document.getElementById('fUnit');
    if (fu) fu.onchange = function () { S.unitFilter = fu.value; buildIndex(); render(); };

    var br = document.getElementById('btn-reload');
    if (br) br.onclick = function () { loadAll(); };
    var be = document.getElementById('btn-export');
    if (be) be.onclick = exportCSV;
    var bo = document.getElementById('btn-out');
    if (bo) bo.onclick = function () { S.authed = false; S.byStudent = {}; render(); };

    document.querySelectorAll('.mini').forEach(function (b) {
      b.onclick = function () { showDetail(b.getAttribute('data-sid')); };
    });
  }

  global.THApp = { S: S, loadAll: loadAll, buildIndex: buildIndex, render: render, showDetail: showDetail, exportCSV: exportCSV };
})(typeof window !== 'undefined' ? window : this);
