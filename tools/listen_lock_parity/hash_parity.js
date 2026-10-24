// 跨实现对拍：浏览器形态（crypto.subtle）vs PG 形态（encode(sha256(...),'hex')）
// 用的是**他们线上 api.js 里那段一字不改的 sha256Hex**，另加一个「BigInt 不补零」变体做对照
const crypto = require('crypto');
const { webcrypto } = crypto;

// —— 线上 api.js 的 sha256Hex（逐字照抄）——
function sha256Hex_browser(str) {
  var c = global.crypto || global.msCrypto;
  var enc = new TextEncoder();
  return c.subtle.digest('SHA-256', enc.encode(String(str))).then(function (buf) {
    var a = new Uint8Array(buf), s = '';
    for (var i = 0; i < a.length; i++) s += ('0' + a[i].toString(16)).slice(-2);
    return s;
  });
}
// —— 反面对照：BigInt 直转 hex（**不补零**）——
function sha256Hex_bigint(str) {
  const h = webcrypto.subtle ? null : null; // 占位
  return crypto.createHash('sha256').update(String(str), 'utf8').digest('hex')
    .replace(/^0+/, '');            // 模拟 BigInt.toString(16) 丢前导零
}
function pg_form(str) {
  return crypto.createHash('sha256').update(String(str), 'utf8').digest('hex'); // = PG encode(sha256(convert_to(t,'utf8')),'hex')
}

(async () => {
  const cases = [];
  // ① 三个真实形态的 32 位 hex 令牌
  cases.push(['典型令牌A', 'a1b2c3d4e5f60718293a4b5c6d7e8f90']);
  cases.push(['典型令牌B', 'b1c2d3e4f5061728394a5b6c7d8e9f001']);
  cases.push(['典型令牌C', '0f1e2d3c4b5a69788796a5b4c3d2e1f0']);
  // ② 故意造一个**哈希以 0 开头**的令牌（前导零最容易被丢）
  let found = null;
  for (let i = 0; i < 2000000 && !found; i++) {
    const t = ('0000000000000000' + i.toString(16)).slice(-32);
    if (pg_form(t)[0] === '0') found = t;
  }
  cases.push(['哈希以0开头', found]);
  // ③ 边界：空串 / 1 字符 / 64 字符 / 非 ASCII（学号+中文）/ 含大写
  cases.push(['空串', '']);
  cases.push(['1字符', 'x']);
  cases.push(['64字符', 'c'.repeat(64)]);
  cases.push(['非ASCII(学号+中文)', '26230326张雪静']);
  cases.push(['含大写', 'ABCDEF0123456789abcdefABCDEF0123456789']);

  const rows = [];
  for (const [name, t] of cases) {
    const b = await sha256Hex_browser(t);
    const g = await sha256Hex_bigint(t);
    const p = pg_form(t);
    rows.push({ name, token: t, browser: b, pg: p, bigint_variant: g,
                eq_browser_pg: b === p, eq_browser_bigint: b === g, len_b: b.length, len_g: g.length });
  }
  console.log('| 用例 | 浏览器(crypto.subtle) | PG sha256 | 逐字相等 | BigInt变体 | 相等 | 长度 |');
  console.log('|---|---|---|---|---|---|---|');
  for (const r of rows) {
    console.log(`| ${r.name} | ${r.browser.slice(0,16)}… | ${r.pg.slice(0,16)}… | ${r.eq_browser_pg ? '✅' : '❌'} | ${r.bigint_variant.slice(0,16)}… | ${r.eq_browser_bigint ? '✅' : '❌'} | ${r.len_b}/${r.len_g} |`);
  }
  const allEq = rows.every(r => r.eq_browser_pg);
  const badBig = rows.filter(r => !r.eq_browser_bigint);
  console.log('\n浏览器形态 == PG 形态：' + (allEq ? `✅ 全部 ${rows.length}/${rows.length} 逐字相等` : '❌ 有不等'));
  console.log('BigInt 变体失败的用例：' + (badBig.length ? badBig.map(r => `${r.name}(长度 ${r.len_b}→${r.len_g})`).join('、') : '无'));
  require('fs').writeFileSync('/workspace/vocab/pkg_private/verify/hash_cases.json', JSON.stringify(rows, null, 1));
})();
