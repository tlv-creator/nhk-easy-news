#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把魔卡 v2.10 的前端改造成 NHK やさしいことばニュース 版。"""
import re
import sys

APP = "/workspace/nhk/site/app"
VER = "v20"


def patch(path, pairs, required=True):
    p = APP + "/" + path
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if old not in s:
            if required:
                print("  !! 未找到片段(%s): %r" % (path, old[:70]))
                sys.exit(1)
            continue
        if s.count(old) > 1:
            print("  ~~ 片段出现多次(%s): %r" % (path, old[:50]))
        s = s.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("  patched", path)


# ============================== index.html ==============================
def do_index():
    pairs = [
        ("<title>魔卡少女樱 · 日中对照学习</title>",
         "<title>NHKやさしいことばニュース · 日中对照精读</title>"),
        ('<meta name="theme-color" content="#e8619c">',
         '<meta name="theme-color" content="#2f6f4f">'),
        # 顶栏：常驻剧名标签（防止认错站）
        ("""    <button id="btnBack" class="icon-btn hidden" aria-label="返回">‹</button>
    <span class="badge top-badge hidden" id="topBadge">EP1</span>
    <div class="titles">
      <h1 id="epTitle">魔卡少女樱</h1>
      <p id="epSub">日語原聲 · 簡體中文逐句對照</p>
    </div>""",
         """    <button id="btnBack" class="icon-btn hidden" aria-label="返回">‹</button>
    <div class="titles">
      <span class="show-tag" id="showTag">📺 NHKやさしいことばニュース</span>
      <h1 id="epTitle">NHKやさしいことばニュース</h1>
      <p id="epSub">慢速音声 · 逐句漢字注音 · 簡體中文對照</p>
    </div>"""),
        # 首页 resume 文案
        ('<div class="resume-sub" id="resumeSub">70 集 · 日語原聲 + 簡體中文逐句對照 · 離線也能學</div>',
         '<div class="resume-sub" id="resumeSub">每日更新的慢速新聞 · 逐句漢字注音 + 簡體中文對照 · 離線也能學</div>'),
        # 剧集 → 新聞
        ("""    <div class="section-head">
      <h2>劇集</h2>
      <span class="muted" id="epCount"></span>
    </div>""",
         """    <div class="section-head">
      <h2>新聞一覽</h2>
      <span class="muted" id="epCount"></span>
    </div>"""),
        ('<input id="epSearch" type="search" placeholder="搜集數 / 標題，例如「小樱」" autocomplete="off">',
         '<input id="epSearch" type="search" placeholder="搜日期 / 標題 / 關鍵字，例如「探査」" autocomplete="off">'),
        # 播放页：版本切换条
        ("""    <!-- 字幕流：主屏幕的主体 -->
    <div class="transcript" id="transcript"></div>""",
         """    <!-- 版本切换：易懂版（慢速）／普通版（原速），两套文本两套音声 -->
    <div class="verbar" id="verBar">
      <button class="vb on" data-ver="easy">易懂版 · 慢速</button>
      <button class="vb" data-ver="web">普通版 · 原速</button>
      <span class="vb-hint" id="vbHint"></span>
    </div>

    <!-- 字幕流：主屏幕的主体 -->
    <div class="transcript" id="transcript"></div>"""),
    ]
    # 版本号
    patch("index.html", pairs)
    s = open(APP + "/index.html", encoding="utf-8").read()
    s = s.replace("?v=v19", "?v=" + VER)
    open(APP + "/index.html", "w", encoding="utf-8").write(s)
    print("  version bumped ->", VER)


