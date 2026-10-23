/* =====================================================================
   每日听力记录站 · API 层
   - 纯 fetch，不引任何 CDN 依赖（断网也不会白屏）
   - 后端：老师自己的 Supabase 项目（复用词汇站那套库）
       · 名单      → vocab_states 里的 __roster_v1 一行（老站不会去读这个键）
       · 听力记录  → vocab_history_logs 里 class_key='LISTEN' 的行（老站按班级 key 精确查，看不到）
       · 教师登录  → 复用已有的 check_teacher_login 函数（密码服务端哈希比对）
   - 前端只有公开的 anon key：名单和密码都不在前端源码里
   - ?demo=1 进入演示模式：本地假数据，不连数据库
   ===================================================================== */
(function (global) {
  'use strict';

  var CFG = {
    SU: 'https://lszwxcmvcvbraqtrtgon.supabase.co',
    KEY: 'sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo',
    ROSTER_KEY: '__roster_v1',   // 名册存放的键（老站只查 JR1/CW3/WL_CW/XQ1，看不到这个）
    LOG_KEY: 'LISTEN',           // 听力记录的 class_key（老站也看不到）
    SALT: 'vocab-unit23-teacher-2026'  // 公开 salt（与词汇站一致）；密码本身不在前端
  };

  function isDemo() {
    return /[?&]demo=1/.test(location.search) || localStorage.getItem('listen.demo') === '1';
  }

  /* ================= 底层 REST ================= */
  function rest(path, opts) {
    opts = opts || {};
    var headers = { apikey: CFG.KEY, Authorization: 'Bearer ' + CFG.KEY };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.prefer) headers['Prefer'] = opts.prefer;
    if (opts.range) headers['Range'] = opts.range;
    return fetch(CFG.SU + '/rest/v1/' + path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.text().then(function (t) {
        if (!r.ok) {
          var msg = 'HTTP ' + r.status;
          try { var j = JSON.parse(t); msg = j.message || j.hint || msg; } catch (e) { }
          throw new Error(msg);
        }
        if (!t) return null;
        try { return JSON.parse(t); } catch (e) { return t; }
      });
    }, function () {
      throw new Error('连不上服务器，请检查网络后重试');
    });
  }

  /* ================= 分页读取 =================
     ⚠️ anon 角色单次最多只能读 1000 行（Supabase 默认 max-rows），
        不翻页的话老师看不到全部记录 → 必须用 Range 头逐页取 */
  function fetchAllRows(path, pageSize) {
    pageSize = pageSize || 1000;
    var out = [], from = 0;
    function step() {
      return rest(path, { range: from + '-' + (from + pageSize - 1) }).then(function (rows) {
        rows = rows || [];
        for (var i = 0; i < rows.length; i++) out.push(rows[i]);
        if (rows.length === pageSize) { from += pageSize; return step(); }
        return out;
      });
    }
    return step();
  }

  /* ================= 名册 ================= */
  var rosterCache = null;

  function getRoster() {
    if (rosterCache) return Promise.resolve(rosterCache);
    return rest('vocab_states?select=state_val&class_key=eq.' + CFG.ROSTER_KEY)
      .then(function (rows) {
        if (!rows || !rows.length) throw new Error('名册还没装载，请联系老师');
        var list = JSON.parse(rows[0].state_val);
        rosterCache = list;
        try { localStorage.setItem('listen.roster', JSON.stringify({ t: Date.now(), v: list })); } catch (e) { }
        return list;
      })
      .catch(function (err) {
        // 断网兜底：用本机上次缓存的名册（24 小时内）
        try {
          var c = JSON.parse(localStorage.getItem('listen.roster') || 'null');
          if (c && c.v && (Date.now() - c.t) < 86400000) { rosterCache = c.v; return c.v; }
        } catch (e2) { }
        throw err;
      });
  }

  function findStudent(list, sid) {
    sid = String(sid || '').trim();
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === sid) return list[i];
    }
    return null;
  }

  /* ================= 工具 ================= */
  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function fmtDate(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function todayStr() { return fmtDate(new Date()); }

  // 2026-10-22 + 21:50 → "2026/10/22 21:50:00"（与老站的时间格式一致）
  function tsString(dateStr, timeStr) {
    var d = (dateStr || todayStr()).split('-');
    var t = (timeStr || '00:00').split(':');
    return d[0] + '/' + parseInt(d[1], 10) + '/' + parseInt(d[2], 10) + ' ' +
      pad(parseInt(t[0], 10) || 0) + ':' + pad(parseInt(t[1], 10) || 0) + ':00';
  }
  // "2026/10/22 21:50:00" → {date:'2026-10-22', time:'21:50'}
  function parseTs(ts) {
    var m = /(\d{4})\/(\d{1,2})\/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?/.exec(ts || '');
    if (!m) return { date: '', time: '' };
    return {
      date: m[1] + '-' + pad(parseInt(m[2], 10)) + '-' + pad(parseInt(m[3], 10)),
      time: m[4] ? pad(parseInt(m[4], 10)) + ':' + m[5] : ''
    };
  }

  function sidOf(token) {
    var m = /^stu:(\d+):/.exec(token || '');
    return m ? m[1] : null;
  }
  function isTeacherToken(t) { return /^tea:/.test(t || ''); }

  function sha256hex(str) {
    if (global.crypto && global.crypto.subtle && global.TextEncoder) {
      return global.crypto.subtle.digest('SHA-256', new TextEncoder().encode(str))
        .then(function (buf) {
          return Array.prototype.map.call(new Uint8Array(buf), function (b) {
            return ('00' + b.toString(16)).slice(-2);
          }).join('');
        });
    }
    return Promise.reject(new Error('这个浏览器不支持安全哈希，请换新版浏览器打开'));
  }

  /* ================= 真实实现 ================= */
  function realRpc(name, args) {
    args = args || {};

    /* ---- 学生登录：学号 + 姓名 必须与名册一致 ---- */
    if (name === 'listen_student_login') {
      return getRoster().then(function (list) {
        var s = findStudent(list, args.p_id);
        if (!s) return { ok: false, msg: '学号不在名册里，请向老师确认' };
        var typed = String(args.p_name || '').trim().replace(/ /g, '');
        if (s.name !== typed) return { ok: false, msg: '姓名与名册不一致，请按名册填写' };
        return {
          ok: true,
          token: 'stu:' + s.id + ':' + Math.random().toString(36).slice(2),
          name: s.name, class_key: s.class_key, class_name: s.class_name
        };
      });
    }

    /* ---- 交一条记录 ---- */
    if (name === 'listen_add_log') {
      var sid = sidOf(args.p_token);
      if (!sid) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      var content = String(args.p_content || '').trim();
      var minutes = parseInt(args.p_minutes, 10);
      if (!content) return Promise.resolve({ ok: false, msg: '请填写听了什么' });
      if (!minutes || minutes < 1 || minutes > 600) return Promise.resolve({ ok: false, msg: '时长要在 1–600 分钟之间' });

      return getRoster().then(function (list) {
        var s = findStudent(list, sid);
        if (!s) return { ok: false, msg: '名册里找不到你的学号' };
        var cat = String(args.p_category || '').trim();
        var note = String(args.p_note || '').trim();
        var action = '【听力】' + (cat || '未分类') + ' | ' + content + (note ? ' | 备注:' + note : '');
        return rest('vocab_history_logs', {
          method: 'POST', prefer: 'return=representation',
          body: {
            class_key: CFG.LOG_KEY,
            class_name: s.class_name,
            student_id: sid,
            timestamp: tsString(args.p_date, args.p_time),
            duration_sec: minutes * 60,
            action: action
          }
        }).then(function (rows) {
          return { ok: true, id: rows && rows[0] ? rows[0].id : null };
        });
      });
    }

    /* ---- 我自己的记录 + 统计 ---- */
    if (name === 'listen_my_logs') {
      var sid2 = sidOf(args.p_token);
      if (!sid2) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      return fetchAllRows('vocab_history_logs?select=*&class_key=eq.' + CFG.LOG_KEY +
        '&student_id=eq.' + sid2 + '&order=id.desc')
        .then(function (rows) {
          var logs = (rows || []).map(rowToLog);
          var days = {}, minutes = 0, last = '', todayM = 0, weekM = 0;
          var weekAgo = (function () { var d = new Date(); d.setDate(d.getDate() - 6); return fmtDate(d); })();
          logs.forEach(function (l) {
            minutes += l.minutes;
            days[l.date] = 1;
            if (!last || l.date > last) last = l.date;
            if (l.date === todayStr()) todayM += l.minutes;
            if (l.date >= weekAgo) weekM += l.minutes;
          });
          return {
            ok: true, logs: logs,
            stats: {
              count: logs.length, days: Object.keys(days).length, minutes: minutes,
              avg: logs.length ? Math.round(minutes / logs.length * 10) / 10 : 0,
              last_date: last || null, today_minutes: todayM, week_minutes: weekM
            }
          };
        });
    }

    /* ---- 删自己的一条 ---- */
    if (name === 'listen_delete_log') {
      var sid3 = sidOf(args.p_token);
      if (!sid3) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      return rest('vocab_history_logs?id=eq.' + Number(args.p_id) +
        '&class_key=eq.' + CFG.LOG_KEY + '&student_id=eq.' + sid3, { method: 'DELETE' })
        .then(function () { return { ok: true, deleted: 1 }; });
    }

    /* ---- 教师登录：密码在服务端比哈希 ---- */
    if (name === 'listen_teacher_login') {
      var tid = String(args.p_id || '').trim();
      var tpass = String(args.p_pass || '');
      if (!tid || !tpass) return Promise.resolve({ ok: false, msg: '请把工号和密码都填上' });
      return sha256hex(CFG.SALT + '|' + tid + '|' + tpass)
        .then(function (h) {
          return rest('rpc/check_teacher_login', { method: 'POST', body: { p_id: tid, p_hash: h } });
        })
        .then(function (r) {
          if (r === true || (r && r.ok === true)) {
            return { ok: true, token: 'tea:' + Math.random().toString(36).slice(2), name: '教师' };
          }
          return { ok: false, msg: '工号或密码不正确' };
        })
        .catch(function (e) {
          if (e && e.message && e.message.indexOf('连不上') >= 0) throw e;
          return { ok: false, msg: '工号或密码不正确' };
        });
    }

    /* ---- 教师取全部数据 ---- */
    if (name === 'listen_teacher_data') {
      if (!isTeacherToken(args.p_token)) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      return Promise.all([
        getRoster(),
        fetchAllRows('vocab_history_logs?select=*&class_key=eq.' + CFG.LOG_KEY + '&order=id.desc')
      ]).then(function (res) {
        var roster = res[0] || [], rows = res[1] || [];
        var keyByName = {};
        roster.forEach(function (s) { keyByName[s.class_name] = s.class_key; });

        var logs = rows.map(function (r) {
          var l = rowToLog(r);
          l.class_key = keyByName[l.class_name] || l.class_name;
          l.name = (roster.filter(function (s) { return s.id === l.student_id; })[0] || {}).name || l.student_id;
          return l;
        });

        if (args.p_from) logs = logs.filter(function (l) { return l.date >= args.p_from; });
        if (args.p_to) logs = logs.filter(function (l) { return l.date <= args.p_to; });
        if (args.p_class) logs = logs.filter(function (l) { return l.class_key === args.p_class; });

        // 每人汇总
        var agg = {};
        logs.forEach(function (l) {
          var b = agg[l.student_id] || (agg[l.student_id] = { cnt: 0, days: {}, mins: 0, last: '' });
          b.cnt++; b.minutes += l.minutes; b.days[l.date] = 1;
          if (l.date > b.last) b.last = l.date;
        });
        var students = roster.map(function (s) {
          var b = agg[s.id] || { cnt: 0, days: {}, mins: 0, last: '' };
          return {
            student_id: s.id, name: s.name, class_key: s.class_key, class_name: s.class_name,
            count: b.cnt, days: Object.keys(b.days).length, minutes: b.mins, last_date: b.last || null
          };
        });

        var classes = {}, today = 0, todayM = 0, active = {};
        // 🔴 班级清单以**名册**为准：按 class_key 去重 + 「这个班多少人」
        //    旧写法是「有多少条记录就建几个班」，0 条记录时会对 157 个学生各 push 一次
        //    ⇒ 下拉里 157 项、5 个班重名、每个都显示 0 人（实测：老师端一眼就露馅）
        roster.forEach(function (s) {
          var c = classes[s.class_key] || (classes[s.class_key] = { key: s.class_key, name: s.class_name, count: 0 });
          c.count++;
        });
        logs.forEach(function (l) {
          if (!classes[l.class_key]) classes[l.class_key] = { key: l.class_key, name: l.class_name, count: 0 };
          active[l.student_id] = 1;
          if (l.date === todayStr()) { today++; todayM += l.minutes; }
        });
        var classList = Object.keys(classes).map(function (k) { return classes[k]; });

        var totalMin = logs.reduce(function (a, l) { return a + l.minutes; }, 0);
        return {
          ok: true, classes: classList, students: students, logs: logs,
          totals: {
            total_logs: logs.length, total_minutes: totalMin,
            active_students: Object.keys(active).length,
            roster: students.length,
            today_count: today, today_minutes: todayM,
            today_students: (function () {
              var s = {}; logs.forEach(function (l) { if (l.date === todayStr()) s[l.student_id] = 1; });
              return Object.keys(s).length;
            })()
          }
        };
      });
    }

    /* ---- 教师删一条 ---- */
    if (name === 'listen_teacher_delete') {
      if (!isTeacherToken(args.p_token)) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      return rest('vocab_history_logs?id=eq.' + Number(args.p_id) + '&class_key=eq.' + CFG.LOG_KEY,
        { method: 'DELETE' }).then(function () { return { ok: true, deleted: 1 }; });
    }

    /* ---- 教师清空全部 ---- */
    if (name === 'listen_teacher_clear') {
      if (!isTeacherToken(args.p_token)) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      return rest('vocab_history_logs?class_key=eq.' + CFG.LOG_KEY, { method: 'DELETE' })
        .then(function () { return { ok: true, deleted: 0 }; });
    }

    return Promise.resolve({ ok: false, msg: '未知操作：' + name });
  }

  // 库里的一行 → 前端的一条记录
  function rowToLog(r) {
    var ts = parseTs(r.timestamp);
    var action = r.action || '';
    var m = /^【听力】(.*?)\s*\|\s*([\s\S]*)$/.exec(action);
    var category = m ? m[1] : '';
    var rest2 = m ? m[2] : action;
    var note = '';
    var ni = rest2.indexOf(' | 备注:');
    if (ni >= 0) { note = rest2.slice(ni + 5); rest2 = rest2.slice(0, ni); }
    return {
      id: r.id,
      student_id: r.student_id,
      name: r.student_id,
      class_key: r.class_key,
      class_name: r.class_name,
      date: ts.date,
      time: ts.time,
      minutes: Math.round((r.duration_sec || 0) / 60),
      content: rest2,
      category: category === '未分类' ? '' : category,
      note: note
    };
  }

  /* ================= 演示后端（本地假数据） ================= */
  var DEMO_NAMES = ['示例·甲', '示例·乙', '示例·丙', '示例·丁', '示例·戊', '示例·己',
                    '示例·庚', '示例·辛', '示例·壬', '示例·癸', '示例·子', '示例·丑'];
  var DEMO_CLASSES = [
    { key: 'JR1', name: '26互联网金融1班' },
    { key: 'CW3', name: '26财务管理3班' },
    { key: 'XQ1', name: '26学前教育1班' }
  ];
  var DEMO_CATS = ['精听', '泛听', '听力理解', '影子跟读', '影视原声', '其他'];
  var DEMO_CONTENT = [
    'BBC 6 Minute English - The Art of Small Talk',
    'VOA Learning English - Making Plans',
    'TED-Ed - How We Process Language',
    '雅思剑 17 Test 2 Section 3',
    '每日英语听力 2026-10-22 早间新闻',
    '影子跟读：Friends S1E3 片段'
  ];
  var DEMO_KEY = 'listen.demo.data';

  function demoSeed() {
    var st = { students: [], logs: [], seq: 1 };
    DEMO_NAMES.forEach(function (nm, i) {
      var cls = DEMO_CLASSES[i % DEMO_CLASSES.length];
      st.students.push({ id: '9000' + (1000 + i), name: nm, class_key: cls.key, class_name: cls.name });
    });
    var now = new Date();
    for (var i = 0; i < 46; i++) {
      var s = st.students[i % st.students.length];
      var d = new Date(now.getTime() - Math.floor(Math.random() * 9) * 86400000);
      st.logs.push({
        id: st.seq++, student_id: s.id, name: s.name, class_key: s.class_key, class_name: s.class_name,
        date: fmtDate(d), time: (8 + Math.floor(Math.random() * 12)) + ':' + pad(Math.floor(Math.random() * 60)),
        minutes: [10, 15, 20, 25, 30, 45, 60][Math.floor(Math.random() * 7)],
        content: DEMO_CONTENT[i % DEMO_CONTENT.length], category: DEMO_CATS[i % DEMO_CATS.length], note: ''
      });
    }
    return st;
  }
  function demoLoad() {
    try { var raw = localStorage.getItem(DEMO_KEY); if (raw) return JSON.parse(raw); } catch (e) { }
    var st = demoSeed(); demoSave(st); return st;
  }
  function demoSave(st) { try { localStorage.setItem(DEMO_KEY, JSON.stringify(st)); } catch (e) { } }
  function dsum(logs) { return logs.reduce(function (a, l) { return a + (l.minutes || 0); }, 0); }
  function uniqStudents(logs) { var s = {}; logs.forEach(function (l) { s[l.student_id] = 1; }); return Object.keys(s).length; }

  function demoRpc(name, args) {
    args = args || {};
    var st = demoLoad();
    function save() { demoSave(st); }
    var weekAgo = (function () { var d = new Date(); d.setDate(d.getDate() - 6); return fmtDate(d); })();

    if (name === 'listen_student_login') {
      return Promise.resolve({ ok: true, token: 'demo-token', name: args.p_name || '示例同学', class_key: 'JR1', class_name: '演示班级' });
    }
    if (name === 'listen_teacher_login') {
      return Promise.resolve({ ok: true, token: 'demo-teacher', name: '教师' });
    }
    if (name === 'listen_add_log') {
      var rec = {
        id: st.seq++, student_id: 'demo-me', name: '示例同学', class_key: 'JR1', class_name: '演示班级',
        date: args.p_date || todayStr(), time: args.p_time || '12:00', minutes: args.p_minutes || 0,
        content: args.p_content || '', category: args.p_category || '', note: args.p_note || ''
      };
      st.logs.unshift(rec); save();
      return Promise.resolve({ ok: true, id: rec.id });
    }
    if (name === 'listen_my_logs') {
      var mine = st.logs.filter(function (l) { return l.student_id === 'demo-me'; });
      var days = {}; mine.forEach(function (l) { days[l.date] = 1; });
      return Promise.resolve({ ok: true, logs: mine, stats: {
        count: mine.length, days: Object.keys(days).length, minutes: dsum(mine),
        avg: mine.length ? Math.round(dsum(mine) / mine.length * 10) / 10 : 0,
        last_date: mine.length ? mine[0].date : null,
        today_minutes: dsum(mine.filter(function (l) { return l.date === todayStr(); })),
        week_minutes: dsum(mine.filter(function (l) { return l.date >= weekAgo; }))
      } });
    }
    if (name === 'listen_delete_log') {
      st.logs = st.logs.filter(function (l) { return l.id !== args.p_id; }); save();
      return Promise.resolve({ ok: true, deleted: 1 });
    }
    if (name === 'listen_teacher_data') {
      var logs = st.logs.slice();
      if (args.p_from) logs = logs.filter(function (l) { return l.date >= args.p_from; });
      if (args.p_to) logs = logs.filter(function (l) { return l.date <= args.p_to; });
      if (args.p_class) logs = logs.filter(function (l) { return l.class_key === args.p_class; });
      var by = {};
      logs.forEach(function (l) {
        var b = by[l.student_id] || (by[l.student_id] = { cnt: 0, days: {}, mins: 0, last: '' });
        b.cnt++; b.mins += l.minutes; b.days[l.date] = 1;
        if (l.date > b.last) b.last = l.date;
      });
      var students = st.students.map(function (s) {
        var b = by[s.id] || { cnt: 0, days: {}, mins: 0, last: '' };
        return { student_id: s.id, name: s.name, class_key: s.class_key, class_name: s.class_name,
          count: b.cnt, days: Object.keys(b.days).length, minutes: b.mins, last_date: b.last || null };
      });
      var today = logs.filter(function (l) { return l.date === todayStr(); });
      return Promise.resolve({ ok: true,
        classes: DEMO_CLASSES.map(function (c) { return { key: c.key, name: c.name, count: st.students.filter(function (s) { return s.class_key === c.key; }).length }; }),
        students: students, logs: logs,
        totals: { total_logs: logs.length, total_minutes: dsum(logs),
          active_students: uniqStudents(logs), roster: st.students.length,
          today_count: today.length, today_minutes: dsum(today), today_students: uniqStudents(today) } });
    }
    if (name === 'listen_teacher_delete') {
      st.logs = st.logs.filter(function (l) { return l.id !== args.p_id; }); save();
      return Promise.resolve({ ok: true, deleted: 1 });
    }
    if (name === 'listen_teacher_clear') {
      st.logs = []; save();
      return Promise.resolve({ ok: true, deleted: 0 });
    }
    return Promise.resolve({ ok: false, msg: '演示模式不支持该操作' });
  }

  global.LISTEN = {
    CFG: CFG,
    isDemo: isDemo,
    rpc: function (name, args) {
      return isDemo() ? demoRpc(name, args) : realRpc(name, args);
    },
    fmtDate: fmtDate,
    todayStr: todayStr
  };
})(window);
