/*!
 * Shun · 引导页（Boot）—— 让 App 自动找到「当前内容站 / 音频站」
 *
 * 为什么需要：
 *   mcode 每次部署都换新域名 → APK 里写死的站点/音频地址会过期。
 *   ⇒ 用一个**地址永久不变**的引导页告诉 App 现在该去哪。
 *   （跟 NHK 那套 GitHub Pages 引导页同一个思路，但用独立的 shun.json，不碰 NHK 的文件）
 *
 * 引导页地址（永久不变）：
 *   https://tlv-creator.github.io/nhk-easy-news/shun.json
 *   → {ver, verName, site, audio, apk, updated, note}
 *
 * 行为：
 *   · 启动时后台拉一次（**不挡首屏**，失败就静默用内置数据）
 *   · 拉到后：集数列表 = 内置的 + 远端新增的（按集号去重，远端优先）
 *   · 新增集的数据和音频都走远端；内置集照旧走本地（离线可用）
 *   · 引导页拿不到 → 完全不影响使用（回落到内置）
 */
(function (global) {
  'use strict';

  var BOOT_URL = 'https://tlv-creator.github.io/nhk-easy-news/shun.json';   /* 可被外部改写（测试用） */
  var BOOT_TIMEOUT = 8000;

  var S = {
    boot: null,        /* 引导页原始内容 */
    bootOk: false,     /* 是否成功拉到 */
    dataBase: '',      /* 远端数据基址（站点根，带 /） */
    audioBase: '',     /* 远端音频基址 */
    siteBase: ''
  };

  /* ---------- 拉引导页 ---------- */
  S.fetch = function (timeoutMs) {
    var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var t = setTimeout(function () { if (ctrl) { try { ctrl.abort(); } catch (e) { } } },
      timeoutMs || BOOT_TIMEOUT);
    var init = { cache: 'no-store' };
    if (ctrl) init.signal = ctrl.signal;
    return fetch(S.BOOT_URL, init).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) {
      if (j && j.site) {
        S.boot = j;
        S.bootOk = true;
        S.siteBase = String(j.site || '').replace(/\/+$/, '') + '/';
        S.dataBase = String(j.data || j.site || '').replace(/\/+$/, '') + '/';
        S.audioBase = String(j.audio || (S.dataBase + 'media/')).replace(/\/+$/, '') + '/';
      } else {
        /* 🔴 拿到的不是有效引导页（404 / 500 / 字段缺失）→ **必须复位 bootOk**。
           否则上一次成功拉到的状态会一直留在内存里，让人以为「引导页还能用」。
           （HTTP 500 不会让 fetch reject，走的是这条 else 分支 —— 真踩过） */
        S.boot = null;
        S.bootOk = false;
      }
      return j;
    }).catch(function () {
      /* 🔴 拿不到就静默 —— 不能因为引导页挂了就用不了。
         ⚠️ 失败时必须**复位 bootOk**，否则上一次成功拉到的状态会一直留在内存里，
            让人以为「引导页还能用」。 */
      S.boot = null;
      S.bootOk = false;
      return null;
    }).then(function (v) {
      clearTimeout(t);
      return v;
    });
  };

  /* ---------- 合并集数列表 ----------
   * 内置的在前（离线可用），远端新增的追加在后面（按集号排序）。
   * 同集号以**远端**为准（远端可能是更新过的数据）。
   */
  S.mergeEpisodes = function (local, remote) {
    local = local || []; remote = remote || [];
    var map = {}, out = [], i;
    for (i = 0; i < local.length; i++) map[local[i].tag] = { ep: local[i], remote: false };
    for (i = 0; i < remote.length; i++) {
      var t = remote[i].tag;
      if (map[t]) { map[t].ep = remote[i]; map[t].remote = true; }
      else map[t] = { ep: remote[i], remote: true };
    }
    for (var k in map) if (map.hasOwnProperty(k)) out.push(map[k]);
    /* 🔴 排序必须按**原始集号 no**（277/276/…），**不能按 tag**！
     *    tag 是**倒着**编的：ep01 = Ep277（最新）… ep12 = Ep266（最老）。
     *    按 tag 排会把整个列表反过来（最新的一集跑到最后）。 */
    function noOf(e) {
      var n = parseInt(String(e && e.no !== undefined ? e.no : '').replace(/\D/g, ''), 10);
      if (!n) n = parseInt(String(e && e.tag || '').replace(/\D/g, ''), 10) || 0;
      return n;
    }
    out.sort(function (a, b) { return noOf(b.ep) - noOf(a.ep); });
    return out.map(function (x) { return x.ep; });
  };

  /* ---------- 某一集的数据该从哪拿 ---------- */
  S.dataUrlFor = function (ep) {
    /* 远端集 → 远端站；内置集 → 本地代理（离线可用） */
    if (ep && ep._remote && S.dataBase) return S.dataBase + 'data/eps/' + ep.tag + '.json';
    return 'data/eps/' + ep.tag + '.json';
  };
  S.blkUrlFor = function (ep) {
    if (ep && ep._remote && S.dataBase) return S.dataBase + 'data/eps/' + ep.tag + '.blk.json';
    return 'data/eps/' + ep.tag + '.blk.json';
  };

  /* ---------- 远端集数索引 ---------- */
  S.fetchRemoteIndex = function () {
    if (!S.dataBase) return Promise.resolve([]);
    var url = S.dataBase + 'data/eps/index.json';
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.ok ? r.json() : {};
    }).then(function (o) {
      return (o && o.episodes) || [];
    }).catch(function () { return []; });
  };

  S.BOOT_URL = BOOT_URL;   /* 暴露出去，方便测试/设置里改地址 */
  global.ShunBoot = S;
})(typeof window !== 'undefined' ? window : this);
