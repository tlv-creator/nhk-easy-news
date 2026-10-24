/* =====================================================================
   /vocab-ops 端点的本地验收（对着 mock Supabase 真跑）
   ---------------------------------------------------------------------
   判据全部落在**事实量**上：
     · 「删了几行」数的是 mock 库里真少了的行，不是看 HTTP 码
     · 「没动别的班」比的是**整表逐行内容**
     · 任何一条失败 ⇒ exit 1
   ===================================================================== */
'use strict';

const mock = require('./mock_supabase');

const PORT = 18731;
const BASE = 'http://127.0.0.1:' + PORT;

/* 真库里的班（用来验证「清空只清目标班」） */
const SEED = [
  { class_key: 'LISTEN', class_name: '26互联网金融1班', student_id: '26270133', timestamp: '2026/10/24 12:00:00', duration_sec: 600, action: '听力 A' },
  { class_key: 'LISTEN', class_name: '26互联网金融1班', student_id: '26270134', timestamp: '2026/10/24 13:00:00', duration_sec: 300, action: '听力 B' },
  { class_key: 'LISTEN', class_name: '26互联网金融2班', student_id: '26270135', timestamp: '2026/10/24 14:00:00', duration_sec: 200, action: '听力 C' },
  { class_key: 'JR1', class_name: 'JR1班', student_id: '10001', timestamp: '2026/10/24 10:00:00', duration_sec: 100, action: '词汇' },
  { class_key: 'CW3', class_name: 'CW3班', student_id: '10002', timestamp: '2026/10/24 11:00:00', duration_sec: 100, action: '词汇' },
  { class_key: 'CET4', class_name: '四级', student_id: '10003', timestamp: '2026/10/24 12:00:00', duration_sec: 100, action: '日报' }
];

let FAILS = [];
let PASSES = 0;

/* 🔴🔴 判据 8/9 会**删掉** globalThis.URL 和 globalThis.fetch（模拟 CloudBase 的 Node 12/16 沙箱）
   ⇒ 连「留一份 fetch 引用」都不行：undici 的 fetch 内部要用全局 URL ⇒ 一删就炸
   ⇒ 所以测试自己调 mock 探针接口也**不许用 fetch** ⇒ 用裸 http.request（纯字符串拼 URL） */
const http = require('http');

