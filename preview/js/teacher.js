/* =====================================================================
   四级精读日报 · 老师端看板
   ---------------------------------------------------------------------
   看板：今日活跃 / 累计参与 / 今日浏览时长 / 今日做题数 / 平均采分点命中率
   明细：学号 姓名 班级 最近学习时间 累计时长 累计做题 累计采分点命中率 已完成期数
   缺席：今天没来的学生
   导出：CSV（带 UTF-8 BOM，Excel 打开中文不乱码）
   🔴 只读 class_key='CET4' 的日志行，绝不删改老师词汇站的任何行
   ===================================================================== */
(function (global) {
  'use strict';

  var SKEY = 'cet4.teacher.session';

  // 🔴 自测号（老师/教研自用）不进老师看板。
  //    权威判据在 api.js 的 isTestId（单一权威），这里只调用，不另立一套标准。
  var API = global.CET4;
  function isTestId(id) {
    return !!(API && API.isTestId && API.isTestId(id));
  }
  function notTest(l) {
    return !isTestId(l && l.student_id);
  }

  /* =====================================================================
     一、纯函数区（自检脚本 node 直接调用）
     ===================================================================== */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function fmtMin(sec) {
    var m = Math.floor((sec || 0) / 60);
    var s = Math.round((sec || 0) % 60);
    return m > 0 ? (m + ' 分 ' + s + ' 秒') : (s + ' 秒');
  }
  function pct(ok, total) {
    if (!total) return '—';
    return Math.round(ok / total * 100) + '%';
  }

  function filterLogs(logs, opt) {
    opt = opt || {};
    var from = opt.from || '', to = opt.to || '', cls = opt.class_key || '';
    return (logs || []).filter(function (l) {
      if (from && l.ts_date && l.ts_date < from) return false;
      if (to && l.ts_date && l.ts_date > to) return false;
      if (cls && l.class_key !== cls) return false;
      return true;
    });
  }

  // 每人汇总 + 看板指标
  /* ---------------------------------------------------------------------
     翻译练习专项统计（2026-10-24 新增）
     ---------------------------------------------------------------------
     三个问题：
       ① 全班平均信息完整度是多少？
       ② 档位分布长什么样（多少人第 1 档、多少人第 5 档）？
       ③ 🔴 全班最弱的 3 个采分点是什么？（下一节课该讲什么）
     ⚠️ 口径（UI 上必须写清，别让老师误读）：
       · 「信息完整度」= 采分点覆盖率，**不是**四级最终分
       · 「最弱采分点」的分母 = **今天的翻译作答人次**
         （速练 2 句模式下每次只判 7 个点，5 句全练判 13 个 ⇒ 只能近似）
     --------------------------------------------------------------------- */
  function transStats(logs, today) {
    var recs = (logs || []).filter(function (l) {
      return l.kind === '翻译练习' && notTest(l) && (today ? l.ts_date === today : true);
    });
    var withComp = recs.filter(function (l) { return l.comp >= 0; });
    var compSum = 0, compN = 0;
    withComp.forEach(function (l) { compSum += l.comp; compN++; });
    var avgComp = compN ? Math.round(compSum / compN) : -1;

    // 档位分布
    var bands = { '第1档': 0, '第2档': 0, '第3档': 0, '第4档': 0, '第5档': 0 };
    var bandN = 0;
    recs.forEach(function (l) {
      if (l.band && bands[l.band] !== undefined) { bands[l.band]++; bandN++; }
    });

    // 最弱采分点：按中文表达聚合漏掉人次
    var missCount = {}, missTotal = 0;
    recs.forEach(function (l) {
      if (!l.miss || !l.miss.length) return;
      missTotal++;
      l.miss.forEach(function (cn) {
        if (!cn) return;
        missCount[cn] = (missCount[cn] || 0) + 1;
      });
    });
    var weak = Object.keys(missCount).map(function (cn) {
      return { cn: cn, n: missCount[cn], pct: missTotal ? Math.round(missCount[cn] / missTotal * 100) : 0 };
    }).sort(function (a, b) {
      if (b.n !== a.n) return b.n - a.n;
      return a.cn.localeCompare(b.cn);
    }).slice(0, 3);

    // 🔴 语法错误类型分布（老师最想看的一个：全班最常犯哪几类错）
    var errCount = {};
    recs.forEach(function (l) {
      (l.errs || []).forEach(function (t) {
        if (!t) return;
        errCount[t] = (errCount[t] || 0) + 1;
      });
    });
    var errTypes = Object.keys(errCount).map(function (t) {
      return { type: t, n: errCount[t] };
    }).sort(function (a, b) { return b.n - a.n || a.type.localeCompare(b.type); });

    return {
      attempts: recs.length,            // 作答人次
      err_types: errTypes,              // 语法错误类型（按人次降序）
      err_total: Object.keys(errCount).length ? Object.keys(errCount).reduce(function (a, k) { return a + errCount[k]; }, 0) : 0,
      students: (function () {
        var s = {};
        recs.forEach(function (l) { s[String(l.student_id)] = 1; });
        return Object.keys(s).length;
      })(),
      avg_comp: avgComp,                // 平均信息完整度（-1 = 老日志没有这个字段）
      with_comp: compN,                 // 有完整度数据的条数
      bands: bands, band_n: bandN,
      weak: weak, weak_base: missTotal  // 最弱采分点的分母（答了但有漏点的份数）
    };
  }

  /* 🔴 AI 精批统计 —— **与本地初判分开，不混算**
   *   理由：AI 批改是**学生自愿点**的（按需调用）⇒ 样本是「点了 AI 的那部分学生」
   *        ⇒ 把它混进「全班平均档位」会得出一个只代表子集的结论，却看着像全班数据。
   *   ⇒ 这里单独统计，并把样本数显式写出来，让老师知道分母是谁。 */
  function aiStats(logs, today) {
    var recs = (logs || []).filter(function (l) {
      return l.kind === 'AI精批' && notTest(l) && (today ? l.ts_date === today : true);
    });
    /* 🔴 口径：**每人只算最新一次**
       理由：学生可以反复点 AI（每次都花钱）⇒ 若按「次」算，
             反复点的那个学生会把平均分和档位分布整个带偏。
       ⇒ teacherData 取的是 `order=id.desc`（最新在前）⇒ 第一次遇到的就是最新的那条。
       ⇒ 「点了多少次」单独报（attempts），统计只用每人最新一次（uniq）。 */
    var byStudent = {};
    recs.forEach(function (l) {
      var sid = String(l.student_id);
      if (!byStudent[sid]) byStudent[sid] = l;
    });
    var uniq = Object.keys(byStudent).map(function (k) { return byStudent[k]; });

    var bands = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    var bandN = 0, scoreSum = 0, scoreN = 0;
    var errCount = {};
    uniq.forEach(function (l) {
      var b = parseInt(l.aiBand, 10);
      if (b >= 1 && b <= 5) { bands[b]++; bandN++; }
      /* 🔴 分数必须有上限校验：四级满分 15 ⇒ 超出 0..15 的值是脏数据
         （模型抽风 / 日志被外部改 / 解析错位）⇒ 剔掉，不进平均。
         不剔的话，一个 99 分能把整班平均分抬飞。 */
      if (l.aiScore >= 0 && l.aiScore <= 15) { scoreSum += l.aiScore; scoreN++; }
      (l.errs || []).forEach(function (t) {
        if (!t) return;
        errCount[t] = (errCount[t] || 0) + 1;
      });
    });
    var errTypes = Object.keys(errCount).map(function (t) {
      return { type: t, n: errCount[t] };
    }).sort(function (a, b) { return b.n - a.n || a.type.localeCompare(b.type); });
    return {
      attempts: recs.length,            // 点了 AI 的总次数
      students: uniq.length,            // 人数（统计口径：每人最新一次）
      bands: bands, band_n: bandN,
      avg_score: scoreN ? Math.round(scoreSum / scoreN * 100) / 100 : -1,
      score_n: scoreN,
      err_types: errTypes
    };
  }

  function aggregate(logs, roster, today, allLogs) {
    // 🔴 自测号的记录一律先剔掉（看板 / 明细 / CSV 全干净）
    logs = (logs || []).filter(notTest);
    allLogs = (allLogs || logs || []).filter(notTest);
    var byId = {}, order = [];
    (roster || []).forEach(function (s) {
      var id = String(s.id);
      byId[id] = {
        student_id: id, name: s.name, class_key: s.class_key, class_name: s.class_name,
        browse_sec: 0, exercises: 0, quiz_ok: 0, quiz_total: 0,
        trans_hit: 0, trans_total: 0, trans_comp_sum: 0, trans_comp_n: 0, issues: {}, last_ts: '', count: 0, unknown: false
      };
      order.push(id);
    });
    function slot(id, l) {
      var b = byId[id];
      if (!b) {
        b = {
          student_id: id, name: (l && l.name) || id,
          class_key: (l && l.class_key) || '', class_name: (l && l.class_name) || '',
          browse_sec: 0, exercises: 0, quiz_ok: 0, quiz_total: 0,
          trans_hit: 0, trans_total: 0, trans_comp_sum: 0, trans_comp_n: 0, issues: {}, last_ts: '', count: 0, unknown: true
        };
        byId[id] = b;
        order.push(id);
      }
      return b;
    }
    function fold(b, l) {
      b.count++;
      if (l.kind === '浏览') {
        b.browse_sec += l.sec || l.duration_sec || 0;
        if (l.issue_date) b.issues[l.issue_date] = 1;
      } else if (l.kind === '翻译练习') {
        b.exercises++; b.trans_hit += l.num || 0; b.trans_total += l.den || 0;
        if (l.comp >= 0) { b.trans_comp_sum += l.comp; b.trans_comp_n++; }
      } else if (l.kind === '理解题') {
        b.exercises++; b.quiz_ok += l.num || 0; b.quiz_total += l.den || 0;
      }
      var ts = (l.ts_date || '') + ' ' + (l.time || '');
      if (ts > b.last_ts) b.last_ts = ts;
    }

    (logs || []).forEach(function (l) { fold(slot(String(l.student_id), l), l); });

    var students = order.map(function (id) { return byId[id]; }).map(function (b) {
      return {
        student_id: b.student_id, name: b.name, class_key: b.class_key, class_name: b.class_name,
        browse_sec: b.browse_sec, browse_min: Math.round(b.browse_sec / 60),
        exercises: b.exercises,
        quiz_ok: b.quiz_ok, quiz_total: b.quiz_total,
        // 🔴 理解题已去掉 ⇒ 「正确率」改用**采分点命中率**（转译练习的命中/总数）
        accuracy: pct(b.trans_hit, b.trans_total),
        trans_hit: b.trans_hit, trans_total: b.trans_total,
        trans_comp: b.trans_comp_n ? Math.round(b.trans_comp_sum / b.trans_comp_n) : -1,
        trans_band: (function () {
          var last = null;
          (logs || []).forEach(function (l) {
            if (l.kind === '翻译练习' && String(l.student_id) === b.student_id && l.band) last = l.band;
          });
          return last || '';
        })(),
        issues_done: Object.keys(b.issues).length,
        last_ts: b.last_ts || '—',
        last_date: b.last_ts ? b.last_ts.slice(0, 10) : '',
        count: b.count
      };
    });

    // 今日指标：按日志时间戳算（跟「今天没来」口径一致）
    var todaySet = {}, rangeSet = {}, todayBrowse = 0, todayEx = 0;
    var todayQuizOk = 0, todayQuizTotal = 0;
    var todayTransHit = 0, todayTransTotal = 0;
    (allLogs || logs || []).forEach(function (l) {
      if (today && l.ts_date === today) {
        todaySet[String(l.student_id)] = 1;
        if (l.kind === '浏览') todayBrowse += l.sec || l.duration_sec || 0;
        if (l.kind === '翻译练习' || l.kind === '理解题') todayEx++;
        if (l.kind === '理解题') { todayQuizOk += l.num || 0; todayQuizTotal += l.den || 0; }
        if (l.kind === '翻译练习') { todayTransHit += l.num || 0; todayTransTotal += l.den || 0; }
      }
    });
    (logs || []).forEach(function (l) { rangeSet[String(l.student_id)] = 1; });

    var quizOk = 0, quizTotal = 0;          // 旧版理解题记录（保留，不丢老数据）
    var transHit = 0, transTotal = 0;       // 采分点命中（现在的主口径）
    (logs || []).forEach(function (l) {
      if (l.kind === '理解题') { quizOk += l.num || 0; quizTotal += l.den || 0; }
      if (l.kind === '翻译练习') { transHit += l.num || 0; transTotal += l.den || 0; }
    });

    var absent = (roster || []).filter(function (s) { return !todaySet[String(s.id)]; })
      .map(function (s) {
        return { student_id: String(s.id), name: s.name, class_key: s.class_key, class_name: s.class_name };
      });

    return {
      students: students,
      absent: absent,
      trans: transStats(logs, today),
      ai: aiStats(logs, today),
      totals: {
        roster: (roster || []).length,
        today_active: Object.keys(todaySet).length,
        range_active: Object.keys(rangeSet).length,
        today_browse_sec: todayBrowse,
        today_exercises: todayEx,
        accuracy: pct(transHit, transTotal),          // 采分点命中率（主口径）
        quiz_ok: quizOk, quiz_total: quizTotal,         // 旧版理解题（留档）
        today_accuracy: pct(todayTransHit, todayTransTotal),   // 今日采分点命中率
        total_logs: (logs || []).length
      }
    };
  }

  // CSV（带 UTF-8 BOM）
  function toCSV(rows) {
    var csv = (rows || []).map(function (r) {
      return (r || []).map(function (c) {
        c = (c === null || c === undefined) ? '' : String(c);
        return /[",\n\r]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c;
      }).join(',');
    }).join('\r\n');
    return '﻿' + csv;
  }

  global.CET4T = {
    esc: esc, fmtMin: fmtMin, pct: pct,
    filterLogs: filterLogs, aggregate: aggregate, toCSV: toCSV,
    transStats: transStats,
    aiStats: aiStats
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
  var tok = null;
  var data = { roster: [], logs: [], allLogs: [] };
  var rangeDays = 6;                 // 默认近 7 天
  var tab = 'detail';

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

  /* ---------------- 字号 / 高对比 ---------------- */
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

  /* ---------------- 启动 ---------------- */
  function boot() {
    if (CET4.isDemo()) $('demoBanner').hidden = false;
    CET4.prefs.applyAll();
    bindPrefs();
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(SKEY) || 'null'); } catch (e) { saved = null; }
    if (saved && saved.token) {
      tok = saved.token;
      $('viewLogin').hidden = true;
      $('viewDash').hidden = false;
      $('btnLogout').hidden = false;
      setWho();
      applyRangeButtons();
      load();
    } else {
      $('viewLogin').hidden = false;
      $('viewDash').hidden = true;
      applyRangeButtons();
    }
  }
  function setWho() {
    var w = $('who');
    w.textContent = '';
    var b = document.createElement('b');
    b.textContent = '教师';
    w.appendChild(b);
  }

  /* ---------------- 登录 ---------------- */
  $('btnLogin').addEventListener('click', function () {
    var id = $('tid').value.trim();
    var pwd = $('tpwd').value;
    if (!id || !pwd) { setMsg($('loginMsg'), '请把工号和密码都填上', 'err'); return; }
    $('btnLogin').disabled = true;
    CET4.teacherLogin(id, pwd).then(function (r) {
      $('btnLogin').disabled = false;
      if (!r || !r.ok) { setMsg($('loginMsg'), (r && r.msg) || '登录失败', 'err'); return; }
      tok = r.token;
      try { localStorage.setItem(SKEY, JSON.stringify({ token: tok, name: r.name })); } catch (e) { }
      setMsg($('loginMsg'), '');
      $('viewLogin').hidden = true;
      $('viewDash').hidden = false;
      $('btnLogout').hidden = false;
      setWho();
      applyRangeButtons();
      load();
    }).catch(function (e) {
      $('btnLogin').disabled = false;
      setMsg($('loginMsg'), (e && e.message) || '登录失败', 'err');
    });
  });
  $('btnLogout').addEventListener('click', function () {
    tok = null;
    try { localStorage.removeItem(SKEY); } catch (e) { }
    $('viewDash').hidden = true;
    $('viewLogin').hidden = false;
    $('btnLogout').hidden = true;
  });

  /* ---------------- 筛选 ---------------- */
  function applyRangeButtons() {
    Array.prototype.forEach.call($('rangeBar').children, function (b) {
      var d = parseInt(b.getAttribute('data-days'), 10);
      b.classList.toggle('on', d === rangeDays);
    });
  }
  Array.prototype.forEach.call($('rangeBar').children, function (b) {
    b.addEventListener('click', function () {
      rangeDays = parseInt(b.getAttribute('data-days'), 10);
      if (rangeDays === -1) { $('fFrom').value = ''; $('fTo').value = ''; }
      else {
        var d = new Date(); d.setDate(d.getDate() - rangeDays);
        $('fFrom').value = CET4.fmtDate(d);
        $('fTo').value = CET4.todayStr();
      }
      applyRangeButtons();
      load();
    });
  });
  function onManualDate() {
    rangeDays = -2;
    applyRangeButtons();
    load();
  }
  $('fFrom').addEventListener('change', onManualDate);
  $('fTo').addEventListener('change', onManualDate);
  $('fClass').addEventListener('change', load);
  $('fSearch').addEventListener('input', render);

  function rangeParams() {
    var from = $('fFrom').value || '';
    var to = $('fTo').value || '';
    if (!from && !to && rangeDays >= 0) {
      var d = new Date(); d.setDate(d.getDate() - rangeDays);
      from = CET4.fmtDate(d);
      to = CET4.todayStr();
    }
    return { from: from, to: to, class_key: $('fClass').value || '' };
  }

  /* ---------------- 取数 ---------------- */
  function load() {
    if (!tok) return;
    setMsg($('dashMsg'), '正在读取…');
    var p = rangeParams();
    CET4.teacherData(tok, p).then(function (r) {
      if (!r || !r.ok) { setMsg($('dashMsg'), (r && r.msg) || '读取失败', 'err'); return; }
      data = r;
      setMsg($('dashMsg'), '');
      fillClasses(r.roster);
      render();
    }).catch(function (e) { setMsg($('dashMsg'), (e && e.message) || '读取失败', 'err'); });
  }

  var filledClasses = false;
  function fillClasses(roster) {
    if (filledClasses) return;
    var sel = $('fClass');
    sel.textContent = '';
    var o0 = document.createElement('option');
    o0.value = ''; o0.textContent = '全部班级';
    sel.appendChild(o0);
    var seen = {};
    (roster || []).forEach(function (s) {
      if (!s.class_key || seen[s.class_key]) return;
      seen[s.class_key] = 1;
      var o = document.createElement('option');
      o.value = s.class_key;
      o.textContent = s.class_name || s.class_key;
      sel.appendChild(o);
    });
    filledClasses = true;
  }

  /* ---------------- 渲染 ---------------- */
  function matchSearch(s) {
    var q = $('fSearch').value.trim().toLowerCase();
    if (!q) return true;
    return String(s.student_id).toLowerCase().indexOf(q) >= 0 ||
      String(s.name || '').toLowerCase().indexOf(q) >= 0 ||
      String(s.class_name || '').toLowerCase().indexOf(q) >= 0;
  }

  function render() {
    var p = rangeParams();
    var logs = CET4T.filterLogs(data.allLogs || data.logs || [], p);
    var agg = CET4T.aggregate(logs, data.roster || [], CET4.todayStr(), data.allLogs || data.logs || []);
    renderStats(agg);
    renderTrans(agg);
    renderAI(agg);
    renderStudents(agg.students.filter(matchSearch));
    renderLogs(logs.filter(function (l) {
      var q = $('fSearch').value.trim().toLowerCase();
      if (!q) return true;
      return String(l.student_id).toLowerCase().indexOf(q) >= 0 ||
        String(l.name || '').toLowerCase().indexOf(q) >= 0 ||
        String(l.class_name || '').toLowerCase().indexOf(q) >= 0;
    }));
    renderAbsent(agg.absent.filter(matchSearch));
    renderClasses(data.roster || []);
  }

  /* ---------------- 课堂速练 · 翻译练习 ---------------- */
  function renderTrans(agg) {
    var t = agg.trans || {};
    var box = $('transBox');
    var scope = $('transScope');
    box.textContent = '';
    if (!t.attempts) {
      scope.textContent = '';
      box.innerHTML = '<p class="lead">这个区间里还没有翻译练习记录。'
        + '学生交卷后，这里会出现「全班平均信息完整度」「档位分布」和'
        + '<b>全班最弱的采分点</b>（下一节课该讲什么）。</p>';
      return;
    }
    scope.textContent = t.students + ' 人交了 ' + t.attempts + ' 次';

    // ① 平均信息完整度
    var compHtml = t.avg_comp >= 0
      ? '<div class="tbig"><b>' + t.avg_comp + '%</b><span>平均信息完整度</span>'
        + '<span class="muted">（' + t.with_comp + ' 人次有数据）</span></div>'
      : '<div class="tbig"><b>—</b><span>平均信息完整度</span>'
        + '<span class="muted">（这些记录是旧版，没有完整度字段）</span></div>';

    // ② 档位分布
    var BAND_NOTE = {
      '第1档': '准确表达 13–15 分',
      '第2档': '基本表达 10–12 分',
      '第3档': '勉强表达 7–9 分',
      '第4档': '仅表达少部分 4–6 分',
      '第5档': '支离破碎 1–3 分'
    };
    var bands = t.bands || {};
    var bandHtml = '<div class="tband-wrap"><div class="tband-lbl">档位分布</div><div class="tband-row">';
    ['第1档', '第2档', '第3档', '第4档', '第5档'].forEach(function (b) {
      var n = bands[b] || 0;
      var pctw = t.band_n ? Math.round(n / t.band_n * 100) : 0;
      bandHtml += '<div class="tband">'
        + '<div class="tb-n">' + n + '</div>'
        + '<div class="tb-bar"><i style="width:' + pctw + '%"></i></div>'
        + '<div class="tb-l">' + b + '</div>'
        + '<div class="tb-s">' + BAND_NOTE[b] + '</div>'
        + '</div>';
    });
    bandHtml += '</div></div>';

    // ③ 🔴 全班最弱的采分点
    var weakHtml;
    if (t.weak && t.weak.length) {
      weakHtml = '<div class="tweak">'
        + '<div class="tweak-h">🔴 全班最弱的 ' + t.weak.length + ' 个采分点</div>'
        + '<div class="tweak-list">'
        + t.weak.map(function (w, i) {
          return '<div class="tw"><span class="tw-i">' + (i + 1) + '</span>'
            + '<span class="tw-cn">' + esc(w.cn) + '</span>'
            + '<span class="tw-n">' + w.n + ' 人漏掉（' + w.pct + '%）</span></div>';
        }).join('')
        + '</div>'
        + '<p class="muted tw-note">口径：今天 ' + t.weak_base
        + ' 份有漏点的答卷里，有多少人漏掉了这一条。'
        + '漏得多 ⇒ 下一节课优先讲它。</p></div>';
    } else {
      weakHtml = '<div class="tweak"><div class="tweak-h">🔴 全班最弱的采分点</div>'
        + '<p class="lead">还没有漏点明细。'
        + '（学生端升级到带「漏点」记录的版本后，交卷就会自动记上。）</p></div>';
    }

    // ④ 🔴 全班最常见的语法错误类型
    var errHtml;
    if (t.err_types && t.err_types.length) {
      var top = t.err_types.slice(0, 5);
      var maxn = top[0].n || 1;
      errHtml = '<div class="terr">'
        + '<div class="terr-h">🔴 全班最常见的 ' + top.length + ' 类语法错误</div>'
        + top.map(function (e, i) {
          return '<div class="te"><span class="te-i">' + (i + 1) + '</span>'
            + '<span class="te-t">' + esc(e.type) + '</span>'
            + '<span class="te-bar"><i style="width:' + Math.round(e.n / maxn * 100) + '%"></i></span>'
            + '<span class="te-n">' + e.n + ' 人次</span></div>';
        }).join('')
        + '<p class="muted tw-note">口径：所选区间内共 ' + t.err_total
        + ' 处错误类型（每份答卷每类只算 1 次）。'
        + '机器查的是常见错误类型，可能有误报 ⇒ 当线索用，别当判卷。</p></div>';
    } else {
      errHtml = '<div class="terr"><div class="terr-h">🔴 全班最常见的语法错误类型</div>'
        + '<p class="lead">还没有错误类型明细。（学生端交卷时自动记录；旧记录没有这个字段。）</p></div>';
    }

    box.innerHTML = compHtml + bandHtml + weakHtml + errHtml;
  }

  /* ---------------- AI 精批（单独口径） ---------------- */
  function renderAI(agg) {
    var a = agg.ai || {};
    var box = $('aiBox');
    var scope = $('aiScope');
    if (!box) return;
    box.textContent = '';
    if (!a.attempts) {
      scope.textContent = '';
      box.innerHTML = '<p class="lead">这个区间里还没有 AI 批改记录。'
        + '学生在学生端点「AI 批改」之后，这里会出现 AI 的定档、分数和它判出的错误类型。'
        + '<br><span class="muted">AI 批改是<b>学生自愿点</b>的（一次约 ¥0.0002，按需调用），'
        + '所以这里的样本只代表<b>点了 AI 的那部分学生</b>，不能当作全班的平均分。</span></p>';
      return;
    }
    scope.textContent = '共点 ' + a.attempts + ' 次 · ' + a.students + ' 人（每人只算最新一次）';

    var head = '<div class="tbig"><b>' + (a.avg_score >= 0 ? a.avg_score : '—') + '</b>'
      + '<span>AI 平均得分（满分 15）</span>'
      + '<span class="muted">' + a.students + ' 人 · 定档 ' + a.band_n + ' 人</span></div>';

    var bandHtml = '<div class="tband-wrap"><div class="tband-lbl">AI 定档分布</div><div class="tband-row">';
    [1, 2, 3, 4, 5].forEach(function (no) {
      var ranges = { 1: '13-15', 2: '10-12', 3: '7-9', 4: '4-6', 5: '1-3' };
      var names = { 1: '准确表达', 2: '基本表达', 3: '勉强表达', 4: '仅表达少部分', 5: '支离破碎' };
      var n = a.bands[no] || 0;
      bandHtml += '<div class="tband"><div class="tb-lbl">第' + no + '档</div>'
        + '<div class="tb-n">' + n + '</div>'
        + '<div class="tb-r">' + ranges[no] + ' 分</div>'
        + '<div class="tb-m">' + names[no] + '</div></div>';
    });
    bandHtml += '</div></div>';

    var errHtml = '';
    if (a.err_types && a.err_types.length) {
      errHtml = '<div class="terr"><div class="terr-h">🔴 AI 判出的主要错误类型</div>'
        + '<div class="ttags">' + a.err_types.slice(0, 8).map(function (e) {
          return '<span class="ttag">' + esc(e.type) + ' <b>' + e.n + '</b></span>';
        }).join('') + '</div>'
        + '<p class="lead">按<b>人次</b>算（同一份答卷里同一类错只算 1 次），'
        + '和上面「本地初判」的错误类型是<b>两套口径</b>，不要相加。</p></div>';
    }

    var note = '<div class="tres-note">'
      + '⚠️ 这几项<b>不能和上面「全班平均完整度 / 档位分布」相加或平均</b>：'
      + 'AI 批改是<b>学生自愿点</b>的，样本天然偏少（愿意点的人通常是想改好的那批）。'
      + '要看全班整体，仍以「课堂速练」那块的初判数据为准。<br>'
      + '统计口径：<b>每个学生只算最新一次</b>（点过多次也只取最后一次）；'
      + '错误类型按<b>人次</b>算（同一份答卷里同一类错只算 1 次），'
      + '和「本地初判」的错误类型是<b>两套口径</b>，不要相加。</div>';

    box.innerHTML = head + bandHtml + errHtml + note;
  }

  function renderStats(agg) {
    var t = agg.totals;
    var box = $('tstats');
    box.textContent = '';
    [
      ['今日活跃人数', t.today_active + ' 人', '名册 ' + t.roster + ' 人'],
      ['累计参与人数', t.range_active + ' 人', '本区间有记录'],
      ['今日总浏览时长', CET4T.fmtMin(t.today_browse_sec), '30 秒内有交互才计'],
      ['今日总做题数', t.today_exercises + ' 次', '翻译 + 理解题'],
      ['平均采分点命中率', t.accuracy, '今日 ' + t.today_accuracy]
    ].forEach(function (p) {
      var d = document.createElement('div');
      d.className = 'stat';
      var n = document.createElement('div');
      n.className = 'n';
      n.textContent = p[1];
      d.appendChild(n);
      var k = document.createElement('div');
      k.className = 'k';
      k.textContent = p[0];
      d.appendChild(k);
      var k2 = document.createElement('div');
      k2.className = 'k';
      k2.textContent = p[2];
      d.appendChild(k2);
      box.appendChild(d);
    });
  }

  function renderStudents(rows) {
    var tb = $('tblSum').querySelector('tbody');
    tb.textContent = '';
    rows = rows.slice().sort(function (a, b) {
      if (b.browse_sec !== a.browse_sec) return b.browse_sec - a.browse_sec;
      return String(a.student_id).localeCompare(String(b.student_id));
    });
    $('sumCount').textContent = '共 ' + rows.length + ' 人';
    if (!rows.length) {
      var tr0 = document.createElement('tr'), td0 = document.createElement('td');
      td0.colSpan = 8; td0.className = 'empty'; td0.textContent = '这个区间没有记录';
      tr0.appendChild(td0); tb.appendChild(tr0); return;
    }
    var today = CET4.todayStr();
    rows.forEach(function (s) {
      var tr = document.createElement('tr');
      if (s.last_date === today) tr.className = 'today';
      [s.student_id, s.name, s.class_name || s.class_key, s.last_ts,
        CET4T.fmtMin(s.browse_sec), s.exercises, s.accuracy, s.issues_done].forEach(function (v, i) {
        var td = document.createElement('td');
        if (i === 4 || i === 5 || i === 7) td.className = 'num';
        td.textContent = v;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
  }

  function renderLogs(logs) {
    var tb = $('tblLog').querySelector('tbody');
    tb.textContent = '';
    var CAP = 500;
    var rows = logs.slice(0, CAP);
    $('logCount').textContent = '共 ' + logs.length + ' 条' +
      (logs.length > CAP ? '（表格只显示最近 ' + CAP + ' 条，导出 CSV 是全量）' : '');
    if (!rows.length) {
      var tr0 = document.createElement('tr'), td0 = document.createElement('td');
      td0.colSpan = 7; td0.className = 'empty'; td0.textContent = '这个区间没有记录';
      tr0.appendChild(td0); tb.appendChild(tr0); return;
    }
    var today = CET4.todayStr();
    rows.forEach(function (l) {
      var tr = document.createElement('tr');
      if (l.ts_date === today) tr.className = 'today';
      [l.ts_date + ' ' + (l.time || ''), l.student_id, l.name || '', l.class_name || l.class_key,
        l.kind || '', l.action || ''].forEach(function (v, i) {
        var td = document.createElement('td');
        if (i === 5) td.className = 'wrapcell';
        td.textContent = v;
        tr.appendChild(td);
      });
      var tdS = document.createElement('td');
      tdS.className = 'num';
      tdS.textContent = l.kind === '浏览' ? (l.sec || 0) + ' 秒' : (l.den ? l.num + '/' + l.den : '—');
      tr.appendChild(tdS);
      tb.appendChild(tr);
    });
  }

  function renderAbsent(rows) {
    var tb = $('tblZero').querySelector('tbody');
    tb.textContent = '';
    $('zeroCount').textContent = '共 ' + rows.length + ' 人';
    if (!rows.length) {
      var tr0 = document.createElement('tr'), td0 = document.createElement('td');
      td0.colSpan = 3; td0.className = 'empty'; td0.textContent = '今天全班都有记录';
      tr0.appendChild(td0); tb.appendChild(tr0); return;
    }
    rows.forEach(function (s) {
      var tr = document.createElement('tr');
      [s.student_id, s.name, s.class_name || s.class_key].forEach(function (v) {
        var td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
  }

  function renderClasses(roster) {
    var tb = $('tblCls').querySelector('tbody');
    tb.textContent = '';
    var map = {};
    (roster || []).forEach(function (s) {
      var k = s.class_key || s.class_name;
      if (!map[k]) map[k] = { key: k, name: s.class_name || k, n: 0, active: 0, sec: 0, ex: 0 };
      map[k].n++;
    });
    var today = CET4.todayStr();
    (data.allLogs || []).filter(notTest).forEach(function (l) {
      var m = map[l.class_key];
      if (!m) return;
      if (l.ts_date === today) {
        m.active = 1;
        if (l.kind === '浏览') m.sec += l.sec || 0;
        if (l.kind === '翻译练习' || l.kind === '理解题') m.ex++;
      }
    });
    Object.keys(map).forEach(function (k) {
      var m = map[k];
      var tr = document.createElement('tr');
      [m.name, m.n, m.active ? m.active + ' 人' : '0 人', CET4T.fmtMin(m.sec), m.ex + ' 次'].forEach(function (v, i) {
        var td = document.createElement('td');
        if (i === 1 || i === 4) td.className = 'num';
        td.textContent = v;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
  }

  /* ---------------- 标签页 ---------------- */
  function setTab(t) {
    tab = t;
    $('paneDetail').hidden = (t !== 'detail');
    $('paneRaw').hidden = (t !== 'raw');
    $('paneZero').hidden = (t !== 'zero');
    $('paneCls').hidden = (t !== 'cls');
    $('tabDetail').classList.toggle('on', t === 'detail');
    $('tabRaw').classList.toggle('on', t === 'raw');
    $('tabZero').classList.toggle('on', t === 'zero');
    $('tabCls').classList.toggle('on', t === 'cls');
  }
  $('tabDetail').addEventListener('click', function () { setTab('detail'); });
  $('tabRaw').addEventListener('click', function () { setTab('raw'); });
  $('tabZero').addEventListener('click', function () { setTab('zero'); });
  $('tabCls').addEventListener('click', function () { setTab('cls'); });

  /* ---------------- 导出 CSV ---------------- */
  function download(name, rows) {
    var blob = new Blob([CET4T.toCSV(rows)], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  $('btnCsvSum').addEventListener('click', function () {
    var p = rangeParams();
    var logs = CET4T.filterLogs(data.allLogs || [], p);
    var agg = CET4T.aggregate(logs, data.roster || [], CET4.todayStr(), data.allLogs || []);
    var head = ['学号', '姓名', '班级', '最近学习时间', '累计时长(秒)', '累计做题(次)', '累计采分点命中率', '已完成期数', '累计浏览分钟'];
    var rows = [head].concat(agg.students.map(function (s) {
      return [s.student_id, s.name, s.class_name || s.class_key, s.last_ts,
        s.browse_sec, s.exercises, s.accuracy, s.issues_done, s.browse_min];
    }));
    download('四级精读_学生明细_' + CET4.todayStr() + '.csv', rows);
  });

  $('btnCsvLog').addEventListener('click', function () {
    var p = rangeParams();
    var logs = CET4T.filterLogs(data.allLogs || [], p);
    var head = ['时间', '学号', '姓名', '班级', '类型', '记录内容', '时长(秒)'];
    var rows = [head].concat(logs.map(function (l) {
      return [l.ts_date + ' ' + (l.time || ''), l.student_id, l.name || '', l.class_name || l.class_key,
        l.kind || '', l.action || '', l.sec || 0];
    }));
    download('四级精读_原始记录_' + CET4.todayStr() + '.csv', rows);
  });

  window.addEventListener('resize', syncTopH);
  window.addEventListener('load', syncTopH);
  boot();
})(typeof window !== 'undefined' ? window : globalThis);
