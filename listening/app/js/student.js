/* =====================================================================
   每日听力记录站 · 学生端
   ===================================================================== */
(function () {
  'use strict';

  var SKEY = 'listen.session';
  var CATS = ['精听', '泛听', '听力理解', '影子跟读', '影视原声', '其他'];
  var MINS = [10, 15, 20, 30, 45, 60];

  var me = null;          // {token, name, class_name, class_key}
  var pickedMin = 0;
  var pickedCat = '';

  var $ = function (id) { return document.getElementById(id); };

  function toast(msg) {
    var t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(t._tm);
    t._tm = setTimeout(function () { t.hidden = true; }, 2600);
  }

  function setMsg(el, msg, kind) {
    el.textContent = msg || '';
    el.className = 'msg' + (kind ? ' ' + kind : '');
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    initClearTk();
    initQuickDates();
    if (LISTEN.isDemo()) $('demoBanner').hidden = false;
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(SKEY) || 'null'); } catch (e) { saved = null; }
    if (saved && saved.token) {
      me = saved;
      // 校验会话还有效
      LISTEN.rpc('listen_my_logs', { p_token: me.token }).then(function (r) {
        if (r && r.ok) { showHome(); refresh(); }
        else { logout(); }
      }).catch(function () { showHome(); refresh(); });
    } else {
      showLogin();
    }
  }

  function showLogin() {
    $('viewLogin').hidden = false;
    $('viewHome').hidden = true;
    $('who').textContent = '';
  }

  function showHome() {
    $('viewLogin').hidden = true;
    $('viewHome').hidden = false;
    $('who').innerHTML = '';
    var b = document.createElement('b');
    b.textContent = me.name || '';
    $('who').appendChild(b);
    $('who').appendChild(document.createTextNode(' · ' + (me.class_name || '')));
  }

  function logout() {
    me = null;
    try { localStorage.removeItem(SKEY); } catch (e) { /* ignore */ }
    showLogin();
  }

  /* ---------------- 登录 ---------------- */
  $('btnLogin').addEventListener('click', function () {
    var sid = $('sid').value.trim();
    var name = $('sname').value.trim();
    if (!sid || !name) { setMsg($('loginMsg'), '请把学号和姓名都填上', 'err'); return; }
    $('btnLogin').disabled = true;
    setMsg($('loginMsg'), '正在核对名册…');
    LISTEN.rpc('listen_student_login', { p_id: sid, p_name: name })
      .then(function (r) {
        $('btnLogin').disabled = false;
        if (!r || !r.ok) { setMsg($('loginMsg'), (r && r.msg) || '登录失败', 'err'); return; }
        me = { token: r.token, name: r.name, class_name: r.class_name, class_key: r.class_key };
        try { localStorage.setItem(SKEY, JSON.stringify(me)); } catch (e) { /* ignore */ }
        setMsg($('loginMsg'), '');
        showHome();
        refresh();
      })
      .catch(function (e) {
        $('btnLogin').disabled = false;
        setMsg($('loginMsg'), e.message || '登录失败', 'err');
      });
  });

  $('btnLogout').addEventListener('click', function () {
    if (confirm('确定退出登录吗？')) logout();
  });

  /* ---------------- 记一笔 ---------------- */
  function buildChips() {
    var mc = $('minChips');
    mc.textContent = '';
    MINS.forEach(function (m) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = m + '分';
      b.addEventListener('click', function () {
        pickedMin = m;
        $('fMinutes').value = m;
        Array.prototype.forEach.call(mc.children, function (c) { c.classList.remove('on'); });
        b.classList.add('on');
      });
      mc.appendChild(b);
    });

    var cc = $('catChips');
    cc.textContent = '';
    CATS.forEach(function (c) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = c;
      b.addEventListener('click', function () {
        pickedCat = (pickedCat === c) ? '' : c;
        Array.prototype.forEach.call(cc.children, function (x) { x.classList.remove('on'); });
        if (pickedCat) b.classList.add('on');
      });
      cc.appendChild(b);
    });
  }
  buildChips();

  $('fMinutes').addEventListener('input', function () {
    pickedMin = 0;
    Array.prototype.forEach.call($('minChips').children, function (c) { c.classList.remove('on'); });
  });

  $('btnAdd').addEventListener('click', function () {
    var now = new Date();
    $('fDate').value = LISTEN.todayStr();
    $('fTime').value = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2);
    $('fContent').value = '';
    $('fMinutes').value = '';
    $('fNote').value = '';
    pickedMin = 0; pickedCat = '';
    Array.prototype.forEach.call($('minChips').children, function (c) { c.classList.remove('on'); });
    Array.prototype.forEach.call($('catChips').children, function (c) { c.classList.remove('on'); });
    setMsg($('addMsg'), '');
    $('addMask').hidden = false;
    setTimeout(function () { $('fContent').focus(); }, 60);
  });

  $('btnCancel').addEventListener('click', function () { $('addMask').hidden = true; });
  $('addMask').addEventListener('click', function (e) { if (e.target === $('addMask')) $('addMask').hidden = true; });

  $('btnSave').addEventListener('click', function () {
    if (!me) return;
    var content = $('fContent').value.trim();
    var minutes = parseInt($('fMinutes').value, 10);
    if (!content) { setMsg($('addMsg'), '请填写「听了什么」', 'err'); return; }
    if (!minutes || minutes < 1) { setMsg($('addMsg'), '请填写听力时长（分钟）', 'err'); return; }
    if (minutes > 600) { setMsg($('addMsg'), '时长最多 600 分钟', 'err'); return; }

    $('btnSave').disabled = true;
    LISTEN.rpc('listen_add_log', {
      p_token: me.token,
      p_date: $('fDate').value || LISTEN.todayStr(),
      p_time: $('fTime').value || '',
      p_minutes: minutes,
      p_content: content,
      p_category: pickedCat,
      p_note: $('fNote').value.trim()
    }).then(function (r) {
      $('btnSave').disabled = false;
      if (!r || !r.ok) { setMsg($('addMsg'), (r && r.msg) || '提交失败', 'err'); return; }
      $('addMask').hidden = true;
      toast('记好了 ✓');
      refresh();
    }).catch(function (e) {
      $('btnSave').disabled = false;
      setMsg($('addMsg'), e.message || '提交失败', 'err');
    });
  });

  /* ---------------- 我的记录 ---------------- */
  function refresh() {
    if (!me) return;
    LISTEN.rpc('listen_my_logs', { p_token: me.token }).then(function (r) {
      if (!r || !r.ok) { toast((r && r.msg) || '读取失败'); return; }
      var s = r.stats || {};
      $('stToday').textContent = s.today_minutes || 0;
      $('stWeek').textContent = s.week_minutes || 0;
      $('stTotal').textContent = s.minutes || 0;
      var logs = r.logs || [];
      $('logCount').textContent = logs.length ? ('共 ' + logs.length + ' 条') : '';
      renderLogs(logs);
    }).catch(function (e) { toast(e.message || '读取失败'); });
  }

  function renderLogs(logs) {
    var box = $('myLogs');
    box.textContent = '';
    if (!logs.length) {
      var em = document.createElement('div');
      em.className = 'empty';
      em.textContent = '还没有记录，点上面的「＋ 记一笔」开始';
      box.appendChild(em);
      return;
    }
    logs.forEach(function (l) {
      var row = document.createElement('div');
      row.className = 'log';

      var mn = document.createElement('div');
      mn.className = 'min';
      mn.textContent = l.minutes + '′';
      row.appendChild(mn);

      var body = document.createElement('div');
      body.className = 'body';
      var t = document.createElement('div');
      t.className = 't';
      t.textContent = l.date + ' ' + (l.time || '');
      body.appendChild(t);
      var c = document.createElement('div');
      c.className = 'c';
      c.textContent = (l.category ? '【' + l.category + '】' : '') + l.content;
      body.appendChild(c);
      if (l.note) {
        var n = document.createElement('div');
        n.className = 'c';
        n.textContent = '备注：' + l.note;
        body.appendChild(n);
      }
      row.appendChild(body);

      var x = document.createElement('button');
      x.className = 'link x';
      x.type = 'button';
      x.textContent = '删除';
      x.addEventListener('click', function () {
        if (!confirm('删除这条记录？')) return;
        // 🔴 令牌**只从本机取**（库里只有哈希 ⇒ 换设备就删不了 ⇒ 界面会明说）
        LISTEN.rpc('listen_delete_log', { p_token: me.token, p_id: l.id })
          .then(function (r) {
            // 🔴 0 行 = **没删掉**（RLS 挡着）⇒ 不能显示「已删除」（那是假成功）
            if (!r || !r.ok) { toast((r && r.msg) || '没有真正删除这条记录，请稍后再试'); return; }
            toast('已删除'); refresh();
          })
          .catch(function (e) { toast(e.message || '删除失败'); });
      });
      row.appendChild(x);

      box.appendChild(row);
    });
  }

  /* ---------------- 补交提示：快捷日期（2026-10-22~24 的记录被误清空 ⇒ 老师让同学重交） ---------------- */
  function initQuickDates() {
    var btns = document.querySelectorAll('[data-filldate]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener('click', function (e) {
        var v = e.currentTarget.getAttribute('data-filldate');
        var d = document.getElementById('fDate');
        if (d) {
          d.value = v;
          // 提示一下已经填好了（日期框可能不在视口里）
          try { d.scrollIntoView({ block: 'center' }); } catch (err) { }
          try { d.focus(); } catch (err) { }
        }
        toast('日期已填成 ' + v + '，写完点「提交」');
      });
    }
  }

  /* ---------------- 清除本机删除凭证（凡往设备存东西必须配删除入口 + 二次确认） ---------------- */
  function initClearTk() {
    var b = document.getElementById('btnClearTk');
    if (!b) return;
    b.addEventListener('click', function () {
      // 🔴 二次确认：要**手打「清除」**两个字 ⇒ 防止手滑把凭证清了导致以后删不了
      var typed = prompt('清除本机的删除凭证后，本机上已提交的记录将无法再删除。\n\n请输入「清除」两个字确认：', '');
      if (String(typed == null ? '' : typed).trim() !== '清除') {
        toast('未输入「清除」，已取消'); return;
      }
      LISTEN.rpc('listen_clear_local_tokens', {}).then(function (r) {
        if (!r || !r.ok) { toast((r && r.msg) || '清除失败'); return; }
        toast('已清除本机 ' + (r.cleared || 0) + ' 条删除凭证');
        refresh();
      }).catch(function (e) { toast(e.message || '清除失败'); });
    });
  }

  boot();
})();
