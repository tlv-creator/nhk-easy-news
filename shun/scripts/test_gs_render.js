#!/usr/bin/env node
/*!
 * test_gs_render.js —— 「句子结构详解」渲染单测（不依赖浏览器）
 *
 * 目的：gsHtml() 是纯函数 ⇒ 可以直接抠出来跑，
 *      这样改渲染时能秒级验证，不必等 Playwright 起浏览器。
 *
 * 🔴 判据落在**用户真正看到的文字/结构**上：
 *    · 大框架的每一块都要渲染出来，且带对应角色标签
 *    · 逐块拆解的每一块都要有「块内骨架 + 成分说明 + 整块意思」
 *    · 没有 gs 时整节不渲染（不留空标题）
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.join(__dirname, '..', 'web');
const appSrc = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');

/* ── 抠出 gsHtml 及其依赖（esc / gsBrkClass / gsFrameHtml / gsBlocksHtml）── */
function grab(name) {
  const start = appSrc.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('找不到函数 ' + name);
  // 从 start 往后找匹配的右花括号
  let i = appSrc.indexOf('{', start), depth = 0, end = -1;
  for (let j = i; j < appSrc.length; j++) {
    if (appSrc[j] === '{') depth++;
    else if (appSrc[j] === '}') { depth--; if (depth === 0) { end = j + 1; break; } }
  }
  if (end < 0) throw new Error('函数 ' + name + ' 括号不配对');
  return appSrc.slice(start, end);
}

const escSrc = `
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
`;
const src = escSrc + grab('gsBrkClass') + grab('gsFrameHtml') + grab('gsBlocksHtml') + grab('gsHtml') +
  '\nmodule.exports={gsHtml,gsBrkClass,gsFrameHtml,gsBlocksHtml};';

const sandbox = { module: { exports: {} }, console };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { gsHtml } = sandbox.module.exports;

/* ── 断言 ── */
let pass = 0, fail = 0;
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (detail !== undefined ? '  ← ' + detail : '')); }
}
function count(h, cls) {
  const re = new RegExp('class="' + cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"', 'g');
  return (h.match(re) || []).length;
}

/* 真实样例（取自生成器产出的形态） */
const GS = {
  frame: [
    { s: 'でも', b: '[]', role: '转折连接' },
    { s: '毎日同じように', b: '［］', role: '修饰谓语从句' },
    { s: 'ワクワクするのは', b: '〈〉', role: '主句内容' },
    { s: '難しいですよね', b: '（）', role: '主句谓语' }
  ],
  skel: '［毎日同じように］（難しい）',
  skelMean: '每天都一样地…是难的',
  chain: ['［毎日同じように］', '〈ワクワクするのは〉'],
  blocks: [
    { s: 'でも', inner: '', parts: [{ s: 'でも', note: '表示转折的连接词' }], mean: '但是' },
    { s: '毎日同じように', inner: '【毎日】 [同じように]',
      parts: [{ s: '毎日', note: '时间名词' }, { s: '同じように', note: '副词短语' }], mean: '每天都一样地' },
    { s: 'ワクワクするのは', inner: '【ワクワクする】',
      parts: [{ s: 'ワクワクする', note: '动词' }, { s: 'のは', note: '形式名词化' }], mean: '感到兴奋的事' },
    { s: '難しいですよね', inner: '【難しい】',
      parts: [{ s: '難しい', note: '形容词' }, { s: 'ですね', note: '句末语气' }], mean: '很难' }
  ],
  whole: '但是每天都一样地感到兴奋是很难的吧。'
};

console.log('=== 渲染结构 ===');
const h = gsHtml(GS);
ok('渲染出内容', h.length > 0);
ok('有「一、大框架」', h.indexOf('一、大框架') >= 0);
ok('有「二、逐块拆解」', h.indexOf('二、逐块拆解') >= 0);
ok('有「主句骨架」', h.indexOf('主句骨架') >= 0);
ok('有「层层修饰主句」', h.indexOf('层层修饰主句') >= 0);
ok('有「整句意思」', h.indexOf('整句意思') >= 0);

console.log('\n=== 大框架：块与角色标签一一对应 ===');
ok('4 个框架块', count(h, 'gs-bk gs-bk') === 0 && (h.match(/class="gs-bk gs-b/g) || []).length === 4,
  (h.match(/class="gs-bk gs-b/g) || []).length);
ok('4 个角色标签', (h.match(/class="gs-rl gs-r/g) || []).length === 4,
  (h.match(/class="gs-rl gs-r/g) || []).length);
['转折连接', '修饰谓语从句', '主句内容', '主句谓语'].forEach(r => {
  ok('角色标签有「' + r + '」', h.indexOf(r) >= 0);
});
ok('四种括号都有对应 class', ['gs-bk0', 'gs-bk1', 'gs-bk2', 'gs-bk3'].every(c => h.indexOf(c) >= 0));

console.log('\n=== 逐块拆解 ===');
ok('4 个块卡片', (h.match(/class="gs-bkcard"/g) || []).length === 4,
  (h.match(/class="gs-bkcard"/g) || []).length);
ok('块序号 第一…第四', h.indexOf('第一块') >= 0 && h.indexOf('第四块') >= 0);
ok('有块内骨架', (h.match(/class="gs-inner"/g) || []).length === 3,
  (h.match(/class="gs-inner"/g) || []).length);
ok('有成分说明 7 条', (h.match(/class="gs-ps"/g) || []).length === 7,
  (h.match(/class="gs-ps"/g) || []).length);
ok('有整块意思 4 条', (h.match(/class="gs-mean"/g) || []).length === 4,
  (h.match(/class="gs-mean"/g) || []).length);
ok('修饰链 2 项', (h.match(/class="gs-ch"/g) || []).length === 2,
  (h.match(/class="gs-ch"/g) || []).length);
ok('链里有箭头', h.indexOf('gs-ar') >= 0);

console.log('\n=== 缺数据时不渲染 ===');
ok('gs 为空 ⇒ 空串', gsHtml(null) === '');
ok('gs 无 frame ⇒ 空串', gsHtml({ blocks: [] }) === '');
ok('gs 无 blocks ⇒ 仍渲染框架', gsHtml({ frame: GS.frame }).indexOf('一、大框架') >= 0);

console.log('\n=== 转义：HTML 注入必须被挡住 ===');
const evil = JSON.parse(JSON.stringify(GS));
evil.frame[0].s = '<img src=x onerror=alert(1)>';
evil.blocks[0].s = '<script>bad()</script>';
const he = gsHtml(evil);
ok('frame 里的 <img> 被转义', he.indexOf('<img src=x') < 0);
ok('blocks 里的 <script> 被转义', he.indexOf('<script>bad()') < 0);
ok('转义后仍能看到文字', he.indexOf('&lt;img') >= 0 || he.indexOf('&lt;script') >= 0);

console.log('\n' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
