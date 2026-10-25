// 逐字 ruby 注音引擎（kuromoji + kanjidic2-json + 仓库熟字訓表）
// 输出格式与原始 SHUN 数据的 ru 一致：[[表层, 读音], ...]  假名的读音为 ""
//
// 算法：
//   1. kuromoji(IPADIC) 分词 → surface + reading(片假名)
//   2. 含汉字的 token：拆成 前导假名 / 汉字串 / 尾部送り仮名
//   3. 送り仮名只贡献自己的读音（清音便）⇒ 从读音预算里扣掉
//   4. 剩下的读音用 DP 逐字切给汉字：
//        候选 = 仓库熟字訓表 > kanjidic2 音读/训读/名乗り
//   5. DP 无解 ⇒ 整块兜底（词级读音）
const kuromoji = require('kuromoji');
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const dicPath = path.join(HERE, 'node_modules/kuromoji/dict');
const KANJI_JSON = require('./node_modules/kanjidic2-json/KANJIS.json');
const CURATED = JSON.parse(fs.readFileSync(
  path.join(HERE, 'repo2/nhk-easy-news-main/shun/tools/kanji_readings.json'), 'utf8'));

const hira2kata = s => s.replace(/[ぁ-ゖ]/g, c => String.fromCharCode(c.charCodeAt(0) + 0x60));
const kata2hira = s => s.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));
const isKanji = c => /[一-鿿㐀-䶿]/.test(c);
const isKana = c => /[぀-ゟ゠-ヿ]/.test(c);
const DAKU = { 'ガ': 'カ', 'ザ': 'サ', 'ダ': 'タ', 'バ': 'ハ', 'パ': 'ハ' };
const seion = s => s.replace(/[ガザダバパ]/g, c => DAKU[c]);

// kanjidic2 索引
const KANJI = {};
for (const k in KANJI_JSON) {
  const v = KANJI_JSON[k];
  if (!v || !v.literal || !v.readings) continue;
  const r = v.readings;
  KANJI[v.literal] = {
    on: (r.ja_on || []).map(hira2kata),
    kun: (r.ja_kun || []).map(x => hira2kata(x.replace(/\./g, ''))).filter(x => x && !x.startsWith('-')),
    nan: (v.nanoris || []).map(hira2kata),
  };
}

/* 候选读音：词典（音读/训读/名乗り）优先；熟字訓表只在词典无候选时兜底。
   🔴 已发布数据用的是**词典读音**（会→カイ），不是熟字訓表的上下文读音（会→あ）⇒ 顺序不能反 */
function candidates(ch, useCuratedFirst) {
  if (useCuratedFirst && CURATED[ch]) return [hira2kata(CURATED[ch])];
  const k = KANJI[ch];
  const out = [];
  if (k) for (const x of [...k.on, ...k.kun, ...k.nan]) if (x && !out.includes(x)) out.push(x);
  if (!out.length && CURATED[ch]) out.push(hira2kata(CURATED[ch]));
  return out;
}
let CURATED_FIRST = false;   // 全局开关：true = 熟字訓优先（旧行为）

// DP：把 reading 切成 n 段分给 n 个汉字
function splitKanji(kanjiStr, reading) {
  const n = kanjiStr.length, m = reading.length;
  const cand = [];
  for (let i = 0; i < n; i++) cand.push(candidates(kanjiStr[i], CURATED_FIRST));
  const memo = new Map();
  function go(i, j) {
    if (i === n) return j === m ? [] : null;
    if (j > m) return null;
    const key = i * 10000 + j;
    if (memo.has(key)) return memo.get(key);
    let res = null;
    for (const c of cand[i]) {
      if (!c) continue;
      if (reading.substr(j, c.length) === c) {
        const rest = go(i + 1, j + c.length);
        if (rest) { res = [c].concat(rest); break; }
      }
    }
    memo.set(key, res);
    return res;
  }
  return go(0, 0);
}

