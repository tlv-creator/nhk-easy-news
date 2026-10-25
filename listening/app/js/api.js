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
    SALT: 'vocab-unit23-teacher-2026',  // 公开 salt（与词汇站一致）；密码本身不在前端
    // 🔴 教师侧「删一条 / 清空全部」的服务端特权端点（挂在 CloudBase 的 ai-proxy 上）
    //    端点内部用 service_role 执行删除，并在**服务端**校验老师口令摘要
    //    ⇒ 前端只负责把摘要送过去；这两个地址是公开的，但**摘要是凭据**
    //    ⇒ 两个候选：老师把路由加在 /ai 后面 或 单独开一个 /vocab-ops，两个都试
    OPS_URLS: [
      'https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai/vocab-ops',
      'https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/vocab-ops'
    ]
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
    if (opts.headers) {
      for (var hk in opts.headers) {
        if (Object.prototype.hasOwnProperty.call(opts.headers, hk)) headers[hk] = opts.headers[hk];
      }
    }
    /* 🔴 网络层失败要**重试**：微信 webview / 弱网 / 连接被重置时，一次失败就报
       「连不上服务器」⇒ 学生直接进不去（2026-10-25 实测有多位同学这样）
       ⇒ 现在：网络层失败重试 2 次（共 3 次，退避 0.7s / 1.6s），HTTP 错误码**不重试**
       ⇒ 另外加 15 秒超时（原来没有 ⇒ 卡住的请求会一直转圈） */
    var left = (opts.retries === undefined) ? 2 : opts.retries;
    var timeoutMs = opts.timeoutMs || 15000;
    function once() {
      var ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      var timer = ctl ? setTimeout(function () { try { ctl.abort(); } catch (e) { } }, timeoutMs) : null;
      return fetch(CFG.SU + '/rest/v1/' + path, {
        method: opts.method || 'GET',
        headers: headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: ctl ? ctl.signal : undefined
      }).then(function (r) {
        return r.text().then(function (t) {
          if (timer) clearTimeout(timer);
          if (!r.ok) {
            throw new Error(friendlyMsg(r.status, t));   /* HTTP 错误码 ⇒ 直接抛，不重试 */
          }
          if (!t) return null;
          try { return JSON.parse(t); } catch (e) { return t; }
        });
      }, function (e) {
        if (timer) clearTimeout(timer);
        var isAbort = e && (e.name === 'AbortError' || e.code === 20);
        var msg = isAbort ? '网络太慢，等太久没连上，请再试一次' : '连不上服务器，请检查网络后重试';
        if (left > 0) {
          left--;
          var wait = 700 + (2 - left) * 900;
          return new Promise(function (res) { setTimeout(res, wait); }).then(once);
        }
        var err = new Error(msg);
        err.net = true;
        throw err;
      });
    }
    return once();
  }

  /* ================= 教师侧特权操作（走云函数端点） =================
     端点（CloudBase ai-proxy 挂载）内部用 service_role 执行删除，
     并在**服务端**用 check_teacher_login 校验老师口令摘要。
     ⇒ 前端不再用 anon key 直接删整班。

     返回四态，**这四态的含义不能混**：
       ok           端点执行成功（body.deleted = 真实删掉的行数）
       unauthorized 端点在线但老师凭据不对 ⇒ **绝不退回 anon 路径**（退回＝绕过校验）
       error        端点在线但请求有问题（400 等）⇒ 如实报错
       notdeployed  端点还没上线（404/405/5xx/网络错）⇒ 允许退回旧的 anon 路径（过渡期）
  */
  function opsPost(payload) {
    var urls = CFG.OPS_URLS.slice();
    function tryOne(i) {
      if (i >= urls.length) {
        return Promise.resolve({ state: 'notdeployed', status: 0, body: null });
      }
      var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      var timer = ctrl ? setTimeout(function () { try { ctrl.abort(); } catch (e) { /* ignore */ } }, 12000) : null;
      return fetch(urls[i], {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ctrl ? ctrl.signal : undefined
      }).then(function (r) {
        return r.text().then(function (t) {
          if (timer) clearTimeout(timer);
          var b = null;
          try { b = t ? JSON.parse(t) : null; } catch (e) { b = null; }
          // 🔴🔴 先认人：ai-proxy 那个函数对**任何** POST 都会回 {"ok":false,"error":"bad_request"}
          //    ⇒ 不带 service 标记的响应 = **我的端点还没上线**（路由没加 / 函数没更新）
          //    ⇒ 必须退回旧的 anon 路径，否则老师的两个按钮直接废
          var mine = !!(b && b.service === 'vocab-ops');
          if (!mine) return { state: 'notdeployed', status: r.status, body: b };
          if (r.status === 401) return { state: 'unauthorized', status: 401, body: b };
          if (r.ok && b && b.ok === true) return { state: 'ok', status: r.status, body: b };
          if (r.status === 400) return { state: 'error', status: 400, body: b };
          if (r.status >= 500) return { state: 'notdeployed', status: r.status, body: b };
          return { state: 'error', status: r.status, body: b };
        });
      }, function () {
        if (timer) clearTimeout(timer);
        return tryOne(i + 1);
      });
    }
    return tryOne(0);
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
        // 🔴 兜底顺序：**本机缓存（24h，更新鲜）→ 随页面发布的静态名册 → 抛错**
        //    静态名册解决的是「第一次打开 + 网络被拦/太慢」这类场景
        //    （之前只有本机缓存 ⇒ 从没成功加载过的设备**永远进不去**）
        try {
          var c = JSON.parse(localStorage.getItem('listen.roster') || 'null');
          if (c && c.v && (Date.now() - c.t) < 86400000) { rosterCache = c.v; return c.v; }
        } catch (e2) { }
        try {
          var fb = global.LISTEN_ROSTER_FALLBACK;
          if (fb && fb.length) {
            rosterCache = fb;
            try { localStorage.setItem('listen.roster', JSON.stringify({ t: Date.now(), v: fb })); } catch (e3) { }
            return fb;
          }
        } catch (e4) { }
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

  /* ================= 错误翻译 =================
     🔴 原始报错会直接显示给学生 —— 浏览器实测：提交失败时页面吐的是
        「new row violates row-level security policy for table "vocab_history_logs"」
        ⇒ 统一翻译成人话；技术细节只进 console（我和老师看日志用）
     ⚠️ 判据：宁可笼统，也不要把数据库/接口内部结构暴露给学生 */
  function friendlyMsg(status, text) {
    var low = String(text || '').toLowerCase();
    var tech = 'HTTP ' + status + ' ' + String(text || '').slice(0, 200);
    try { console.warn('[听力站]', tech); } catch (e) { }
    if (status === 401 || status === 403 || low.indexOf('row-level security') >= 0 ||
        low.indexOf('42501') >= 0 || low.indexOf('permission denied') >= 0) {
      return '服务器暂时收不到数据。请稍后再试一次，还是不行就告诉老师。';
    }
    if (status === 404 || low.indexOf('pgrst202') >= 0) {
      return '页面版本不一致。请刷新一次，还是不行就告诉老师。';
    }
    if (status === 429) {
      return '请求太频繁，请稍后再试。';
    }
    if (status >= 500) {
      return '服务器出错了，请稍后再试。';
    }
    if (status === 0) {
      return '连不上服务器，请检查网络后重试。';
    }
    return '操作没成功，请稍后再试。';
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
        // 🔴 末尾挂「行令牌」：这是这条记录**唯一**的删除凭证
        //    （RLS 侧：只有带着同一枚令牌 + 同一个学号，才允许删这一行）
        //    🔴🔴 库里只写 **SHA-256(原始令牌)**；原始令牌只留本机
        //        （action 列 anon 可读 ⇒ 明文令牌会被同学读走 ⇒ 就能删他的记录）
        var raw = randomToken();
        return sha256Hex(raw).then(function (h) {
        var action = '【听力】' + (cat || '未分类') + ' | ' + content
          + (note ? ' | 备注:' + note : '') + ' | #tk=' + h;
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
          var id = rows && rows[0] ? rows[0].id : null;
          if (id !== null && id !== undefined) tkSave(id, raw);   // 原始令牌只留本机
          return { ok: true, id: id };
        });
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

    /* ---- 清除本机删除凭证（界面入口用） ---- */
  if (name === 'listen_clear_local_tokens') {
    return Promise.resolve({ ok: true, cleared: tkClearAll() });
  }

  /* ---- 删自己的一条 ---- */
    if (name === 'listen_delete_log') {
      var sid3 = sidOf(args.p_token);
      if (!sid3) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      // 🔴 令牌从**本机**取（库里只有哈希 ⇒ 取不到）⇒ 换设备就删不了 ⇒ 明说
      // ⚠️ **不要**回退到 args.p_tk：那是库里那份**哈希**，不是原始令牌 ⇒ 回退等于送错凭证
      var tk = tkGet(args.p_id);
      if (!tk) return Promise.resolve({
        ok: false, msg: '本机没有这条记录的删除凭证（换过设备或清了缓存）⇒ 请找老师帮你删'
      });
      return rest('vocab_history_logs?id=eq.' + Number(args.p_id) +
        '&class_key=eq.' + CFG.LOG_KEY + '&student_id=eq.' + sid3,
        {
          method: 'DELETE', prefer: 'return=representation',
          // 🔴 RLS 现在按「x-student-id 头 + 行令牌」限定只能删自己那一条 ⇒ 两个头都要带
          headers: { 'x-student-id': sid3, 'x-row-token': tk }
        })
        // 🔴 RLS 挡 DELETE 时 PostgREST 仍返回 200/204 + 空数组 ⇒ 「看着删了、其实没删」
        //    ⇒ 判定必须落在**返回行数**上，不能看 HTTP 码
        .then(function (rows) {
          var n = Array.isArray(rows) ? rows.length : 0;
          if (!n) return { ok: false, msg: '服务器没有真正删除这条记录，请稍后再试或联系老师' };
          return { ok: true, deleted: n };
        });
    }

    /* ---- 教师登录：密码在服务端比哈希 ---- */
    if (name === 'listen_teacher_login') {
      var tid = String(args.p_id || '').trim();
      var tpass = String(args.p_pass || '');
      if (!tid || !tpass) return Promise.resolve({ ok: false, msg: '请把工号和密码都填上' });
      var thash = '';
      return sha256hex(CFG.SALT + '|' + tid + '|' + tpass)
        .then(function (h) {
          thash = h;
          return rest('rpc/check_teacher_login', { method: 'POST', body: { p_id: tid, p_hash: h } });
        })
        .then(function (r) {
          if (r === true || (r && r.ok === true)) {
            // 🔴 把摘要一起交出去：它是「服务端特权端点」的凭据
            //    （端点自己会用 check_teacher_login 再校验一遍，前端这层只是省一次往返）
            return { ok: true, token: 'tea:' + Math.random().toString(36).slice(2), name: '教师', hash: thash, tid: tid };
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
      var dtid = String(args.p_tid || ''), dhash = String(args.p_thash || '');
      if (!dtid || !dhash) return Promise.resolve({ ok: false, msg: '请重新登录后再操作' });
      return opsPost({
        action: 'delete_log', teacher_id: dtid, teacher_hash: dhash,
        id: Number(args.p_id), class_key: CFG.LOG_KEY
      }).then(function (op) {
        if (op.state === 'ok') {
          var n = op.body && typeof op.body.deleted === 'number' ? op.body.deleted : 0;
          if (!n) return { ok: false, msg: '服务器没有真正删除这条记录，请稍后再试' };
          return { ok: true, deleted: n, via: 'ops' };
        }
        // 🔴 unauthorized 绝不能退回 anon 路径（那等于绕过老师凭据校验）
        if (op.state === 'unauthorized') return { ok: false, msg: '登录已过期，请重新登录' };
        if (op.state === 'error') return { ok: false, msg: '服务器没有真正删除这条记录，请稍后再试' };
        // 端点还没上线 ⇒ 退回旧的 anon 路径（过渡期；收紧 SQL 收掉 LISTEN 例外后就没了）
        return rest('vocab_history_logs?id=eq.' + Number(args.p_id) + '&class_key=eq.' + CFG.LOG_KEY,
          { method: 'DELETE', prefer: 'return=representation' })
          .then(function (rows) {
            var n = Array.isArray(rows) ? rows.length : 0;
            if (!n) return { ok: false, msg: '服务器没有真正删除这条记录，请稍后再试' };
            return { ok: true, deleted: n, via: 'anon' };
          });
      });
    }

    /* ---- 教师清空全部 ---- */
    if (name === 'listen_teacher_clear') {
      if (!isTeacherToken(args.p_token)) return Promise.resolve({ ok: false, msg: '登录已过期，请重新登录' });
      var ctid = String(args.p_tid || ''), chash = String(args.p_thash || '');
      if (!ctid || !chash) return Promise.resolve({ ok: false, msg: '请重新登录后再操作' });
      return opsPost({
        action: 'clear_class', teacher_id: ctid, teacher_hash: chash, class_key: CFG.LOG_KEY
      }).then(function (op) {
        if (op.state === 'ok') {
          var n = op.body && typeof op.body.deleted === 'number' ? op.body.deleted : 0;
          // 🔴 0 行 = **没删掉**（权限被挡）⇒ 绝不能回 ok:true（那是「假成功」）
          if (!n) return { ok: false, msg: '服务器没有真正清空（可能被数据库权限挡住了），请稍后再试' };
          return { ok: true, deleted: n, via: 'ops' };
        }
        if (op.state === 'unauthorized') return { ok: false, msg: '登录已过期，请重新登录' };
        if (op.state === 'error') return { ok: false, msg: '服务器没有真正清空（可能被数据库权限挡住了），请稍后再试' };
        // 🔴🔴🔴 端点没上线 ⇒ **不走 anon 退路**（就地禁用这个按钮）
        //   原因：anon 退路就是 `DELETE ?class_key=eq.LISTEN` —— **一把清空全班**的那个形状
        //   ⇒ 2026-10-24 那 34 条真实学生记录就是被这个形状清掉的
        //   ⇒ 端点上线后（op.state==='ok'）这个按钮自动恢复，走 service_role，**不再碰 anon**
        //   ⇒ 过渡期老师如果真要清空：先让端点上线（或找我），不要用这个按钮
        return { ok: false, msg: '「清空全部」暂时不可用：服务端还没上线，走老路会用公开权限直接清空全班（这正是上次出事的那个操作）。请先让云函数上线，或找管理员处理。' };
      });
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
    // 🔴🔴 抽出「行令牌」并**从显示文本里剥掉**
    //    令牌是这条记录的唯一删除凭证（RLS 用它判「只能删自己那一条」）
    //    ⇒ 必须藏起来：老师看表格 / 导出 CSV / 学生看列表，都不该出现这串东西
    var tk = '';
    var tm = /#tk=([A-Za-z0-9]{16,64})/.exec(rest2);
    if (tm) { tk = tm[1]; rest2 = rest2.replace(tm[0], '').replace(/\s*\|\s*$/, ''); }
    if (note) { var nm2 = /#tk=([A-Za-z0-9]{16,64})/.exec(note); if (nm2) note = note.replace(nm2[0], '').replace(/\s*\|\s*$/, ''); }
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
      note: note,
      tk: tk
    };
  }

  /* 🔴🔴 令牌**不明文落库**（2026-10-24 修正）
     背景：action 列对 anon 是**可读**的（老师看板要读它）⇒ 明文令牌人人可读
        ⇒ 而名册也在 anon 可读的 vocab_states 里（登录页要读）
        ⇒ 任何学生都能拿到同学的学号 + 令牌 ⇒ 能把某个同学的全部记录删光
     ⇒ 所以：库里只存 **SHA-256(原始令牌)**；原始令牌**只留本机**（localStorage）
     ⇒ 删除时发原始令牌，RLS 侧算 sha256 后比对 ⇒ 读到 action 的人反推不出原始令牌
     ⇒ 代价：学生换设备 / 清缓存后，删不了自己**以前**的记录（老师可代删）⇒ 界面上要说明 */
  function sha256Hex(str) {
    var c = global.crypto || global.msCrypto;
    if (!c || !c.subtle || !c.subtle.digest) {
      return Promise.reject(new Error('本浏览器不支持加密摘要，无法生成删除凭证'));
    }
    var enc = new TextEncoder();
    return c.subtle.digest('SHA-256', enc.encode(String(str))).then(function (buf) {
      var a = new Uint8Array(buf), s = '';
      for (var i = 0; i < a.length; i++) s += ('0' + a[i].toString(16)).slice(-2);
      return s;
    });
  }

  /* 本机凭证存取：key = 行 id ⇒ 值 = 原始令牌 */
  function tkKey(id) { return 'listen.tk.' + id; }
  function tkSave(id, raw) {
    try { localStorage.setItem(tkKey(id), raw); } catch (e) { /* 隐私模式等 */ }
  }
  function tkGet(id) {
    try { return localStorage.getItem(tkKey(id)) || ''; } catch (e) { return ''; }
  }
  function tkClearAll() {
    var n = 0;
    try {
      var ks = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf('listen.tk.') === 0) ks.push(k);
      }
      ks.forEach(function (k) { localStorage.removeItem(k); n++; });
    } catch (e) { /* ignore */ }
    return n;
  }

  /* 生成「行令牌」：这条记录的唯一删除凭证
     🔴 必须是**不可猜**的随机串（RLS 靠它判「只能删自己那一条」）
     ⇒ 浏览器自带的 crypto.getRandomValues；没有就退化成多段 Math.random（弱，仅兜底） */
  function randomToken() {
    try {
      var c = (global.crypto || global.msCrypto);
      var a = new Uint8Array(16);
      c.getRandomValues(a);
      var s = '';
      for (var i = 0; i < a.length; i++) s += ('0' + a[i].toString(16)).slice(-2);
      return s;
    } catch (e) {
      // 🔴 兜底分支也必须是**32 位 hex、字符集 [0-9a-f]**：
      //    ① RLS 的白名单是 ^[A-Za-z0-9]{16,64}$ ⇒ base36 虽然能过，但 4 段各至少 1 字符
      //       ⇒ 理论上可能短于 16 位 ⇒ 被白名单挡掉 ⇒ 自己的记录永远删不掉（静默失效）
      //    ② 形状与主分支统一 ⇒ 两条路径的哈希/白名单行为一致
      var t = '';
      for (var j = 0; j < 32; j++) {
        t += Math.floor(Math.random() * 16).toString(16);
      }
      return t;
    }
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
