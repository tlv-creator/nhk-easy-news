/* =====================================================================
   前端「opsPost 状态机」单测（node）
   ---------------------------------------------------------------------
   为什么要有这个：
     2026-10-24 我把 400 当成「端点在线但请求有问题」⇒ **没有退回 anon 路径**
     ⇒ 老师的「删一条 / 清空全部」直接废了（浏览器 E2E 当场抓到）
     ⇒ 根因：ai-proxy 那个函数对**任何** POST 都回 {"ok":false,"error":"bad_request"}
       ⇒ 「400」根本不能用来判断「我的端点上线了没」
     ⇒ 修法：靠 **service 标记**认人 ⇒ 补这个单测把这个坑钉死。

   跑法：node build/test_ops_state.js
   ===================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const API_JS = path.join(__dirname, '..', 'web', 'js', 'api.js');

let fails = 0, passes = 0;
function ck(name, cond, detail) {
  if (cond) { passes++; console.log('  ✅ ' + name); }
  else { fails++; console.log('  ❌ ' + name + (detail ? ('  ← ' + detail) : '')); }
}

/* ---- 造一个假浏览器环境，把 api.js 塞进去 ---- */
function loadApi(fetchStub) {
  const sandbox = {
    console,
    fetch: fetchStub,
    setTimeout, clearTimeout,
    Promise, JSON, Math, Date, Number, String, Object, Array, RegExp, Error,
    location: { search: '' },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    crypto: { subtle: undefined },   // ⇒ 走不到 sha256 那条路（我们不测登录）
    TextEncoder: undefined
  };
  sandbox.window = sandbox;
  sandbox.global = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(API_JS, 'utf8'), sandbox, { filename: 'api.js' });
  return sandbox.LISTEN;
}

function resp(status, bodyObj) {
  const text = bodyObj === undefined ? '' : JSON.stringify(bodyObj);
  return {
    ok: status >= 200 && status < 300,
    status: status,
    text: () => Promise.resolve(text)
  };
}