function rubyForToken(surface, reading) {
  const out = [];
  if (!surface) return out;
  let s = surface;
  let lead = '';
  let trail = '';
  // 前导假名
  let m = s.match(/^[぀-ゟ゠-ヿ]+/);
  if (m) { lead = m[0]; s = s.slice(lead.length); }
  // 尾部送り仮名
  m = s.match(/[぀-ゟ゠-ヿ]+$/);
  if (m) { trail = m[0]; s = s.slice(0, s.length - trail.length); }
  if (lead) out.push([lead, '']);
  if (s) {
    const kanjiOnly = [...s].every(isKanji);
    if (kanjiOnly) {
      let budget = reading || '';
      if (trail) {
        // 送り仮名贡献的读音 = 自己的清音
        const t = seion(hira2kata(trail));
        if (budget.endsWith(t)) budget = budget.slice(0, budget.length - t.length);
        else if (budget.endsWith(hira2kata(trail))) budget = budget.slice(0, budget.length - hira2kata(trail).length);
      }
      const parts = splitKanji(s, budget);
      if (parts && parts.length === [...s].length) {
        // 🔴 DP 切出来的读音拼起来必须等于词典读音，否则说明 DP 选了个
        //    「单字读音」而不是这个词的标准读音（人:ひと→ひ / 国:くに→く）
        //    ⇒ 此时退回「词典读音 + 逐字同读音」（保留标准音）
        const joined = parts.join('');
        if (joined === budget) {
          [...s].forEach((c, i) => out.push([c, kata2hira(parts[i])]));
        } else {
          [...s].forEach(c => out.push([c, kata2hira(budget)]));
        }
      } else {
        out.push([s, kata2hira(budget || reading || '')]);
      }
    } else {
      // 汉字+假名混合（词内送り仮名）⇒ 整词读音
      out.push([s, kata2hira(reading || '')]);
    }
  }
  if (trail) out.push([trail, '']);
  return out;
}

function rubyForTokens(tokens) {
  const out = [];
  for (const t of tokens) {
    const r = t.reading || '';
    if (!/[一-鿿㐀-䶿]/.test(t.surface_form)) {
      out.push([t.surface_form, '']);
    } else {
      out.push(...rubyForToken(t.surface_form, r));
    }
  }
  return out;
}

/* 🔴 「整词优先」层的正确实现
   —— 词表命中 ⇒ 整块（读音直接用表里的整词读音）
   🔴 不要用「len(表值) == len(汉字串)」当命中条件：
      已发布的 187 条表是「整词一个读音」（'初心者' -> 'しょしんしゃ'，长 4 vs 3）
      ⇒ 那个条件会**静默跳过 170/187 条**（只剩 17 条碰巧等长，而且那 17 条是拆开读）
      ⇒ 这就是「整词优先」落不了地的真正原因（不是触发条件错，是消费格式不匹配） */
function rubyWithWordTable(pairs, wordTable) {
  const out = [];
  for (const [t, r] of pairs) {
    if (!t) continue;
    if (isGlossSurface(t)) { out.push([t, r]); continue; }
    out.push([t, r]);
  }
  return out;
}
function isGlossSurface(t) { return false; }

/* 词表优先的整块切分：输入 kuromoji token，输出 ruby 单元 */
function rubyTokenWithTable(tok, wordTable) {
  const s = tok.surface_form;
  if (!/[\u4e00-\u9fff\u3400-\u4dbf]/.test(s)) return [[s, '']];
  let i = 0; while (i < s.length && !isKanji(s[i])) i++;
  let j = s.length; while (j > i && !isKanji(s[j - 1])) j--;
  const pre = s.slice(0, i), kanji = s.slice(i, j), post = s.slice(j);
  const out = [];
  if (pre) out.push([pre, '']);
  const hit = wordTable && wordTable[kanji];
  if (hit) {
    out.push([kanji, hit]);
    if (post) out.push([post, '']);
    return out;
  }
  return rubyForToken(s, tok.reading || '');
}

