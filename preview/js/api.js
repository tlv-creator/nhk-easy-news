/* =====================================================================
   四级精读日报 · API 层
   ---------------------------------------------------------------------
   纯 fetch，零依赖，断网也不会白屏。
   后端：老师自己的 Supabase 项目（复用词汇站那套库，零 DDL）
     · 学生名册 → vocab_states 的 __roster_v1 一行（只读，绝不写）
     · 每日索引 → vocab_states 的 __cet4_index 一行
     · 每日正文 → vocab_states 的 __cet4_day_YYYY-MM-DD 一行
     · 活动记录 → vocab_history_logs 里 class_key='CET4' 的行
     · 老师登录 → 复用已有的 check_teacher_login（密码服务端哈希比对）
   🔴 只碰 __cet4_* 和 class_key='CET4' 的行；
      JR1 / CW3 / WL_CW / XQ1 / __roster_v1 一律只读不动。
   ?demo=1 → 演示模式：内置假 issue，不连数据库。
   ===================================================================== */
(function (global) {
  'use strict';

  var CFG = {
    SU: 'https://lszwxcmvcvbraqtrtgon.supabase.co',
    KEY: 'sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo',
    ROSTER_KEY: '__roster_v1',       // 名册（只读）
    INDEX_KEY: '__cet4_index',       // 每日索引
    DAY_PREFIX: '__cet4_day_',       // 每日正文
    LOG_KEY: 'CET4',                 // 活动记录 class_key
    SALT: 'vocab-unit23-teacher-2026', // 公开 salt（与词汇站一致）；密码本身不在前端
    QKEY: 'cet4.logqueue',           // 断网时的本地待发队列
    DEMO_DATE: '2026-10-24',
    // 🔴 自测号（老师 / 教研 / 设备调试用）
    TEST_SID: '9999999999',          // 固定学号，姓名随意
    TEST_NAME: '测试同学',
    TEST_PREFIX: '__test__'          // 日志里 student_id 加这个前缀 ⇒ 一眼认出、一条正则过滤
  };

  var hasLS = (function () {
    try { return !!global.localStorage; } catch (e) { return false; }
  })();

  function lsGet(k) { try { return hasLS ? global.localStorage.getItem(k) : null; } catch (e) { return null; } }
  function lsSet(k, v) { try { if (hasLS) global.localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  function lsDel(k) { try { if (hasLS) global.localStorage.removeItem(k); } catch (e) { /* ignore */ } }

  function isDemo() {
    return /[?&]demo=1/.test((global.location && global.location.search) || '') ||
      lsGet('cet4.demo') === '1';
  }

  /* ================= 底层 REST ================= */
  function rest(path, opts) {
    opts = opts || {};
    var headers = { apikey: CFG.KEY, Authorization: 'Bearer ' + CFG.KEY };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.prefer) headers['Prefer'] = opts.prefer;
    if (opts.range) headers['Range'] = opts.range;
    return global.fetch(CFG.SU + '/rest/v1/' + path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      keepalive: !!opts.keepalive
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
     ⚠️ anon 角色单次最多读 1000 行（Supabase 默认 max-rows），
        不翻页老师看不到全部记录 → 必须用 Range 头逐页取 */
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

  /* ================= 名册（只读） ================= */
  var rosterCache = null;

  function getRoster() {
    if (isDemo()) return Promise.resolve(demoRoster());
    if (rosterCache) return Promise.resolve(rosterCache);
    return rest('vocab_states?select=state_val&class_key=eq.' + CFG.ROSTER_KEY)
      .then(function (rows) {
        if (!rows || !rows.length) throw new Error('名册还没装载，请联系老师');
        var list = JSON.parse(rows[0].state_val);
        rosterCache = list;
        lsSet('cet4.roster', JSON.stringify({ t: Date.now(), v: list }));
        return list;
      })
      .catch(function (err) {
        // 断网兜底：用本机上次缓存的名册（24 小时内）
        try {
          var c = JSON.parse(lsGet('cet4.roster') || 'null');
          if (c && c.v && (Date.now() - c.t) < 86400000) { rosterCache = c.v; return c.v; }
        } catch (e2) { }
        throw err;
      });
  }

  function findStudent(list, sid) {
    sid = String(sid || '').trim();
    for (var i = 0; i < list.length; i++) {
      if (String(list[i].id) === sid) return list[i];
    }
    return null;
  }

  /* ================= 每日内容 ================= */
  var issueCache = {};

  function getIndex() {
    if (isDemo()) return Promise.resolve(demoIndex());
    return rest('vocab_states?select=state_val&class_key=eq.' + CFG.INDEX_KEY)
      .then(function (rows) {
        if (!rows || !rows.length) throw new Error('每日索引还没装载');
        return JSON.parse(rows[0].state_val);
      })
      .catch(function (err) {
        try {
          var c = JSON.parse(lsGet('cet4.index') || 'null');
          if (c && c.v && (Date.now() - c.t) < 86400000) return c.v;
        } catch (e) { }
        // 🔴 断网兜底：读站点自带的本地索引（data/index.json）⇒ 学生没网也能进站
        return fetch('data/index.json', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (j && j.latest) return j; throw err; })
          .catch(function () { throw err; });
      })
      .then(function (idx) {
        try { lsSet('cet4.index', JSON.stringify({ t: Date.now(), v: idx })); } catch (e) { }
        return idx;
      });
  }

  function getIssue(date) {
    var d = date || CFG.DEMO_DATE;
    if (isDemo()) return Promise.resolve(demoIssue());
    if (issueCache[d]) return Promise.resolve(issueCache[d]);
    return rest('vocab_states?select=state_val&class_key=eq.' + CFG.DAY_PREFIX + d)
      .then(function (rows) {
        if (!rows || !rows.length) throw new Error('这一期的内容还没装载，请稍后再来');
        var issue = JSON.parse(rows[0].state_val);
        issueCache[d] = issue;
        try { lsSet('cet4.issue.' + d, JSON.stringify({ t: Date.now(), v: issue })); } catch (e) { }
        return issue;
      })
      .catch(function (err) {
        try {
          var c = JSON.parse(lsGet('cet4.issue.' + d) || 'null');
          if (c && c.v && (Date.now() - c.t) < 86400000) { issueCache[d] = c.v; return c.v; }
        } catch (e) { }
        // 🔴 断网兜底：读站点自带的本地那一期（data/issues/<日期>.json）
        return fetch('data/issues/' + d + '.json', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (j && j.date) { issueCache[d] = j; return j; } throw err; })
          .catch(function () { throw err; });
        throw err;
      });
  }

  /* ================= 时间工具 ================= */
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmtDate(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function todayStr() { return fmtDate(new Date()); }
  function nowTime() {
    var n = new Date();
    return pad(n.getHours()) + ':' + pad(n.getMinutes()) + ':' + pad(n.getSeconds());
  }
  // 2026-10-24 + 21:50 → "2026/10/24 21:50:00"（与老师老站的时间格式一致）
  function tsString(dateStr, timeStr) {
    var d = (dateStr || todayStr()).split('-');
    var t = (timeStr || nowTime()).split(':');
    return d[0] + '/' + parseInt(d[1], 10) + '/' + parseInt(d[2], 10) + ' ' +
      pad(parseInt(t[0], 10) || 0) + ':' + pad(parseInt(t[1], 10) || 0) + ':' +
      pad(parseInt(t[2], 10) || 0);
  }
  // "2026/10/24 21:50:00" → {date:'2026-10-24', time:'21:50'}
  function parseTs(ts) {
    var m = /(\d{4})\/(\d{1,2})\/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?/.exec(ts || '');
    if (!m) return { date: '', time: '' };
    return {
      date: m[1] + '-' + pad(parseInt(m[2], 10)) + '-' + pad(parseInt(m[3], 10)),
      time: m[4] ? pad(parseInt(m[4], 10)) + ':' + m[5] : ''
    };
  }

  /* ================= 记录格式（BRIEF 第 6 节） ================= */
  function logBrowse(date, sec) {
    return '【CET4】浏览 ' + date + ' | 时长: ' + Math.max(0, Math.round(sec || 0)) + '秒';
  }
  function logTrans(date, hit, total, comp, band, miss, errs) {
    var s = '【CET4】翻译练习 ' + date + ' | 命中: ' + hit + '/' + total;
    if (comp != null && comp !== '') s += ' | 完整度: ' + comp + '%';
    if (band) s += ' | 档位: ' + band;
    // 🔴 语法错误类型（老师端靠它统计「全班最常见的错误类型」）
    if (errs && errs.length) {
      s += ' | 错: ' + errs.slice(0, 6).map(function (x) {
        return String(x).replace(/[，,|｜\n]/g, ' ').trim();
      }).filter(Boolean).join(',');
    }
    // 漏掉的采分表达（中文短语，逗号分隔）—— 老师端靠它算「全班最弱的采分点」
    // ⚠️ 最多记 5 个，再多 action 会很长
    if (miss && miss.length) {
      s += ' | 漏: ' + miss.slice(0, 5).map(function (x) {
        return String(x).replace(/[，,|｜\n]/g, ' ').trim();
      }).filter(Boolean).join(',');
    }
    return s;
  }
  function logQuiz(date, ok, total) {
    return '【CET4】理解题 ' + date + ' | 答对: ' + ok + '/' + total;
  }
  // 反解 action：老师端统计全靠它
  function parseLog(action) {
    var out = { kind: '', date: '', num: 0, den: 0, sec: 0, comp: -1, band: '', miss: [], errs: [] };
    var m = /^【CET4】(浏览|翻译练习|理解题)\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(时长|命中|答对):\s*(\d+)(?:\/(\d+))?(秒)?/.exec(action || '');
    if (!m) return out;
    out.kind = m[1];
    out.date = m[2];
    out.num = parseInt(m[4], 10) || 0;
    out.den = m[5] ? (parseInt(m[5], 10) || 0) : 0;
    out.sec = m[6] ? out.num : 0;
    // 翻译练习的新字段（向后兼容：老日志没有这些）
    var cm = /\|\s*完整度:\s*(\d+)%/.exec(action || '');
    if (cm) out.comp = parseInt(cm[1], 10);
    var bm = /\|\s*档位:\s*([^\n|]+)/.exec(action || '');
    if (bm) out.band = bm[1].trim();
    var mm = /\|\s*漏:\s*([^\n|]+)/.exec(action || '');
    if (mm) {
      out.miss = mm[1].split(',').map(function (x) { return x.trim(); })
        .filter(function (x) { return !!x; });
    }
    // 语法错误类型（老日志没有这个字段 ⇒ 保持空数组，兼容）
    var em = /\|\s*错:\s*([^\n|]+)/.exec(action || '');
    if (em) {
      out.errs = em[1].split(',').map(function (x) { return x.trim(); })
        .filter(function (x) { return !!x; });
    }
    return out;
  }

  /* ================= 活动记录写入（失败不阻塞做题） ================= */
  function queueGet() {
    try { return JSON.parse(lsGet(CFG.QKEY) || '[]'); } catch (e) { return []; }
  }
  function queueSet(q) { lsSet(CFG.QKEY, JSON.stringify(q || [])); }

  function postLog(body, keepalive) {
    return rest('vocab_history_logs', {
      method: 'POST', prefer: 'return=representation', body: body, keepalive: keepalive
    }).then(function (rows) {
      return { ok: true, id: rows && rows[0] ? rows[0].id : null };
    });
  }

  // 写一条记录；失败 → 进本地队列，稍后重试（学生做题不受影响）
  function addLog(rec) {
    var body = {
      class_key: CFG.LOG_KEY,
      class_name: rec.class_name || '',
      student_id: rec.student_id || '',
      timestamp: rec.timestamp || tsString(todayStr(), nowTime()),
      duration_sec: Math.max(0, Math.round(rec.duration_sec || 0)),
      action: rec.action || ''
    };
    if (isDemo()) { demoAddLog(body); return Promise.resolve({ ok: true, id: 'demo', demo: true }); }
    return postLog(body).catch(function (err) {
      var q = queueGet();
      q.push(body);
      queueSet(q);
      return { ok: false, queued: true, msg: err && err.message ? err.message : '记录暂时发不出去，已存到本机' };
    });
  }

  // 重试本地队列（联网后自动跑）
  function flushQueue() {
    var q = queueGet();
    if (!q.length) return Promise.resolve({ ok: true, sent: 0 });
    if (isDemo()) { queueSet([]); return Promise.resolve({ ok: true, sent: 0, demo: true }); }
    var sent = 0;
    function step() {
      if (!q.length) return Promise.resolve({ ok: true, sent: sent });
      var one = q[0];
      return postLog(one).then(function () {
        q.shift(); queueSet(q); sent++;
        return step();
      }).catch(function () {
        queueSet(q);
        return { ok: false, sent: sent };
      });
    }
    return step();
  }

  /* ================= 自测号 =================
     🔴 老师/教研自测专用：不占名册、不进老师看板统计。
     ① 学号固定 9999999999，姓名随意（登录页有按钮一键填好）
     ② 日志里 student_id 存成 __test__9999999999 ⇒ 一眼认得出
     ③ 老师看板 / 明细 / CSV 一律过滤掉它 ⇒ 自测一百遍也不污染班级数据
     ⚠️ 这个号谁都能进（anon key 公开在前端）⇒ 它的记录只作自测用，不作教学证据 */
  function isTestId(id) {
    return String(id || '').indexOf(CFG.TEST_PREFIX) === 0;
  }

  /* ================= 登录 ================= */
  function studentLogin(id, name) {
    var sid = String(id || '').trim();
    var typed = String(name || '').trim().replace(/ /g, '');
    if (!sid || !typed) return Promise.resolve({ ok: false, msg: '请把学号和姓名都填上' });
    // 🔴 自测号：最先判断（演示模式也走这条 ⇒ demo 里自测同样带标记、不进看板）
    if (sid === CFG.TEST_SID) {
      return Promise.resolve({
        ok: true, token: 'test:' + sid,
        name: CFG.TEST_NAME, student_id: CFG.TEST_PREFIX + sid,
        class_key: 'CET4TEST', class_name: '自测账号',
        is_test: true, display_id: sid
      });
    }
    if (isDemo()) {
      return Promise.resolve({
        ok: true, token: 'demo:' + sid,
        name: typed, student_id: sid, class_key: 'DEMO1', class_name: '演示班级'
      });
    }
    return getRoster().then(function (list) {
      var s = findStudent(list, sid);
      if (!s) return { ok: false, msg: '学号不在名册里，请向老师确认' };
      if (String(s.name) !== typed) return { ok: false, msg: '姓名与名册不一致，请按名册填写' };
      return {
        ok: true,
        token: 'stu:' + s.id + ':' + Math.random().toString(36).slice(2),
        name: s.name, student_id: s.id, class_key: s.class_key, class_name: s.class_name
      };
    });
  }

  function sha256hex(str) {
    var c = global.crypto;
    if (c && c.subtle && global.TextEncoder) {
      return c.subtle.digest('SHA-256', new TextEncoder().encode(str))
        .then(function (buf) {
          return Array.prototype.map.call(new Uint8Array(buf), function (b) {
            return ('00' + b.toString(16)).slice(-2);
          }).join('');
        });
    }
    return Promise.reject(new Error('这个浏览器不支持安全哈希，请换新版浏览器打开'));
  }

  function teacherLogin(id, pass) {
    var tid = String(id || '').trim();
    var tpass = String(pass || '');
    if (!tid || !tpass) return Promise.resolve({ ok: false, msg: '请把工号和密码都填上' });
    if (isDemo()) return Promise.resolve({ ok: true, token: 'demo-teacher', name: '教师' });
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

  /* ================= 老师端取数 ================= */
  function teacherData(token, opts) {
    opts = opts || {};
    if (!/^tea:|^demo-teacher$/.test(token || '')) {
      return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
    }
    if (isDemo()) return Promise.resolve(demoTeacherData(opts));
    return Promise.all([
      getRoster(),
      fetchAllRows('vocab_history_logs?select=*&class_key=eq.' + CFG.LOG_KEY + '&order=id.desc')
    ]).then(function (res) {
      var roster = res[0] || [], rows = res[1] || [];
      var keyByName = {};
      roster.forEach(function (s) { keyByName[s.class_name] = s.class_key; });
      var nameById = {};
      roster.forEach(function (s) { nameById[String(s.id)] = s; });

      var logs = rows.map(function (r) {
        var ts = parseTs(r.timestamp);
        var p = parseLog(r.action);
        var st = nameById[String(r.student_id)];
        return {
          id: r.id,
          student_id: r.student_id,
          name: st ? st.name : r.student_id,
          class_key: st ? st.class_key : (keyByName[r.class_name] || r.class_name),
          class_name: r.class_name || (st ? st.class_name : ''),
          ts_date: ts.date, time: ts.time,
          action: r.action || '',
          kind: p.kind, issue_date: p.date,
          num: p.num, den: p.den, sec: p.sec,
          comp: p.comp, band: p.band, miss: p.miss, errs: p.errs,
          duration_sec: r.duration_sec || 0
        };
      });

      var from = opts.from || '', to = opts.to || '', cls = opts.class_key || '';
      // 🔴 自测号（__test__ 前缀）的记录不进老师看板 —— 它不是班里的学生
      var realLogs = logs.filter(function (l) { return !isTestId(l.student_id); });
      var filtered = realLogs.filter(function (l) {
        if (from && l.ts_date && l.ts_date < from) return false;
        if (to && l.ts_date && l.ts_date > to) return false;
        if (cls && l.class_key !== cls) return false;
        return true;
      });

      return { ok: true, roster: roster, logs: filtered, allLogs: realLogs };
    });
  }

  /* ================= 字号 / 高对比（墨水屏 + 老花眼） ================= */
  var prefs = {
    zoom: function () {
      var z = parseInt(lsGet('cet4.zoom') || '100', 10);
      if (isNaN(z) || z < 100) z = 100;      // 不许低于 19px 正文
      if (z > 160) z = 160;
      return z;
    },
    setZoom: function (z) {
      z = Math.max(100, Math.min(160, Math.round(z / 10) * 10));
      lsSet('cet4.zoom', String(z));
      prefs.applyZoom();
      return z;
    },
    applyZoom: function () {
      var d = global.document;
      if (!d || !d.documentElement) return;
      // 基准字号取 CSS 里的 --fs（19px），缩放只往上不往下 —— 正文永远 ≥19px
      var base = 19;
      try {
        if (global.getComputedStyle) {
          var v = global.getComputedStyle(d.documentElement).getPropertyValue('--fs');
          var n = parseFloat(v);
          if (n > 0) base = n;
        }
      } catch (e) { /* 用默认 19 */ }
      d.documentElement.style.fontSize = (base * prefs.zoom() / 100) + 'px';
    },
    hcOn: function () { return lsGet('cet4.hc') === '1'; },
    setHc: function (on) { lsSet('cet4.hc', on ? '1' : '0'); prefs.applyHc(); },
    applyHc: function () {
      var d = global.document;
      if (!d || !d.documentElement) return;
      d.documentElement.classList.toggle('hc', prefs.hcOn());
    },
    applyAll: function () { prefs.applyZoom(); prefs.applyHc(); }
  };

  /* ================= 演示模式数据 ================= */
  // 演示名册：36 人 / 3 个班 —— 规模接近真实（老师名册 157 人），
  // 否则看板的「平均完整度 / 档位分布 / 最弱采分点」样本太少，看不出效果。
  var DEMO_ROSTER = (function () {
    var SURN = '赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜';
    var GIVEN = ['子涵', '欣怡', '浩然', '梓萱', '宇轩', '思远', '雨桐', '若曦', '嘉懿', '一鸣',
      '静姝', '皓轩', '沐宸', '知微', '书瑶', '清越', '逸凡', '砚清', '婉宁', '致远'];
    var out = [], i, j;
    for (i = 0; i < 36; i++) {
      j = i % 3 + 1;
      out.push({
        id: '2026' + (1000 + i),
        name: '示例·' + SURN.charAt(i % SURN.length) + GIVEN[i % GIVEN.length],
        class_key: 'DEMO' + j,
        class_name: '演示' + ['一', '二', '三'][j - 1] + '班'
      });
    }
    return out;
  })();

  function demoRoster() { return DEMO_ROSTER.slice(); }

  function demoIndex() {
    return {
      latest: CFG.DEMO_DATE,
      issues: [{ date: CFG.DEMO_DATE, seq: 1, topic: '民营经济', title: '民营经济与中国经济新动能' }]
    };
  }

  var demoLogs = null;
  function demoAddLog(body) {
    if (!demoLogs) demoLogs = demoSeed();
    demoLogs.unshift({
      id: demoLogs.length + 1,
      student_id: body.student_id, class_name: body.class_name,
      ts_date: (body.timestamp || tsString()).slice(0, 10).replace(/-/g, '/').split('/')
        .reverse().join('-'),
      time: (body.timestamp || '').slice(11, 16),
      action: body.action, duration_sec: body.duration_sec
    });
  }

  function demoSeed() {
    // 确定性伪随机（不用 Math.random，保证每次演示数据一致）
    var seed = 20261024;
    function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }
    var logs = [];
    var kinds = ['浏览', '翻译练习', '理解题'];
    for (var i = 0; i < 42; i++) {
      var s = DEMO_ROSTER[Math.floor(rnd() * DEMO_ROSTER.length)];
      var dayOffset = Math.floor(rnd() * 5);
      var d = new Date();
      d.setDate(d.getDate() - dayOffset);
      var date = fmtDate(d);
      var kind = kinds[Math.floor(rnd() * kinds.length)];
      var action;
      if (kind === '浏览') {
        action = logBrowse(date, 120 + Math.floor(rnd() * 400));
      } else if (kind === '翻译练习') {
        action = logTrans(date, 5 + Math.floor(rnd() * 7), 12);
      } else {
        action = logQuiz(date, 2 + Math.floor(rnd() * 4), 5);
      }
      logs.push({
        id: i + 1, student_id: s.id, name: s.name, class_key: s.class_key, class_name: s.class_name,
        ts_date: date, time: pad(8 + Math.floor(rnd() * 12)) + ':' + pad(Math.floor(rnd() * 60)),
        action: action, duration_sec: kind === '浏览' ? parseInt(/(\d+)秒/.exec(action)[1], 10) : 0
      });
    }

    // ---- 今天：课堂速练的翻译记录（带完整度 / 档位 / 漏点）----
    // ⚠️ 档位字符串在这里是**直接给的**，不做映射 ——
    //    映射的唯一权威是 scoring.js（学生端算出来的），这里只是让演示数据看着像真的。
    var DEMO_EXPRS = ((DEMO_ISSUE.translation && DEMO_ISSUE.translation.exprs) || [])
      .map(function (e) { return e.cn; });
    var DEMO_BANDS = ['第1档', '第2档', '第3档', '第4档', '第5档'];
    var today = fmtDate(new Date());
    for (var j = 0; j < DEMO_ROSTER.length; j++) {
      if (rnd() > 0.80) continue;             // 约 80% 的人今天做了
      var st = DEMO_ROSTER[j];
      var total = 7;                          // 速练 2 句 ≈ 7 个采分点
      // 漏 0~3 个为主，偶尔漏一大半（模拟「整句没写」的学生）
      var nMiss = (rnd() < 0.14) ? 4 + Math.floor(rnd() * 2) : Math.floor(rnd() * 4);
      if (nMiss > total) nMiss = total;
      var miss = [];
      var guard = 0;
      while (miss.length < nMiss && DEMO_EXPRS.length && guard++ < 20) {
        var pick = DEMO_EXPRS[Math.floor(rnd() * DEMO_EXPRS.length)];
        if (miss.indexOf(pick) < 0) miss.push(pick);
      }
      var hit = total - miss.length;
      var comp = Math.round(hit / total * 100);
      var band = comp >= 95 ? DEMO_BANDS[0] : comp >= 85 ? DEMO_BANDS[1]
        : comp >= 70 ? DEMO_BANDS[2] : comp >= 40 ? DEMO_BANDS[3] : DEMO_BANDS[4];
      // 🔴 带语法错误类型 ⇒ 老师看板的「全班最常见的语法错误类型」有数据可看
      var DEMO_ERRS = [['时态', '冠词'], ['时态'], ['冠词', '主谓一致'], ['单复数'],
        ['时态', '冠词', '介词搭配'], ['拼写'], ['动词形式'], ['时态', '主谓一致', '单复数']];
      var errs = DEMO_ERRS[Math.floor(rnd() * DEMO_ERRS.length)];
      if (rnd() < 0.25) errs = [];          // 有些学生一条都没错
      logs.push({
        id: logs.length + 1, student_id: st.id, name: st.name,
        class_key: st.class_key, class_name: st.class_name,
        ts_date: today, time: pad(9 + Math.floor(rnd() * 8)) + ':' + pad(Math.floor(rnd() * 60)),
        action: logTrans(today, hit, total, comp, band, miss, errs),
        duration_sec: 0
      });
    }
    return logs;
  }

  function demoTeacherData(opts) {
    opts = opts || {};
    if (!demoLogs) demoLogs = demoSeed();
    var logs = demoLogs.map(function (l) {
      var p = parseLog(l.action);
      return {
        id: l.id, student_id: l.student_id, name: l.name,
        class_key: l.class_key, class_name: l.class_name,
        ts_date: l.ts_date, time: l.time, action: l.action,
        kind: p.kind, issue_date: p.date, num: p.num, den: p.den, sec: p.sec,
        comp: p.comp, band: p.band, miss: p.miss, errs: p.errs,
        duration_sec: l.duration_sec || 0
      };
    });
    var from = opts.from || '', to = opts.to || '', cls = opts.class_key || '';
    var filtered = logs.filter(function (l) {
      if (from && l.ts_date < from) return false;
      if (to && l.ts_date > to) return false;
      if (cls && l.class_key !== cls) return false;
      return true;
    });
    return Promise.resolve({ ok: true, roster: demoRoster(), logs: filtered, allLogs: logs });
  }

  /* ================= 演示用假 issue（字段见 SCHEMA.md） ================= */
  var DEMO_ISSUE = {
  "date": "2026-10-24",
  "seq": 1,
  "topic": "民营经济",
  "title": "民营经济与中国经济新动能",
  "subtitle": "读短文、背生词、做翻译、测理解，一天一期",
  "source": {
    "name": "自编事例",
    "url": "",
    "date": "2026-10-24"
  },
  "article": {
    "en": "Private enterprises have become a major force in China's economy. In recent years, the Chinese government has attached great importance to their development and has introduced a series of policies to support them.\n\nBy the end of March 2025, the number of private enterprises nationwide exceeded 57 million, and they account for 92.3% of the total number of enterprises registered in the country. Small and medium-sized firms create most of the local jobs.\n\nPrivate firms have grown steadily in number, and their quality and structure have improved over the same period. Investment in research and development within the private sector keeps rising, and progress is fast in new generation information technology and artificial intelligence.\n\nSome economists warn that private firms still face higher financing costs than large state-owned companies. The rising competitiveness of private firms now gives solid support to the high-quality growth of China's economy.",
    "zh": "民营企业已成为中国经济的一支重要力量。近年来，中国政府<b>高度重视</b>民营企业的发展，出台了<b>一系列</b>政策<b>支持</b>民营企业。<br><br>截至 2025 年 3 月底，全国民营企业<b>数量超过</b>5700 万家，<b>占</b>全国登记企业总数的 92.3%。中小企业提供了大部分本地就业岗位。<br><br>民营企业<b>不仅</b>数量<b>稳步增长</b>，质量和结构也<b>在不断提升</b>。民营企业的<b>研发投入不断增加</b>，在<b>新一代信息技术</b>、人工智能等<b>领域发展迅速</b>。<br><br>一些经济学家警告，私营企业面临的<b>融资成本</b>仍高于大型国有企业。民营企业<b>核心竞争力的增强</b>为中国经济的<b>高质量发展</b><b>提供了有力支撑</b>。",
    "words": 143
  },
  "vocab": [
    {
      "w": "enterprise",
      "pos": "n.",
      "cn": "企业",
      "ex": "Private enterprises have become a major force in the economy.",
      "note": "复数 enterprises；注意与 entrepreneur 区分"
    },
    {
      "w": "attach",
      "pos": "v.",
      "cn": "重视；附上",
      "ex": "The government has attached great importance to their development.",
      "note": "attach importance to 是固定搭配，不接 very"
    },
    {
      "w": "policies",
      "pos": "n.",
      "cn": "政策（复数）",
      "ex": "The government has introduced a series of policies to support them.",
      "note": "单数 policy；搭配 introduce a series of"
    },
    {
      "w": "exceed",
      "pos": "v.",
      "cn": "超过",
      "ex": "The number of private enterprises exceeded 57 million.",
      "note": "本身含「超过」义，勿再加 more than"
    },
    {
      "w": "account for",
      "pos": "phr.",
      "cn": "占（比例）",
      "ex": "Private firms account for 92.3% of all enterprises.",
      "note": "后接百分数；也可说 make up"
    },
    {
      "w": "steadily",
      "pos": "adv.",
      "cn": "稳步地",
      "ex": "Private enterprises have grown steadily in number.",
      "note": "修饰动词 grow，不可用 steady 代替"
    },
    {
      "w": "investment",
      "pos": "n.",
      "cn": "投资",
      "ex": "The research and development investment has been increasing.",
      "note": "research and development 缩写 R&D"
    },
    {
      "w": "artificial intelligence",
      "pos": "phr.",
      "cn": "人工智能",
      "ex": "They are developing rapidly in the field of artificial intelligence.",
      "note": "缩写 AI；四级作文常考"
    },
    {
      "w": "financing",
      "pos": "n.",
      "cn": "融资",
      "ex": "Private firms still face higher financing costs.",
      "note": "搭配 financing costs"
    },
    {
      "w": "competitiveness",
      "pos": "n.",
      "cn": "竞争力",
      "ex": "The enhancement of the core competitiveness provides strong support.",
      "note": "core competitiveness 核心竞争力"
    }
  ],
  "translation": {
    "title": "民营经济",
    "brief": "本篇五句取自今日短文，先自己写，再逐条核对采分表达。",
    "sentences": [
      {
        "no": 1,
        "cn": "近年来，中国政府<b>高度重视</b>民营经济的发展，出台了<b>一系列</b>政策<b>支持</b>民营企业。",
        "ref": "In recent years, the Chinese government has attached great importance to the development of the private economy and has introduced a series of policies to support private enterprises.",
        "hints": {
          "kw": [
            "高度重视",
            "一系列",
            "支持"
          ],
          "struct": "时间状语 + 并列谓语 (has attached ... and has introduced ...)",
          "goal": "写出两个并列的现在完成时谓语"
        },
        "pts": [
          {
            "t": "时间状语 <code>In recent years</code> 不能删，它决定整句用<b>现在完成时</b> has attached。"
          },
          {
            "t": "<code>attach great importance to</code> 是第 1 句核心采分点。<b>to 是介词</b>，后面必须接名词。"
          },
          {
            "t": "两个并列谓语 <b>has attached</b> / <b>has introduced</b> 用 and 连接，<b>时态要一致</b>。"
          },
          {
            "t": "「出台政策」直译成 <code>make policies</code> 会判错，<code>introduce a series of policies</code> 才是地道表达。"
          }
        ]
      },
      {
        "no": 2,
        "cn": "<b>截至</b>2025年3<b>月底</b>，全国民营企业<b>数量超过</b>5700万家，<b>占</b>企业总量的92.3%。",
        "ref": "By the end of March 2025, the number of private enterprises nationwide exceeded 57 million, and they account for 92.3% of the total number of enterprises.",
        "hints": {
          "kw": [
            "截至…月底",
            "数量超过",
            "占…比例"
          ],
          "struct": "By the end of ... + 主语 + 过去式谓语",
          "goal": "主语 the number of 用单数谓语"
        },
        "pts": [
          {
            "t": "<code>By the end of March 2025</code> 是<b>过去时间</b>，所以用过去式 exceeded，这是本句最大的时态陷阱。"
          },
          {
            "t": "主语是 <code>the number of</code>（单数），谓语用<b>单数</b> exceeded。"
          },
          {
            "t": "<b>不要加 more than</b>：exceed 本身即「超过」，<code>exceeded more than 57 million</code> 是病句。"
          },
          {
            "t": "<code>account for 92.3%</code> 是本句第二个采分点，后接百分数，不要漏掉 for。"
          }
        ]
      },
      {
        "no": 3,
        "cn": "民营企业<b>不仅</b>数量<b>稳步增长</b>，质量和结构也<b>在不断提升</b>。",
        "ref": "Private enterprises have not only grown steadily in number but also improved in quality and structure.",
        "hints": {
          "kw": [
            "不仅…而且",
            "稳步增长",
            "不断提升"
          ],
          "struct": "主语 + have not only 过去分词 + but also 过去分词",
          "goal": "两部分时态一致，都用 have"
        },
        "pts": [
          {
            "t": "<code>not only ... but also ...</code> 是本句骨架，两部分<b>时态必须一致</b>。"
          },
          {
            "t": "「数量稳步增长」补出 <code>in number</code> 让英文更完整，是<b>加分项</b>。"
          },
          {
            "t": "「质量和结构也在不断提升」用 <code>improved in quality and structure</code>，用 in 引出维度。"
          },
          {
            "t": "常见错误是漏助动词：<code>Private enterprises not only grown...</code> 缺 have，语法直接判错。"
          }
        ]
      },
      {
        "no": 4,
        "cn": "民营企业的<b>研发投入不断增加</b>，在<b>新一代信息技术</b>、人工智能等<b>领域发展迅速</b>。",
        "ref": "The research and development investment of private enterprises has been increasing, and they are developing rapidly in such fields as the new generation of information technology and artificial intelligence.",
        "hints": {
          "kw": [
            "研发投入",
            "领域",
            "发展迅速"
          ],
          "struct": "has been increasing + and they are developing",
          "goal": "R&D 要写全 research and development"
        },
        "pts": [
          {
            "t": "<code>research and development investment</code>（R&amp;D investment）是固定术语，写成 <code>research investment</code> 会丢分。"
          },
          {
            "t": "「不断增加」用<b>现在完成进行时</b> <code>has been increasing</code>，强调持续性。"
          },
          {
            "t": "「等领域」用 <code>in such fields as</code>，其中 as 引导的是<b>举例</b>。"
          },
          {
            "t": "本句是<b>并列谓语</b>，主语切换到企业，第二句用 <b>they</b> 而不重复名词。"
          }
        ]
      },
      {
        "no": 5,
        "cn": "民营企业<b>核心竞争力的增强</b>为中国经济的<b>高质量发展</b><b>提供了有力支撑</b>。",
        "ref": "The enhancement of the core competitiveness of private enterprises has provided strong support for the high-quality development of China's economy.",
        "hints": {
          "kw": [
            "核心竞争力的增强",
            "高质量发展",
            "提供了有力支撑"
          ],
          "struct": "抽象名词作主语 + has provided strong support for",
          "goal": "主语是「核心竞争力的增强」"
        },
        "pts": [
          {
            "t": "本句<b>最易错的主语</b>：主语应是 <code>The enhancement of...</code>，不是 private enterprises。"
          },
          {
            "t": "<code>provide strong support for</code>，for 后接受益对象 China's economy，这是第 5 句核心采分点。"
          },
          {
            "t": "「高质量发展」是 <code>high-quality development</code>，作定语时用<b>连字符</b>。"
          },
          {
            "t": "全句用现在完成时 <code>has provided</code>，与全文「近年来持续趋势」的基调一致。"
          }
        ]
      }
    ],
    "exprs": [
      {
        "cn": "高度重视",
        "en": "attach great importance to",
        "note": "固定搭配，不接 very；介词 to 必须保留。",
        "re": "attach(ed)?\\s+(great|considerable|significant)\\s+importance\\s+to|attach\\s+importance\\s+to",
        "sno": 1
      },
      {
        "cn": "出台（政策）",
        "en": "introduce",
        "note": "政策类动词用 introduce，勿用 make policies。",
        "re": "introduc|roll\\s+out|issu",
        "sno": 1
      },
      {
        "cn": "一系列",
        "en": "a series of",
        "note": "数词可数，谓语用复数：a series of policies have...",
        "re": "a\\s+series\\s+of",
        "sno": 1
      },
      {
        "cn": "支持",
        "en": "support",
        "note": "可作及物动词直接带宾语。",
        "re": "support",
        "sno": 1
      },
      {
        "cn": "截至…月底",
        "en": "by the end of",
        "note": "高频错点。until 意为「持续到」，语义相反。",
        "re": "by\\s+the\\s+end\\s+of",
        "sno": 2
      },
      {
        "cn": "数量超过",
        "en": "exceed",
        "note": "exceed 已含「超过」义，勿再加 more than。",
        "re": "exceed",
        "sno": 2
      },
      {
        "cn": "占…的百分比",
        "en": "account for",
        "note": "后接百分数。也可 make up / constitute。",
        "re": "account\\s+for|make\\s+up|constitut",
        "sno": 2
      },
      {
        "cn": "不仅…而且",
        "en": "not only ... but also ...",
        "note": "本篇第 3 句骨架，置于主语后需用助动词。",
        "re": "not\\s+only",
        "sno": 3
      },
      {
        "cn": "稳步增长",
        "en": "grow steadily",
        "note": "副词修饰动词，不可用 steady 代替。",
        "re": "steadily|grow\\s+steadily|grown\\s+steadily",
        "sno": 3
      },
      {
        "cn": "不断提升",
        "en": "keep improving",
        "note": "持续性动词优先用 keep doing。",
        "re": "keep\\s+\\w+ing|improv",
        "sno": 3
      },
      {
        "cn": "研发投入",
        "en": "research and development investment",
        "note": "固定缩写 R&D，正式写作建议写全。",
        "re": "research\\s+and\\s+development|r\\s*&\\s*d",
        "sno": 4
      },
      {
        "cn": "提供有力支撑",
        "en": "provide strong support for",
        "note": "for 后接受益方；support 不可数。",
        "re": "strong\\s+support|provide\\s+\\w*\\s*support|powerful\\s+support",
        "sno": 5
      }
    ],
    "pitfalls": [
      {
        "title": "搭配错误",
        "wrong": "The government very attaches importance to the private economy.",
        "why": "attach importance to 本身不接 very，且 to 是介词不能省。",
        "right": "The government has attached great importance to the private economy."
      },
      {
        "title": "时间状语漏译",
        "wrong": "Until March 2025, the number of private enterprises exceeded 57 million.",
        "why": "「截至…月底」的固定表达是 by the end of，until 表示「持续到」，语义相反。",
        "right": "By the end of March 2025, the number of private enterprises exceeded 57 million."
      },
      {
        "title": "比较级用错",
        "wrong": "The number of enterprises has exceeded over 57 million.",
        "why": "exceed 本身含「超过」义，再加 over 就是病句。",
        "right": "The number of enterprises exceeded 57 million."
      },
      {
        "title": "定语从句误用",
        "wrong": "The enterprises which have more than 57 million are increasing.",
        "why": "汉语「数量超过 5700 万家企业」是名词短语，套 which 会变成「拥有 5700 万的企业」。",
        "right": "The number of private enterprises that has exceeded 57 million is increasing."
      },
      {
        "title": "时态不一致",
        "wrong": "Private enterprises not only grow steadily but also improve in quality.",
        "why": "本篇讲「近年来的持续趋势」，应用现在完成时；且 not only 后要有助动词。",
        "right": "Private enterprises have not only grown steadily but also improved in quality."
      },
      {
        "title": "被动语态滥用",
        "wrong": "R&D investment is increased continuously by private enterprises.",
        "why": "汉语「不断增加」无施动者，英译常直接用主动 has been increasing 更自然。",
        "right": "The R&D investment of private enterprises has been increasing."
      }
    ],
    "classroom": [
      1,
      2
    ],
    "classroomNote": "课堂速练用的两句（2 句模式默认出这两句；5 句全练出全部）"
  },
  "quiz": [
    {
      "q": "What is the main idea of the first paragraph?",
      "opts": [
        "The government supports private firms.",
        "Private firms create most local jobs.",
        "Small local banks are closing every year.",
        "Private firms invest in new fields."
      ],
      "answer": 0,
      "why": "第一段讲政府高度重视并出台政策支持民营企业。",
      "evidence": "In recent years, the Chinese government has attached great importance to their development"
    },
    {
      "q": "How many private enterprises were there nationwide by the end of March 2025?",
      "opts": [
        "Exactly 92.3% of them.",
        "About 60 thousand.",
        "Less than 5 million.",
        "More than 57 million."
      ],
      "answer": 3,
      "why": "第二段给出数量：超过 5700 万家。",
      "evidence": "the number of private enterprises nationwide exceeded 57 million"
    },
    {
      "q": "What percentage do private enterprises account for?",
      "opts": [
        "57% of the local job market.",
        "60% of the national tax revenue.",
        "92.3% of all registered enterprises.",
        "23.9% of the new investment."
      ],
      "answer": 2,
      "why": "第二段说民营企业占登记企业总数的 92.3%。",
      "evidence": "they account for 92.3% of the total number of enterprises registered in the country"
    },
    {
      "q": "Which fields are mentioned as areas of fast development?",
      "opts": [
        "Banking, insurance and shipping.",
        "New IT and artificial intelligence.",
        "Coal mining and steel making.",
        "Tourism and film production."
      ],
      "answer": 1,
      "why": "第三段点名新一代信息技术与人工智能等领域。",
      "evidence": "new generation information technology and artificial intelligence"
    },
    {
      "q": "What problem do economists warn about?",
      "opts": [
        "Private firms face higher financing costs.",
        "Private firms create too many jobs.",
        "The government supports them too much.",
        "Small firms are growing too quickly."
      ],
      "answer": 0,
      "why": "末段指出私营企业融资成本仍高于大型国有企业。",
      "evidence": "private firms still face higher financing costs than large state-owned companies"
    }
  ]
};

  function demoIssue() { return DEMO_ISSUE; }

  /* ================= 导出 ================= */
  global.CET4 = {
    CFG: CFG,
    isDemo: isDemo,
    rest: rest,
    fetchAllRows: fetchAllRows,
    getRoster: getRoster,
    findStudent: findStudent,
    getIndex: getIndex,
    getIssue: getIssue,
    studentLogin: studentLogin,
    isTestId: isTestId,
    testSid: CFG.TEST_SID,
    testName: CFG.TEST_NAME,
    teacherLogin: teacherLogin,
    teacherData: teacherData,
    addLog: addLog,
    flushQueue: flushQueue,
    logBrowse: logBrowse,
    logTrans: logTrans,
    logQuiz: logQuiz,
    parseLog: parseLog,
    tsString: tsString,
    parseTs: parseTs,
    fmtDate: fmtDate,
    todayStr: todayStr,
    nowTime: nowTime,
    prefs: prefs,
    demoIssue: demoIssue,
    demoRoster: demoRoster,
    demoIndex: demoIndex
  };
})(typeof window !== 'undefined' ? window : globalThis);
