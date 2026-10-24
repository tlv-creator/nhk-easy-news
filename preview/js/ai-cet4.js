/* =====================================================================
   四级精读日报 · AI 精批客户端
   ---------------------------------------------------------------------
   调中转的 /ai-cet4 分支（CloudBase HTTP 云函数 → MiniMax）
   🔴 只在学生主动点「AI 批改」时才发请求 —— **不默认全班自动批**
   🔴 本地初判（js/scoring.js）判的是「信息完整度」；
      AI 判的是「语法 + 通顺 + 定档」—— 两者在 UI 上分开显示，不混为一谈
   🔴 超时 75 秒（上游 60s / 云函数 90s / 前端 75s，留 15s 余量）
   🔴 不做流式：墨水屏逐字刷新会疯狂重绘
   ===================================================================== */
(function (global) {
  'use strict';

  /* ---- 单一权威：AI 中转地址只写这一处 ---- */
  /* 🔴 路径实测结论（2026-10-24）：
        网关只把 `/ai` 前缀映射到 ai-proxy 函数 ⇒ `/ai-cet4` 在网关层就 404
        `/ai/xxx` 全部进函数；函数里认 `/prompt` `/quota`，其它走默认分支
     ⇒ 所以四级端点必须挂在 `/ai` **下面**。
     ⚠️ 取名避开 `/prompt` 后缀：现役的路由判断若是 `endsWith('/prompt')`，
        `/ai/cet4/prompt` 会先撞上日语端 ⇒ 用 `/ai/cet4-spec` 取 prompt + sha。 */
  var CFG = {
    ENDPOINT: 'https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai/cet4',
    TIMEOUT: 75000
  };

  /* ---- 错误码 → 人话（技术细节只进 console，不给学生看事故现场） ---- */
  var ERRS = {
    not_configured: 'AI 批改还没配置好，先用上面的机器初判。',
    unauthorized: 'AI 批改还没配置好，先用上面的机器初判。',
    too_many: '今天 AI 用得有点多了，过一会儿再试。',
    quota_exceeded: 'AI 额度用完了，等额度回满再试。',
    timeout: '网络慢，这次没赶上。再点一次试试。',
    upstream_params: 'AI 暂时不可用，稍后再试。',
    upstream_error: 'AI 暂时不可用，稍后再试。',
    upstream_auth: 'AI 暂时不可用，稍后再试。',
    not_deployed: 'AI 批改还没上线，先用上面的机器初判。',
    bad_request: '这次提交的数据有问题，请检查后重试。'
  };

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ================================================================
     发请求
     ================================================================ */
  function grade(payload, onTick) {
    if (!global.fetch) {
      return Promise.resolve({ ok: false, error: 'bad_request' });
    }
    var timer = null, started = Date.now();
    if (onTick) {
      timer = setInterval(function () { onTick(Math.round((Date.now() - started) / 1000)); }, 1000);
    }
    var ctrl = ('AbortController' in global) ? new AbortController() : null;
    var to = setTimeout(function () { if (ctrl) ctrl.abort(); }, CFG.TIMEOUT);

    return global.fetch(CFG.ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctrl ? ctrl.signal : undefined
    }).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = JSON.parse(t); } catch (e) { data = null; }
        if (!data) {
          if (r.status === 404) return { ok: false, error: 'not_deployed' };
          return { ok: false, error: 'upstream_error' };
        }
        if (!r.ok || !data.ok) {
          return { ok: false, error: data.error || 'upstream_error' };
        }
        return { ok: true, data: data };
      });
    }).catch(function (e) {
      if (e && (e.name === 'AbortError' || /abort/i.test(String(e.message || '')))) {
        return { ok: false, error: 'timeout' };
      }
      return { ok: false, error: 'upstream_error' };
    }).then(function (r) {
      clearTimeout(to);
      if (timer) clearInterval(timer);
      return r;
    });
  }

  /* ================================================================
     渲染
     ----------------------------------------------------------------
     墨水屏口径：纯黑白、无渐变、描边半透明、控件用文字
     逐句详情**默认收起**（点开才看逐句改法）—— 首屏只给结论
     ================================================================ */
  function bandLabel(r) {
    if (!r || !r.band) return '—';
    return '第 ' + r.band + ' 档（' + (r.score_range || '') + ' 分）· ' + (r.band_name || '');
  }

  function renderHTML(r) {
    if (!r || !r.ok) {
      var msg = ERRS[r && r.error] || 'AI 暂时不可用，稍后再试。';
      return '<div class="ai-err"><b>AI 批改没跑成</b><br>' + esc(msg)
        + '<br><span class="muted">上面的机器初判结果照常有效，不用重做。</span></div>';
    }

    var d = r.data || {};

    /* 🔴 降级一档：模型没按 JSON 输出（中转会把原文透回来）
       ⇒ 给用户看原话，而不是一句「失败了」。 */
    if (d.unparsed && d.raw) {
      return '<div class="ai-raw">'
        + '<div class="ai-lbl">AI 的批改（这次没能自动整理成结构，下面是它写的原文）</div>'
        + '<pre class="ai-pre">' + esc(d.raw) + '</pre>'
        + '<div class="ai-note">AI 的原话可能有出入，以参考译文和老师的讲评为准。</div>'
        + '</div>';
    }

    var res = d.result || d;      /* 中转已经校验过一层，这里再兜一层 */

    /* 头部：定档 */
    var head = '<div class="ai-head">'
      + '<div class="ai-lbl">AI 精批 · 按四级五档标准定档</div>'
      + '<div class="ai-band">' + esc(bandLabel(res)) + '</div>'
      + (res.score ? '<div class="ai-score">得分 <b>' + esc(res.score) + '</b> / ' + esc(res.score_range || '') + '</div>' : '')
      + (res.confidence ? '<div class="ai-conf">把握：' + esc({ high: '较有把握', medium: '中等', low: '不太确定' }[res.confidence] || res.confidence) + '</div>' : '')
      + '</div>';

    /* 总评 */
    var sum = res.summary ? '<div class="ai-sum">' + esc(res.summary) + '</div>' : '';

    /* 错误类型统计 */
    var types = res.error_types || {};
    var tks = Object.keys(types).map(function (k) {
      return '<span class="ai-tag">' + esc(k) + ' ' + esc(types[k]) + '</span>';
    }).join('');
    var typeBox = tks ? '<div class="ai-types"><span class="ai-lbl2">这次的主要错误类型</span>'
      + '<div class="ai-tagrow">' + tks + '</div></div>' : '';

    /* 最该补的点 */
    var weak = '';
    if (res.weakest_points && res.weakest_points.length) {
      weak = '<div class="ai-weak"><span class="ai-lbl2">最该补的</span>'
        + res.weakest_points.map(function (w) { return '<div class="ai-w">· ' + esc(w) + '</div>'; }).join('')
        + '</div>';
    }

    /* 逐句详情：默认收起 */
    var sents = res.sentences || [];
    var sHTML = sents.map(function (s) {
      var gs = (s.grammar || []).map(function (g) {
        return '<div class="ai-g">'
          + '<div class="ai-g-h"><span class="ai-gt">' + esc(g.type) + '</span>'
          + (g.span ? '<span class="ai-gs">「' + esc(g.span) + '」</span>' : '') + '</div>'
          + (g.fix ? '<div class="ai-gf"><b>改成：</b>' + esc(g.fix) + '</div>' : '')
          + (g.note ? '<div class="ai-gn">' + esc(g.note) + '</div>' : '')
          + '</div>';
      }).join('');
      var mp = (s.missing_points || []).map(function (m) {
        return '<div class="ai-m">✗ 漏了：' + esc(m) + '</div>';
      }).join('');
      var body = '<div class="ai-sbody">'
        + (s.comment ? '<div class="ai-sc">' + esc(s.comment) + '</div>' : '')
        + (mp ? '<div class="ai-mp">' + mp + '</div>' : '')
        + (gs ? '<div class="ai-gs-box">' + gs + '</div>' : '<div class="ai-none">这句没查出明显的语法错误。</div>')
        + (s.revised ? '<div class="ai-rev"><span class="ai-lbl2">改后整句</span><p>' + esc(s.revised) + '</p></div>' : '')
        + '</div>';
      return '<div class="ai-s" data-no="' + esc(s.no) + '" role="button" tabindex="0">'
        + '<div class="ai-stop"><span class="ai-sno">第 ' + esc(s.no) + ' 句</span>'
        + '<span class="ai-sarrow">展开</span></div>' + body + '</div>';
    }).join('');

    var foot = '<div class="ai-note">AI 按四级评分标准给的参考判分，<b>不是老师最终判分</b>；'
      + '机器也可能看漏。拿不准的地方以老师的讲评为准。</div>';

    return head + sum + typeBox + weak
      + '<div class="ai-sents">' + sHTML + '</div>' + foot;
  }

  global.CET4AI = {
    CFG: CFG,
    grade: grade,
    renderHTML: renderHTML,
    bandLabel: bandLabel,
    ERRS: ERRS
  };
})(typeof window !== 'undefined' ? window : globalThis);
