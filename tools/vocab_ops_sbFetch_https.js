/* ============================================================================
 * sbFetch 的 drop-in 替换版（给每日听力记录站的 vocab-ops 用）
 * ----------------------------------------------------------------------------
 * 为什么必须换：
 *   原版用全局 fetch()，但 CloudBase 的 Node 沙箱（scf_bootstrap 起的那个 node）
 *   是 Node 12/16 ⇒ **没有全局 fetch** ⇒ 每次调用都 ReferenceError: fetch is not defined
 *   ⇒ 函数能起来（部署"成功"），但 /vocab-ops 一调就 500
 *   ⇒ 证据：现役 ai-proxy 的 index.js 第 44-46 行用 require('https')，
 *           第 255 行还专门写着「绝对不要用 new URL()：CloudBase 的 Node 沙箱里没有全局 URL」
 *   ⇒ 同一个沙箱不会凭空多出 fetch。
 *
 * 契约不变：sbFetch(path, method, body, prefer) → Promise<parsed|raw>
 *   · 2xx → resolve(JSON.parse(body))，空 body → resolve(null)
 *   · 非 2xx → reject(Error('supabase_<code>'))，并挂 .status / .body
 *   · 超时 → reject(Error('timeout'))
 * ==========================================================================*/

var https = require('https');
var http = require('http');

function sbFetch(path, method, body, prefer) {
  var headers = {
    'apikey': SB_KEY,
    'Authorization': 'Bearer ' + SB_KEY,
    'Content-Type': 'application/json'
  };
  if (prefer) headers['Prefer'] = prefer;

  var payload = (body === undefined || body === null) ? null : JSON.stringify(body);
  if (payload !== null) headers['Content-Length'] = Buffer.byteLength(payload);

  return new Promise(function (resolve, reject) {
    /* 🔴 不用 new URL()：沙箱里没有全局 URL ⇒ 纯字符串解析 */
    var um = /^(https?):\/\/([^/:?#]+)(?::(\d+))?([^?#]*)(\?[^#]*)?/.exec(SB_URL + path);
    if (!um) return reject(new Error('bad_sb_url:' + SB_URL));

    var opts = {
      hostname: um[2],
      port: um[3] ? parseInt(um[3], 10) : 443,
      path: (um[4] || '/') + (um[5] || ''),
      method: method || 'GET',
      headers: headers,
      timeout: TIMEOUT_MS
    };

    var req = (um[1] === 'http' ? http : https).request(opts, function (res) {
      var buf = '';
      res.setEncoding('utf8');
      res.on('data', function (d) { buf += d; });
      res.on('end', function () {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          var e = new Error('supabase_' + res.statusCode);
          e.status = res.statusCode;
          e.body = buf;
          return reject(e);
        }
        if (!buf) return resolve(null);
        try { resolve(JSON.parse(buf)); } catch (e2) { resolve(buf); }
      });
    });

    req.on('timeout', function () { req.destroy(new Error('timeout')); });
    req.on('error', function (e) { reject(e); });
    if (payload !== null) req.write(payload);
    req.end();
  });
}