# ============================== app.js ==============================
def do_app():
    pairs = [
        ("  var APP_VER = 'v19';", "  var APP_VER = '%s';" % VER),
        # ---- 状态：版本 ----
        ("""    ep: null,          // 当前剧集
    subs: [],          // 当前剧集字幕""",
         """    ep: null,          // 当前期
    ver: 'easy',       // easy=易懂版(慢速音声) | web=普通版(原速音声)
    subs: [],          // 当前版本的字幕"""),
        # ---- 列表：按日期分组 ----
        ("""  function renderList() {
    var box = $('#epList');
    box.innerHTML = '';
    var q = (S.epQuery || '').trim().toLowerCase();
    $('#epCount').textContent = q ? ('找到 ' + EPISODES.length + ' 集里的 ' + q) : (EPISODES.length + ' 集 · 離線');
    var shown = 0;
    EPISODES.forEach(function (ep) {
      if (q) {
        var hay = (ep.no + ' ' + ep.title + ' ' + (ep.zhTitle || '')).toLowerCase();
        if (hay.indexOf(q) < 0) return;
      }
      shown++;""",
         """  function renderList() {
    var box = $('#epList');
    box.innerHTML = '';
    var q = (S.epQuery || '').trim().toLowerCase();
    $('#epCount').textContent = q ? ('找到 ' + EPISODES.length + ' 期里的 ' + q) : (EPISODES.length + ' 期 · 離線');
    var shown = 0;
    // 按日期分组（NHK 每天一期）
    var groups = [], gmap = {};
    EPISODES.forEach(function (ep) {
      if (q) {
        var hay = (ep.no + ' ' + ep.title + ' ' + (ep.easyTitle || '') + ' ' +
                   (ep.date || '') + ' ' + (ep.time || '')).toLowerCase();
        if (hay.indexOf(q) < 0) return;
      }
      shown++;
      var gk = ep.date || '';
      if (!gmap[gk]) { gmap[gk] = { d: gk, items: [] }; groups.push(gmap[gk]); }
      gmap[gk].items.push(ep);
    });
    groups.forEach(function (grp) {
      var hd = document.createElement('div');
      hd.className = 'ep-group';
      hd.innerHTML = '<span class="ep-gd">' + (grp.d || '全部') +
        '</span><span class="ep-gn">' + grp.items.length + ' 期</span>';
      box.appendChild(hd);
      grp.items.forEach(function (ep) {"""),
        # ---- 列表项内部：把 ep.no 换成时间，标题用普通版+易懂版 ----
        ("""      var el = document.createElement('div');
      el.className = 'ep-item' + (pct >= 100 ? ' done' : '') + (pct > 0 && pct < 100 ? ' doing' : '');
      el.innerHTML =
        '<div class="ep-no">' + ep.no + '</div>' +
        '<div class="ep-meta">' +
        '<div class="ep-name">' + ep.title +
        (vc ? '<em class="ep-vc">📖' + vc + '</em>' : '') +
        '<small>' + escapeHtml(ep.zhTitle || '') + '</small></div>' +
        (pct > 0 ? '<div class="ep-bar"><i style="width:' + pct + '%"></i></div>' +
          '<div class="ep-pct">' + (pct >= 100 ? '已看完' : '已看 ' + pct + '% · ' + fmt(watched)) + '</div>' : '') +
        '</div>';
      el.addEventListener('click', function () { openEpisode(ep.id); });
      box.appendChild(el);
    });
    if (!shown) {""",
         """      var el = document.createElement('div');
      el.className = 'ep-item' + (pct >= 100 ? ' done' : '') + (pct > 0 && pct < 100 ? ' doing' : '');
      el.innerHTML =
        '<div class="ep-no">' + escapeHtml(ep.time || ep.no) + '</div>' +
        '<div class="ep-meta">' +
        '<div class="ep-name">' + escapeHtml(ep.title || '') +
        (vc ? '<em class="ep-vc">📖' + vc + '</em>' : '') +
        '<small>' + escapeHtml(ep.easyTitle || '') + '</small></div>' +
        (pct > 0 ? '<div class="ep-bar"><i style="width:' + pct + '%"></i></div>' +
          '<div class="ep-pct">' + (pct >= 100 ? '已看完' : '已看 ' + pct + '% · ' + fmt(watched)) + '</div>' : '') +
        '</div>';
      el.addEventListener('click', function () { openEpisode(ep.id); });
      box.appendChild(el);
      });
    });
    if (!shown) {"""),
        # ---- resume 文案 ----
        ("if (sub) sub.textContent = EPISODES.length + ' 集 · 日語原聲 + 簡體中文逐句對照 · 離線也能學';",
         "if (sub) sub.textContent = EPISODES.length + ' 期慢速新聞 · 逐句漢字注音 + 簡體中文對照 · 離線也能學';"),
        # ---- CDN 候选：支持 {slow, fast} ----
        ("""  function cdnCandidates(ep) {
    var m = window.__CDN__ || {};
    var a = m[ep && ep.id];
    if (!a) return [];
    if (typeof a === 'string') a = [a];""",
         """  function cdnCandidates(ep) {
    var m = window.__CDN__ || {};
    var a = m[ep && ep.id];
    if (!a) return [];
    if (typeof a === 'string') a = [a];
    // NHK：同一期有两套音声（易懂版=慢速 / 普通版=原速）
    if (!Array.isArray(a) && typeof a === 'object') {
      a = a[S.ver === 'web' ? 'fast' : 'slow'] || a.slow || [];
    }"""),
        # ---- openEpisode：标题 / 字幕 / 版本 ----
        ("""    var tb = $('#topBadge');
    if (tb) { tb.textContent = 'EP' + ep.no; tb.classList.remove('hidden'); }
    $('#epTitle').textContent = ep.title;
    $('#epSub').textContent = (ep.zhTitle || '') + ' · ' + fmt(ep.duration || 0);""",
         """    var tb = $('#topBadge');
    if (tb) { tb.textContent = (ep.date || '').slice(5) + ' ' + (ep.time || ''); tb.classList.remove('hidden'); }
    $('#epTitle').textContent = ep.title || (ep.date || '');
    $('#epSub').textContent = (ep.date || '') + ' ' + (ep.time || '') +
      ' · ' + (S.ver === 'web' ? '普通版 · 原速 ' + fmt(ep.durFast || 0)
                                : '易懂版 · 慢速 ' + fmt(ep.duration || 0));"""),
        # ---- 字幕加载：按版本取 ----
        ("""      S.subs = SUBS[ep.id] || [];
      renderTranscript();""",
         """      S.subs = cuesOf(ep);
      renderTranscript();
      refreshVerBar(ep);"""),
        # ---- document.title ----
        ("document.title = '生词表 · 句型表 · 魔卡少女樱';",
         "document.title = '生词表 · 句型表 · NHKやさしいことばニュース';"),
        ("document.title = '魔卡少女樱 · 日中对照学习';",
         "document.title = 'NHKやさしいことばニュース · 日中对照精读';"),
        ("document.title = (S.ep ? S.ep.title : '魔卡少女樱') + ' · 日中对照学习';",
         "document.title = (S.ep ? (S.ep.title || S.ep.date || '') : 'NHKやさしいことばニュース') + ' · 日中对照精读';"),
    ]
    patch("app.js", pairs)

    # ---- 追加：版本切换相关函数 ----
    s = open(APP + "/app.js", encoding="utf-8").read()
    anchor = "  function showView(v) {"
    add = r"""  /* ============ 版本切换：易懂版（慢速音声）／普通版（原速音声） ============
     两版是两套完全不同的文本 + 两套音声：
       easy → NHK「やさしい版」短句，慢速音声，8~12 句
       web  → 普通版書き起こし（新闻稿），原速音声，30~150 句
     所以版本和语速是绑定的，切换时文本、音声、时间轴一起换。 */
  function cuesOf(ep) {
    var d = (window.__SUBS__ || {})[ep && ep.id];
    if (!d) return [];
    if (Object.prototype.toString.call(d) === '[object Array]') return d;   // 兼容单版本
    return d[S.ver] || d.easy || [];
  }

  function verAvailable(ep, ver) {
    if (!ep) return false;
    var vs = ep.ver || ['easy'];
    if (vs.indexOf(ver) < 0) return false;
    if (ver === 'web') return (ep.durFast || 0) > 1;
    return (ep.duration || 0) > 1;
  }

  function refreshVerBar(ep) {
    var bar = $('#verBar');
    if (!bar) return;
    Array.prototype.forEach.call(bar.querySelectorAll('.vb'), function (b) {
      var v = b.getAttribute('data-ver');
      b.classList.toggle('on', v === S.ver);
      var ok = verAvailable(ep, v);
      b.disabled = !ok;
      b.classList.toggle('off', !ok);
    });
    var hint = $('#vbHint');
    if (hint) {
      hint.textContent = S.ver === 'easy' ? '簡單句 · 慢速' : '新聞稿 · 原速';
    }
  }

  function setVersion(ver) {
    if (!S.ep || S.ver === ver) return;
    if (!verAvailable(S.ep, ver)) { toast('這一期沒有原速音声'); return; }
    var prevIdx = S.idx;
    S.ver = ver;
    S.subs = cuesOf(S.ep);
    S.idx = -1;
    renderTranscript();
    refreshVerBar(S.ep);
    // 换音声 + 时间轴：按句子序号在新版里找同位置的句子
    var urls = cdnCandidates(S.ep);
    if (urls.length) {
      attachAudio(S.ep, 0);
      P.playbackRate = S.speed;
      P.play();
    }
    var target = 0;
    if (prevIdx >= 0 && S.subs[prevIdx]) target = S.subs[prevIdx].s / 1000;
    else if (S.subs[0]) target = S.subs[0].s / 1000;
    if (target) { try { P.currentTime = target + 0.05; } catch (e) { } }
    syncCue();
    $('#epSub').textContent = (S.ep.date || '') + ' ' + (S.ep.time || '') +
      ' · ' + (S.ver === 'web' ? '普通版 · 原速 ' + fmt(S.ep.durFast || 0)
                                : '易懂版 · 慢速 ' + fmt(S.ep.duration || 0));
    toast(S.ver === 'easy' ? '已切到易懂版 · 慢速音声' : '已切到普通版 · 原速音声');
  }

"""
    if anchor not in s:
        print("!! 找不到 showView 锚点")
        sys.exit(1)
    s = s.replace(anchor, add + anchor, 1)

    # 绑定 verbar + 初始化
    s = s.replace("""  /* ============ 启动 ============ */
  P.init();""",
                  """  /* ============ 版本切换条 ============ */
  Array.prototype.forEach.call(document.querySelectorAll('#verBar .vb'), function (b) {
    b.addEventListener('click', function () { setVersion(b.getAttribute('data-ver')); });
  });

  /* ============ 启动 ============ */
  P.init();""", 1)
    open(APP + "/app.js", "w", encoding="utf-8").write(s)
    print("  app.js: 版本切换函数已注入")


