/*!
 * Shun 专属 AI 讲解接入（基于 ai-client.js v1.2）
 *
 * 三条 Shun 专属规则：
 *   1. refs 必须是**干净台词**：
 *      - 行中的读音括号删掉（Shun 是单人播客，没有说话人括号可保留）
 *      - **日文后粘连的英文要拆开**（Shun 讲会话用语的固定段落：
 *        `時期period` / `連絡をとるtocontact` —— 模型会原样抄进例句）
 *   2. 逐字注音**完全不依赖模型**：渲染层用 App 自己的 ruby 表生成，
 *      模型给的括号假名一律不显示（避免两套注音打架）
 *   3. 长句按子句切（Shun 一句话能说 30+ 词，整句丢给模型会散）
 */
(function (global) {
  'use strict';

  var SHUN = {};

  /* ── 清洗：删行中括号假名 ── */
  SHUN.cleanJa = function (s) {
    if (!s) return '';
    var t = String(s);
    /* 行中的括号（读音）→ 删。Shun 无说话人括号，所以行首行中一律删。 */
    t = t.replace(/[（(][ぁ-んァ-ヶー\s]+[）)]/g, '');
    return t.replace(/\s+/g, ' ').trim();
  };

  /* ── 拆分「日文 + 粘连英文」──
   * 例：`時期period` → `時期`；`連絡をとるtocontact` → `連絡をとる`
   * 依据：日文表层形后面紧跟 ≥2 个 ASCII 字母且没有空格。
   */
  SHUN.splitGlued = function (s) {
    if (!s) return { ja: '', en: '' };
    var t = String(s);
    var m = t.match(/^([぀-ヿ一-鿿々〆ヵヶ、。！？!?「」『』・\s]+?)([A-Za-z]{2,}[\sA-Za-z]*)$/);
    if (m) return { ja: m[1].trim(), en: m[2].trim() };
    /* 句中粘连：`...Taking t...` → 逐处拆 */
    var ja = '', en = '', rest = t;
    var re = /([぀-ヿ一-鿿々〆ヵヶ]+)([A-Za-z]{2,})/g;
    var last = 0, mm;
    while ((mm = re.exec(rest)) !== null) {
      ja += rest.slice(last, mm.index) + mm[1];
      en += (en ? ' ' : '') + mm[2];
      last = mm.index + mm[0].length;
    }
    ja += rest.slice(last);
    return { ja: ja.trim(), en: en.trim() };
  };

  /* ── 干净台词：清洗 + 拆粘连 ── */
  SHUN.cleanLine = function (s) {
    var g = SHUN.splitGlued(SHUN.cleanJa(s));
    return g.ja;
  };

  /* ── 子句切分（Shun 长句专用）──
   * ① 先按句末标点切 ② 再按助词边界切 ③ 合并到 ≤40 字
   */
  SHUN.splitClauses = function (ja, zh, maxLen) {
    maxLen = maxLen || 40;
    var out = [];
    if (!ja) return out;
    var parts = String(ja).split(/(?<=[。！？!?])/);
    var buf = '';
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (!p) continue;
      if ((buf + p).length > maxLen && buf) {
        out.push(buf);
        buf = p;
      } else {
        buf += p;
      }
    }
    if (buf) out.push(buf);
    /* 还是太长 → 按助词边界再切 */
    var final = [];
    for (var j = 0; j < out.length; j++) {
      var s = out[j];
      if (s.length <= maxLen) { final.push(s); continue; }
      var pieces = s.split(/(?=[がをはにでともからまでより])/);
      var b = '';
      for (var k = 0; k < pieces.length; k++) {
        if ((b + pieces[k]).length > maxLen && b) { final.push(b); b = pieces[k]; }
        else b += pieces[k];
      }
      if (b) final.push(b);
    }
    return final.map(function (x) { return x.trim(); }).filter(Boolean);
  };

  /* ── 取上下文（prev/next 各 ≤2 句）+ refs（同集、干净）── */
  SHUN.buildCtx = function (opts) {
    opts = opts || {};
    var lines = opts.lines || [];
    var idx = opts.index || 0;
    var prev = [], next = [], i;
    for (i = idx - 1; i >= 0 && prev.length < 2; i--) {
      var lp = lines[i];
      if (lp && lp.row && lp.row.t) prev.unshift({ ja: SHUN.cleanLine(lp.row.t), zh: lp.row.zh || '' });
    }
    for (i = idx + 1; i < lines.length && next.length < 2; i++) {
      var ln = lines[i];
      if (ln && ln.row && ln.row.t) next.push({ ja: SHUN.cleanLine(ln.row.t), zh: ln.row.zh || '' });
    }
    /* refs：同一集里挑 2~4 句**长短适中**的干净台词 */
    var refs = [], cands = [];
    for (i = 0; i < lines.length; i++) {
      var L = lines[i];
      if (!L || !L.row || !L.row.t) continue;
      var clean = SHUN.cleanLine(L.row.t);
      if (clean.length < 8 || clean.length > 45) continue;
      if (/[a-zA-Z]{3,}/.test(clean)) continue;   /* 含英文残留的不要 */
      cands.push({ ja: clean, zh: L.row.zh || '' });
    }
    for (i = 0; i < cands.length && refs.length < 4; i++) {
      if (i % Math.max(1, Math.floor(cands.length / 4)) === 0) refs.push(cands[i]);
    }
    return { prev: prev, next: next, refs: refs };
  };

  /* ── 逐字 ruby 查表（给模型输出做纠正用；主渲染不依赖它）── */
  SHUN.readingLookup = function (word) {
    if (!word) return '';
    var lex = (global.__shunLex && global.__shunLex()) || {};
    var v = lex.vocab || {};
    var e = v[word];
    if (!e) return '';
    return e.reading || e.r || '';
  };

  global.ShunAI = SHUN;
})(typeof window !== 'undefined' ? window : this);
