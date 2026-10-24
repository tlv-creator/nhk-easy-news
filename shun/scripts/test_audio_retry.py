#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频失败处理三场景测试（Python 版）。

🔴🔴🔴 **2026-10-24：本 Shun 项目的权威实现是 `scripts/test_audio_retry.js`（15/0 全绿）**
   ⇒ 本 Python 版是**独立实现**（两份实现 = 会漂移）⇒ **本项目请用 JS 版**
   ⇒ ⚠️ 本版用 `?audio=` URL 参数换源；**实测在真实浏览器里 app.js 读到的 AUDIO_BASE
      不是 URL 参数的值** ⇒ 请求会打到**真实生产站** ⇒ 「失败场景」测不到真东西
   ⇒ ⇒ **别照抄这一份**，要模板就抄 JS 版里「拦截 app.js 替换 AUDIO_BASE_LOCAL 常量」
      那个做法（对应生产换站方式 set_audio_base.sh，测的仍是真实代码路径）。

（原始说明保留如下。）
音频失败处理三场景测试（Python 版）—— 通用模板。

背景（2026-10-23 实测踩到）：
  CDN 冷启动会瞬时 503 ⇒ 一次网络抖动就报「这一集没声音」= **假故障报给用户**。
  修法：**双路判定**（error 事件 + 看门狗）+ 最多 2 次重试（第 2 次带 cache-buster）。

🔴🔴 **两条判据原则（比测试代码本身值钱）**：
  ① **判据要落在「用户真正看到的」上** —— 读 `document.body.innerText`，
     **不要** hook `createElement`（有的页面走 `HubShell.toast`，根本不创建 div ⇒ 抓不到 = 假失败）。
  ② **轮询而不是固定 sleep**（机器忙的时候固定等待会假红）。

三个场景（**正反两向**）：
  A. 第一次 503、之后正常 → **必须自动救回来**（能解码出时长 + 请求 ≥2 次 + **没有**「没声音」提示）
  B. 一直 503              → **必须最终如实提示** + **请求次数有上限**（不无限刷）
  C. 🔴 **服务端挂起**（不返回、连 error 事件都不触发）
     → **必须靠看门狗兜住**并如实提示（**这一条防「静默失败」**：用户必须看到提示）

⚠️ **「光有双路判定还不够 ⇒ 得证明『只有一路可用时另一路真能兜住』」** —— C 就是干这个的。

用法（默认测本项目）：
    python3 scripts/test_audio_retry.py
换成别的项目（示例）：
    python3 scripts/test_audio_retry.py \\
        --page http://127.0.0.1:PORT/tadoku/index.html \\
        --block "**/version.json" --open-js "document.querySelector('.ep-item').click()" \\
        --expect-text 没声音
