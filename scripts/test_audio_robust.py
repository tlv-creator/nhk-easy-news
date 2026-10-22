#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**audio-robust.js 的自检**（A/B/C 三场景 + 反向验证）

🔴 验的是**补丁文件本身**（不是某个项目）⇒ 用一个最小页面 + 一个 `<audio id="audio">` 即可
⇒ **不依赖任何项目** ⇒ hub 六个项目都能用同一份

三场景：
  A 第一次 503（CDN 冷启动）→ 必须自动救回来，且**不弹提示**
  B 一直 503（真故障）        → 必须最终如实提示，且请求次数有上限
  C 音频元素一直 readyState<1（模拟「连 error 都不触发」）→ **看门狗必须兜住**

反向验证：**不加载补丁** ⇒ B 场景必然「无提示（静默失败）」⇒ 证明补丁真的在起作用
"""
import http.server
import os
import socketserver
import sys
import threading
import time
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(HERE, 'audio-robust.js')
ROOT = os.path.dirname(HERE)          # /workspace/output
PASS, FAIL = [], []

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>t</title>%(patch)s</head><body>
<h1>测试</h1>
<audio id="audio" preload="metadata" playsinline></audio>
</body></html>"""

WAV = (b'RIFF' + (36).to_bytes(4, 'little') + b'WAVEfmt ' + (16).to_bytes(4, 'little')
       + (1).to_bytes(2, 'little') + (1).to_bytes(2, 'little') + (8000).to_bytes(4, 'little')
       + (8000).to_bytes(4, 'little') + (1).to_bytes(2, 'little') + (8).to_bytes(2, 'little')
       + b'data' + (0).to_bytes(4, 'little'))


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + name + (('  — ' + detail) if detail else ''))


def serve():
    """本地静态服务器：根目录给 /workspace/output，页面由内存提供"""
    patch = open(PATCH, encoding='utf-8').read()
    html = PAGE % {'patch': '<script>/*PATCH*/</script>'.replace('/*PATCH*/', patch)}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == '/' or self.path.startswith('/index'):
                body = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
    srv = socketserver.TCPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def run(page, base, mode, with_patch=True):
    hits = {'n': 0}

    def route(r):
        if '.wav' in r.request.url or '.mp3' in r.request.url or '.m4a' in r.request.url:
            hits['n'] += 1
            if mode == 'once' and hits['n'] >= 2:
                r.fulfill(status=200, content_type='audio/wav', body=WAV)
            else:
                r.fulfill(status=503, content_type='text/plain', body='cold start')
        else:
            r.continue_()
    page.route('**/*', route)
    page.goto(base + '/', wait_until='domcontentloaded')
    page.wait_for_timeout(800)
    # 触发一次加载
    page.evaluate("""() => { const a=document.querySelector('#audio');
        a.src='https://t.example/ep01.wav'; a.load(); }""")
    # 🔴 轮询「用户真正看到的文字」（不 hook createElement —— 有的页面走 HubShell.toast）
    seen = False
    deadline = time.time() + 9
    while time.time() < deadline:
        try:
            if page.evaluate("() => (document.body.innerText||'').includes('没声音')"):
                seen = True
                break
        except Exception:
            pass
        page.wait_for_timeout(200)
    return hits['n'], seen


def main():
    srv, port = serve()
    base = 'http://127.0.0.1:%d' % port
    print('本地服务器: %s' % base)
    with sync_playwright() as p:
        br = p.chromium.launch()

        print('\n--- 场景 A：第一次 503 → 必须自动救回来，且不弹提示 ---')
        pg = br.new_page(); n, toast = run(pg, base, 'once'); pg.close()
        check('A1 发生了重试（请求次数 ≥ 2）', n >= 2, '实际 %d 次' % n)
        check('A2 重试期间没有弹「没声音」提示', toast is False, '提示出现=%s' % toast)

        print('\n--- 场景 B：一直 503 → 必须最终如实报错，且有上限 ---')
        pg = br.new_page(); n2, toast2 = run(pg, base, 'always'); pg.close()
        check('B1 最终弹出了「没声音」提示（不能永远静默）', toast2 is True)
        check('B2 重试有上限（请求次数 ≤ 5）', n2 <= 5, '实际 %d 次' % n2)

        print('\n--- 场景 C：readyState 一直 < 1（连 error 都不触发）→ 看门狗必须兜住 ---')
        pg = br.new_page()
        pg.goto(base + '/', wait_until='domcontentloaded')
        pg.wait_for_timeout(800)
        # 让 audio 元素处于「永远没到位」的状态：不 src、不 load
        pg.evaluate("""() => { const a=document.querySelector('#audio');
            a.__ro = true; }""")
        # 手动触发看门狗路径：模拟 loadstart 后一直不 ready
        pg.evaluate("""() => { const a=document.querySelector('#audio');
            a.dispatchEvent(new Event('loadstart')); }""")
        seenC = False
        deadline = time.time() + 14
        while time.time() < deadline:
            try:
                if pg.evaluate("() => (document.body.innerText||'').includes('没声音')"):
                    seenC = True
                    break
            except Exception:
                pass
            pg.wait_for_timeout(200)
        pg.close()
        check('C1 看门狗兜住了（连 error 都不触发时也如实报错）', seenC)

        print('\n--- 反向验证：不加载补丁 ⇒ B 场景必然「静默失败」---')
        srv2, port2 = serve()
        # 造一个不带补丁的服务器
        class H2(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = (PAGE % {'patch': ''}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        s2 = socketserver.TCPServer(('127.0.0.1', 0), H2)
        threading.Thread(target=s2.serve_forever, daemon=True).start()
        base2 = 'http://127.0.0.1:%d' % s2.server_address[1]
        pg = br.new_page(); n3, toast3 = run(pg, base2, 'always'); pg.close()
        s2.shutdown()
        check('R1 没有补丁时不会弹提示（= 静默失败，反证补丁在起作用）', toast3 is False,
              '实际提示=%s 请求=%d' % (toast3, n3))

        br.close()
    srv.shutdown()

    print('\n═══ %d 通过 / %d 失败 ═══' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  ❌ ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
