#!/usr/bin/env python3
"""
本地 HLS 代理（多期共用）：给 ffmpeg 补上 NHK 需要的 Referer + 媒体令牌。

问题：NHK 的分片 URL 必须带 ?hdnts=<媒体令牌>，且要 Referer；
      ffmpeg 走 crypto+https 时会丢掉 -headers 里的自定义头 → 403。

用法：
  proxy = hls_proxy.start(port)
  proxy.set("/ep001", segments=[...], key=b16bytes)     # 注册一期
  ffmpeg -i http://127.0.0.1:PORT/ep001/pl.m3u8 ...
"""
import http.server
import socketserver
import threading
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REFERER = "https://news.web.nhk/"


class _Registry:
    def __init__(self):
        self.eps = {}
        self.lock = threading.Lock()

    def set(self, name, segments, key):
        with self.lock:
            self.eps[name] = {"segments": list(segments), "key": key}

    def get(self, name):
        with self.lock:
            return self.eps.get(name)


REG = _Registry()


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            return self._send(404, b"not found")
        name = parts[0]
        ep = REG.get(name)
        if not ep:
            return self._send(404, b"no episode")
        port = self.server.server_address[1]
        what = parts[1]

        if what == "pl.m3u8":
            lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:7",
                     "#EXT-X-MEDIA-SEQUENCE:1", "#EXT-X-PLAYLIST-TYPE:VOD"]
            if ep["key"]:
                lines.append('#EXT-X-KEY:METHOD=AES-128,URI="http://127.0.0.1:%d/%s/key.bin"'
                             % (port, name))
            for i in range(len(ep["segments"])):
                lines.append("#EXTINF:6.006,")
                lines.append("http://127.0.0.1:%d/%s/seg/%d.ts" % (port, name, i))
            lines.append("#EXT-X-ENDLIST")
            return self._send(200, ("\n".join(lines) + "\n").encode(),
                              "application/vnd.apple.mpegurl")

        if what == "key.bin":
            k = ep["key"]
            if not k:
                return self._send(404, b"no key")
            return self._send(200, k, "application/octet-stream")

        if what == "seg":
            try:
                idx = int(parts[2].split(".")[0])
            except Exception:
                return self._send(404, b"bad seg")
            segs = ep["segments"]
            if idx >= len(segs):
                return self._send(404, b"no seg")
            try:
                req = urllib.request.Request(segs[idx], headers={
                    "User-Agent": UA, "Referer": REFERER, "Accept": "*/*",
                    "Origin": "https://news.web.nhk"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
            except Exception as e:
                return self._send(502, str(e).encode())
            return self._send(200, body, "video/mp2t")
        return self._send(404, b"not found")


class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(port=8099):
    srv = Srv(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