"""
import argparse
import http.server
import os
import re
import socketserver
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get(
    "CHROME_PATH", "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome")

PASS = 0
FAIL = 0
FAILS = []


def ok(cond, name, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ " + name)
    else:
        FAIL += 1
        FAILS.append(name + (("  → " + str(extra)) if extra is not None else ""))
        print("  ❌ " + name + (("  → " + str(extra)) if extra is not None else ""))


class Handler(http.server.SimpleHTTPRequestHandler):
    """静态站 + 三个「坏掉的音频」路由。"""

    hits = {"flaky": 0, "dead": 0, "hang": 0}
    web_dir = os.path.join(ROOT, "web")
    audio_dir = os.path.join(ROOT, "audio")

    def log_message(self, *a):        # 静音
        pass

    def _send_audio(self):
        f = os.path.join(self.audio_dir, os.path.basename(self.path))
        if not os.path.exists(f):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404")
            return
        st = os.stat(f)
        self.send_response(200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Length", str(st.st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with open(f, "rb") as fh:
            self.wfile.write(fh.read())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/flaky/"):
            Handler.hits["flaky"] += 1
            if Handler.hits["flaky"] == 1:      # 第一次 503（模拟 CDN 冷启动）
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"cold start")
                return
            return self._send_audio()
        if path.startswith("/dead/"):           # 一直 503
            Handler.hits["dead"] += 1
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"always down")
            return
        if path.startswith("/hang/"):           # 挂起：连 error 事件都不触发
            Handler.hits["hang"] += 1
            return
        # 静态文件
        rel = path.lstrip("/") or "index.html"
        f = os.path.join(Handler.web_dir, rel)
        if os.path.isfile(f):
            ctype = {".html": "text/html", ".js": "application/javascript",
                     ".css": "text/css", ".json": "application/json",
                     ".png": "image/png", ".m4a": "audio/mp4"}.get(
                         os.path.splitext(f)[1], "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            with open(f, "rb") as fh:
                self.wfile.write(fh.read())
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404")


def start_server():
    """🔴 端口用 **0**（系统分配）⇒ 绝不会撞上活着的旧服务器
    （「端口被占」= 打到的不是你要验的那个 —— 今晚的第 ⑤ 例）。"""
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def open_ep(page, open_js, expect_text, wait_ms):
    """打开某集 + **轮询**读状态（不是固定 sleep）。"""
    page.evaluate(open_js)
    deadline = time.time() + wait_ms / 1000.0
    st = None
    while time.time() < deadline:
        st = page.evaluate("""() => {
            const a = document.getElementById('au');
            const txt = (document.body.innerText || '');
            return {
              ready: a ? a.readyState : -1,
              dur: a ? a.duration : 0,
              err: a && a.error ? a.error.code : 0,
              hit: txt.indexOf(%s) >= 0
            };
        }""" % json_str(expect_text))
        if (st["ready"] >= 1 and st["dur"] > 60) or st["hit"]:
            break
        page.wait_for_timeout(300)
    return st


def json_str(s):
    import json
    return json.dumps(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default="")            # 留空 = 测本项目
    ap.add_argument("--block", default="**/shun.json",
                    help="要拦掉的引导页（它会覆盖本地音频地址 ⇒ 不拦会打到真实站）")
    ap.add_argument("--open-js", default=
                    "(document.querySelector('#epList .ep-item')||{}).click && "
                    "document.querySelector('#epList .ep-item').click()")
    ap.add_argument("--expect-text", default="没声音")
    ap.add_argument("--audio-prefix", default="")    # 留空 = 用本项目的三个坏路由
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    srv, port = start_server()
    base = "http://127.0.0.1:%d" % port
    page_url = args.page or (base + "/index.html")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=CHROME,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"])
            try:
                # ---------- A：第一次 503 ----------
                print("\n【A】第一次请求 503（CDN 冷启动）")
                Handler.hits["flaky"] = 0
                Handler.audio_base = base + "/flaky/" 
                page = browser.new_page(viewport={"width": 412, "height": 915})
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                # 🔴 必须拦掉引导页：它的 audio 字段会覆盖本地参数 ⇒ 测试会打到真实站 ⇒ 恒绿
                if args.block:
                    page.route(args.block, lambda r: r.abort())
                # ⚠️ 这里仍用 ?audio= 参数（**已知在本项目不可靠**，见文件头警告）
                #   ⇒ 本项目的权威测试是 scripts/test_audio_retry.js
                #   ⇒ 而替换常量正好对应生产行为（部署时 set_audio_base.sh 就是这么换站的）
                page.goto(page_url + ("&" if "?" in page_url else "?") +
                          "audio=" + quote(base + "/flaky/"),
                          wait_until="domcontentloaded")
                page.wait_for_selector(".cue", timeout=20000)
                st = open_ep(page, args.open_js, args.expect_text, 9000)
                ok(Handler.hits["flaky"] >= 2, "确实重试了（请求次数 ≥ 2）", Handler.hits["flaky"])
                ok(st["ready"] >= 1 and st["dur"] > 60, "重试后音频真的解码出来了", st)
                ok(not st["hit"], "**没有**弹提示（重试期间不打扰用户）", st)
                ok(not errs, "无 JS 错误", errs[:2])
                page.close()

                # ---------- B：一直 503 ----------
                print("\n【B】一直 503（真故障）")
                Handler.hits["dead"] = 0
                Handler.audio_base = base + "/dead/" 
                page = browser.new_page(viewport={"width": 412, "height": 915})
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                if args.block:
                    page.route(args.block, lambda r: r.abort())
                page.goto(page_url + ("&" if "?" in page_url else "?") +
                          "audio=" + quote(base + "/dead/"),
                          wait_until="domcontentloaded")
                page.wait_for_selector(".cue", timeout=20000)
                st = open_ep(page, args.open_js, args.expect_text, 12000)
                ok(Handler.hits["dead"] <= 5, "重试有上限（不会无限刷）", Handler.hits["dead"])
                ok(st["hit"], "最终如实提示（不能永远静默）", st)
                ok(not errs, "无 JS 错误", errs[:2])
                page.close()

                # ---------- C：服务端挂起 → 看门狗兜住 ----------
                print("\n【C】服务端挂起（连 error 事件都不触发）→ 看门狗必须兜住")
                Handler.hits["hang"] = 0
                Handler.audio_base = base + "/hang/" 
                page = browser.new_page(viewport={"width": 412, "height": 915})
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                if args.block:
                    page.route(args.block, lambda r: r.abort())
                page.goto(page_url + ("&" if "?" in page_url else "?") +
                          "audio=" + quote(base + "/hang/"),
                          wait_until="domcontentloaded")
                page.wait_for_selector(".cue", timeout=20000)
                st = open_ep(page, args.open_js, args.expect_text, 16000)
                ok(Handler.hits["hang"] >= 1, "请求确实发出去了", Handler.hits["hang"])
                ok(st["hit"], "**看门狗兜住了**并如实提示（没有静默失败）", st)
                ok(not errs, "无 JS 错误", errs[:2])
                page.close()
            finally:
                browser.close()
    finally:
        srv.shutdown()

    print("\n═══ %d 通过 / %d 失败 ═══" % (PASS, FAIL))
    if FAIL:
        print("失败项：\n  " + "\n  ".join(FAILS))
        sys.exit(1)


def quote(u):
    from urllib.parse import quote as q
    return q(u, safe="")


if __name__ == "__main__":
    main()