module.exports = { rubyForTokens, rubyForToken, rubyTokenWithTable,
                   setCuratedFirst: v => { CURATED_FIRST = v; } };

if (require.main === module) {
  const mode = process.argv[2] || 'validate';
  if (mode === 'validate') {
    // 用原始 13 集的 ru 当基准，验证我的生成器
    const SRC = '/workspace/attachments/98db9ef04c3d952c/Japanese-with-Shun-Text.html';
    const html = fs.readFileSync(SRC, 'utf8');
    const line = html.split('\n').find(l => l.startsWith('<script>window.SHUN='));
    const D = JSON.parse(line.slice('<script>window.SHUN='.length, line.lastIndexOf(';</script>')));
    kuromoji.builder({ dicPath }).build((err, tok) => {
      if (err) throw err;
      let sent = 0, exact = 0, kanjiOnly = 0, kanjiExact = 0, diffs = [];
      for (const e of D.eps) for (const s of e.sents) {
        const toks = tok.tokenize(s.t);
        const mine = rubyForTokens(toks);
        const theirs = s.ru;
        sent++;
        if (JSON.stringify(mine) === JSON.stringify(theirs)) exact++;
        // 只看含汉字的句子的汉字部分
        const mk = mine.filter(x => /[一-鿿]/.test(x[0]));
        const tk = theirs.filter(x => /[一-鿿]/.test(x[0]));
        if (tk.length) {
          kanjiOnly++;
          if (JSON.stringify(mk) === JSON.stringify(tk)) kanjiExact++;
          else if (diffs.length < 12) diffs.push({ t: s.t, mine: mk, theirs: tk });
        }
      }
      console.log('句数', sent, '整句 ru 完全一致', exact, `(${(exact/sent*100).toFixed(1)}%)`);
      console.log('含汉字的句', kanjiOnly, '汉字部分完全一致', kanjiExact,
        `(${(kanjiExact/Math.max(1,kanjiOnly)*100).toFixed(1)}%)`);
      console.log('差异样本:');
      for (const d of diffs) console.log('  ', d.t, '\n     mine  ', JSON.stringify(d.mine), '\n     theirs', JSON.stringify(d.theirs));
    });
  } else if (mode === 'peer') {
    // 给 peer 的 7 集生成 ru
    const BASE = path.join(HERE, 'repo2/nhk-easy-news-main/shun-web/data');
    const files = fs.readdirSync(BASE).filter(f => /^s\d+\.json$/.test(f)).sort();
    kuromoji.builder({ dicPath }).build((err, tok) => {
      if (err) throw err;
      let n = 0, sent = 0, kanjiSent = 0, kanjiOk = 0, noRead = 0;
      const out = {};
      for (const fn of files) {
        const d = JSON.parse(fs.readFileSync(path.join(BASE, fn), 'utf8'));
        out[d.tag] = [];
        for (const s of d.sents) {
          const toks = tok.tokenize(s.t);
          const ru = rubyForTokens(toks);
          out[d.tag].push(ru);
          sent++;
          const joined = ru.map(x => x[0]).join('');
          if (joined !== s.t) { console.log('MISMATCH', JSON.stringify(s.t), JSON.stringify(joined)); n++; }
          const hasK = ru.some(x => /[一-鿿]/.test(x[0]) && x[1]);
          if (hasK) { kanjiSent++; if (ru.every(x => !/[一-鿿]/.test(x[0]) || x[1])) kanjiOk++; }
          if (!ru.some(x => x[1])) noRead++;
        }
      }
      fs.writeFileSync(path.join(HERE, 'ruby_peer.json'), JSON.stringify(out));
      console.log('集', files.length, '句', sent, '拼接不符', n,
        '｜ 含汉字句', kanjiSent, '其中每个汉字都有读音', kanjiOk,
        '｜ 整句无读音(纯假名)', noRead);
    });
  }
}