# ============================== style.css ==============================
def do_css():
    css = r"""

/* ===================== NHK 版新增 ===================== */
/* 顶栏常驻剧名标签：同系列 App 长得一样，必须一眼看出是哪个节目 */
.show-tag {
  display: inline-block; font-size: 11.5px; font-weight: 800; letter-spacing: .3px;
  color: var(--accent-ink, #fff); background: var(--accent);
  padding: 2px 8px; border-radius: 7px; margin-bottom: 3px;
  max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.titles h1 { margin-top: 1px; }

/* 新闻列表按日期分组 */
.ep-group {
  display: flex; align-items: baseline; gap: 8px;
  padding: 12px 4px 7px; margin-top: 2px;
  font-size: 14px; font-weight: 800; color: var(--fg-dim);
  border-bottom: 1px solid var(--line);
}
.ep-group .ep-gd { font-size: 15.5px; color: var(--fg); letter-spacing: .4px; }
.ep-group .ep-gn { font-size: 12.5px; font-weight: 600; }

/* ep-no 放播出时间 */
.ep-no { font-size: 14px; letter-spacing: 0; }

/* 版本切换条 */
.verbar {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; flex: none;
  border-bottom: 1px solid var(--line);
  background: var(--bg-2);
}
.verbar .vb {
  font: inherit; font-size: 14px; font-weight: 700; line-height: 1.2;
  padding: 7px 12px; border-radius: 9px; cursor: pointer;
  border: 1px solid var(--line); background: var(--bg-3); color: var(--fg-dim);
  min-height: 34px;
}
.verbar .vb.on { background: var(--accent); color: var(--accent-ink, #fff); border-color: var(--accent); }
.verbar .vb.off { opacity: .38; cursor: not-allowed; }
.verbar .vb-hint { margin-left: auto; font-size: 12.5px; color: var(--fg-dim); white-space: nowrap; }
"""
    p = APP + "/style.css"
    s = open(p, encoding="utf-8").read()
    s += css
    open(p, "w", encoding="utf-8").write(s)
    print("  patched style.css (+%d bytes)" % len(css))


if __name__ == "__main__":
    print("== 前端改造 ==")
    do_index()
    do_app()
    do_css()
    print("== 完成 ==")
