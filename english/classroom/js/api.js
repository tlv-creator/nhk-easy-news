/* =====================================================================
   课堂投影页 · 数据层
   ---------------------------------------------------------------------
   🔴 这个页面**复用现成的四级日报内容库**，不另造内容管线：
     每日索引 → vocab_states 的 __cet4_index 一行
     每日正文 → vocab_states 的 __cet4_day_YYYY-MM-DD 一行
     （与四级站 student/teacher 端**完全同一份数据**）
   ⇒ 内容更新 = 往库里写新的一行 ⇒ 投影页**自动**跟着更新，不用重新部署。

   🔴 投影场景的关键取舍：
     · 断网必须还能上课 ⇒ 读不到库时退回**站点自带的本地副本**
       （data/index.json + data/issues/<日期>.json），跟四级站同一个套路
     · 只读，不写库
   ===================================================================== */
'use strict';

var CLS = (function (global) {

  var CFG = {
    SU: 'https://lszwxcmvcvbraqtrtgon.supabase.co',
    KEY: 'sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo',
    INDEX_KEY: '__cet4_index',
    DAY_PREFIX: '__cet4_day_'
  };

  var LS = 'cls4.';
  var issueCache = {};

  function lsGet(k, def) {
    try { var v = localStorage.getItem(LS + k); return v == null ? def : JSON.parse(v); }
    catch (e) { return def; }
  }
  function lsSet(k, v) {
    try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch (e) { }
  }

  function rest(path) {
    return fetch(CFG.SU + '/rest/v1/' + path, {
      method: 'GET',
      headers: { apikey: CFG.KEY, Authorization: 'Bearer ' + CFG.KEY }
    }).then(function (r) {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    });
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  /* 🔴 库里日期是 '2026-10-24'；但有的地方写成 '2026/10/24' ⇒ 两种都认 */
  function normDate(s) {
    if (!s) return '';
    var m = /^(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})/.exec(String(s));
    if (!m) return String(s);
    return m[1] + '-' + pad(parseInt(m[2], 10)) + '-' + pad(parseInt(m[3], 10));
  }

  /* ---------------- 每日索引 ---------------- */
  function getIndex() {
    return rest('vocab_states?select=state_val&class_key=eq.' + CFG.INDEX_KEY)
      .then(function (rows) {
        if (!rows || !rows.length) throw new Error('每日索引还没装载');
        return JSON.parse(rows[0].state_val);
      })
      .catch(function (err) {
        /* ① 先用上次缓存（24 小时内） */
        try {
          var c = lsGet('index');
          if (c && c.v && (Date.now() - c.t) < 86400000) return c.v;
        } catch (e) { }
        /* ② 再读站点自带的本地索引（断网也能上课） */
        return fetch('data/index.json', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (j && j.latest) return j; throw err; })
          .catch(function () { throw err; });
      })
      .then(function (idx) {
        try { lsSet('index', { t: Date.now(), v: idx }); } catch (e) { }
        return idx;
      });
  }

  /* ---------------- 某一期正文 ---------------- */
  function getIssue(date) {
    var d = normDate(date);
    if (issueCache[d]) return Promise.resolve(issueCache[d]);
    return rest('vocab_states?select=state_val&class_key=eq.' + CFG.DAY_PREFIX + d)
      .then(function (rows) {
        if (!rows || !rows.length) throw new Error('这一期的内容还没装载');
        var issue = JSON.parse(rows[0].state_val);
        issueCache[d] = issue;
        try { lsSet('issue.' + d, { t: Date.now(), v: issue }); } catch (e) { }
        return issue;
      })
      .catch(function (err) {
        try {
          var c = lsGet('issue.' + d);
          if (c && c.v && (Date.now() - c.t) < 86400000) { issueCache[d] = c.v; return c.v; }
        } catch (e) { }
        return fetch('data/issues/' + d + '.json', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (j && j.date) { issueCache[d] = j; return j; } throw err; })
          .catch(function () { throw err; });
      });
  }

  /* ---------------- 音频（可选增强） ----------------
     🔴 **听力是可选的**：某一天没有配套音频 ⇒ 这个槽位直接不出现
        ⇒ 主流程（阅读/生词/翻译/答案）完全不受影响
     音频文件放在站点 audio/<日期>.mp3（若部署了），或由 issue.audio 指定 */
  function getAudio(date) {
    var d = normDate(date);
    return getIssue(d).then(function (issue) {
      if (issue && issue.audio && issue.audio.url) return issue.audio;
      return null;
    }).catch(function () { return null; });
  }

  global.CLS = {
    CFG: CFG,
    getIndex: getIndex,
    getIssue: getIssue,
    getAudio: getAudio,
    todayStr: todayStr,
    normDate: normDate,
    pad: pad
  };
  return global.CLS;
})(typeof window !== 'undefined' ? window : this);
