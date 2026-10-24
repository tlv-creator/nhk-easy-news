/* =====================================================================
   四级精读日报 · 语法错误类型引擎
   ---------------------------------------------------------------------
   作用：学生交卷后，找出译文里的**常见语法问题**，并归成「错误类型」
        （老师端据此统计「全班最常见的 3 类错误」）。

   🔴 为什么要自己写规则，不直接用 LanguageTool：
       实测 9 个典型中式英语错句，LanguageTool 只报了 2 处 ——
       它查的是「母语者也会犯的错」（拼写/时态/一致），
       「the number of…have」「importance on」这类**中式特有错**它基本不报。
       ⇒ 主力是本文件的中式英语规则，LanguageTool 作为补充（可选调用）。

   🔴 判定口径（必须写在界面上）：
       · 这是**机器提示**，可能有误报，不是老师的判决
       · 宁可漏报不可误报 —— 每条规则都宁可放过，也不给假警报
       · 参考译文永远是最权威的答案，本引擎只是帮学生先自查一遍

   依赖：零。词库（cet4_lexicon.json，可选注入）只用来做「事实量」判断：
         「这个词是可数单数名词吗」「这个词是动词原形吗」—— 有据可查，不是手写感觉。
   ===================================================================== */
(function (global) {
  'use strict';

  /* ---------------- 错误类型（老师看板统计的分类，固定别乱改） ---------------- */
  var TYPES = [
    '主谓一致', '冠词', '时态', '单复数', '动词形式',
    '介词搭配', '拼写', '重复用词', '中式表达', '超纲用词'
  ];

  /* ---------------- 词表（可选注入；没有它就只跑不依赖词表的规则） ---------------- */
  var LEX = null;   // {words:{w:[pos]}, nouns:[], verbs:[], adjectives:[], adverbs:[]}

  function setLexicon(lex) {
    // ⚠️ 守卫不能只看 lex.words —— 精简版词库**没有** words 字段（省 72KB）
    LEX = lex && (lex.words || lex.nouns || lex.verbs) ? lex : null;
  }
  function hasLex() { return !!LEX; }

  function posOf(w) {
    if (!LEX) return null;
    var key = String(w || '').toLowerCase();
    // 🔴 不用 words 字段（它占 72KB，用不上）—— 只靠四个词类数组判断
    var pos = [];
    if (LEX.nouns && LEX.nouns.indexOf(key) >= 0) pos.push('n');
    if (LEX.verbs && LEX.verbs.indexOf(key) >= 0) pos.push('v');
    if (LEX.adjectives && LEX.adjectives.indexOf(key) >= 0) pos.push('a');
    if (LEX.adverbs && LEX.adverbs.indexOf(key) >= 0) pos.push('ad');
    return pos.length ? pos : null;
  }
  function inSet(w, set) {
    if (!LEX || !LEX[set]) return false;
    return LEX[set].indexOf(String(w || '').toLowerCase()) >= 0;
  }
  /** 是「可数单数名词」吗？—— 依据：词库标了 n，且不在动词/形容词/副词表，且不以 s 结尾 */
  function isCountableNoun(w) {
    var p = posOf(w);
    if (!p || p.indexOf('n') < 0) return false;
    if (IRREGULAR_PLURAL.indexOf(String(w).toLowerCase()) >= 0) return false;
    if (inSet(w, 'verbs') || inSet(w, 'adjectives') || inSet(w, 'adverbs')) return false;
    if (/\w+s$/.test(w)) return false;          // 复数形态
    return true;
  }
  function isVerbLemma(w) {
    return inSet(w, 'verbs');
  }

  /* ---------------- 固定搭配表（每条都要有出处/常识支撑，别随手加） ---------------- */
  // 名词/动词 + 必须搭配的介词（不在允许集合里 ⇒ 报「介词搭配」）
  var PREP_PAIRS = {
    'importance': ['to'], 'attention': ['to'], 'contribute': ['to'], 'contribution': ['to'],
    'depend': ['on'], 'focus': ['on'], 'result': ['in'], 'lead': ['to'], 'adapt': ['to'],
    'apply': ['to'], 'respond': ['to'], 'refer': ['to'], 'account': ['for'],
    'benefit': ['from'], 'consist': ['of'], 'remind': ['of'], 'replace': ['with'],
    'provide': ['with'], 'similar': ['to'], 'suitable': ['for'], 'capable': ['of'],
    'aware': ['of'], 'responsible': ['for'], 'famous': ['for'], 'proud': ['of'],
    'afraid': ['of'], 'fond': ['of'], 'different': ['from'], 'suffer': ['from'],
    'prevent': ['from'], 'keep': ['from'], 'stop': ['from'], 'object': ['to'],
    'access': ['to'], 'approach': ['to'], 'solution': ['to'], 'key': ['to'],
    'reaction': ['to'], 'answer': ['to'], 'devote': ['to'], 'amount': ['to'],
    'contribute to': ['to'], 'pay attention': ['to'], 'take part': ['in'],
    'take part in': ['in'], 'in addition to': ['to'], 'be similar to': ['to']
  };
  var PREP_WORDS = ['on', 'in', 'at', 'to', 'for', 'with', 'of', 'from', 'by', 'about', 'for'];

  /* 常见错拼 → 正确拼写（都是确凿的误拼，不收「form/from」这种歧义对） */
  var MISSPELL = {
    'enviroment': 'environment', 'goverment': 'government', 'developement': 'development',
    'recieve': 'receive', 'seperate': 'separate', 'occurence': 'occurrence',
    'arguement': 'argument', 'beleive': 'believe', 'acheive': 'achieve',
    'sucess': 'success', 'sucessful': 'successful', 'sucessfully': 'successfully',
    'independant': 'independent', 'existance': 'existence', 'publically': 'publicly',
    'refered': 'referred', 'commited': 'committed', 'begining': 'beginning',
    'neccessary': 'necessary', 'accomodate': 'accommodate', 'embarass': 'embarrass',
    'priviledge': 'privilege', 'restaraunt': 'restaurant', 'definately': 'definitely',
    'comparision': 'comparison', 'oppotunity': 'opportunity', 'benifit': 'benefit',
    'intergrate': 'integrate', 'maintainance': 'maintenance', 'occassion': 'occasion',
    'perseverence': 'perseverence', 'questionaire': 'questionnaire',
    'responsability': 'responsibility', 'transfered': 'transferred',
    'vaccum': 'vacuum', 'wierd': 'weird', 'writting': 'writing',
    'grammer': 'grammar', 'langauge': 'language', 'knowlege': 'knowledge',
    'competance': 'competence', 'experiance': 'experience', 'persistant': 'persistent',
    'simular': 'similar', 'truely': 'truly', 'unfortunatly': 'unfortunately',
    'untill': 'until', 'usualy': 'usually', 'wich': 'which', 'writen': 'written',
    'yeares': 'years', 'tecnology': 'technology', 'becuase': 'because',
    'sepcific': 'specific', 'acess': 'access', 'adress': 'address',
    'calender': 'calendar', 'collegue': 'colleague', 'concious': 'conscious',
    'curiousity': 'curiosity', 'dicuss': 'discuss', 'fourty': 'forty',
    'harrass': 'harass', 'intelligance': 'intelligence', 'liason': 'liaison',
    'millenium': 'millennium', 'noticable': 'noticeable', 'pavillion': 'pavilion',
    'posession': 'possession', 'prefered': 'preferred', 'pronounciation': 'pronunciation',
    'wellcome': 'welcome', 'begining': 'beginning', 'comming': 'coming'
  };

  /* 中式英语固定错法（→ 更地道的说法） */
  var CHINGLISH = [
    { re: /\bvery like\b/gi, msg: '「very like」是中式说法', fix: 'really like / like ... very much' },
    { re: /\bopen the light\b/gi, msg: '「open the light」是中式说法', fix: 'turn on the light' },
    { re: /\beat medicine\b/gi, msg: '「eat medicine」是中式说法', fix: 'take medicine' },
    { re: /\baccording to my opinion\b/gi, msg: '「according to my opinion」是中式说法', fix: 'in my opinion' },
    { re: /\baccording to our opinion\b/gi, msg: '「according to our opinion」是中式说法', fix: 'in our opinion' },
    { re: /\bstudy knowledge\b/gi, msg: '「study knowledge」是中式说法', fix: 'acquire knowledge / learn' },
    { re: /\bimprove my English level\b/gi, msg: '「improve my English level」是中式说法', fix: 'improve my English' },
    { re: /\bbig rain\b/gi, msg: '「big rain」是中式说法', fix: 'heavy rain' },
    { re: /\bpeople can live without\b/gi, msg: '注意「live without…」后面缺宾语的完整性', fix: '检查这句是否写完' },
    { re: /\bwith the development of[^.]{0,40}living standard\b/gi, msg: '「living standard」搭配生硬', fix: "people's living conditions" }
  ];

  /* 不可数名词（排除「缺冠词」误报）—— 宁可多排除，也不要给假警报 */
  /* 不规则复数名词（拼写不以 s 结尾 ⇒ 不加进表就会被当成单数 ⇒ many people 误报） */
  var IRREGULAR_PLURAL = ('people men women children data media criteria phenomena teeth feet '
    + 'mice geese lives halves thirds staff police clothes goods means species aircraft '
    + 'offspring').split(' ');

  var UNCOUNTABLE = ('importance information knowledge progress development environment government '
    + 'education population technology economy society culture research management production '
    + 'quality quantity safety health ability responsibility opportunity benefit industry '
    + 'business energy traffic weather news advice evidence equipment luggage furniture work harm '
    + 'damage peace freedom justice happiness wealth stuff physics mathematics economics electronics '
    + 'logistics feedback homework housework infrastructure productivity efficiency security '
    + 'stability sustainability infrastructure employment behaviour labor machinery equipment '
    + 'travel luggage clothing furniture').split(' ');

  /* 高频不规则动词的过去式/过去分词（用于反查情态动词后用错形式） */
  var IRREGULAR = ('made went taken given seen known found thought brought bought caught taught '
    + 'left felt put meant kept held written spoken driven broken chosen forgotten hidden '
    + 'risen fallen eaten beaten begun become become done gone shown seen').split(' ');

  var AUX = ['can', 'could', 'should', 'must', 'might', 'may', 'would', 'will', 'shall'];

  /* =====================================================================
     规则表：每条规则 = {id, type, run(text) → [{offset,len,msg,fix}]}
     ⚠️ 规则只报**高置信**的问题；宁可漏，不可误。
     ===================================================================== */
  var RULES = [];

  function addRule(r) { RULES.push(r); }

  /* ---- R1 冠词：系动词 + 单数可数名词（缺 a/an/the） ---- */
  addRule({
    id: 'R1_article', type: '冠词',
    run: function (text) {
      var out = [];
      if (!hasLex()) return out;
      var re = /\b(?:is|are|was|were|be|been|being|became|become|becomes|get|gets|got|remain|remains|seems|seem|appear|appears)\s+([a-z]+)\b/gi;
      var m;
      while ((m = re.exec(text)) !== null) {
        var w = m[1];
        if (UNCUNC(w)) continue;
        if (!isCountableNoun(w)) continue;
        var before = text.slice(0, m.index);
        // 前面已有冠词/限定词 ⇒ 没问题
        if (/\b(a|an|the|my|your|his|her|its|our|their|this|that|these|those|some|any|each|every|no|another)\s+$/i.test(before)) continue;
        // 上一句的限定词跨过来？只看本句内
        if (/\b(a|an|the)\s+$/i.test(before)) continue;
        out.push({
          offset: m.index + m[0].indexOf(w),
          len: w.length,
          msg: '「' + w + '」是可数单数名词，前面一般要加冠词',
          fix: 'a / an / the ' + w
        });
      }
      return out;
    }
  });
  function UNCUNC(w) { return UNCOUNTABLE.indexOf(String(w).toLowerCase()) >= 0; }

  /* ---- R1b a / an 用错（按**发音**，不是按字母 —— 四级常考点） ---- */
  var NEED_AN = ['important', 'hour', 'honest', 'honor', 'honour', 'heir', 'heiress'];
  var NEED_A = ['university', 'european', 'one', 'useful', 'user', 'unique', 'union', 'unit'];
  addRule({
    id: 'R1b_an', type: '冠词',
    run: function (text) {
      var out = [];
      var m;
      var re1 = /\ba\s+([a-z]+)\b/gi;
      while ((m = re1.exec(text)) !== null) {
        if (NEED_AN.indexOf(m[1].toLowerCase()) < 0) continue;
        out.push({
          offset: m.index, len: m[0].length,
          msg: '「' + m[1] + '」以元音音素开头 ⇒ 前面用 an', fix: 'an ' + m[1]
        });
      }
      var re2 = /\ban\s+([a-z]+)\b/gi;
      while ((m = re2.exec(text)) !== null) {
        if (NEED_A.indexOf(m[1].toLowerCase()) < 0) continue;
        out.push({
          offset: m.index, len: m[0].length,
          msg: '「' + m[1] + '」以辅音音素开头 ⇒ 前面用 a', fix: 'a ' + m[1]
        });
      }
      return out;
    }
  });

  /* ---- R2 主谓一致：the number of + 复数动词 ---- */
  addRule({
    id: 'R2_number_agree', type: '主谓一致',
    run: function (text) {
      var out = [];
      var re = /\bthe number of\b[^.;!?]{0,90}?\b(have|are|do|were|seem|appear|include|remain|account|make|take)\b/gi;
      var m;
      while ((m = re.exec(text)) !== null) {
        out.push({
          offset: m.index + m[0].lastIndexOf(m[1]),
          len: m[1].length,
          msg: 'the number of 后面用单数（the number 是单数）',
          fix: 'the number of ... ' + singleOf(m[1])
        });
      }
      return out;
    }
  });
  function singleOf(v) {
    return { have: 'has', are: 'is', do: 'does', were: 'was', seem: 'seems', appear: 'appears',
      include: 'includes', remain: 'remains', account: 'accounts', make: 'makes', take: 'takes' }[v] || v;
  }

  /* ---- R3 已撤掉（2026-10-24）----
     「a series of + 单数谓语」在英语界有争议（整体看=单数 / members=复数），
     按「宁可漏报不可误报」的口径撤掉这条规则，别给学生假警报。
     保留 R2（the number of + 复数动词）—— 那条没有争议。 ---- */

  /* ---- R4 单复数：many + 单数可数名词 ---- */
  addRule({
    id: 'R4_many_plural', type: '单复数',
    run: function (text) {
      var out = [];
      if (!hasLex()) return out;
      var re = /\bmany\s+([a-z]+)\b/gi;
      var m;
      while ((m = re.exec(text)) !== null) {
        var w = m[1];
        if (UNCUNC(w)) continue;
        if (!isCountableNoun(w)) continue;
        out.push({
          offset: m.index + m[0].indexOf(w), len: w.length,
          msg: 'many 后面要用复数：' + w, fix: pluralOf(w)
        });
      }
      return out;
    }
  });
  function pluralOf(w) {
    if (/(s|x|z|ch|sh)$/.test(w)) return w + 'es';
    if (/[^aeiou]y$/.test(w)) return w.slice(0, -1) + 'ies';
    return w + 's';
  }

  /* ---- R5 时态：since/for + 年份，主句却用了过去时（没走完成时） ---- */
  addRule({
    id: 'R5_tense', type: '时态',
    run: function (text) {
      var out = [];
      var re = /\b(since|for)\s+((?:19|20)\d{2})\b/gi;
      var m;
      while ((m = re.exec(text)) !== null) {
        var tail = text.slice(m.index, m.index + 120);
        if (/\b(has|have)\b/i.test(tail)) continue;       // 完成时，没问题
        if (!/\b(was|were|did)\b/i.test(tail) && !/\b\w+ed\b/i.test(tail)) continue;
        out.push({
          offset: m.index, len: m[0].length,
          msg: '「' + m[1] + ' ' + m[2] + '」到今天为止 ⇒ 常用现在完成时',
          fix: 'has/have + 过去分词'
        });
        break;   // 一句只报一次，别刷屏
      }
      return out;
    }
  });

  /* ---- R6 动词形式：情态动词后用了过去式/三单 ---- */
  addRule({
    id: 'R6_modal_form', type: '动词形式',
    run: function (text) {
      var out = [];
      var re = new RegExp('\\b(' + AUX.join('|') + ')\\s+([a-z]+)\\b', 'gi');
      var m;
      while ((m = re.exec(text)) !== null) {
        var w = m[2];
        var base = null;
        if (/s$/.test(w) && w.length > 2) base = w.slice(0, -1);
        else if (/ed$/.test(w) && w.length > 3) base = w.slice(0, -2);
        else if (IRREGULAR.indexOf(w) >= 0) base = null;
        if (!base) continue;
        if (!isVerbLemma(base)) continue;      // 不是我们认识的动词原形 ⇒ 不报
        out.push({
          offset: m.index + m[0].indexOf(w), len: w.length,
          msg: '情态动词 ' + m[1] + ' 后面要用动词原形',
          fix: m[1] + ' ' + base
        });
      }
      return out;
    }
  });

  /* ---- R7 介词搭配 ---- */
  addRule({
    id: 'R7_preposition', type: '介词搭配',
    run: function (text) {
      var out = [];
      for (var key in PREP_PAIRS) {
        if (!PREP_PAIRS.hasOwnProperty(key)) continue;
        var allowed = PREP_PAIRS[key];
        var re = new RegExp('\\b(' + key.replace(/ /g, '\\s+') + ')\\s+(' + PREP_WORDS.join('|') + ')\\b', 'gi');
        var m;
        while ((m = re.exec(text)) !== null) {
          var p = m[2].toLowerCase();
          if (allowed.indexOf(p) >= 0) continue;
          out.push({
            offset: m.index + m[0].lastIndexOf(m[2]), len: m[2].length,
            msg: '「' + key + '」后面应该用 ' + allowed.join(' / '),
            fix: key + ' ' + allowed[0]
          });
        }
      }
      return out;
    }
  });

  /* ---- R8 重复用词 ---- */
  addRule({
    id: 'R8_dup', type: '重复用词',
    run: function (text) {
      var out = [];
      var re = /\b([a-z]{2,})\s+\1\b/gi;
      var m;
      while ((m = re.exec(text)) !== null) {
        out.push({
          offset: m.index, len: m[0].length,
          msg: '「' + m[1] + '」重复了', fix: m[1]
        });
      }
      return out;
    }
  });

  /* ---- R9 拼写 ---- */
  addRule({
    id: 'R9_spelling', type: '拼写',
    run: function (text) {
      var out = [];
      var re = /\b[A-Za-z]+\b/g;
      var m;
      while ((m = re.exec(text)) !== null) {
        var w = m[0];
        var fix = MISSPELL[w.toLowerCase()];
        if (!fix) continue;
        out.push({
          offset: m.index, len: w.length,
          msg: '「' + w + '」拼错了', fix: fix
        });
      }
      return out;
    }
  });

  /* ---- R10 中式表达 ---- */
  addRule({
    id: 'R10_chinglish', type: '中式表达',
    run: function (text) {
      var out = [];
      for (var i = 0; i < CHINGLISH.length; i++) {
        var c = CHINGLISH[i];
        // ⚠️ 必须保留 g 标志：exec 靠 lastIndex 推进，去掉 g 会永远匹配同一处 ⇒ 死循环
        var re = new RegExp(c.re.source, c.re.flags.indexOf('g') >= 0 ? c.re.flags : c.re.flags + 'g');
        var m;
        while ((m = re.exec(text)) !== null) {
          out.push({
            offset: m.index, len: m[0].length,
            msg: c.msg, fix: c.fix
          });
          if (m[0].length === 0) re.lastIndex++;   // 防零宽匹配死循环
        }
      }
      return out;
    }
  });

  /* ---- R11 超纲用词（需要分级词库；没有就不跑） ---- */
  addRule({
    id: 'R11_beyond', type: '超纲用词',
    run: function (text) {
      var out = [];
      if (!global.CET4Levels || !global.CET4Levels.levelOf) return out;
      var re = /\b[A-Za-z][A-Za-z'-]{2,}\b/g;
      var m;
      while ((m = re.exec(text)) !== null) {
        var w = m[0].toLowerCase();
        if (!global.CET4Levels.isOutline(w)) continue;
        var lv = global.CET4Levels.levelOf(w);
        if (lv === 'beyond') {
          out.push({ offset: m.index, len: m[0].length, msg: '「' + m[0] + '」不在四级大纲词汇里', fix: '换成四级词汇' });
        }
      }
      return out;
    }
  });

  /* =====================================================================
     对外接口
     ===================================================================== */
  function snippet(text, offset, len) {
    var s = Math.max(0, offset - 18);
    var e = Math.min(text.length, offset + len + 18);
    return (s > 0 ? '…' : '') + text.slice(s, e).replace(/\s+/g, ' ') + (e < text.length ? '…' : '');
  }

  /**
   * @param {string} text 学生译文
   * @param {object} opts { rules: ['R1','R2'] 只跑指定规则 }
   * @returns {{errors:Array, types:Object, typeList:Array, counts:Object}}
   */
  function check(text, opts) {
    opts = opts || {};
    text = String(text || '');
    var only = opts.rules || null;
    var errors = [];
    for (var i = 0; i < RULES.length; i++) {
      var r = RULES[i];
      if (only && only.indexOf(r.id) < 0) continue;
      var got;
      try { got = r.run(text); } catch (e) { got = []; }
      for (var j = 0; j < (got || []).length; j++) {
        var g = got[j];
        g.type = r.type;
        g.rule = r.id;
        g.snippet = snippet(text, g.offset, g.len);
        errors.push(g);
      }
    }
    // 排序：按出现位置 ⇒ 学生看到的顺序跟句子一致
    errors.sort(function (a, b) { return a.offset - b.offset; });

    var types = {};
    for (var k = 0; k < errors.length; k++) {
      types[errors[k].type] = (types[errors[k].type] || 0) + 1;
    }
    var typeList = Object.keys(types).sort(function (a, b) {
      return types[b] - types[a] || a.localeCompare(b);
    });
    return { errors: errors, types: types, typeList: typeList, total: errors.length };
  }

  global.CET4Grammar = {
    TYPES: TYPES,
    rules: RULES,
    setLexicon: setLexicon,
    hasLex: hasLex,
    check: check,
    MISSPELL: MISSPELL
  };
})(typeof window !== 'undefined' ? window : globalThis);