function httpJSON(path, method, bodyObj) {
  return new Promise((resolve, reject) => {
    const payload = bodyObj === undefined ? null : JSON.stringify(bodyObj);
    const req = http.request({
      host: '127.0.0.1', port: PORT, path: path, method: method || 'GET',
      headers: payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}
    }, (res) => {
      let b = '';
      res.setEncoding('utf8');
      res.on('data', (d) => { b += d; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(b); } catch (e) { j = b; }
        resolve(j);
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function ck(name, cond, detail) {
  if (cond) { PASSES++; console.log('  ✅ ' + name); }
  else { FAILS.push(name); console.log('  ❌ ' + name + (detail ? ('  ← ' + detail) : '')); }
}

async function call(payload) {
  const mod = require('./vocab_ops');
  const ev = {
    path: '/vocab-ops',
    httpMethod: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload)
  };
  const r = await mod.handleVocabOps(ev);
  let body = {};
  try { body = JSON.parse(r.body); } catch (e) { body = { _raw: r.body }; }
  return { status: r.statusCode, body: body };
}

async function rows() {
  const j = await httpJSON('/__probe__/count', 'GET');
  return (j && j.rows) || [];
}

function fp(rs) {
  return rs.map((r) => [r.id, r.class_key, r.student_id, r.action].join('|')).sort().join('\n');
}

(async function main() {
  const server = await mock.start(PORT);

  process.env.SB_URL = BASE;
  process.env.SB_SERVICE_ROLE = 'test-service-role-key';

  const mod = require('./vocab_ops');
  const GOOD_ID = mock.TEACHER_ID;
  const GOOD_HASH = mock.TEACHER_HASH;
  const BAD_HASH = mock.sha256(mock.SALT + '|' + mock.TEACHER_ID + '|wrong-pass');

  /* ---------- 0) 环境没配 ⇒ 必须如实报错，不能静默走弱路径 ---------- */
  console.log('\n【0】没配环境变量');
  {
    const savedUrl = process.env.SB_URL, savedKey = process.env.SB_SERVICE_ROLE;
    delete process.env.SB_URL; delete process.env.SB_SERVICE_ROLE;
    delete require.cache[require.resolve('./vocab_ops')];
    const m2 = require('./vocab_ops');
    const r = await m2.handleVocabOps({ path: '/vocab-ops', httpMethod: 'POST', body: JSON.stringify({ action: 'clear_class', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, class_key: 'LISTEN' }) });
    const b = JSON.parse(r.body);
    ck('没配环境变量 → 500 server_not_configured', r.statusCode === 500 && b.error === 'server_not_configured', 'status=' + r.statusCode + ' err=' + b.error);
    process.env.SB_URL = savedUrl; process.env.SB_SERVICE_ROLE = savedKey;
    delete require.cache[require.resolve('./vocab_ops')];
  }

  /* ---------- 1) ping 不碰数据 ---------- */
  console.log('\n【1】ping');
  {
    await httpJSON('/__probe__/reset', 'POST', SEED);
    const before = fp(await rows());
    const r = await call({ action: 'ping' });
    const after = fp(await rows());
    ck('ping → 200', r.status === 200 && r.body.ok === true, JSON.stringify(r));
    ck('ping 一行都没动', before === after);
  }

  /* ---------- 2) 未授权 ⇒ 401 且一行都没删（这是最重要的一条） ---------- */
  console.log('\n【2】未授权');
  {
    await httpJSON('/__probe__/reset', 'POST', SEED);
    const before = fp(await rows());

    const r1 = await call({ action: 'clear_class', teacher_id: GOOD_ID, teacher_hash: BAD_HASH, class_key: 'LISTEN' });
    ck('错摘要 → 401 unauthorized', r1.status === 401 && r1.body.error === 'unauthorized', JSON.stringify(r1));
    ck('错摘要 → 一行都没删', fp(await rows()) === before);

    const r2 = await call({ action: 'delete_log', teacher_id: GOOD_ID, teacher_hash: BAD_HASH, id: 9000, class_key: 'LISTEN' });
    ck('错摘要删一条 → 401', r2.status === 401, JSON.stringify(r2));
    ck('错摘要删一条 → 一行都没删', fp(await rows()) === before);

    const r3 = await call({ action: 'clear_class', teacher_id: '9999', teacher_hash: GOOD_HASH, class_key: 'LISTEN' });
    ck('工号不对 → 401', r3.status === 401, JSON.stringify(r3));
    ck('工号不对 → 一行都没删', fp(await rows()) === before);

    const r4 = await call({ action: 'clear_class', teacher_id: GOOD_ID, class_key: 'LISTEN' });
    ck('没给摘要 → 401', r4.status === 401, JSON.stringify(r4));
    ck('没给摘要 → 一行都没删', fp(await rows()) === before);
  }

  /* ---------- 3) 删一条：只删那一条，别的行不动 ---------- */
  console.log('\n【3】删一条（已授权）');
  {
    await httpJSON('/__probe__/reset', 'POST', SEED);
    const before = await rows();
    const target = before[1];

    const r = await call({ action: 'delete_log', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, id: target.id, class_key: 'LISTEN' });
    ck('删一条 → 200', r.status === 200 && r.body.ok === true, JSON.stringify(r));
    ck('删一条 → deleted 数 = 真删掉的那一条', r.body.deleted === 1, 'deleted=' + r.body.deleted);

    const after = await rows();
    ck('库里真少了那一行', !after.some((x) => x.id === target.id));
    ck('其余 5 行一行没动', after.length === before.length - 1 && fp(after) === fp(before.filter((x) => x.id !== target.id)));

    /* id 不存在 ⇒ 如实报 0，不能谎报成功 */
    const r2 = await call({ action: 'delete_log', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, id: 999999, class_key: 'LISTEN' });
    ck('删不存在的 id → deleted=0（如实，不谎报）', r2.status === 200 && r2.body.deleted === 0, JSON.stringify(r2));
  }

  /* ---------- 4) 清空全部：只清 LISTEN，别的班一行不动 ---------- */
  console.log('\n【4】清空全部（已授权）');
  {
    await httpJSON('/__probe__/reset', 'POST', SEED);
    const before = await rows();
    const listenBefore = before.filter((x) => x.class_key === 'LISTEN').length;
    const othersBefore = before.filter((x) => x.class_key !== 'LISTEN');

    const r = await call({ action: 'clear_class', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, class_key: 'LISTEN' });
    ck('清空 → 200', r.status === 200 && r.body.ok === true, JSON.stringify(r));
    ck('清空 → deleted 数 = LISTEN 的真实行数', r.body.deleted === listenBefore, 'deleted=' + r.body.deleted + ' 期望=' + listenBefore);

    const after = await rows();
    ck('LISTEN 一条不剩', !after.some((x) => x.class_key === 'LISTEN'));
    ck('别的班一行没动（JR1/CW3/CET4）', fp(after) === fp(othersBefore),
      'before=' + othersBefore.length + ' after=' + after.length);
  }

  /* ---------- 5) class_key 白名单：动不了别的班 ---------- */
  console.log('\n【5】class_key 白名单');
  {
    await httpJSON('/__probe__/reset', 'POST', SEED);
    const before = fp(await rows());

    const r1 = await call({ action: 'clear_class', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, class_key: 'JR1' });
    ck('想清 JR1 → 400 bad_class_key', r1.status === 400 && r1.body.error === 'bad_class_key', JSON.stringify(r1));
    ck('想清 JR1 → 一行都没删', fp(await rows()) === before);

    const r2 = await call({ action: 'delete_log', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, id: 9003, class_key: 'CET4' });
    ck('想删 CET4 的行 → 400 bad_class_key', r2.status === 400 && r2.body.error === 'bad_class_key', JSON.stringify(r2));
    ck('想删 CET4 的行 → 一行都没删', fp(await rows()) === before);

    const r3 = await call({ action: 'nuke_everything', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH });
    ck('未知 action → 400', r3.status === 400, JSON.stringify(r3));
  }

  /* ---------- 6) 路由：挂在 /ai 上不能误触发 ---------- */
  console.log('\n【6】路由保护');
  {
    await httpJSON('/__probe__/reset', 'POST', SEED);
    const before = fp(await rows());
    const ev = { path: '/ai', httpMethod: 'POST', body: JSON.stringify({ action: 'clear_class', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, class_key: 'LISTEN' }) };
    const r = await mod.handleVocabOps(ev);
    ck('路径不是 /vocab-ops → 404，不执行', r.statusCode === 404, 'status=' + r.statusCode);
    ck('路径不是 /vocab-ops → 一行都没删', fp(await rows()) === before);
  }

  /* ---------- 7) OPTIONS 预检 ---------- */
  console.log('\n【7】CORS 预检');
  {
    const r = await mod.handleVocabOps({ path: '/vocab-ops', httpMethod: 'OPTIONS', body: '' });
    ck('OPTIONS → 200', r.statusCode === 200, 'status=' + r.statusCode);
    ck('OPTIONS 带 CORS 头', !!(r.headers && r.headers['Access-Control-Allow-Origin']));
    const r2 = await mod.handleVocabOps({ path: '/vocab-ops', httpMethod: 'GET', body: '' });
    ck('GET → 405 method_not_allowed', r2.statusCode === 405, 'status=' + r2.statusCode);
  }

  console.log('\n【8】云函数沙箱没有全局 URL ⇒ 端点必须照跑');
  {
    // 🔴 判据用**行为**不用文本扫描（文本扫描会把自己的注释判成违规）
    delete globalThis.URL;
    ck('globalThis.URL 真的被删掉了（判据本身有效）', typeof URL === 'undefined', typeof URL);
    const r = await mod.handleVocabOps({ path: '/vocab-ops', httpMethod: 'POST', body: JSON.stringify({ action: 'ping' }) });
    const b = JSON.parse(r.body);
    ck('没有全局 URL 也能跑（ping → 200）', r.statusCode === 200 && b.ok === true, r.statusCode + ' ' + r.body);
  }

  console.log('\n【9】🔴🔴 云函数沙箱**也没有全局 fetch** ⇒ 端点必须照跑（Node 12/16）');
  {
    // 🔴 这是真事故：原来那版 sbFetch 用全局 fetch()
    //    ⇒ CloudBase 沙箱里一调就 500（部署"成功"但端点全挂）
    await httpJSON('/__probe__/reset', 'POST', SEED);
    delete globalThis.fetch;
    ck('globalThis.fetch 真的被删掉了（判据本身有效）', typeof fetch === 'undefined', typeof fetch);
    const r = await mod.handleVocabOps({ path: '/vocab-ops', httpMethod: 'POST', body: JSON.stringify({ action: 'ping' }) });
    const b = JSON.parse(r.body);
    ck('没有全局 fetch 也能跑（ping → 200）', r.statusCode === 200 && b.ok === true, r.statusCode + ' ' + r.body);
    // 真删一次（要真的发 HTTP 请求 ⇒ 证明 https.request 那条路是通的）
    // ⚠ id 要**现取**：mock 的 NEXT_ID 是自增的 ⇒ reset 之后的 id 不是 9000
    const live = await rows();
    const target = live.find((x) => x.class_key === 'LISTEN');
    ck('mock 里有可删的 LISTEN 行（判据前提）', !!target, JSON.stringify(live.map((x) => x.id)));
    const r2 = await mod.handleVocabOps({
      path: '/vocab-ops', httpMethod: 'POST',
      body: JSON.stringify({ action: 'delete_log', teacher_id: GOOD_ID, teacher_hash: GOOD_HASH, id: target.id, class_key: 'LISTEN' })
    });
    const b2 = JSON.parse(r2.body);
    ck('没有全局 fetch 也能真删（deleted 计数对）', r2.statusCode === 200 && b2.ok === true && b2.deleted === 1,
       r2.statusCode + ' ' + r2.body);
  }

  server.close();

  console.log('\n================ 结果 ================');
  console.log('通过 ' + PASSES + ' 条，失败 ' + FAILS.length + ' 条');
  if (FAILS.length) { FAILS.forEach((f) => console.log('  ❌ ' + f)); process.exit(1); }
  console.log('🟢 全绿');
  process.exit(0);
})().catch((e) => { console.error('💥', e); process.exit(2); });
