/* =====================================================================
   统一教师看板 · 数据层
   ---------------------------------------------------------------------
   🔴 核心事实（决定了整套设计）：
     学生数据**不在网站里，在 Supabase 库里**。
     单词 / 听力 / 四级阅读翻译 三样**已经在同一个库、同一张表**里，
     只靠 class_key 区分：
        JR1 / CW3 / WL_CW / XQ1 → 词汇学习站（单词）
        LISTEN                   → 每日听力记录站（听力）
        CET4                     → 四级精读日报（阅读 + 翻译 + 理解题）
     ⇒ 所以「合并到一个地方看」= **给同一个库配一个新页面**，
        **不需要迁移任何数据，也不需要改任何学生网址**。

   🔴 本文件**只读**，不写库。口令校验走云端 RPC（check_teacher_login），
      前端**不存明文、不存摘要**。
   ===================================================================== */
'use strict';

var TH = (function (global) {

  /* ---------------- 配置 ---------------- */
  var CFG = {
    SU: 'https://lszwxcmvcvbraqtrtgon.supabase.co',
    /* 公开的 anon key（设计上就是公开的，安全性 100% 靠 RLS） */
    KEY: 'sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo',
    /* 与现有两个老师端**完全一致**的公开 salt（密码本身不在这里） */
    SALT: 'vocab-unit23-teacher-2026',
    /* 名册存放的 class_key（老站只查 JR1/CW3/WL_CW/XQ1，看不到这个） */
    ROSTER_KEY: '__roster_v1'
  };

  /* 词汇站的老班 key（历史数据的归属，不动它） */
  var VOCAB_KEYS = ['JR1', 'CW3', 'WL_CW', 'XQ1'];
  /* 听力站 */
  var LISTEN_KEY = 'LISTEN';
  /* 四级日报站 */
  var CET4_KEY = 'CET4';

  var CLASS_NAME = {
    JR1: '26互联网金融1班',
    CW3: '26财务管理3班',
    WL_CW: '物流财务合上课班',
    XQ1: '26学前教育1班',
    CWZT1: '26财资（待确认）',
    WL1: '26物流1班',
    LISTEN: '每日听力记录',
    CET4: '四级精读日报'
  };

  /* 自测号：数据层隔离，不进任何统计
     （口径与词汇站老师端一致 —— 同一个权威，不另立标准） */
  var TEST_ID_LIST = ['2601', '9999999999'];

  function isTestId(sid) {
    if (!sid) return false;
    var s = String(sid).trim();
    if (s.indexOf('__test__') === 0) return true;   /* 新格式 */
    return TEST_ID_LIST.indexOf(s) >= 0;              /* 旧格式（存量数据，不用迁移） */
  }
  function testIdOf(sid) { return String(sid || '').replace(/^__test__/, ''); }
  function displayIdOf(sid) { return isTestId(sid) ? (testIdOf(sid) + '（自测）') : String(sid || ''); }

  /* ---------------- HTTP ---------------- */
  function sbHeaders(extra) {
    var h = { apikey: CFG.KEY, Authorization: 'Bearer ' + CFG.KEY };
    if (extra) { for (var k in extra) h[k] = extra[k]; }
    return h;
  }

  /* 🔴🔴🔴 anon 角色单次最多只能读 1000 行（Supabase 默认 max-rows）
     ⇒ 凡是「要看全部记录」的表，必须分页取。

     🔴🔴🔴 **不能用 `Range` 请求头做分页！**（这是本项目真踩过的坑）
        浏览器跨域请求时，`Range` **不在 CORS safelist** 里 ⇒ 预检不带它
        ⇒ 服务端**永远返回第 1 页**（content-range 恒为 0-999/总数）
        ⇒ 「本页 < 1000 就停」的判断永远不成立 ⇒ **无限翻页 → 页面卡死**
        （而且不报错、不空数据，是最难查的那种挂）
        ⇒ 正确做法：用 **查询参数** `offset=` + `limit=`（普通参数，不受 CORS 限制）
           并且**用 content-range 里的总行数**判断终止（不靠本页长度猜）。

     依据：Range 头在跨域 + 自定义 header 组合下会被丢弃；query param 不会。 */
  function fetchAllRows(table, query, onPage) {
    var base = query || '';
    var rows = [];
    var LIMIT = 1000;
    var total = null;

    function page(offset) {
      var sep = base ? '&' : '?';
      var url = CFG.SU + '/rest/v1/' + table + '?' + base + sep +
        'offset=' + offset + '&limit=' + LIMIT;
      return fetchWithTimeout(url, {
        method: 'GET',
        headers: sbHeaders({ 'Prefer': 'count=exact' })
      }, 30000).then(function (r) {
        if (!r.ok) throw new Error('http ' + r.status);
        /* 🔴 终止判据用**服务端给的总数**，不靠本页长度 */
        var cr = r.headers.get('content-range') || '';
        var m = /\/(\d+|\*)$/.exec(cr);
        if (m && m[1] !== '*') total = parseInt(m[1], 10);
        return r.json();
      }).then(function (data) {
        if (!Array.isArray(data)) throw new Error('not-array');
        rows = rows.concat(data);
        if (onPage) onPage(rows.length, total);
        /* 三个终止条件任一满足就停（缺一不可） */
        var gotAll = (total != null) && (rows.length >= total);
        var emptyPage = data.length === 0;
        var shortPage = data.length < LIMIT;
        if (gotAll || emptyPage || shortPage) return rows;
        if (offset > 100000) throw new Error('pagination-guard');
        return page(offset + LIMIT);
      });
    }
    return page(0);
  }

  /* 🔴 读表先看 HTTP 码，别拿「返回体的长度」当条数
     ⇒ 这里统一抛错，不让上层把错误体当数据。
     🔴 加**硬超时**：挂死的请求不能让页面永远转圈（老师会以为网站坏了）。 */
  function fetchOne(table, query) {
    return fetchWithTimeout(CFG.SU + '/rest/v1/' + table + (query ? '?' + query : ''), {
      method: 'GET',
      headers: sbHeaders({ 'Prefer': 'count=exact' })
    }, 30000).then(function (r) {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    }).then(function (d) {
      if (!Array.isArray(d)) throw new Error('not-array');
      return d;
    });
  }

  /* 硬超时包装（浏览器端 fetch 没有默认超时） */
  function fetchWithTimeout(url, opts, ms) {
    var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () {
      try { ctrl.abort(); } catch (e) { }
    }, ms || 30000) : null;
    var o = opts || {};
    if (ctrl) o.signal = ctrl.signal;
    return fetch(url, o).then(function (r) {
      if (timer) clearTimeout(timer);
      return r;
    }, function (e) {
      if (timer) clearTimeout(timer);
      throw new Error('timeout-or-network: ' + (e && e.name ? e.name : 'error'));
    });
  }

  /* ---------------- 口令校验（云端，前端无明文） ---------------- */
  function sha256Hex(str) {
    if (global.crypto && global.crypto.subtle) {
      return global.crypto.subtle.digest('SHA-256', new TextEncoder().encode(str))
        .then(function (buf) {
          return Array.prototype.map.call(new Uint8Array(buf), function (b) {
            return ('0' + b.toString(16)).slice(-2);
          }).join('');
        });
    }
    return Promise.reject(new Error('no-crypto'));
  }

  function teacherLogin(teacherId, teacherPass) {
    if (!teacherId || !teacherPass) return Promise.reject(new Error('empty'));
    return sha256Hex(CFG.SALT + '|' + teacherId + '|' + teacherPass).then(function (hash) {
      return fetch(CFG.SU + '/rest/v1/rpc/check_teacher_login', {
        method: 'POST',
        headers: sbHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ p_id: String(teacherId), p_hash: hash })
      }).then(function (r) {
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      }).then(function (ok) {
        /* 🔴 只把 true 当通过；null / false / 异常 一律按「不通过」处理 */
        if (ok !== true) throw new Error('unauthorized');
        return true;
      });
    });
  }

  /* ---------------- 名册 ---------------- */
  function loadRoster() {
    return fetchOne('vocab_states', 'class_key=eq.' + CFG.ROSTER_KEY + '&select=state_val')
      .then(function (rows) {
        if (!rows.length) return [];
        var raw = rows[0].state_val;
        var list = (typeof raw === 'string') ? JSON.parse(raw) : raw;
        return Array.isArray(list) ? list : [];
      });
  }

  /* ---------------- 各站数据 ---------------- */
  function loadVocabLogs(onPage) {
    return fetchAllRows('vocab_history_logs',
      'select=*&class_key=in.(' + VOCAB_KEYS.join(',') + ')', onPage);
  }
  function loadListenLogs(onPage) {
    return fetchAllRows('vocab_history_logs',
      'select=*&class_key=eq.' + LISTEN_KEY, onPage);
  }
  function loadCet4Logs(onPage) {
    return fetchAllRows('vocab_history_logs',
      'select=*&class_key=eq.' + CET4_KEY, onPage);
  }
  function loadCet4Days() {
    /* 四级日报的「期」列表存在 vocab_states 的 __cet4_day_YYYY-MM-DD */
    return fetchAllRows('vocab_states', 'select=class_key,state_val', null)
      .then(function (rows) {
        return rows.filter(function (r) {
          return String(r.class_key || '').indexOf('__cet4_day_') === 0;
        }).map(function (r) {
          var v = r.state_val;
          if (typeof v === 'string') { try { v = JSON.parse(v); } catch (e) { v = {}; } }
          return v || {};
        });
      });
  }

  global.TH = {
    CFG: CFG,
    VOCAB_KEYS: VOCAB_KEYS,
    LISTEN_KEY: LISTEN_KEY,
    CET4_KEY: CET4_KEY,
    CLASS_NAME: CLASS_NAME,
    isTestId: isTestId,
    testIdOf: testIdOf,
    displayIdOf: displayIdOf,
    teacherLogin: teacherLogin,
    loadRoster: loadRoster,
    loadVocabLogs: loadVocabLogs,
    loadListenLogs: loadListenLogs,
    loadCet4Logs: loadCet4Logs,
    loadCet4Days: loadCet4Days
  };
  return global.TH;
})(typeof window !== 'undefined' ? window : this);