(async function main() {
  console.log('== ① ai-proxy 的通用响应（端点没上线）⇒ 必须判成 notdeployed 并退回 anon ==');
  {
    const calls = [];
    const fetchStub = (url, opts) => {
      calls.push({ url: String(url), method: (opts && opts.method) || 'GET' });
      if (String(url).indexOf('service.tcloudbase.com') >= 0) {
        // 🔴 ai-proxy 对任何 POST 都回这个
        return Promise.resolve(resp(400, { ok: false, error: 'bad_request' }));
      }
      // anon 的 DELETE：模拟「删掉了 1 条」
      return Promise.resolve(resp(200, [{ id: 1, class_key: 'LISTEN' }]));
    };
    const LISTEN = loadApi(fetchStub);
    const r = await LISTEN.rpc('listen_teacher_delete',
      { p_token: 'tea:x', p_id: 1, p_tid: '2013', p_thash: 'deadbeef' });
    ck('退回 anon 路径并成功（ok:true）', r && r.ok === true, JSON.stringify(r));
    ck('确实走了 anon DELETE', calls.some(c => c.method === 'DELETE' &&
      c.url.indexOf('vocab_history_logs?id=eq.1') >= 0), JSON.stringify(calls));
  }

  console.log('== ② 端点在线且成功（带 service 标记）⇒ ok，不该再碰 anon ==');
  {
    const calls = [];
    const fetchStub = (url, opts) => {
      calls.push({ url: String(url), method: (opts && opts.method) || 'GET' });
      if (String(url).indexOf('service.tcloudbase.com') >= 0) {
        return Promise.resolve(resp(200, { service: 'vocab-ops', ok: true, deleted: 1 }));
      }
      return Promise.resolve(resp(200, []));
    };
    const LISTEN = loadApi(fetchStub);
    const r = await LISTEN.rpc('listen_teacher_delete',
      { p_token: 'tea:x', p_id: 7, p_tid: '2013', p_thash: 'deadbeef' });
    ck('端点成功 ⇒ ok:true deleted=1', r && r.ok === true && r.deleted === 1, JSON.stringify(r));
    ck('没有再走 anon DELETE', !calls.some(c => c.method === 'DELETE' &&
      c.url.indexOf('lszwxcmvcvbraqtrtgon') >= 0), JSON.stringify(calls));
  }

  console.log('== ③ 端点说「未授权」（401）⇒ **绝不退回 anon**（退回＝绕过老师校验）==');
  {
    const calls = [];
    const fetchStub = (url, opts) => {
      calls.push({ url: String(url), method: (opts && opts.method) || 'GET' });
      if (String(url).indexOf('service.tcloudbase.com') >= 0) {
        return Promise.resolve(resp(401, { service: 'vocab-ops', ok: false, error: 'unauthorized' }));
      }
      return Promise.resolve(resp(200, [{ id: 1 }]));
    };
    const LISTEN = loadApi(fetchStub);
    const r = await LISTEN.rpc('listen_teacher_delete',
      { p_token: 'tea:x', p_id: 1, p_tid: '2013', p_thash: 'wrong' });
    ck('401 ⇒ 不报成功', r && r.ok !== true, JSON.stringify(r));
    ck('401 ⇒ 没有走 anon DELETE', !calls.some(c => c.method === 'DELETE' &&
      c.url.indexOf('lszwxcmvcvbraqtrtgon') >= 0), JSON.stringify(calls));
  }

  console.log('== ④ 端点在线但请求有问题（400 + service 标记）⇒ 如实报错，不退回 ==');
  {
    const calls = [];
    const fetchStub = (url, opts) => {
      calls.push({ url: String(url), method: (opts && opts.method) || 'GET' });
      if (String(url).indexOf('service.tcloudbase.com') >= 0) {
        return Promise.resolve(resp(400, { service: 'vocab-ops', ok: false, error: 'bad_class_key' }));
      }
      return Promise.resolve(resp(200, [{ id: 1 }]));
    };
    const LISTEN = loadApi(fetchStub);
    const r = await LISTEN.rpc('listen_teacher_clear',
      { p_token: 'tea:x', p_tid: '2013', p_thash: 'deadbeef' });
    ck('400 ⇒ 不报成功', r && r.ok !== true, JSON.stringify(r));
    ck('400 ⇒ 没有走 anon 整班删', !calls.some(c => c.method === 'DELETE' &&
      c.url.indexOf('lszwxcmvcvbraqtrtgon') >= 0), JSON.stringify(calls));
  }

  console.log('== ⑤ 网络完全不通（端点没上线/函数挂了）⇒ 退回 anon ==');
  {
    const calls = [];
    const fetchStub = (url, opts) => {
      calls.push({ url: String(url), method: (opts && opts.method) || 'GET' });
      if (String(url).indexOf('service.tcloudbase.com') >= 0) {
        return Promise.reject(new Error('network down'));
      }
      return Promise.resolve(resp(200, [{ id: 1 }]));
    };
    const LISTEN = loadApi(fetchStub);
    const r = await LISTEN.rpc('listen_teacher_clear',
      { p_token: 'tea:x', p_tid: '2013', p_thash: 'deadbeef' });
    ck('网络不通 ⇒ 退回 anon 并成功', r && r.ok === true, JSON.stringify(r));
    ck('确实走了 anon 整班删', calls.some(c => c.method === 'DELETE' &&
      c.url.indexOf('class_key=eq.LISTEN') >= 0), JSON.stringify(calls));
  }

  console.log('== ⑥ 没有老师摘要 ⇒ 不发任何请求（不许匿名瞎试）==');
  {
    let called = 0;
    const fetchStub = () => { called++; return Promise.resolve(resp(200, [])); };
    const LISTEN = loadApi(fetchStub);
    const r = await LISTEN.rpc('listen_teacher_clear', { p_token: 'tea:x' });
    ck('没摘要 ⇒ 提示重新登录', r && r.ok !== true && /重新登录/.test(r.msg || ''), JSON.stringify(r));
    ck('没摘要 ⇒ 一个请求都没发', called === 0, '发了 ' + called + ' 个');
  }

  console.log('\n================ 结果 ================');
  console.log('通过 ' + passes + ' 条，失败 ' + fails + ' 条');
  if (fails) process.exit(1);
  console.log('🟢 全绿');
  process.exit(0);
})().catch((e) => { console.error('💥', e); process.exit(2); });
