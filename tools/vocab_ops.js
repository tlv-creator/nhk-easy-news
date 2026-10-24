/* =====================================================================
   教师侧「删一条 / 清空全部」——服务端特权操作
   挂在现役 CloudBase HTTP 云函数 ai-proxy 上（端点 /vocab-ops）
   ---------------------------------------------------------------------
   为什么要有这个端点：
     学生端/老师端现在都拿**公开的 anon key** 直连 Supabase
     ⇒ RLS 分不出「谁是老师」⇒ 任何人拿 anon key 都能 DELETE 整个班
     ⇒ 这个端点用 **service_role**（只在云端环境变量里）执行删除，
       并且**在服务端校验老师口令摘要**（复用现成的 check_teacher_login）。

   🔴 三条硬规矩（每一条都对应一次真实踩坑）：
     ① **绝不用 new URL()** —— CloudBase 的 Node 沙箱里没有全局 URL
        （本地全绿、线上 0.8 秒就挂）⇒ 全部自己拼字符串。
     ② **service_role 只能进环境变量**，绝不进前端 / 静态站 / APK / 任何 zip。
     ③ **class_key 服务端白名单**：请求里带的 class_key 只做校验，不做透传
        ⇒ 就算老师那串摘要泄漏，也只能动 LISTEN 一个班，动不了 JR1/CW3/…

   契约（与协作方约定的那一版一致）：
     POST {BASE}/vocab-ops
       { action:'ping' }
       { action:'delete_log', teacher_id, teacher_hash, id, class_key }
       { action:'clear_class', teacher_id, teacher_hash, class_key }
     → 200 {ok:true, deleted:N}      N = **真实删掉的行数**
     → 401 {ok:false, error:'unauthorized'}   校验不过 ⇒ 一行都不删
     → 400 {ok:false, error:'bad_request' | 'bad_class_key' | 'unknown_action'}
   ===================================================================== */
'use strict';

/* ---------------- 配置（只从环境变量读，绝不硬编码） ---------------- */
var SB_URL = (process.env.SB_URL || '').replace(/\/+$/, '');
var SB_KEY = process.env.SB_SERVICE_ROLE || '';

/* 服务端白名单：这个端点**只**服务每日听力记录站。
   别的班（JR1/CW3/WL_CW/XQ1/CET4…）一律 400，不给任何入口。 */
var ALLOWED_CLASS_KEYS = ['LISTEN'];

var TIMEOUT_MS = 15000;

/* ---------------- 小工具 ---------------- */

function json(statusCode, obj) {
  var o = { service: 'vocab-ops' };   // 🔴 身份标记：前端靠它分辨「这是我的端点」还是「ai-proxy 的通用响应」
  for (var k in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, k)) o[k] = obj[k];
  }
  return {
    statusCode: statusCode,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'Content-Type,Authorization',
      'Access-Control-Allow-Methods': 'POST,OPTIONS'
    },
    body: JSON.stringify(o)
  };
}

function err(statusCode, code, extra) {
  var o = { ok: false, error: code };
  if (extra) { for (var k in extra) { if (Object.prototype.hasOwnProperty.call(extra, k)) o[k] = extra[k]; } }
  return json(statusCode, o);
}

/* Supabase REST 调用（自己拼 URL，不用 URL / URLSearchParams） */
function sbFetch(path, method, body, prefer) {
  var headers = {
    'apikey': SB_KEY,
    'Authorization': 'Bearer ' + SB_KEY,
    'Content-Type': 'application/json'
  };
  if (prefer) headers['Prefer'] = prefer;

  var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  var timer = ctrl ? setTimeout(function () { try { ctrl.abort(); } catch (e) { /* ignore */ } }, TIMEOUT_MS) : null;

  return fetch(SB_URL + path, {
    method: method,
    headers: headers,
    body: (body === undefined || body === null) ? undefined : JSON.stringify(body),
    signal: ctrl ? ctrl.signal : undefined
  }).then(function (r) {
    return r.text().then(function (t) {
      if (timer) clearTimeout(timer);
      if (!r.ok) {
        var e = new Error('supabase_' + r.status);
        e.status = r.status;
        e.body = t;
        throw e;
      }
      if (!t) return null;
      try { return JSON.parse(t); } catch (e2) { return t; }
    });
  }, function (e) {
    if (timer) clearTimeout(timer);
    throw e;
  });
}

/* ---------------- 老师口令校验（复用现成的 check_teacher_login） ---------------- */
/*   注意：只把 true 当通过。null / false / 异常 一律按「不通过」处理。
     ——「取不到证据就当通过了」是这类校验最容易犯的错。 */
function checkTeacher(teacherId, teacherHash) {
  if (!teacherId || !teacherHash) return Promise.resolve(false);
  return sbFetch('/rest/v1/rpc/check_teacher_login', 'POST', { p_id: String(teacherId), p_hash: String(teacherHash) })
    .then(function (r) { return r === true; })
    .catch(function () { return false; });
}

/* ---------------- 三个动作 ---------------- */

