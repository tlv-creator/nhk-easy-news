/*!
 * AI 讲解客户端 · 独立无依赖版
 * ------------------------------------------------------------------
 * 用途：任何「日中对照精读 App」都能直接接 CloudBase AI 讲解中转。
 *
 *   <script src="ai-client.js"></script>
 *   AIC.config({ endpoint: 'https://xxx.service.tcloudbase.com/ai' });
 *   AIC.config({ readingLookup: function (word) { return 'よる'; } });   // 可选：假名查表
 *
 *   var text = await AIC.ask({
 *     scene:'explain_sentence', title:'若草物語', episode:'第1话',
 *     ja:'昨日の夜は寒かった。', zh:'昨晚很冷。',
 *     prev:[{ja:'',zh:''}], next:[], word:'', refs:[{ja:'',zh:''}]
 *   }, { onTick: function (sec) { el.textContent = '思考中… 已用 ' + sec + ' 秒'; } });
 *
 *   el.innerHTML = AIC.md(text);          // markdown → HTML（含表格转行式）
 *   el.innerHTML = AIC.fixReading(html);  // 用 App 查表覆盖模型假名（可选）
 *
 * 契约（字段名不可改）：
 *   POST {endpoint}
 *   body {scene,title,episode,ja,zh,prev[],next[],word,refs[]}
 *   → 200 {"ok":true,"text":"markdown 全文"}
 *   → 失败 {"ok":false,"error":"not_configured|unauthorized|too_many|quota_exceeded|bad_request|timeout|upstream_params|upstream_error|upstream_auth"}
 *
 * 🔴 硬性要求（踩过的坑）：
 *   1. 不做流式 —— 墨水屏逐字刷新会疯狂重绘闪烁、耗电。等结果一次性渲染。
 *   2. 超时 75 秒（中转侧 40 秒兜底）。
 *   3. 注音一律以 App 本地查表为准，模型括号假名只作 fallback —— 实测模型会把「昨日」标成「あさ?」。
 *   4. 切段时「标题必须整行相等」—— 表格表头含「语法」二字会被误判成标题，导致逐词段整段为空。
 *   5. 送模型的 refs 必须是干净台词：行首说话人括号保留、行中读音括号删掉、日文后粘连的英文要拆开。
 *
 * 无外部依赖，零构建，扔进 <script> 就能用。
 */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* 配置                                                                */
  /* ------------------------------------------------------------------ */
  var CFG = {
    endpoint: '',
    timeout: 75000,
    /* 与中转完全一致 —— 「复制提问」和「问 AI」两个模式共用同一份上下文，
       所以两个模式的输出风格才会一致。改这里 = 两边一起改。 */
                    systemPrompt:
      '【角色】\n' +
      '你是日语老师，给中文母语的学习者讲日语。讲解用中文；日语语法术语第一次出现时写成「中文（日本語用語）」双写。\n' +
      '\n' +
      '【任务】\n' +
      '解析用户给你的一句日语台词（可能附带上下文）。严格按下面固定结构输出：不要寒暄、不要复述题目、不要用 emoji、不要自由发挥。\n' +
      '\n' +
      '【输出结构（顺序固定）】\n' +
      '1. 原文：日语文本，汉字后用括号注假名（例：元気（げんき））\n' +
      '2. 中文翻译\n' +
      '3. 句子结构剖析\n' +
      '\n' +
      '【句子结构剖析的固定写法】\n' +
      '3.1 整体大框架：只写一行，成分用字母标记（从 A 开始按出现顺序），被包住的部分用【】括起来。\n' +
      '    格式：A は 【B】 言っています\n' +
      '3.2 成分展开：每个成分写两行，格式固定\n' +
      '    A：<该成分原文，逐字取自原句>\n' +
      '    A 说明：<它是什么 + 词性/语法功能 + 在这句里跟谁是什么关系>\n' +
      '    （可选）这个成分在本剧有对应的真实台词时，紧跟着一行写：\n' +
      '    例句：<逐字取自上下文的本剧台词原文>\n' +
      '3.3 成分内部骨架：只有当某个成分内部还有结构（修饰成分、并列、介词短语、补助动词等）时才写\n' +
      '    B 内部骨架：X に Y ように\n' +
      '    X = <原文>\n' +
      '    X 说明：<说明>\n' +
      '    に说明：<助词单独解释：接在谁后面、提示什么、全句有几处这种>\n' +
      '    Y 说明：<说明>\n' +
      '    ように说明：<说明>\n' +
      '3.4 主句谓语：谓语如果已经是框架里的某个字母位，就不用单独写；否则单独写\n' +
      '    主句谓语：<原文>\n' +
      '    说明：<说明>\n' +
      '\n' +
      '【硬性规则】\n' +
      '- 成分原文必须逐字取自原句，不许改写、不许换成罗马音\n' +
      '- 内部骨架里的助词（如 に / は / を / ので）要单独拎出来解释，一行一个\n' +
      '- 说明要讲「这个成分在这句里的具体作用」，不要只抄词典释义；每条 1~2 句\n' +
      '- 框架里没有的成分不要硬凑；句子简单就少拆几层\n' +
      '- 例句是**可选**的：只在能拿到能体现该要点的**本剧真实台词**时才写；拿不到就**整行不写**\n' +
      '      - 一旦要写，必须逐字取自上下文（refs / prev / next 里原样搬运），一个字都别改\n' +
      '      - 不许编造、不许改写、不许把当前句重复当例句\n' +
      '      - 引用例句时只写日文原文，不要加括号注音、不要加下划线、不要加英文残留\n' +
      '- 不需要罗马音\n' +
      '- 不确定的地方直说不确定，不要编造语法术语或读音\n' +
      '- 假名标注只作参考，App 端有标准注音时以标准注音为准'
  };

  /* 错误码 → 用户看得懂的文案（墨水屏上要短） */
  var ERR_TEXT = {
    not_configured: 'AI 还没配置好',
    unauthorized: 'AI 未配置',
    too_many: '今天用得有点多，稍后再试',
    quota_exceeded: 'AI 额度用完了，等一会儿再来',
    bad_request: '请求内容有问题',
    timeout: '網絡失敗，请重试',
    upstream_params: 'AI 暫時不可用',
    upstream_error: 'AI 暫時不可用',
    upstream_auth: 'AI 暫時不可用',
    server_error: 'AI 暫時不可用',
    network: '網絡失敗，请重试'
  };

  /* ------------------------------------------------------------------ */
  /* 小工具                                                              */
  /* ------------------------------------------------------------------ */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function mkErr(code, msg) {
    var e = new Error(msg || code);
    e.aiCode = code;
    return e;
  }

  function errText(code) {
    return ERR_TEXT[code] || 'AI 暫時不可用';
  }

  /* ------------------------------------------------------------------ */
  /* 一、组请求体（契约）                                                  */
  /* ------------------------------------------------------------------ */
  function normList(list, max) {
    if (!Array || !list || !list.length) return [];
    var out = [];
    for (var i = 0; i < list.length && out.length < max; i++) {
      var x = list[i] || {};
      var ja = String(x.ja || x.jp || x.text || '').trim();
      if (!ja) continue;
      out.push({ ja: ja, zh: String(x.zh || x.zhs || x.zhHans || '').trim() });
    }
    return out;
  }

  function buildPayload(o) {
    o = o || {};
    return {
      scene: o.scene || 'explain_sentence',
      title: String(o.title || ''),
      episode: String(o.episode || ''),
      ja: String(o.ja || ''),
      zh: String(o.zh || ''),
      prev: normList(o.prev, 2),
      next: normList(o.next, 2),
      word: String(o.word || ''),
      refs: normList(o.refs, 4)
    };
  }

  /* ------------------------------------------------------------------ */
  /* 二、发送请求（不流式 + 每秒心跳 + 75 秒超时）                          */
  /* ------------------------------------------------------------------ */
  function ask(payload, opts) {
    opts = opts || {};
    if (!CFG.endpoint) return Promise.reject(mkErr('not_configured', 'endpoint empty'));

    var started = Date.now();
    var tick = null;
    if (opts.onTick) {
      try { opts.onTick(0); } catch (e) {}
      tick = setInterval(function () {
        try { opts.onTick(Math.floor((Date.now() - started) / 1000)); } catch (e) {}
      }, 1000);
    }

    var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) { try { ctrl.abort(); } catch (e) {} } }, CFG.timeout);

    var init = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    };
    if (ctrl) init.signal = ctrl.signal;

    function done(fn, arg) {
      clearTimeout(timer);
      if (tick) clearInterval(tick);
      fn(arg);
    }

    return fetch(CFG.endpoint, init).then(function (res) {
      return res.text().then(function (raw) {
        var j = null;
        try { j = JSON.parse(raw); } catch (e) {}
        if (!j) throw mkErr('network', 'bad response: ' + String(raw).slice(0, 120));
        if (j.ok && j.text) return j.text;
        var code = j.error || 'upstream_error';
        throw mkErr(code, code);
      });
    }).catch(function (e) {
      if (e && e.aiCode) throw e;
      if (e && e.name === 'AbortError') throw mkErr('timeout', 'timeout');
      throw mkErr('network', (e && e.message) || 'network');
    }).then(function (v) { done(function () {}); return v; },
             function (err) { done(function () {}); throw err; });
  }

  /* ------------------------------------------------------------------ */
  /* 二·B、额度查询（真信号，不烧模型额度）                                  */
  /*   GET {endpoint}/quota                                                 */
  /*   → {ok:true, quota:{interval_percent, weekly_percent,                 */
  /*                    interval_reset_in_ms, weekly_reset_in_ms}}           */
  /*   interval = 5 小时窗口剩余 %；weekly = 周窗口剩余 %                   */
  /* ------------------------------------------------------------------ */
  function quota() {
    if (!CFG.endpoint) return Promise.reject(mkErr('not_configured', 'endpoint empty'));
    var url = CFG.endpoint.replace(/\/+$/, '') + '/quota';
    var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) { try { ctrl.abort(); } catch (e) {} } }, 15000);
    var init = { method: 'GET', headers: {} };
    if (ctrl) init.signal = ctrl.signal;
    return fetch(url, init).then(function (res) {
      return res.text().then(function (raw) {
        var j = null;
        try { j = JSON.parse(raw); } catch (e) {}
        if (!j) throw mkErr('network', 'bad quota response');
        if (j.ok && j.quota) return j.quota;
        throw mkErr(j.error || 'network', 'quota failed');
      });
    }).catch(function (e) {
      if (e && e.aiCode) throw e;
      if (e && e.name === 'AbortError') throw mkErr('timeout', 'quota timeout');
      throw mkErr('network', (e && e.message) || 'network');
    }).then(function (v) { clearTimeout(timer); return v; },
             function (err) { clearTimeout(timer); throw err; });
  }

  /* 把额度翻成一句人话（墨水屏上要短） */
  function quotaText(q) {
    if (!q) return '';
    var ip = Number(q.interval_percent), wp = Number(q.weekly_percent);
    var ir = Number(q.interval_reset_in_ms || 0), wr = Number(q.weekly_reset_in_ms || 0);
    function hrs(ms) { return ms > 0 ? (ms / 3600000) : 0; }
    var parts = [];
    if (wp <= 25) {
      parts.push('本週額度剩 ' + wp + '%');
      if (wr > 0) parts.push(Math.round(wr / 86400000 * 10) / 10 + ' 天後回滿');
    } else if (ip >= 50) {
      parts.push('AI 額度充足');
      if (ir > 0) parts.push(Math.round(hrs(ir) * 10) / 10 + ' 小時後回滿');
    } else {
      parts.push('AI 額度剩 ' + ip + '%');
      if (ir > 0) parts.push(Math.round(hrs(ir) * 10) / 10 + ' 小時後回滿');
    }
    if (wp > 25 && wp < 60 && ip >= 50) parts.push('本週剩 ' + wp + '%');
    return parts.join(' · ');
  }

  /* ------------------------------------------------------------------ */
  /* 三、markdown → HTML                                                  */
  /*    · 表格 → 行式（墨水屏上表格没法看）                                */
  /*    · ``` 围栏 → <pre>                                                */
  /*    · 硬换行（行尾两空格）→ <br>                                       */
  /*    · 标题判定：必须是「整行相等」的结构，不能被表格表头误伤                */
  /* ------------------------------------------------------------------ */
  var HEAD_NUM = /^\s*(?:[①②③④⑤⑥]|\d{1,2})\s*[.、)．:：]?\s*/;

  function isHeadingLine(line) {
    var t = line.trim();
    if (!t) return false;
    if (t.charAt(0) === '|') return false;               /* 表格行永远不是标题 */
    if (t.charAt(0) === '>' || t.charAt(0) === '#') return false;
    if (!HEAD_NUM.test(t)) return false;
    var rest = t.replace(HEAD_NUM, '').trim();
    if (!rest) return true;
    /* 「1. 原文：xxx」→ 标题名是「原文」，冒号后是内容 → 整体算标题行 */
    var m = rest.match(/^([^：:]{1,14})[：:](.*)$/);
    if (m) return true;
    /* 光秃秃一个短词（≤ 14 字）且没有句号 → 标题 */
    if (rest.length <= 14 && !/[。．.!?！？]$/.test(rest)) return true;
    return false;
  }

  function splitHead(line) {
    var t = line.trim();
    var rest = t.replace(HEAD_NUM, '').trim();
    var m = rest.match(/^([^：:]{1,14})[：:](.*)$/);
    if (m) return { head: m[1].trim(), body: m[2].trim() };
    return { head: rest, body: '' };
  }

  /* 表格 → 行式：`| 日语 | 中文 | 词性 |` → `日语 → 中文 → 词性` */
  function tableToRows(block) {
    var rows = [];
    for (var i = 0; i < block.length; i++) {
      var t = block[i].trim();
      if (!t) continue;
      if (/^\|?[\s:\-|]+\|?$/.test(t) && /-/.test(t)) continue;   /* 分隔行 */
      var cells = t.replace(/^\|/, '').replace(/\|$/, '').split('|');
      var vals = [];
      for (var j = 0; j < cells.length; j++) {
        var c = cells[j].trim();
        if (c) vals.push(c);
      }
      if (vals.length) rows.push(vals);
    }
    if (!rows.length) return '';
    var out = [];
    /* 第一行当表头（如果有 ≥2 行且第二行像表头） */
    var head = rows[0];
    var body = rows.slice(1);
    if (body.length && head.length >= 2) {
      out.push('<div class="ai-thead">' + esc(head.join(' · ')) + '</div>');
      for (var k = 0; k < body.length; k++) {
        out.push('<div class="ai-trow">' + inline(body[k].join(' → ')) + '</div>');
      }
    } else {
      for (var m2 = 0; m2 < rows.length; m2++) {
        out.push('<div class="ai-trow">' + inline(rows[m2].join(' → ')) + '</div>');
      }
    }
    return out.join('\n');
  }

  function inline(s) {
    var t = esc(s);
    t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    t = t.replace(/「([^」]*)」/g, '「$1」');
    t = t.replace(/【([^】]*)】/g, '<span class="ai-key">【$1】</span>');
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    return t;
  }

  /* ------------------------------------------------------------------ */
  /* 模型输出的「残缺标记」清洗                                            */
  /*                                                                      */
  /* 实测（Shun 真接口 2026-10-22，原始 markdown 里就有，不是渲染器弄坏）：*/
  /*   - 】：原句中的「か」属于疑问表达…                                   */
  /*   ↑ 行首只有一个孤立的右括号 + 冒号 —— 模型想写「【注】：」之类的标签，  */
  /*     结果只吐出了右半边。                                              */
  /*                                                                      */
  /* 规则：**行首**（可带列表符）出现「只有右括号 / 空括号对」+ 冒号 → 删掉。 */
  /* 绝不动行内的正常括号：                                                */
  /*   ・（助词）…  ・【重点】…  ・「する」…  ・主格助词（主格助詞・ wa）…    */
  /* ------------------------------------------------------------------ */
  var ORPHAN_HEAD_RE = /^((\s*)(?:[-*•]\s+)?)((?:[】\]\)）]|【】|\[\]|〔〕)+)\s*[:：]\s*/;

  function cleanOrphan(text) {
    return String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n').map(function (line) {
      var m = line.match(ORPHAN_HEAD_RE);
      if (!m) return line;
      return m[1] + m[2] + line.slice(m[0].length);
    }).join('\n');
  }

  /* ------------------------------------------------------------------ */
  /* 模型把假名标崩：括号里混进了非假名内容                                */
  /*   实测（Tadoku 真接口 2026-10-22，绘本第 1 句）：                      */
  /*     逐词拆解：緑茶（りょ在国际这里指"绿茶"）を：助词…                   */
  /*     固定搭配：绿茶→「緑茶（りょ-cha tea）」                            */
  /*   括号里**不是纯假名** → 这不是注音，是模型崩了 → 整组括号剥掉。       */
  /*                                                                      */
  /* 保留：纯假名（げんき）· 纯汉字（绿茶）· 含数字（31.2度）                */
  /* 剥除：含 ASCII 字母 / 直引号 —— 模型漏出来的英文或乱码                 */
  /* （行内出现 `東京（Tokyo）` 这种罗马音也会被剥掉：按 prompt「不用罗马音」 */
  /*   本来就不该出现，而且「英語：名，意为英语」剥掉后照样读得懂。）        */
  /* ------------------------------------------------------------------ */
  var WORD_PAREN_RE = /([぀-ゟ゠-ヿ一-鿿々〆ヵヶ]+)\s*[（(]([^（()）]{1,60})[）)]/g;
  var GARBAGE_IN_PAREN_RE = /[A-Za-z"'`]/;

  function cleanBadParen(text) {
    return String(text == null ? '' : text).replace(WORD_PAREN_RE, function (m, w, inner) {
      if (GARBAGE_IN_PAREN_RE.test(inner)) return w;   /* 崩了 → 剥掉整组括号 */
      return m;
    });
  }

  /* ------------------------------------------------------------------ */
  /* 「句子结构剖析」渲染器（新答题模式，2026-10-22）                        */
  /*                                                                      */
  /* 模型输出固定四小节：                                                  */
  /*   3.1 整体大框架：A は 【B】 に 【C】 に行きます                        */
  /*   3.2 成分展开：  A：<原文> / A 说明：<…>                             */
  /*   3.3 成分内部骨架：X = <原文> / X 说明：<…> / に说明：<…>              */
  /*   3.4 主句谓语：<原文> / 说明：<…>                                     */
  /*                                                                      */
  /* ⚠️ 说明行的前缀**不限于大写字母** —— 模型会写「に说明：」「ように说明：」*/
  /*   （助词本身是假名）→ 所以用「任意短前缀 + 说明」来匹配。              */
  /* 本函数与 hub 的 shell/ai.js 里的同名实现**逐字同源**（行为对拍用）。   */
  /* ------------------------------------------------------------------ */
  /* ------------------------------------------------------------------ */
  /* 内部骨架行预清洗（2026-10-22，NHK 真接口实测踩到）                     */
  /*                                                                      */
  /* 模型的「成分内部骨架」行会手滑吐英文垃圾，而且常「先吐一版错的、      */
  /* 再吐一版对的」：                                                       */
  /*   C 内部骨架：X で Y ickle?                             ← 垃圾        */
  /*   C 内部骨架：今までで いちばん 遅い 真夏日 になりました  ← 正确        */
  /*                                                                      */
  /* 规则 A：内部骨架行**正文**里出现连续 ≥2 个 ASCII 字母 → 整行丢弃     */
  /*   合法内容都不含 ≥2 位连续字母：                                       */
  /*     X や Y も / X になって / 今までで いちばん 遅い 真夏日 になりました */
  /*   （单字母 X / Y / Z 是占位符，不算；数字如 30℃ 不算）                */
  /*                                                                      */
  /* 规则 B：同一个 tag 的「内部骨架」行只保留**最后一次**出现              */
  /*   （先做 A 再做 B —— A 已经把垃圾行清掉了，B 留的就是最后那版对的内容）*/
  /* ------------------------------------------------------------------ */
  var STRUCT_TAG_RE = /^([A-Z]{1,3})\s*内部骨架\s*[：:](.*)$/;

  function stripStructNo(line) {
    return String(line == null ? '' : line)
      .replace(/^\s+|\s+$/g, '')
      .replace(/^\s*\d{1,2}\.\d{1,2}\s*/, '');
  }

  function cleanStructLines(lines) {
    var arr = (lines || []).map(function (l) { return l == null ? '' : String(l); });
    var i, b, m;
    /* 规则 A：英文垃圾行整行丢 */
    for (i = 0; i < arr.length; i++) {
      b = stripStructNo(arr[i]);
      m = b.match(STRUCT_TAG_RE);
      if (m && /[A-Za-z]{2,}/.test(m[2])) arr[i] = '';
    }
    /* 规则 B：同 tag 只留最后一次 */
    var last = {};
    for (i = 0; i < arr.length; i++) {
      b = stripStructNo(arr[i]);
      m = b.match(STRUCT_TAG_RE);
      if (m) last[m[1]] = i;
    }
    for (i = 0; i < arr.length; i++) {
      b = stripStructNo(arr[i]);
      m = b.match(STRUCT_TAG_RE);
      if (m && last[m[1]] !== i) arr[i] = '';
    }
    return arr;
  }

  function aiStruct(text) {
    var raw = String(text || '').trim();
    if (!raw) return '';
    raw = cleanBadParen(cleanOrphan(raw));
    var lines = cleanStructLines(raw.split(/\r?\n/));
    var out = '';
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i].replace(/^\s+|\s+$/g, '');
      if (!ln) { continue; }
      /* 🔴 剥掉小节编号（3.1 / 3.4 …）—— 不剥的话
       *   「3.4 主句谓语：…」匹配不上谓语规则、「3.2 成分展开：」会被当普通行。 */
      var body = ln.replace(/^\s*\d{1,2}\.\d{1,2}\s*/, '');
      var e = esc(body);
      /* ① 整体大框架：含【】且不是「说明」行 → 高亮框 */
      if (/【[^】]+】/.test(body) && !/说明[：:]/.test(body)) {
        out += '<div class="ai-frame">' + e + '</div>';
        continue;
      }
      /* ② 成分标题行：「A：」「B 内部骨架：」→ 标签样式 */
      var m1 = body.match(/^([A-Z]{1,3})\s*(内部骨架)?\s*[：:](.*)$/);
      if (m1) {
        var tag = m1[1] + (m1[2] ? ' 内部骨架' : '');
        out += '<div class="ai-comp"><span class="ai-tag">' + esc(tag) + '</span>' +
          (m1[3] ? esc(m1[3].replace(/^\s+/, '')) : '') + '</div>';
        continue;
      }
      /* ③ 「X = 原文」→ 成分原文行
       *    🔴 前缀**不限于大写字母** —— 模型会写「毎朝 = 毎朝」「六時 = 六時」
       *       （汉字/假名成分名），只认 [A-Z] 会掉进兜底、丢掉等号行的样式。 */
      var m2 = body.match(/^([^=：:]{1,10}?)\s*=\s*(.+)$/);
      if (m2) {
        out += '<div class="ai-eq"><span class="ai-tag">' + esc(m2[1].trim()) + '</span>' +
          '<span class="ai-eq-sign">=</span>' + esc(m2[2]) + '</div>';
        continue;
      }
      /* ④ 「X 说明：…」→ 说明行（缩进 + 灰字）
       *    ⚠️ 前缀**不限于大写字母** —— 模型会写「に说明：」「ように说明：」
       *       （助词本身是假名），所以用「任意短前缀 + 说明」来匹配。 */
      var m3 = body.match(/^([^：:]{1,12}?)\s*说明[：:]\s*(.*)$/);
      if (m3) {
        var pre = m3[1].trim();
        out += '<div class="ai-note">' +
          (pre ? '<span class="ai-tag">' + esc(pre) + '</span>' : '') +
          '<span class="ai-note-l">说明</span>' + esc(m3[2]) + '</div>';
        continue;
      }
      /* ⑤ 「主句谓语：…」/「谓语：…」→ 谓语块 */
      var m4 = body.match(/^(主句谓语|谓语)\s*[：:]\s*(.*)$/);
      if (m4) {
        out += '<div class="ai-comp ai-predicate"><span class="ai-tag">' + esc(m4[1]) +
          '</span>' + esc(m4[2]) + '</div>';
        continue;
      }
      if (/^说明[：:]\s*(.*)$/.test(body)) {
        out += '<div class="ai-note"><span class="ai-note-l">说明</span>' +
          esc(body.replace(/^说明[：:]\s*/, '')) + '</div>';
        continue;
      }
      /* ⑥ 兜底：普通行 */
      out += '<div class="ai-line">' + e + '</div>';
    }
    return out;
  }

  function md(src) {
    var lines = cleanBadParen(cleanOrphan(src)).replace(/\r\n?/g, '\n').split('\n');
    var out = [], i;
    var codeBuf = null, codeLang = '';
    var tableBuf = [];
    /* 「句子结构剖析」段的正文行（整段交给 aiStruct） */
    var structBuf = null, structNum = -1;

    function flushStruct() {
      if (!structBuf) return;
      out.push('<div class="ai-struct">' + aiStruct(structBuf.join('\n')) + '</div>');
      structBuf = null;
    }

    function flushTable() {
      if (!tableBuf.length) return;
      out.push(tableToRows(tableBuf));
      tableBuf = [];
    }
    function flushCode() {
      if (codeBuf === null) return;
      out.push('<pre class="ai-code">' + esc(codeBuf.join('\n')) + '</pre>');
      codeBuf = null; codeLang = '';
    }

    for (i = 0; i < lines.length; i++) {
      var line = lines[i];

      /* ---- 「句子结构剖析」段：整段收进 structBuf，最后交给 aiStruct ---- */
      if (structBuf) {
        /* 遇到**别的顶层段**（如 4. 语法说明）→ 本段结束。
         * ⚠️ 必须排除 3.1 / 3.2 这类子编号 —— 后面紧跟数字才算子编号。 */
        var tm = line.match(/^\s*(\d{1,2})\s*[.、)．]\s*([^\d].*)$/);
        if (tm && parseInt(tm[1], 10) !== structNum) {
          flushStruct();
        } else {
          structBuf.push(line);
          continue;
        }
      }

      /* ``` 围栏 */
      var fence = line.match(/^\s*```\s*([A-Za-z0-9+#-]*)\s*$/);
      if (fence) {
        if (codeBuf === null) { flushTable(); codeBuf = []; codeLang = fence[1] || ''; }
        else { flushCode(); }
        continue;
      }
      if (codeBuf !== null) { codeBuf.push(line); continue; }

      /* 表格行 */
      if (/^\s*\|.*\|\s*$/.test(line)) { tableBuf.push(line); continue; }
      flushTable();

      /* 空行 */
      if (!line.trim()) { out.push(''); continue; }

      /* 「句子结构剖析」段的开头（编号可有可无，带不带冒号都认） */
      var sm = line.match(/^\s*(?:[0-9]{1,2}\s*[.、)．]\s*)?(?:[【\[]\s*)?句子结构剖析\s*(?:\s*[】\]])?\s*[：:]?\s*(.*)$/);
      if (sm) {
        out.push('<h4 class="ai-h">句子结构剖析</h4>');
        var nm2 = line.match(/^\s*(\d{1,2})\s*[.、)．]/);
        structNum = nm2 ? parseInt(nm2[1], 10) : 3;
        structBuf = [];
        if (sm[1]) structBuf.push(sm[1]);
        continue;
      }

      /* 标题 */
      if (isHeadingLine(line)) {
        var h = splitHead(line);
        if (h.body) {
          out.push('<h4 class="ai-h">' + esc(h.head) + '</h4>');
          out.push('<p class="ai-lead">' + inline(h.body) + '</p>');
        } else {
          out.push('<h4 class="ai-h">' + esc(h.head) + '</h4>');
        }
        continue;
      }

      /* 无序列表 */
      var ul = line.match(/^(\s*)[-*•]\s+(.*)$/);
      if (ul) {
        out.push('<div class="ai-li">' + inline(ul[2].replace(/\s{2,}$/, '')) + '</div>');
        continue;
      }

      /* 缩进树（└─ / → / ・） */
      var tree = line.match(/^(\s*)([└├│]*\s*[→\-–—]\s*|[└├│]\s*)(.*)$/);
      if (tree && tree[3]) {
        var ind = tree[1].replace(/[^\s]/g, '');
        out.push('<div class="ai-tree" style="padding-left:' + (ind.length * 8) + 'px">' + inline(tree[3]) + '</div>');
        continue;
      }

      /* 普通段落（行尾两空格 = markdown 硬换行） */
      var hard = /\s{2,}$/.test(line);
      out.push('<p class="ai-p">' + inline(line.replace(/\s{2,}$/, '')) + (hard ? '<br>' : '') + '</p>');
    }
    flushTable();
    flushCode();
    flushStruct();
    return out.join('\n');
  }

  /* ------------------------------------------------------------------ */
  /* 四、假名纠正：用 App 本地查表覆盖模型括号假名                            */
  /*    ⚠️ 词里夹假名（「女の子」）整词查不到表 → 保留模型的（fallback）      */
  /* ------------------------------------------------------------------ */
  var READING_RE = /([぀-ゟ゠-ヿ一-鿿々〆ヵヶ]+)\s*[（(]\s*([ぁ-んァ-ヶー・]+)\s*[）)]/g;

  function fixReading(html, lookup) {
    lookup = lookup || CFG.readingLookup;
    if (!lookup || !html) return html;
    return String(html).replace(READING_RE, function (m, word, kana) {
      var r = null;
      try { r = lookup(word); } catch (e) { r = null; }
      if (!r) return m;                       /* 查不到 → 保留模型的 */
      return word + '（' + r + '）';
    });
  }

  /* ------------------------------------------------------------------ */
  /* 五、降级：复制提问（endpoint 为空，或请求失败时的兜底）                  */
  /* ------------------------------------------------------------------ */
  function buildPrompt(p) {
    var L = [];
    L.push('【角色】' + CFG.systemPrompt.split('\n\n')[0].replace(/^【角色】/, ''));
    L.push('请解析下面这句日语台词，严格按五段结构输出（①原文 ②中文翻译 ③逐词拆解 ④语法说明 ⑤整句结构框架）。');
    L.push('');
    L.push('【课文】' + (p.title || '') + (p.episode ? ' · ' + p.episode : ''));
    if (p.ja) L.push('【当前句】' + p.ja + (p.zh ? '（' + p.zh + '）' : ''));
    if (p.word) L.push('【本次要重点讲解的词】' + p.word);
    if (p.prev && p.prev.length) {
      L.push('【上文】');
      for (var i = 0; i < p.prev.length; i++) L.push('  ' + p.prev[i].ja + (p.prev[i].zh ? '（' + p.prev[i].zh + '）' : ''));
    }
    if (p.next && p.next.length) {
      L.push('【下文】');
      for (var j = 0; j < p.next.length; j++) L.push('  ' + p.next[j].ja + (p.next[j].zh ? '（' + p.next[j].zh + '）' : ''));
    }
    if (p.refs && p.refs.length) {
      L.push('【本剧真实台词（造例句用）】');
      for (var k = 0; k < p.refs.length; k++) L.push('  ' + p.refs[k].ja + (p.refs[k].zh ? '（' + p.refs[k].zh + '）' : ''));
    }
    return L.join('\n');
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(ta);
        ok ? resolve() : reject(new Error('copy failed'));
      } catch (e) { reject(e); }
    });
  }

  /* ------------------------------------------------------------------ */
  /* 六、对外 API                                                         */
  /* ------------------------------------------------------------------ */
  var AIC = {
    version: '1.2.0',
    CFG: CFG,
    ERR_TEXT: ERR_TEXT,

    /* config({endpoint, readingLookup, timeout}) */
    config: function (o) {
      o = o || {};
      if (o.endpoint !== undefined) CFG.endpoint = String(o.endpoint || '');
      if (o.timeout) CFG.timeout = parseInt(o.timeout, 10) || CFG.timeout;
      if (o.readingLookup) CFG.readingLookup = o.readingLookup;
      if (o.systemPrompt) CFG.systemPrompt = o.systemPrompt;
      return AIC;
    },

    buildPayload: buildPayload,
    ask: ask,
    quota: quota,
    quotaText: quotaText,
    md: md,
    aiStruct: aiStruct,
    cleanOrphan: cleanOrphan,
    cleanStructLines: cleanStructLines,
    cleanBadParen: cleanBadParen,
    fixReading: fixReading,
    /* 只读：当前生效的 SYSTEM_PROMPT（测试断言它与中转基准逐字一致用） */
    cfgPrompt: function () { return CFG.systemPrompt; },
    buildPrompt: buildPrompt,
    copy: copyText,
    errText: errText,

    /* 一站式：拿 payload → 请求 → 渲染 → 纠正假名 */
    explain: function (o, opts) {
      opts = opts || {};
      var p = buildPayload(o);
      return ask(p, opts).then(function (text) {
        var html = md(text);
        html = fixReading(html, opts.readingLookup);
        return { text: text, html: html };
      });
    },

    /* 降级：把上下文拼成纯文本，复制到剪贴板（贴到 MiniMax 就能出同样格式） */
    copyPrompt: function (o) {
      var p = buildPayload(o);
      var t = buildPrompt(p);
      return copyText(t).then(function () { return t; });
    },

    /* 按钮文案：有 endpoint → 「问 AI」；没有 → 「複製提問」 */
    mode: function () { return CFG.endpoint ? 'ask' : 'copy'; }
  };

  global.AIC = AIC;
  if (typeof module !== 'undefined' && module.exports) module.exports = AIC;
})(typeof window !== 'undefined' ? window : this);
