// 实测：「整词优先」的触发条件到底该怎么定？
// 基准 = 已发布 13 集的 ru（1154 句，原始 SHUN 文件 + 仓库 ep*.json 同一份）
// 候选规则：
//   A  逐字 DP（无整词层）                        ← 我上一版
//   B  词表命中 ⇒ 整块（读音=词表值）
//   C  词表命中 且 词表读音 ≠ 逐字DP 拼出的读音 ⇒ 整块；否则逐字
//   D  词表命中 ⇒ 整块，但只有当表里的词确实在原句里作为整词出现（防过宽）
const kuromoji = require('kuromoji');
const fs = require('fs');
const path = require('path');
const { rubyForTokens } = require('./ruby_gen.js');

const HERE = __dirname;
const REPO = path.join(HERE, 'repo3/nhk-easy-news-2ea927d3');
const dicPath = path.join(HERE, 'node_modules/kuromoji/dict');
const WR = JSON.parse(fs.readFileSync(path.join(REPO, 'shun/tools/word_reading.json'), 'utf8'));

const isKanji = c => /[一-鿿㐀-䶿]/.test(c);
const isKana = c => /[぀-ゟ゠-ヿ]/.test(c);
const kata2hira = s => s.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));

/* 把一个 token 拆成 (前导假名, 漢字串, 尾部送り仮名) */
function splitTok(s) {
  let i = 0; while (i < s.length && !isKanji(s[i])) i++;
  let j = s.length; while (j > i && !isKanji(s[j - 1])) j--;
  return [s.slice(0, i), s.slice(i, j), s.slice(j)];
}

/* 逐字 DP 的结果（用于「读音≠DP」判定） */
function dpReadings(tok) {
  return rubyForTokens([tok]).filter(x => x[1]).map(x => x[1]).join('');
}

/* 规则 B/C/D：整词优先层 */
function rubyRule(tok, rule) {
  const s = tok.surface_form;
  if (!/[一-鿿㐀-䶿]/.test(s)) return [[s, '']];
  const [pre, kanji, post] = splitTok(s);
  const out = [];
  if (pre) out.push([pre, '']);
  const hit = WR[kanji];
  let useWhole = false;
  if (hit) {
    if (rule === 'B' || rule === 'D') useWhole = true;
    else if (rule === 'C') {
      const dp = dpReadings(tok);
      useWhole = (kata2hira(hit) !== dp);
    }
  }
  if (useWhole) {
    out.push([kanji, kata2hira(hit)]);
  } else {
    // 退回逐字 DP
    return rubyForTokens([tok]);
  }
  if (post) out.push([post, '']);
  return out;
}

const SRC = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html';
const html = fs.readFileSync(SRC, 'utf8');
const line = html.split('\n').find(l => l.startsWith('<script>window.SHUN='));
const D = JSON.parse(line.slice('<script>window.SHUN='.length, line.lastIndexOf(';</script>')));

kuromoji.builder({ dicPath }).build((err, tok) => {
  if (err) throw err;
  const rules = ['A', 'B', 'C', 'D'];
  const res = {};
  for (const r of rules) res[r] = { exact: 0, sent: 0, diff: [] };

  for (const e of D.eps) for (const s of e.sents) {
    const toks = tok.tokenize(s.t);
    for (const r of rules) {
      const mine = r === 'A'
        ? rubyForTokens(toks)
        : toks.flatMap(t => rubyRule(t, r));
      res[r].sent++;
      if (JSON.stringify(mine) === JSON.stringify(s.ru || [])) res[r].exact++;
      else if (res[r].diff.length < 8) {
        res[r].diff.push({ t: s.t, mine, theirs: s.ru });
      }
    }
  }
  console.log('=== 整词优先：四种规则的「整句 ru 逐字一致率」（基准 = 已发布 13 集 1154 句）===');
  for (const r of rules) {
    console.log(`  规则 ${r}: ${res[r].exact}/${res[r].sent} = ${(res[r].exact / res[r].sent * 100).toFixed(1)}%`);
  }
  console.log('\n--- 规则 B（词表命中⇒整块）不一致的样本 ---');
  for (const d of res.B.diff) {
    console.log('  ', d.t);
    console.log('     mine  ', JSON.stringify(d.mine));
    console.log('     theirs', JSON.stringify(d.theirs));
  }
  console.log('\n--- 规则 C（命中 且 读音≠DP⇒整块）不一致的样本 ---');
  for (const d of res.C.diff) {
    console.log('  ', d.t);
    console.log('     mine  ', JSON.stringify(d.mine));
    console.log('     theirs', JSON.stringify(d.theirs));
  }
});
