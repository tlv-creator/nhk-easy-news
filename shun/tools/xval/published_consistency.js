// 已发布 13 集的 ru 是不是一个「单一参考」？
// 测两件事：
//   ① 表里的 187 个词，在已发布数据里**是否总是**整块？（还是有的地方整块、有的地方逐字）
//   ② 已发布数据里，有多少个「同一词两种读音」
const fs = require('fs');
const path = require('path');
const HERE = __dirname;
const _raw = JSON.parse(fs.readFileSync(path.join(HERE, 'word_reading_rebuilt.json'), 'utf8'));
const WR = Array.isArray(_raw) ? Object.fromEntries(_raw) : (_raw.WR || _raw.table || _raw);
const SRC = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html';
const html = fs.readFileSync(SRC, 'utf8');
const line = html.split('\n').find(l => l.startsWith('<script>window.SHUN='));
const D = JSON.parse(line.slice('<script>window.SHUN='.length, line.lastIndexOf(';</script>')));

// 每句的「已发布 ruby 单元表层序列」
const seqs = [];
for (const e of D.eps) for (const s of e.sents) seqs.push((s.ru || []).map(x => x[0]));

// ① 对每个表词 W：统计「作为单个单元出现」与「被拆成多个单元出现」
const wholeCnt = new Map(), splitCnt = new Map();
const splitExamples = [];
for (const units of seqs) {
  for (let i = 0; i < units.length; i++) {
    const u = units[i];
    if (WR[u]) {
      wholeCnt.set(u, (wholeCnt.get(u) || 0) + 1);
      continue;
    }
    // 是否是某个表词的「逐字拆开」？尝试 2~4 字窗口
    for (let L = 2; L <= 4; L++) {
      const w = units.slice(i, i + L).join('');
      if (WR[w] && w.length === L) {
        splitCnt.set(w, (splitCnt.get(w) || 0) + 1);
        if (splitExamples.length < 12) splitExamples.push([w, units.slice(i, i + L)]);
        break;
      }
    }
  }
}
const both = Object.keys(WR).filter(w => wholeCnt.get(w) && splitCnt.get(w));
console.log('=== ① 表里的 187 个词，在已发布数据里的表现 ===');
console.log('  只整块出现        ', Object.keys(WR).filter(w => wholeCnt.get(w) && !splitCnt.get(w)).length);
console.log('  只逐字出现        ', Object.keys(WR).filter(w => splitCnt.get(w) && !wholeCnt.get(w)).length);
console.log('  🔴 既整块又逐字   ', both.length);
console.log('  例:', both.slice(0, 18).join(' '));
console.log('  样例明细:');
for (const [w, ex] of splitExamples.slice(0, 8)) {
  console.log(`    ${w}  被拆成 ${JSON.stringify(ex)}  （整块读音 ${WR[w]}）`);
}

// ② 同一「词形」两种读音
const readCnt = new Map();
for (const e of D.eps) for (const s of e.sents) {
  for (const [surf, rd] of (s.ru || [])) {
    if (!surf || !/[一-鿿]/.test(surf)) continue;
    const k = surf;
    if (!readCnt.has(k)) readCnt.set(k, new Map());
    const m = readCnt.get(k);
    m.set(rd, (m.get(rd) || 0) + 1);
  }
}
const multi = [...readCnt.entries()].filter(([k, m]) => m.size > 1);
console.log('\n=== ② 同一词形出现多种读音 ===');
console.log('  词形总数', readCnt.size, '｜ 有多种读音的', multi.length);
for (const [k, m] of multi.slice(0, 14)) {
  console.log(`    ${k}: ${[...m.entries()].map(([r, n]) => r + '×' + n).join(' / ')}`);
}