/* 删一条：只删 id 匹配且 class_key 在白名单里的那一行 */
function doDeleteLog(req) {
  var id = Number(req.id);
  if (!id || id <= 0 || id % 1 !== 0) return Promise.resolve(err(400, 'bad_request', { detail: 'id 不合法' }));
  var ck = String(req.class_key || 'LISTEN');
  if (ALLOWED_CLASS_KEYS.indexOf(ck) < 0) return Promise.resolve(err(400, 'bad_class_key'));

  /* return=representation ⇒ 返回体里就是**真被删掉的行**
     ⇒ deleted 数直接从返回体数出来，不看 HTTP 码（RLS 挡 DELETE 也可能返回 200+[]） */
  return sbFetch('/rest/v1/vocab_history_logs?id=eq.' + id + '&class_key=eq.' + encodeURIComponent(ck),
    'DELETE', undefined, 'return=representation')
    .then(function (rows) {
      var n = Array.isArray(rows) ? rows.length : 0;
      return json(200, { ok: true, deleted: n, id: id, class_key: ck });
    });
}

/* 清空全部：只清白名单里的那个班 */
function doClearClass(req) {
  var ck = String(req.class_key || 'LISTEN');
  if (ALLOWED_CLASS_KEYS.indexOf(ck) < 0) return Promise.resolve(err(400, 'bad_class_key'));

  return sbFetch('/rest/v1/vocab_history_logs?class_key=eq.' + encodeURIComponent(ck),
    'DELETE', undefined, 'return=representation')
    .then(function (rows) {
      var n = Array.isArray(rows) ? rows.length : 0;
      return json(200, { ok: true, deleted: n, class_key: ck });
    });
}

/* ---------------- 事件解析（云函数 HTTP 触发的几种形状都兼容） ---------------- */

function pickPath(event) {
  var cands = [
    event && event.path,
    event && event.rawPath,
    event && event.requestContext && event.requestContext.http && event.requestContext.http.path,
    event && event.requestContext && event.requestContext.path,
    event && event.headers && (event.headers['x-tcb-path'] || event.headers['X-Tcb-Path']),
    event && event.queryStringParameters && event.queryStringParameters.path
  ];
  for (var i = 0; i < cands.length; i++) {
    if (cands[i]) {
      var s = String(cands[i]);
      var k = s.indexOf('/vocab-ops');
      if (k >= 0) return '/vocab-ops';
      if (s) return s;
    }
  }
  return '';
}

function pickMethod(event) {
  var m = (event && (event.httpMethod || event.requestMethod)) ||
    (event && event.requestContext && event.requestContext.httpMethod) ||
    (event && event.requestContext && event.requestContext.http && event.requestContext.http.method) ||
    'POST';
  return String(m).toUpperCase();
}

function pickBody(event) {
  var b = event && event.body;
  if (b === undefined || b === null || b === '') return {};
  if (typeof b === 'string') {
    try { return JSON.parse(b); } catch (e) { return null; }   // 解析失败 ⇒ 返回 null（坏请求）
  }
  if (typeof b === 'object') return b;
  return null;
}

/* ---------------- 主入口 ---------------- */

function handleVocabOps(event) {
  var path = pickPath(event);
  var method = pickMethod(event);

  if (method === 'OPTIONS') return json(200, { ok: true });

  /* 路由：只有 /vocab-ops 走这里（防止误挂在 /ai 上把 AI 讲解打挂） */
  if (path && path.indexOf('/vocab-ops') < 0) {
    return json(404, { ok: false, error: 'not_found' });
  }

  var body = pickBody(event);
  if (body === null) return err(400, 'bad_request', { detail: 'body 不是合法 JSON' });

  var action = String(body.action || '');

  if (action === 'ping') {
    return json(200, { ok: true, service: 'vocab-ops', actions: ['ping', 'delete_log', 'clear_class'] });
  }

  if (!SB_URL || !SB_KEY) {
    /* 🔴 没配环境变量就**如实报错**，不要静默走 anon（那等于没加固） */
    return err(500, 'server_not_configured', { detail: '缺少 SB_URL / SB_SERVICE_ROLE 环境变量' });
  }

  if (action !== 'delete_log' && action !== 'clear_class') {
    return err(400, 'unknown_action', { detail: String(action || '(空)') });
  }

  /* 🔴 先校验老师身份，**校验不过一行都不删** */
  return checkTeacher(body.teacher_id, body.teacher_hash).then(function (ok) {
    if (!ok) return err(401, 'unauthorized');
    if (action === 'delete_log') return doDeleteLog(body);
    return doClearClass(body);
  }).catch(function (e) {
    /* 技术细节不外泄；真要排查就让老师看 console */
    try { console.warn('[vocab-ops]', (e && e.message) || e); } catch (e2) { /* ignore */ }
    return err(500, 'server_error');
  });
}

module.exports = {
  handleVocabOps: handleVocabOps,
  /* 导出内部件供本地 mock 验收用（生产不依赖它们） */
  _internal: {
    checkTeacher: checkTeacher,
    sbFetch: sbFetch,
    ALLOWED_CLASS_KEYS: ALLOWED_CLASS_KEYS
  }
};
