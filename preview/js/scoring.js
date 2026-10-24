/* =====================================================================
   四级翻译 · 机器初判（纯函数，无 DOM、无网络）
   ---------------------------------------------------------------------
   作用：把「学生写的那几句」换算成**四级翻译五档评分标准**能用的数。

   🔴 铁律：本模块判的是「信息完整度」这一维，**不是**最终四级分数。
      四级是总体印象评分法，阅卷看三件事：
        ① 意思准不准（信息完整度） ② 语言对不对（语法） ③ 通不通顺（表达）
      本模块只能判 ①（外加句子结构完整性）。
      ⇒ 因此对外只报「档位上限 + 区间」，**绝不报一个假的确定分数**。
      ⇒ 真正定档要靠 AI 批改（按需调中转，不烧本模块的额度）。

   本模块被前端和 node 单测共用，所以必须是纯函数、无副作用、可预测。
   ===================================================================== */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------------
     五档评分标准
     分数区间与描述取自官方评分标准；「完整度下限」取自新东方给的量化口径
     （13-15 档信息完整度 100%；10-12 档 ≥95%；7-9 档约 80%；4-6 档偏低）。
     ⚠️ 完整度是**下限**（该档至少要达到），不是上限 —— 达到 100% 也
        可能因语言错误落到下一档，所以对外说「档位上限」是不准确的，
        准确说法是「按信息完整度看，落在第 X 档区间；语言错误会掉档」。
     ------------------------------------------------------------------ */
  var BANDS = [
    {
      min: 13, max: 15, no: 1, name: '准确表达',
      lo: 95,
      std: '译文准确表达了原文的意思。用词贴切，行文流畅，基本上无语言错误，仅有个别小错。'
    },
    {
      min: 10, max: 12, no: 2, name: '基本表达',
      lo: 85,
      std: '译文基本上表达了原文的意思。文字通顺、连贯，无重大语言错误。'
    },
    {
      min: 7, max: 9, no: 3, name: '勉强表达',
      lo: 70,
      std: '译文勉强表达了原文的意思。用词欠准确，语言错误相当多，其中有些是严重语言错误。'
    },
    {
      min: 4, max: 6, no: 4, name: '仅表达少部分',
      lo: 40,
      std: '译文仅表达了一小部分原文的意思。用词不准确，有相当多的严重语言错误。'
    },
    {
      min: 1, max: 3, no: 5, name: '支离破碎',
      lo: 1,
      std: '译文支离破碎。除个别词语或句子，绝大部分文字没有表达原文意思。'
    },
    {
      min: 0, max: 0, no: 6, name: '未作答',
      lo: -1,
      std: '未作答，或只有几个孤立的词，或译文与原文毫不相关。'
    }
  ];

  /* ------------------------------------------------------------------
     小工具
     ------------------------------------------------------------------ */
  function stripTags(s) { return String(s || '').replace(/<[^>]+>/g, ''); }
  function wordCount(s) {
    return (String(s || '').trim().match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
  }
  function cnCount(s) {
    return (stripTags(s).match(/[一-鿿]/g) || []).length;
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ------------------------------------------------------------------
     句子结构完整性（机器能可靠判的部分）
     · 谓语：be 动词 / 情态 / 动词原形或屈折形
     · 结尾标点
     · 残片（只有 1-2 个词）
     ⚠️ 这是「明显缺陷」筛查，**不是**语法检查器。
        它抓的是「整句不成立」这种大硬伤，正好对应评分标准里的
        「支离破碎」「句子不通顺」。
     ------------------------------------------------------------------ */
  var BE = /^(am|is|are|was|were|be|been|being)$/i;
  var MODAL = /^(can|could|will|would|shall|should|may|might|must|ought|need)$/i;
  // 常见动词（含不规则过去式/过去分词），够用即可 —— 只用来判断「有没有谓语」
  var VERBS = new RegExp(
    '^(' + [
      'have', 'has', 'had', 'do', 'does', 'did', 'go', 'goes', 'went', 'gone',
      'make', 'makes', 'made', 'take', 'takes', 'took', 'taken', 'get', 'gets', 'got',
      'give', 'gives', 'gave', 'given', 'see', 'sees', 'saw', 'seen', 'know', 'knows', 'knew', 'known',
      'think', 'thinks', 'thought', 'say', 'says', 'said', 'tell', 'tells', 'told',
      'become', 'becomes', 'became', 'use', 'uses', 'used', 'find', 'finds', 'found',
      'work', 'works', 'worked', 'call', 'calls', 'called', 'try', 'tries', 'tried',
      'need', 'needs', 'help', 'helps', 'helped', 'increase', 'increases', 'increased',
      'grow', 'grows', 'grew', 'grown', 'show', 'shows', 'showed', 'shown',
      'play', 'plays', 'played', 'start', 'starts', 'started', 'continue', 'continues', 'continued',
      'carry', 'carries', 'carried', 'bring', 'brings', 'brought', 'build', 'builds', 'built',
      'hold', 'holds', 'held', 'keep', 'keeps', 'kept', 'let', 'lets', 'put', 'puts',
      'mean', 'means', 'meant', 'receive', 'receives', 'received', 'remain', 'remains', 'remained',
      'seem', 'seems', 'seemed', 'support', 'supports', 'supported', 'develop', 'develops', 'developed',
      'provide', 'provides', 'provided', 'consider', 'considers', 'considered', 'include', 'includes', 'included',
      'improve', 'improves', 'improved', 'reduce', 'reduces', 'reduced', 'increase', 'increases', 'increased',
      'protect', 'protects', 'protected', 'preserve', 'preserves', 'preserved', 'attract', 'attracts', 'attracted',
      'promote', 'promotes', 'promoted', 'require', 'requires', 'required', 'achieve', 'achieves', 'achieved',
      'complete', 'completes', 'completed', 'enable', 'enables', 'enabled', 'allow', 'allows', 'allowed',
      'increase', 'increases', 'increased', 'reach', 'reaches', 'reached', 'remain', 'remains', 'remained',
      'reflect', 'reflects', 'reflected', 'represent', 'represents', 'represented', 'form', 'forms', 'formed',
      'suffer', 'suffers', 'suffered', 'face', 'faces', 'faced', 'cost', 'costs', 'costed'
    ].join('|') + ')$', 'i'
  );

  function sentenceIssues(text) {
    var t = String(text || '').trim();
    var out = [];
    if (!t) return out;
    var words = t.split(/\s+/).filter(Boolean);
    if (words.length <= 2) {
      out.push({ kind: 'fragment', msg: '只有 ' + words.length + ' 个词，像没写完的片段' });
      return out;
    }
    if (!/[.!?;)"'”’]$/.test(t)) {
      out.push({ kind: 'nopunct', msg: '句末没有标点' });
    }
    // 找谓语：取「不在名词位置的最后一个动词性词」
    var hasPred = false;
    for (var i = words.length - 1; i >= 0; i--) {
      var w = words[i].replace(/[^A-Za-z'-]/g, '').toLowerCase();
      if (!w) continue;
      if (BE.test(w) || MODAL.test(w) || VERBS.test(w)) { hasPred = true; break; }
      // 遇到明显是名词/代词的词就继续往前找（最多找 3 个）
    }
    if (!hasPred) {
      out.push({ kind: 'nopred', msg: '看不出谓语动词，句子可能不成立' });
    }
    return out;
  }

  /* ------------------------------------------------------------------
     主入口
     opts = {
         sentences : [{no, cn, ref, hints}]   ← 本次练习的句子
         exprs    : [{cn, en, re, sno}]      ← 本次练习对应的采分点
         drafts    : { '1': '...', '2': '...' }
       }
     ------------------------------------------------------------------ */
  function score(opts) {
    opts = opts || {};
    var sentences = opts.sentences || [];
    var exprs = opts.exprs || [];
    var drafts = opts.drafts || {};

    var perSentence = [];
    var hits = 0, total = 0;
    var exprItems = [];
    var anyWord = false;
    var i, s, no, text, words;

    /* ---- 逐句：长度 + 结构 ---- */
    for (i = 0; i < sentences.length; i++) {
      s = sentences[i];
      no = s.no || (i + 1);
      text = String(drafts[no] == null ? (drafts[i + 1] == null ? '' : drafts[i + 1]) : drafts[no]);
      words = wordCount(text);
      if (words > 0) anyWord = true;
      var refWords = wordCount(s.ref || '');
      var ratio = (refWords > 0 && words > 0) ? Math.round(words / refWords * 100) : 0;
      perSentence.push({
        no: no,
        cn: stripTags(s.cn),
        words: words,
        refWords: refWords,
        ratio: ratio,
        issues: sentenceIssues(text),
        short: (words > 0 && refWords > 0 && ratio < 55),
        empty: (words === 0)
      });
    }

    /* ---- 逐采分点：命中 ---- */
    for (i = 0; i < exprs.length; i++) {
      var e = exprs[i];
      var ok = false;
      // 只在该采分点所属句子（或所属句子 + 全篇）里找，避免跨句误命中
      var scope = '';
      var j;
      for (j = 0; j < sentences.length; j++) {
        var sno = sentences[j].no || (j + 1);
        if (e.sno == null || e.sno === sno || e.sno === 0) scope += ' ' + (drafts[sno] || '');
      }
      if (!scope) scope = allText(drafts, sentences);
      try { ok = new RegExp(e.re, 'i').test(scope); } catch (err) { ok = false; }
      if (ok) hits++;
      total++;
      exprItems.push({ i: i, no: i + 1, sno: e.sno, cn: e.cn, en: e.en, ok: ok });
    }

    var pct = total ? Math.round(hits / total * 100) : 0;
    var comp = pct;                       // 信息完整度 = 采分点覆盖率
    var band = bandOf(comp, anyWord);

    /* ---- 提示 ---- */
    var notes = [];
    var missing = exprItems.filter(function (x) { return !x.ok; });
    if (!anyWord) {
      notes.push({ level: 'warn', text: '一个字都没写 —— 这条按 0 分算。' });
    } else {
      if (missing.length) {
        notes.push({
          level: 'warn',
          text: '漏了 ' + missing.length + ' 个采分表达。漏关键信息是四级翻译最重的扣分项，直接掉档。'
        });
      } else {
        notes.push({ level: 'ok', text: '本句采分表达全部命中，信息完整度 100%。' });
      }
      perSentence.forEach(function (p) {
        if (p.empty) {
          notes.push({ level: 'warn', text: '第 ' + p.no + ' 句空着 —— 漏一整句直接进第 4 档往下。' });
        } else if (p.short) {
          notes.push({
            level: 'warn',
            text: '第 ' + p.no + ' 句只有参考译文的 ' + p.ratio + '%（' + p.words + '/' + p.refWords + ' 词），多半漏了半句。'
          });
        }
        p.issues.forEach(function (iss) {
          notes.push({ level: 'warn', text: '第 ' + p.no + ' 句：' + iss.msg + '。' });
        });
      });
    }

    return {
      hits: hits,
      total: total,
      pct: pct,
      completeness: comp,
      band: band,
      perSentence: perSentence,
      items: exprItems,
      missing: missing,
      notes: notes,
      answered: anyWord
    };
  }

  function allText(drafts, sentences) {
    var out = '', i;
    for (i = 0; i < sentences.length; i++) {
      var no = sentences[i].no || (i + 1);
      out += ' ' + (drafts[no] || '');
    }
    if (!out.trim()) {
      for (var k in drafts) { if (Object.prototype.hasOwnProperty.call(drafts, k)) out += ' ' + drafts[k]; }
    }
    return out;
  }

  function bandOf(comp, answered) {
    if (!answered) return BANDS[5];
    for (var i = 0; i < BANDS.length - 1; i++) {
      if (comp >= BANDS[i].lo) return BANDS[i];
    }
    return BANDS[4];
  }

  /* 档位文本（给 UI 用） */
  function bandText(b) {
    if (!b) return '';
    if (b.min === 0) return '0 分 · 未作答';
    return '第 ' + b.no + ' 档（' + b.min + '–' + b.max + ' 分）· ' + b.name;
  }

  /* 「这一档意味着什么」+「为什么机器给不了最终分」 */
  function bandAdvice(b) {
    if (!b) return '';
    return b.std;
  }

  global.CET4Score = {
    BANDS: BANDS,
    score: score,
    bandOf: bandOf,
    bandText: bandText,
    bandAdvice: bandAdvice,
    wordCount: wordCount,
    cnCount: cnCount,
    esc: esc,
    sentenceIssues: sentenceIssues
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = global.CET4Score;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
