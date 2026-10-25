// 从「已发布 13 集的 ru」精确反推整词表 + 验证「整词优先」规则
// 判据（不靠 token 对齐，直接扫已发布 ruby 单元）：
//   凡是一个 ruby 单元的表层 **含汉字且长度 ≥2** ⇒ 这个词是「整词」（命中过词表）
//   凡是一个 ruby 单元是**单个汉字** ⇒ 那个字是逐字切的
//   ⇒ 表 = 所有「整词单元」的 表层→读音
// 然后用「表命中⇒整块；未命中⇒逐字 DP」去复现已发布 ru，看能不能 100% 还原。
const kuromoji = require('kuromoji');
const fs = require('fs');
const path = require('path');
const { rubyForTokens } = require('./ruby_gen.js');

const HERE = __dirname;
const dicPath = path.join(HERE, 'node_modules/kuromoji/dict');
const SRC = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html';
const html = fs.readFileSync(SRC, 'utf8');
const line = html.split('\n').find(l => l.startsWith('<script>window.SHUN='));
const D = JSON.parse(line.slice('<script>window.SHUN='.length, line.lastIndexOf(';</script>')));

const isKanji = c => /[一-鿿㐀-䶿]/.test(c);
const kata2hira = s => s.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));

// ① 扫已发布 ru，收集整词单元
const counts = new Map();   // 表层 -> {reading, n}
for (const e of D.eps) for (const s of e.sents) {
  for (const [surf, rd] of (s.ru || [])) {
    if (!surf || surf.length < 2) continue;
    if (!/[一-鿿㐀-䶿]/.test(surf)) continue;
    const k = counts.get(surf) || { readings: new Map(), n: 0 };
    k.readings.set(rd, (k.readings.get(rd) || 0) + 1);
    k.n++;
    counts.set(surf, k);
  }
}
const table = {};
const conflicts = [];
for (const [k, v] of counts) {
  if (v.readings.size > 1) { conflicts.push([k, [...v.readings.entries()]]); }
  const best = [...v.readings.entries()].sort((a, b) => b[1] - a[1])[0];
  table[k] = best[0];
}
console.log('=== 反推整词表 ===');
console.log('  整词单元种类', counts.size, '｜ 同一词有多个读音的', conflicts.length);
for (const c of conflicts.slice(0, 8)) console.log('    ⚠️', c[0], JSON.stringify(c[1]));
for (const w of ['会社', '日本語', '教師', '初心者', '時間', '天気', '今日', '日本', '会話', '生活', '旅行'])
  console.log(`    ${w}: ${w in table ? '表内 → ' + table[w] : '不在表 ⇒ 逐字'}`);

// ② 规则：表命中⇒整块；未命中⇒逐字 DP
function splitTok(s) {
  let i = 0; while (i < s.length && !isKanji(s[i])) i++;
  let j = s.length; while (j > i && !isKanji(s[j - 1])) j--;
  return [s.slice(0, i), s.slice(i, j), s.slice(j)];
}
function rubyRule(tok) {
  const s = tok.surface_form;
  if (!/[一-鿿㐀-䶿]/.test(s)) return [[s, '']];
  const [pre, kanji, post] = splitTok(s);
  const out = [];
  if (pre) out.push([pre, '']);
  if (table[kanji]) {
    out.push([kanji, kata2hira(table[kanji])]);
    if (post) out.push([post, '']);      // 表词分支：送り仮名要自己补
    return out;
  }
  // 非表词：逐字 DP（rubyForTokens 自己已经处理了前导/尾部假名）
  return rubyForTokens([tok]);
}

kuromoji.builder({ dicPath }).build((err, tok) => {
  if (err) throw err;
  let exact = 0, sent = 0; const diffs = [];
  for (const e of D.eps) for (const s of e.sents) {
    const mine = tok.tokenize(s.t).flatMap(rubyRule);
    sent++;
    if (JSON.stringify(mine) === JSON.stringify(s.ru || [])) exact++;
    else if (diffs.length < 12) diffs.push({ t: s.t, mine, theirs: s.ru });
  }
  console.log('\n=== 「表命中⇒整块；未命中⇒逐字 DP」复现已发布 ru ===');
  console.log(`  逐字一致 ${exact}/${sent} = ${(exact / sent * 100).toFixed(1)}%`);
  for (const d of diffs) {
    console.log('  ', d.t);
    console.log('     mine  ', JSON.stringify(d.mine));
    console.log('     theirs', JSON.stringify(d.theirs));
  }
  fs.writeFileSync(path.join(HERE, 'word_reading_rebuilt.json'), JSON.stringify(table, null, 0));
  console.log('\n  写出 word_reading_rebuilt.json（', Object.keys(table).length, '条 ）');
});
