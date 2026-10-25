// 给 repo4x 的 6 集（s001-s006）重算 ruby：整词表命中⇒整块 + 候选并集 DP
// 词表 = 仓库 shun/tools/word_reading.json（权威）+ 词典读音消歧修正（2 条）
const kuromoji = require('kuromoji');
const fs = require('fs');
const path = require('path');
const RG = require('./ruby_gen.js');

const HERE = __dirname;
const REPO = path.join(HERE, 'repo4x/nhk-easy-news-main');
const BASE = path.join(REPO, 'shun-web/data');
const TAGS = ['s001', 's002', 's003', 's004', 's005', 's006'];
const dicPath = path.join(HERE, 'node_modules/kuromoji/dict');

const TABLE = JSON.parse(fs.readFileSync(path.join(REPO, 'shun/tools/word_reading.json'), 'utf8'));
// 🔴 仓库那份是「多数票」值 ⇒ 幼馴染 漏「み」、若者=わかも
//    用**词典读音**消歧（词典给的是标准读音）⇒ 这两条是已发布数据里就有的冲突
TABLE['幼馴染'] = 'おさななじみ';
TABLE['若者'] = 'わかもの';

kuromoji.builder({ dicPath }).build((err, tok) => {
  if (err) throw err;
  const out = {};
  let sent = 0, mismatch = 0, kanjiSent = 0, kanjiOk = 0, whole = 0, split = 0;
  for (const tag of TAGS) {
    const d = JSON.parse(fs.readFileSync(path.join(BASE, tag + '.json'), 'utf8'));
    out[tag] = [];
    for (const s of d.sents) {
      const toks = tok.tokenize(s.t);
      const ru = toks.flatMap(t => RG.rubyTokenWithTable(t, TABLE));
      out[tag].push(ru);
      sent++;
      if (ru.map(x => x[0]).join('') !== s.t) { mismatch++; console.log('MISMATCH', JSON.stringify(s.t)); }
      const hasK = ru.some(x => /[一-鿿]/.test(x[0]));
      if (hasK) { kanjiSent++; if (ru.every(x => !/[一-鿿]/.test(x[0]) || x[1])) kanjiOk++; }
      for (const x of ru) if (/[一-鿿]/.test(x[0])) { if (x[0].length >= 2) whole++; else split++; }
    }
  }
  fs.writeFileSync(path.join(HERE, 'ruby_peer6.json'), JSON.stringify(out));
  console.log('集', TAGS.length, '句', sent, '拼接不符', mismatch,
    '｜ 含汉字句', kanjiSent, '其中每个汉字都有读音', kanjiOk);
  console.log('汉字单元：整词(>=2字)', whole, '｜ 逐字(1字)', split);

  // 抽样：会社 / 日本語 / 教師
  for (const w of ['会社', '日本語', '教師', '時間']) {
    const t = tok.tokenize(w)[0];
    console.log(' ', w.padEnd(5), JSON.stringify(RG.rubyTokenWithTable(t, TABLE)));
  }
});
