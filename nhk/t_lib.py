#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_lib.py —— 测试公用件：本地静态服务器 + 浏览器 + 假原生桥"""
import http.server
import json
import os
import socket
import socketserver
import threading

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "app")


def _find_chrome():
    """🔴🔴 2026-11-24 修：**别把浏览器版本号写死**

    坑：原来写死 `/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome`
      ⇒ 沙箱一重置，playwright 缓存里的版本就变了（今天是 1255）
      ⇒ 报 `executable doesn't exist` ⇒ **验收脚本自己跑不起来 = 没有验收**
      （和「闸门自己跑不起来 = 没有闸门」同一族）
    ✅ 现在按顺序找：写死的旧路径 → ms-playwright 缓存里任意版本 → 系统 chromium
    """
    import glob
    fixed = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
    if os.path.exists(fixed):
        return fixed
    cands = sorted(glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome"))
    cands += sorted(glob.glob("/root/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"))
    for c in cands:
        if os.path.exists(c):
            return c
    for c in ("/usr/local/bin/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(c):
            return c
    return fixed


CHROME = _find_chrome()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        http.server.SimpleHTTPRequestHandler.end_headers(self)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, directory=SITE):
        self.port = free_port()
        handler = lambda *a, **k: Quiet(*a, directory=directory, **k)
        self.httpd = socketserver.TCPServer(("127.0.0.1", self.port), handler)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# 假原生桥：🔴 契约照 app.js 的 NativeImpl
#    playerState() → {"pos":ms,"dur":ms,"ready":bool,"playing":bool,
#                     "actualPlaying":bool,"completed":bool,"error":""}
NATIVE_STUB = r"""
window.__played = [];
window.__paused = 0;
window.__seeked = [];
window.__state = { pos: 0, dur: 0, ready: true, playing: true,
                   actualPlaying: true, completed: false, error: '' };
window.Android = {
  playAudio: function (u, startMs) { window.__played.push({ url: u, startMs: startMs }); },
  pauseAudio: function () { window.__paused++; },
  resumeAudio: function () {},
  stopAudio: function () {},
  seekAudio: function (ms) { window.__seeked.push(ms); },
  setSpeed: function () {},
  playerState: function () { return JSON.stringify(window.__state); },
  resolveAudio: function () { return ''; },
  ping: function () { return '{}'; }
};
"""


async def new_page(browser, base, native=True):
    ctx = await browser.new_context(locale="ja-JP", permissions=["clipboard-read", "clipboard-write"])
    pg = await ctx.new_page()
    if native:
        await pg.add_init_script(NATIVE_STUB)
    pg.on("dialog", lambda d: asyncio_safe_close(d))
    return ctx, pg


def asyncio_safe_close(d):
    try:
        d.dismiss()
    except Exception:
        pass
