/* =====================================================================
   本地 mock 的 Supabase：只实现这个端点真正用到的两件事
     ① POST /rest/v1/rpc/check_teacher_login  → 校验老师口令摘要
     ② DELETE /rest/v1/vocab_history_logs?... → 按过滤条件删行，
        并且**返回真被删掉的行**（模拟 Prefer: return=representation）
   另加一个「探针」接口，用来数「到底删了几行」，避免验收脚本自己骗自己。
   ===================================================================== */
'use strict';

const http = require('http');
const crypto = require('crypto');

/* 真表（内存）。每行：{ id, class_key, class_name, student_id, timestamp, duration_sec, action } */
let ROWS = [];
let NEXT_ID = 9000;

/* 老师凭据（模拟真库：存在一张 anon 读不到的表里） */
const TEACHER_ID = '2013';
const TEACHER_PASS = '781108';
const SALT = 'vocab-unit23-teacher-2026';
const TEACHER_HASH = crypto.createHash('sha256')
  .update(SALT + '|' + TEACHER_ID + '|' + TEACHER_PASS).digest('hex');

function sha256(s) { return crypto.createHash('sha256').update(s).digest('hex'); }

function seed(list) {
  ROWS = list.map((r, i) => Object.assign({ id: NEXT_ID + i }, r));
  NEXT_ID += list.length;
}

function applyFilters(qs) {
  const out = [];
  for (const row of ROWS) {
    let keep = true;
    for (const [k, v] of Object.entries(qs)) {
      if (k === 'id' || k === 'class_key' || k === 'student_id') {
        const want = String(v).replace(/^eq\./, '');
        if (String(row[k]) !== want) { keep = false; break; }
      }
    }
    if (keep) out.push(row);
  }
  return out;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let b = '';
    req.on('data', (c) => { b += c; });
    req.on('end', () => resolve(b));
    req.on('error', reject);
  });
}

function start(port) {
  const server = http.createServer(async (req, res) => {
    /* 🔴 不用 new URL()：mock 也要能在「全局 URL 被删掉」的沙箱模拟下工作
       （判据 8 会真删 globalThis.URL ⇒ mock 自己用了 URL 就会先挂 ⇒ 假红） */
    const _m = /^([^?#]*)(?:\?([^#]*))?/.exec(req.url || '/');
    const path = _m ? _m[1] : '/';
    const qsRaw = (_m && _m[2]) ? _m[2] : '';
    const qs = {};
    for (const kv of qsRaw.split('&')) {
      if (!kv) continue;
      const i = kv.indexOf('=');
      const k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i));
      const v = decodeURIComponent(i < 0 ? '' : kv.slice(i + 1));
      qs[k] = v;
    }
    const body = await readBody(req);
    const send = (code, obj) => {
      res.writeHead(code, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(obj));
    };

    /* 探针接口：验收脚本用它数「库里还剩几行」「一共删了几行」 */
    if (path === '/__probe__/count') {
      return send(200, { total: ROWS.length, rows: ROWS });
    }
    if (path === '/__probe__/reset') {
      seed(JSON.parse(body || '[]'));
      return send(200, { ok: true, total: ROWS.length });
    }

    /* ① 老师口令校验 */
    if (path === '/rest/v1/rpc/check_teacher_login' && req.method === 'POST') {
      let p = {};
      try { p = JSON.parse(body || '{}'); } catch (e) { return send(400, { code: '22P02' }); }
      const h = String(p.p_hash || '');
      const ok = String(p.p_id || '') === TEACHER_ID && h === TEACHER_HASH;
      return send(200, ok);
    }

    /* ② 删除（模拟 return=representation：返回真被删掉的行） */
    if (path === '/rest/v1/vocab_history_logs' && req.method === 'DELETE') {
      const victims = applyFilters(qs);
      const ids = new Set(victims.map((r) => r.id));
      ROWS = ROWS.filter((r) => !ids.has(r.id));
      return send(200, victims);
    }

    /* 其它一律 404（模拟「这个 RPC 不存在」） */
    return send(404, { code: 'PGRST202', message: 'Could not find the function' });
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)));
}

module.exports = { start, seed, TEACHER_ID, TEACHER_PASS, SALT, TEACHER_HASH, sha256 };
