// 通用 kuromoji 分词：输入 {eps:[{sents:[{t,zh,ru}]}]}  ⇒ 输出 [{t,zh,toks}]
// 用法: node extract_generic.js <in.json> <out.json>
const kuromoji = require('kuromoji');
const fs = require('fs');
const path = require('path');
const IN = process.argv[2];
const OUT = process.argv[3];
if (!IN || !OUT) { console.error('用法: node extract_generic.js <in.json> <out.json>'); process.exit(1); }
const D = JSON.parse(fs.readFileSync(IN, 'utf8'));
const dicPath = process.env.KUROMOJI_DIC || path.join(__dirname, 'node_modules/kuromoji/dict');
kuromoji.builder({ dicPath }).build((err, tok) => {
  if (err) throw err;
  const out = [];
  let mismatch = 0, unk = 0, tot = 0;
  for (const e of (D.eps || D)) {
    for (const s of e.sents) {
      const toks = tok.tokenize(s.t).map(t => ({
        s: t.surface_form, b: t.basic_form, p: t.pos,
        d1: t.pos_detail_1, d2: t.pos_detail_2,
        ct: t.conjugated_type, cf: t.conjugated_form, r: t.reading,
      }));
      tot += toks.length;
      for (const t of toks) if (t.p === 'UNK') unk++;
      if (toks.map(t => t.s).join('') !== s.t) mismatch++;
      out.push({ tag: e.tag, t: s.t, zh: s.zh, toks });
    }
  }
  fs.writeFileSync(OUT, JSON.stringify(out));
  console.log('sents', out.length, 'tokens', tot, 'concat mismatch', mismatch, 'UNK', unk);
});
