/* =====================================================================
   算 index.js 里 SYSTEM_PROMPT 的**运行时** sha256
   （读字面量不行：它是模板字符串 ⇒ 必须把整个 index.js 在沙箱里跑一遍取运行时值）
   用法：node prompt_sha.js <index.js 路径>
   输出：<长度> <sha256>
   ===================================================================== */
'use strict';
const fs = require('fs');
const crypto = require('crypto');

const p = process.argv[2];
if (!p) { console.error('用法: node prompt_sha.js <index.js>'); process.exit(2); }
const src = fs.readFileSync(p, 'utf8');

let prompt = null;
try {
  /* new Function 的作用域**拿不到 require** ⇒ 必须显式传进去（协作方的教训：require 传 stub） */
  const stubTarget = function () { return stubTarget; };
  const stub = new Proxy({}, { get: function (t, k) {
    if (k === 'then') return undefined;              // 别被当成 thenable
    return function () { return stub; };
  }});
  stubTarget.createServer = function () { return stub; };
  const req = function (name) {
    if (name === 'http' || name === 'https') return stub;
    if (name === 'crypto') return { createHash: () => ({ update: () => ({ digest: () => '' }) }) };
    return {};
  };
  const fn = new Function('require', 'module', 'exports', '__dirname', '__filename',
    src + '\n; return (typeof SYSTEM_PROMPT !== "undefined") ? SYSTEM_PROMPT : null;');
  prompt = fn(req, { exports: {} }, {}, process.cwd(), process.cwd() + '/index.js');
} catch (e) {
  console.error('执行失败：' + (e && e.message));
  process.exit(3);
}
if (prompt === null || prompt === undefined) {
  console.error('没找到 SYSTEM_PROMPT');
  process.exit(3);
}
console.log(prompt.length + ' ' + crypto.createHash('sha256').update(prompt, 'utf8').digest('hex'));
