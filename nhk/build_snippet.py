#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装浏览器一键脚本 create_cron_snippet.js（把 md5.js + prompt 正文塞进去）。"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
md5_src = (HERE / "md5.js").read_text(encoding="utf-8")
prompt = (HERE / "cron_prompt.txt").read_text(encoding="utf-8")
# 去掉 md5.js 里的 CommonJS 导出行
md5_src = md5_src.replace("if (typeof module !== 'undefined') module.exports = md5;", "")

TEMPLATE = r"""/* NHK 定时任务一键脚本
 * 在 https://agent.minimax.cn 页面按 F12 → Console → 粘贴 → 回车
 * 依次：列出任务 → 删掉 nhk-updater 旧任务 → 新建「NHK 每日更新（唯一）」→ 再列一次
 * 把最后的输出贴回给助手。
 */
(async () => {
__MD5__

  const PROMPT = __PROMPT__;
  const API = '/minimax-cloud/api/v1';
  const token = localStorage.getItem('_token');
  if (!token) { console.error('❌ 没找到登录令牌（localStorage._token 为空）——请先在 agent.minimax.cn 登录'); return; }
  let ud = {};
  try { ud = JSON.parse(localStorage.getItem('user_detail_agent') || '{}'); } catch (e) {}

  async function call(method, path, bodyObj) {
    const ms = Date.now(), ts = Math.floor(ms / 1000);
    const params = {
      device_platform: 'web', biz_id: '3', app_id: '3001', version_code: '22201',
      unix: String(ts), timezone_offset: String(-60 * new Date().getTimezoneOffset()),
      sys_language: 'zh', lang: 'zh',
      uuid: localStorage.getItem('UNIQUE_USER_ID') || '0',
      device_id: ud.device_id || '0',
      os_name: (navigator.userAgent.includes('Mac') ? 'macOS' : navigator.userAgent.includes('Windows') ? 'Windows' : 'Linux'),
      browser_name: 'Chrome', user_id: ud.realUserID || 0, op_ticket: ud.op_ticket,
      client: 'web', region: 'cn', token: token,
    };
    const qs = Object.keys(params)
      .filter(k => params[k] !== undefined && params[k] !== null)
      .map(k => k + '=' + encodeURIComponent(String(params[k]))).join('&');
    const full = path + '?' + qs;
    const body = bodyObj === undefined ? '' : JSON.stringify(bodyObj);
    const headers = {
      'Content-Type': 'application/json', 'Accept': 'application/json',
      'x-timestamp': String(ts),
      'x-signature': md5(ts + 'I*7Cf%WZ#S&%1RlZJ&C2' + body),
      'yy': md5(encodeURIComponent(full) + '_' + body + md5(String(ms)) + 'ooui'),
      'X-Mavis-Cron-Request-Source': 'ui',
    };
    const r = await fetch(full, { method: method, headers: headers, body: body === '' ? undefined : body });
    const txt = await r.text();
    if (!r.ok) throw new Error(method + ' ' + path + ' → HTTP ' + r.status + ' ' + txt.slice(0, 300));
    try { return JSON.parse(txt); } catch (e) { return txt; }
  }

  const line = t => '  • ' + (t.agent_name || '?') + ' / ' + (t.cron_name || '?') + ' / ' + (t.schedule || '?')
    + ' / enabled=' + t.enabled + ' / id=' + (t.cron_id || t.id);

  try {
    console.log('1) 当前定时任务列表：');
    let list = await call('GET', API + '/cron');
    let tasks = (list && (list.tasks || list.data || [])) || [];
    tasks.forEach(t => console.log(line(t)));

    const mine = tasks.filter(t => t.agent_name === 'nhk-updater' || (t.cron_name || '').indexOf('NHK') >= 0);
    console.log('\n2) 删除 nhk-updater 名下旧任务 ' + mine.length + ' 个：');
    for (const t of mine) {
      const id = t.cron_id || t.id;
      try { await call('DELETE', API + '/cron/' + encodeURIComponent(id)); console.log('   已删: ' + id); }
      catch (e) { console.log('   删除失败 ' + id + ' → ' + e.message); }
    }

    console.log('\n3) 新建「NHK 每日更新（唯一）」：');
    const created = await call('POST', API + '/agent/' + encodeURIComponent('nhk-updater') + '/cron/' + encodeURIComponent('NHK 每日更新（唯一）'), {
      schedule: '0 21 * * *', prompt: PROMPT, timezone: 'Asia/Shanghai',
      session: { mode: 0 }, enabled: true,
    });
    console.log('   ✅ ' + JSON.stringify(created).slice(0, 400));

    console.log('\n4) 最终列表：');
    list = await call('GET', API + '/cron');
    tasks = (list && (list.tasks || list.data || [])) || [];
    tasks.forEach(t => console.log(line(t)));
    console.log('\n🎉 搞定 —— 把这段输出贴回给助手。');
  } catch (e) {
    console.error('❌ 出错：' + e.message);
  }
})();
"""

out = TEMPLATE.replace("__MD5__", md5_src).replace("__PROMPT__", __import__("json").dumps(prompt, ensure_ascii=False))
(HERE / "create_cron_snippet.js").write_text(out, encoding="utf-8")
print("✅ 已生成 create_cron_snippet.js  (", len(out), "bytes )")
