/* =====================================================================
   每日听力记录站 · 教师看板
   ===================================================================== */
(function () {
  'use strict';

  var SKEY = 'listen.teacher.session';
  var tok = null;
  var data = { classes: [], students: [], logs: [], totals: {} };
  var rangeDays = 6;          // 近7天
  var tab = 'detail';

  var $ = function (id) { return document.getElementById(id); };

  function toast(msg) {
    var t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(t._tm);
    t._tm = setTimeout(function () { t.hidden = true; }, 2600);
  }
  function setMsg(el, msg, kind) { el.textContent = msg || ''; el.className = 'msg' + (kind ? ' ' + kind : ''); }

  /* ---------------- 字号 / 高对比（墨水屏、老花眼） ---------------- */
  function applyZoom() {
    var z = parseInt(localStorage.getItem('listen.zoom') || '100', 10);
    document.documentElement.style.zoom = (z / 100);
    $('zoom').value = z;
  }
  $('btnAa').addEventListener('click', function () {
    var p = $('zoomPanel');
    p.hidden = !p.hidden;
  });
  function setZoom(z) {
    z = Math.max(90, Math.min(170, z));
    localStorage.setItem('listen.zoom', z);
    applyZoom();
  }
  function applyHc() {
    var on = localStorage.getItem('listen.hc') === '1';
    document.documentElement.classList.toggle('hc', on);
    $('btnHc').style.borderColor = on ? 'var(--ink)' : 'var(--line)';
    $('btnHc').style.background = on ? 'var(--ink)' : 'var(--card)';
    $('btnHc').style.color = on ? 'var(--card)' : 'var(--ink)';
  }
  applyZoom(); applyHc();
  $('zoom').addEventListener('input', function () { setZoom(parseInt(this.value, 10)); });
  $('btnZoomIn').addEventListener('click', function () { setZoom(parseInt($('zoom').value, 10) + 10); });
  $('btnZoomOut').addEventListener('click', function () { setZoom(parseInt($('zoom').value, 10) - 10); });
  $('btnHc').addEventListener('click', function () {
    localStorage.setItem('listen.hc', localStorage.getItem('listen.hc') === '1' ? '0' : '1');
    applyHc();
  });

  /* ---------------- 启动 ---------------- */
  function boot() {
    if (LISTEN.isDemo()) $('demoBanner').hidden = false;
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(SKEY) || 'null'); } catch (e) { saved = null; }
    if (saved && saved.token) {
      tok = saved.token;
      $('viewLogin').hidden = true;
      $('viewDash').hidden = false;
      setWho();
      load();
    } else {
      $('viewLogin').hidden = false;
      $('viewDash').hidden = true;
    }
    applyRangeButtons();
  }

  function setWho() {
    var n = document.createElement('b');
    n.textContent = '教师';
    $('who').textContent = '';
    $('who').appendChild(n);
  }

  /* ---------------- 登录 ---------------- */
  $('btnLogin').addEventListener('click', function () {
    var id = $('tid').value.trim();
    var pwd = $('tpwd').value;
    if (!id || !pwd) { setMsg($('loginMsg'), '请把工号和密码都填上', 'err'); return; }
    $('btnLogin').disabled = true;
    LISTEN.rpc('listen_teacher_login', { p_id: id, p_pass: pwd })
      .then(function (r) {
        $('btnLogin').disabled = false;
        if (!r || !r.ok) { setMsg($('loginMsg'), (r && r.msg) || '登录失败', 'err'); return; }
        tok = r.token;
        try { localStorage.setItem(SKEY, JSON.stringify({ token: tok, name: r.name })); } catch (e) { /* ignore */ }
        setMsg($('loginMsg'), '');
        $('viewLogin').hidden = true;
        $('viewDash').hidden = false;
        setWho();
        load();
      })
      .catch(function (e) {
        $('btnLogin').disabled = false;
        setMsg($('loginMsg'), e.message || '登录失败', 'err');
      });
  });

  $('btnLogout').addEventListener('click', function () {
    tok = null;
    try { localStorage.removeItem(SKEY); } catch (e) { /* ignore */ }
    $('viewDash').hidden = true;
    $('viewLogin').hidden = false;
  });

  /* ---------------- 筛选 ---------------- */
  function applyRangeButtons() {
    Array.prototype.forEach.call($('rangeBar').children, function (b) {
      var d = parseInt(b.getAttribute('data-days'), 10);
      b.classList.toggle('on', d === rangeDays);
      b.style.background = d === rangeDays ? 'var(--ink)' : 'var(--card)';
      b.style.color = d === rangeDays ? 'var(--card)' : 'var(--ink)';
    });
  }
  Array.prototype.forEach.call($('rangeBar').children, function (b) {
    b.addEventListener('click', function () {
      rangeDays = parseInt(b.getAttribute('data-days'), 10);
      if (rangeDays === -1) {
        $('fFrom').value = ''; $('fTo').value = '';
      } else {
        var d = new Date(); d.setDate(d.getDate() - rangeDays);
        $('fFrom').value = LISTEN.fmtDate(d);
        $('fTo').value = LISTEN.todayStr();
      }
      applyRangeButtons();
      load();
    });
  });
  function onManualDate() {          // 手动改日期 → 取消快捷区间高亮
    rangeDays = -2;
    applyRangeButtons();
    load();
  }
  $('fFrom').addEventListener('change', onManualDate);
  $('fTo').addEventListener('change', onManualDate);
  $('fClass').addEventListener('change', render);
  $('fSearch').addEventListener('input', render);

  function rangeParams() {
    var from = $('fFrom').value || '';
    var to = $('fTo').value || '';
    if (!from && !to && rangeDays >= 0) {
      var d = new Date(); d.setDate(d.getDate() - rangeDays);
      from = LISTEN.fmtDate(d);
      to = LISTEN.todayStr();
    }
    return { p_from: from || null, p_to: to || null, p_class: $('fClass').value || null };
  }

  /* ---------------- 取数 ---------------- */
  function load() {
    if (!tok) return;
    setMsg($('dashMsg'), '正在读取…');
    var p = rangeParams();
    LISTEN.rpc('listen_teacher_data', {
      p_token: tok, p_from: p.p_from, p_to: p.p_to, p_class: p.p_class
    }).then(function (r) {
      if (!r || !r.ok) { setMsg($('dashMsg'), (r && r.msg) || '读取失败', 'err'); return; }
      data = r;
      setMsg($('dashMsg'), '');
      fillClasses(r.classes);
      render();
    }).catch(function (e) { setMsg($('dashMsg'), e.message || '读取失败', 'err'); });
  }

  var filledClasses = false;
  function fillClasses(classes) {
    if (filledClasses) return;
    var sel = $('fClass');
    sel.textContent = '';
    var o0 = document.createElement('option');
    o0.value = ''; o0.textContent = '全部班级';
    sel.appendChild(o0);
    (classes || []).forEach(function (c) {
      var o = document.createElement('option');
      o.value = c.key;
      o.textContent = c.name + '（' + c.count + '人）';
      sel.appendChild(o);
    });
    filledClasses = true;
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    renderStats();
    renderLogs();
    renderSum();
    renderZero();
  }

  function renderStats() {
    var t = data.totals || {};
    var box = $('tstats');
    box.textContent = '';
    [
      ['今天提交', (t.today_students || 0) + ' 人', (t.today_count || 0) + ' 条'],
      ['今天时长', (t.today_minutes || 0) + ' 分', ''],
      ['区间时长', (t.total_minutes || 0) + ' 分', (t.total_logs || 0) + ' 条'],
      ['区间活跃', (t.active_students || 0) + ' 人', '名册 ' + (t.roster || 0) + ' 人']
    ].forEach(function (p) {
      var d = document.createElement('div');
      d.className = 'tstat';
      var n = document.createElement('div');
      n.className = 'n';
      n.textContent = p[1];
      d.appendChild(n);
      var k = document.createElement('div');
      k.className = 'k';
      k.textContent = p[0] + (p[2] ? ' · ' + p[2] : '');
      d.appendChild(k);
      box.appendChild(d);
    });
  }

  function matchSearch(s) {
    var q = $('fSearch').value.trim().toLowerCase();
    if (!q) return true;
    return String(s.student_id).toLowerCase().indexOf(q) >= 0 ||
           String(s.name || '').toLowerCase().indexOf(q) >= 0;
  }

  function clsName(key) {
    var c = (data.classes || []).filter(function (x) { return x.key === key; })[0];
    return c ? c.name : key;
  }

  function renderLogs() {
    var tb = $('tblLog').querySelector('tbody');
    tb.textContent = '';
    var today = LISTEN.todayStr();
    var all = (data.logs || []).filter(matchSearch);
    var CAP = 500;                      // 表格最多渲染 500 行，导出仍是全量
    var rows = all.slice(0, CAP);
    $('detailCount').textContent = '共 ' + all.length + ' 条' +
      (all.length > CAP ? '（表格只显示最近 ' + CAP + ' 条，导出 CSV 是全量）' : '') +
      ($('fSearch').value.trim() ? '（已按搜索过滤）' : '');
    if (!rows.length) {
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.colSpan = 9;
      td.className = 'empty';
      td.textContent = '这个区间没有记录';
      tr.appendChild(td); tb.appendChild(tr);
      return;
    }
    rows.forEach(function (l) {
      var tr = document.createElement('tr');
      if (l.date === today) tr.className = 'today';
      [String(l.student_id), l.name || '', clsName(l.class_key), l.date, l.time || '',
       l.minutes + '分'].forEach(function (v, i) {
        var td = document.createElement('td');
        if (i === 5) td.className = 'num';
        td.textContent = v;
        tr.appendChild(td);
      });
      var tdC = document.createElement('td');
      tdC.className = 'wrapcell';
      tdC.textContent = l.content + (l.note ? '（' + l.note + '）' : '');
      tr.appendChild(tdC);

      var tdCat = document.createElement('td');
      tdCat.textContent = l.category || '';
      tr.appendChild(tdCat);

      var tdX = document.createElement('td');
      var b = document.createElement('button');
      b.className = 'link';
      b.type = 'button';
      b.textContent = '删除';
      b.addEventListener('click', function () {
        if (!confirm('删除 ' + (l.name || '') + ' 在 ' + l.date + ' 的这条记录？')) return;
        LISTEN.rpc('listen_teacher_delete', { p_token: tok, p_id: l.id })
          .then(function () { toast('已删除'); load(); })
          .catch(function (e) { toast(e.message || '删除失败'); });
      });
      tdX.appendChild(b);
      tr.appendChild(tdX);
      tb.appendChild(tr);
    });
  }

  function renderSum() {
    var tb = $('tblSum').querySelector('tbody');
    tb.textContent = '';
    var rows = (data.students || []).filter(matchSearch).slice().sort(function (a, b) {
      if (b.minutes !== a.minutes) return b.minutes - a.minutes;
      return String(a.student_id).localeCompare(String(b.student_id));
    });
    $('sumCount').textContent = '共 ' + rows.length + ' 人';
    rows.forEach(function (s) {
      var tr = document.createElement('tr');
      [String(s.student_id), s.name, s.class_name || clsName(s.class_key),
       s.count, s.days, s.minutes, s.count ? (Math.round(s.minutes / s.count * 10) / 10) : 0,
       s.last_date || '—'].forEach(function (v, i) {
        var td = document.createElement('td');
        if (i >= 3 && i <= 6) td.className = 'num';
        td.textContent = v;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
  }

  function renderZero() {
    var tb = $('tblZero').querySelector('tbody');
    tb.textContent = '';
    var rows = (data.students || []).filter(function (s) { return (!s.count || s.count === 0) && matchSearch(s); });
    if (!rows.length) {
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.colSpan = 4; td.className = 'empty'; td.textContent = '这个区间全班都有记录 👍';
      tr.appendChild(td); tb.appendChild(tr);
      return;
    }
    rows.forEach(function (s) {
      var tr = document.createElement('tr');
      [String(s.student_id), s.name, s.class_name || clsName(s.class_key), s.minutes].forEach(function (v, i) {
        var td = document.createElement('td');
        if (i === 3) td.className = 'num';
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
    $('paneSum').hidden = (t !== 'sum');
    $('paneZero').hidden = (t !== 'zero');
    $('tabDetail').classList.toggle('on', t === 'detail');
    $('tabSum').classList.toggle('on', t === 'sum');
    $('tabZero').classList.toggle('on', t === 'zero');
  }
  $('tabDetail').addEventListener('click', function () { setTab('detail'); });
  $('tabSum').addEventListener('click', function () { setTab('sum'); });
  $('tabZero').addEventListener('click', function () { setTab('zero'); });

  /* ---------------- 导出 CSV ---------------- */
  function download(name, rows) {
    var csv = rows.map(function (r) {
      return r.map(function (c) {
        c = (c === null || c === undefined) ? '' : String(c);
        return /[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c;
      }).join(',');
    }).join('\r\n');
    var blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  $('btnCsvLog').addEventListener('click', function () {
    var head = ['学号', '姓名', '班级', '日期', '时间', '时长(分钟)', '内容', '类别', '备注'];
    var rows = [head].concat((data.logs || []).map(function (l) {
      return [l.student_id, l.name, clsName(l.class_key), l.date, l.time,
              l.minutes, l.content, l.category, l.note];
    }));
    download('听力记录明细_' + LISTEN.todayStr() + '.csv', rows);
  });

  $('btnCsvSum').addEventListener('click', function () {
    var head = ['学号', '姓名', '班级', '条数', '天数', '累计分钟', '平均分钟', '最近一次'];
    var rows = [head].concat((data.students || []).map(function (s) {
      return [s.student_id, s.name, s.class_name || clsName(s.class_key),
              s.count, s.days, s.minutes,
              s.count ? (Math.round(s.minutes / s.count * 10) / 10) : 0, s.last_date || ''];
    }));
    download('听力记录汇总_' + LISTEN.todayStr() + '.csv', rows);
  });

  /* ---------------- 清空 ---------------- */
  $('btnClear').addEventListener('click', function () {
    if (!confirm('确定清空全部听力记录吗？此操作不可撤销。')) return;
    if (!confirm('再确认一次：所有学生的记录都会被删掉。')) return;
    LISTEN.rpc('listen_teacher_clear', { p_token: tok })
      .then(function (r) {
        if (!r || !r.ok) { toast((r && r.msg) || '清空失败'); return; }
        toast('已清空');
        load();
      })
      .catch(function (e) { toast(e.message || '清空失败'); });
  });

  boot();
})();
