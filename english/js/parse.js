/* =====================================================================
   统一教师看板 · 解析层
   ---------------------------------------------------------------------
   🔴 铁律：这里的每一条正则都**逐字复用**现役老师端的那一条。
      自己「重写一个差不多的」⇒ 两边报表数字对不上 ⇒ 老师会以为数据在骗人。
      来源：
        · 词汇站   vocab/index.html  → parsePracticeLogs()
        · 四级站   js/api.js         → parseLog()
        · 听力站   js/api.js         → rowToLog()
      改动这里的正则 = 改三个站的共同口径，必须三处一起改。
   ===================================================================== */
'use strict';

(function (global) {
  'use strict';

  var TH = global.TH;

  /* ---------------- 词汇：平时练习 ----------------
     格式 A（新）: 【平时练习】Unit N | Page N | 模式: X | 时长: N秒 | 配对: N对 | 点击: N次 | 在站: N秒
     格式 B（旧）: 【平时练习提交】Page N | 匹配成功: N对 | 耗时: N秒
     口径与词汇站老师端一致：旧格式一律归第三课（历史数据一行不改） */
  function parsePracticeLogs(logs, unitFilter) {
    logs = (logs || []).filter(function (l) {
      /* 🔴 隔离落在数据层：只认词汇站的四个班 key */
      if (TH.VOCAB_KEYS.indexOf(l.class_key) < 0) return false;
      return !TH.isTestId(l.student_id);
    });
    var map = {};
    logs.forEach(function (l) {
      var a = l.action || '';
      var g = function (re) { var m = a.match(re); return m ? parseInt(m[1], 10) : 0; };
      var unit = 0, page = 0, sec = 0, pairs = 0, clicks = 0, wall = 0, segs = 0, mode = '';
      if (a.indexOf('【平时练习】') === 0) {
        unit = g(/Unit (\d+)/);
        page = g(/Page (\d+)/);
        sec = g(/时长: (\d+)秒/);
        pairs = g(/配对: (\d+)对/);
        clicks = g(/点击: (\d+)次/);
        wall = g(/在站: (\d+)秒/);
        segs = 1;
        var mm = a.match(/模式: ([^|]+)/); if (mm) mode = mm[1].trim();
      } else if (a.indexOf('【平时练习提交】') === 0) {
        unit = 3;
        page = g(/Page (\d+)/);
        sec = g(/耗时: (\d+)秒/);
        pairs = g(/匹配成功: (\d+)对/);
        wall = sec; segs = 1; mode = '消消乐配对';
      } else {
        return;   /* 只统计平时练习，正式测验不混进来 */
      }
      if (unitFilter && String(unit) !== String(unitFilter)) return;
      var it = map[l.student_id] || (map[l.student_id] = {
        sid: l.student_id, cls: l.class_name || '', sec: 0, pairs: 0, clicks: 0, wall: 0,
        segs: 0, units: {}, pages: {}, last: '', mode: {},
        /* 🔴 班级以**日志自己**的 class_key / class_name 为准
           （名册的班 ≠ 日志的班：某位同学名册 CWZT1，日志 WL_CW） */
        clsKey: l.class_key || '', clsName: l.class_name || ''
      });
      it.sec += sec; it.pairs += pairs; it.clicks += clicks; it.wall += wall; it.segs += segs;
      it.units[unit] = (it.units[unit] || 0) + 1;
      it.pages[unit + '-' + page] = (it.pages[unit + '-' + page] || 0) + 1;
      if (mode) it.mode[mode] = (it.mode[mode] || 0) + 1;
      if (!it.last || String(l.timestamp) > String(it.last)) it.last = l.timestamp;
    });
    return Object.keys(map).map(function (k) { return map[k]; });
  }

  /* 词汇站里其它行为日志（登录/练习入口）—— 用于「活跃天数」等辅助指标 */
  function isVocabActivity(action) {
    var a = action || '';
    return a.indexOf('🔓') === 0 || a.indexOf('🎮') === 0
      || a.indexOf('【平时练习】') === 0 || a.indexOf('【平时练习提交】') === 0;
  }

  /* ---------------- 四级：浏览 / 翻译练习 / 理解题 ----------------
     格式: 【CET4】(浏览|翻译练习|理解题) YYYY-MM-DD | (时长|命中|答对): N(/N)(秒)? */
  function parseCet4Log(action) {
    var out = { kind: '', date: '', num: 0, den: 0, sec: 0 };
    var m = /^【CET4】(浏览|翻译练习|理解题)\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(时长|命中|答对):\s*(\d+)(?:\/(\d+))?(秒)?/.exec(action || '');
    if (!m) return out;
    out.kind = m[1];
    out.date = m[2];
    out.num = parseInt(m[4], 10) || 0;
    out.den = m[5] ? (parseInt(m[5], 10) || 0) : 0;
    out.sec = m[6] ? out.num : 0;
    return out;
  }

  function parseCet4Logs(logs) {
    var map = {};
    (logs || []).forEach(function (l) {
      /* 🔴 隔离落在数据层（同上：不靠调用方记得传对） */
      if (l.class_key !== TH.CET4_KEY) return;
      if (TH.isTestId(l.student_id)) return;
      var p = parseCet4Log(l.action || '');
      if (!p.kind) return;
      var it = map[l.student_id] || (map[l.student_id] = {
        sid: l.student_id, cls: l.class_name || '',
        browse_sec: 0, trans_n: 0, trans_hit: 0, trans_den: 0,
        quiz_n: 0, quiz_ok: 0, quiz_den: 0,
        days: {}, last: '', issues: {},
        clsKey: l.class_key || '', clsName: l.class_name || ''
      });
      if (p.kind === '浏览') {
        it.browse_sec += p.sec;
        it.days[p.date] = 1;
      } else if (p.kind === '翻译练习') {
        it.trans_n++; it.trans_hit += p.num; it.trans_den += p.den;
        it.issues[p.date] = (it.issues[p.date] || 0) + 1;
      } else if (p.kind === '理解题') {
        it.quiz_n++; it.quiz_ok += p.num; it.quiz_den += p.den;
        it.issues[p.date] = (it.issues[p.date] || 0) + 1;
      }
      if (!it.last || String(l.timestamp) > String(it.last)) it.last = l.timestamp;
    });
    return Object.keys(map).map(function (k) { return map[k]; });
  }

  /* ---------------- 听力：一条记录 = 一次听力 ----------------
     格式: 【听力】类别 | 内容 [| 备注:xxx] [| #tk=token]
     🔴 token 是删除凭证，解析时**必须剥掉**，不能出现在老师看到的「内容」里。

     🔴🔴 「没有【听力】前缀」的记录**照样保留**（类别为空、内容=整条 action）
        —— 这是听力站 rowToLog() 的**原样行为**，不是我的选择：
        LISTEN 这个 class_key 整个就是听力站独占写的，格式坏了也要在老师表里可见
        （悄悄吞掉 = 老师以为学生没交，其实交了但没记录）。
        ⇒ 口径必须与现役听力站一致，否则两个老师端数字对不上。 */
  function parseListenLog(row) {
    var out = { category: '', content: '', note: '', minutes: 0, date: '', time: '' };
    /* 🔴 用统一的 parseTs（库里有两种 timestamp 形态，手写正则会漏一种） */
    var ts = parseTs(row.timestamp);
    out.date = ts.date;
    out.time = ts.time;
    var action = row.action || '';
    var m = /^【听力】(.*?)\s*\|\s*([\s\S]*)$/.exec(action);
    var category = m ? m[1] : '';
    var rest2 = m ? m[2] : action;
    /* 剥掉行令牌 */
    rest2 = rest2.replace(/\s*\|\s*#tk=[A-Za-z0-9]+/g, '');
    var nm = /\|\s*备注:([\s\S]*)$/.exec(rest2);
    var note = '';
    if (nm) { note = nm[1].trim(); rest2 = rest2.slice(0, nm.index); }
    out.category = category;
    out.content = rest2.trim();
    out.note = note;
    out.minutes = Math.round((row.duration_sec || 0) / 60);
    return out;
  }

  function parseListenLogs(logs) {
    var map = {};
    (logs || []).forEach(function (l) {
      /* 🔴 隔离必须落在**数据层**，不能靠调用方「记得传对 class_key」
         ⇒ 将来谁传了全量数组，别的站的行也混不进来。 */
      if (l.class_key !== TH.LISTEN_KEY) return;
      if (TH.isTestId(l.student_id)) return;
      var p = parseListenLog(l);
      if (!p.category && !p.content) return;
      var it = map[l.student_id] || (map[l.student_id] = {
        sid: l.student_id, cls: l.class_name || '',
        cnt: 0, minutes: 0, days: {}, cats: {}, last: '', rows: [],
        clsKey: l.class_key || '', clsName: l.class_name || ''
      });
      it.cnt++;
      it.minutes += p.minutes;
      it.days[p.date] = 1;
      it.cats[p.category] = (it.cats[p.category] || 0) + 1;
      if (!it.last || String(l.timestamp) > String(it.last)) it.last = l.timestamp;
      it.rows.push({ ts: l.timestamp, cat: p.category, content: p.content, note: p.note, min: p.minutes });
    });
    return Object.keys(map).map(function (k) { return map[k]; });
  }

  /* ---------------- 工具 ---------------- */
  function fmtSec(sec) {
    sec = Math.round(sec || 0);
    var m = Math.floor(sec / 60);
    return m > 0 ? (m + '分' + (sec % 60) + '秒') : (sec + '秒');
  }
  function unitLabel(unit) {
    return { 1: '第一课', 2: '第二课', 3: '第三课' }[unit] || ('U' + unit);
  }

  /* 🔴🔴 时间戳**在库里有两种形态**（实测出来的，不是猜的）：
       ① '2026/9/23 21:33:25'      ← 词汇站/听力站写的（斜杠，年在前）
       ② '9/28/2026, 1:22:00 PM'   ← 某个站用 new Date().toLocaleString() 写的
          （英文 locale，**月在前、12 小时制、带 AM/PM**）
     ⇒ 只认 ① 的正则会让 ② 原样透传 ⇒ 老师看到两种格式混排
     ⇒ 而且 ② 的**排序是错的**（字符串比大小：'9/28/2026' vs '2026/9/23'
        → '9' > '2' ⇒ 9 月的会被当成"更新"）⇒ 排序和"最近"都会错
     ⇒ 统一解析成 {date:'2026-09-28', time:'13:22', ts:Date}
  */
  function parseTs(raw) {
    var s = String(raw == null ? '' : raw).trim();
    var out = { date: '', time: '', num: NaN };
    if (!s) return out;

    /* 形态 ②：英文 locale —— '9/28/2026, 1:22:00 PM' / '9/28/2026, 1:22:00 AM' */
    var m2 = /^(\d{1,2})\/(\d{1,2})\/(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?/i.exec(s);
    if (m2) {
      var hh = parseInt(m2[4], 10);
      var ap = (m2[7] || '').toUpperCase();
      if (ap === 'PM' && hh < 12) hh += 12;
      if (ap === 'AM' && hh === 12) hh = 0;
      out.date = m2[3] + '-' + ('0' + m2[1]).slice(-2) + '-' + ('0' + m2[2]).slice(-2);
      out.time = ('0' + hh).slice(-2) + ':' + m2[5];
      out.num = new Date(out.date + 'T' + out.time).getTime();
      return out;
    }

    /* 形态 ①：斜杠，年在前 —— '2026/9/23 21:33:25' 或 '2026/09/28 13:22:00' */
    var m1 = /(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/.exec(s);
    if (m1) {
      out.date = m1[1] + '-' + ('0' + m1[2]).slice(-2) + '-' + ('0' + m1[3]).slice(-2);
      if (m1[4] != null) out.time = ('0' + m1[4]).slice(-2) + ':' + m1[5];
      out.num = new Date(out.date + 'T' + (out.time || '00:00')).getTime();
      return out;
    }

    return out;   /* 认不出来 ⇒ 空（宁可空也不要错） */
  }

  /* 统一显示：'2026-09-28 13:22' */
  function fmtTs(raw) {
    var p = parseTs(raw);
    if (!p.date) return String(raw == null ? '' : raw).trim();
    return p.date + (p.time ? ' ' + p.time : '');
  }
  function pct(num, den) {
    if (!den) return '';
    return Math.round(num * 1000 / den) / 10 + '%';
  }
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  }

  TH.parse = {
    parsePracticeLogs: parsePracticeLogs,
    isVocabActivity: isVocabActivity,
    parseCet4Log: parseCet4Log,
    parseCet4Logs: parseCet4Logs,
    parseListenLog: parseListenLog,
    parseListenLogs: parseListenLogs,
    parseTs: parseTs,
    fmtTs: fmtTs,
    fmtSec: fmtSec,
    unitLabel: unitLabel,
    pct: pct,
    todayStr: todayStr
  };
})(typeof window !== 'undefined' ? window : this);
