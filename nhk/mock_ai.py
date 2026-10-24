#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mock_ai.py —— AI 中转的**本地假实现**（给 test_ai.py / test_ai_copy.py 用）

🔴 真接口每次调用都烧 MiniMax 周额度 ⇒ 测试一律用这个 mock，真接口只在最后验一次。
🔴 契约（必须与前端 app.js 的 aiBuildBody() **逐字一致**）：
    POST {AI_ENDPOINT}
    body = {scene, title, episode, ja, zh, prev[], next[], word, refs[]}
    resp = {ok: true, text: "<markdown>"}

用法：
    python3 mock_ai.py 8898
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
COUNT = {"n": 0}


def build_text(body):
    """按中转侧的规则拼出回复（结构要能被前端的 aiSplit() 正确分流）"""
    ja = body.get("ja", "")
    zh = body.get("zh", "")
    word = body.get("word", "")
    lines = ["## 原文", ja or "（无）", "", "## 中文翻译", zh or "（无）", ""]
    # 逐词拆解：🔴 实测 M3 用 markdown **表格**，表头是「| 日语 | 中文意思 | 词性／语法功能 |」
    #   （表头含「语法」二字 ⇒ 前端的标题判定必须「整行相等」，不能「包含关键词」）
    lines += ["## 逐词拆解", "",
              "| 日语 | 中文意思 | 词性／语法功能 |",
              "|---|---|---|",
              "| %s | %s | 名词 |" % (word or "（本次未指定单词）", word or "（无）"), ""]
    lines += ["## 语法说明", "", "这是测试用的语法说明。", ""]
    lines += ["## 整句结构框架", "", "A は 【B】 言っています", ""]
    return "\n".join(lines)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        # 🔴 浏览器里页面在 127.0.0.1:另一个端口 ⇒ **跨域**
        #    前端发的是 Content-Type: application/json ⇒ **不是** CORS 简单头 ⇒ 会先发 OPTIONS 预检
        #    ⇒ mock 必须回 CORS 头 + 处理 OPTIONS，否则浏览器直接拦掉（前端只看到「AI 暫時不可用」）
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "600")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        COUNT["n"] += 1
        # 🔴 每次调用都记一行（测试用来验「前端真的发了什么」）
        log = os.environ.get("MOCK_LOG", "")
        if log:
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"body": body}, ensure_ascii=False) + "\n")
            except Exception:
                pass
        # 契约自检：少一个字段就报错（前端/中转不一致 ⇒ 立刻炸出来）
        need = ["scene", "title", "episode", "ja", "zh", "prev", "next", "word", "refs"]
        missing = [k for k in need if k not in body]
        if missing:
            out = json.dumps({"ok": False, "error": "missing: %s" % ",".join(missing)}).encode()
            self.send_response(400)
        else:
            out = json.dumps({"ok": True, "text": build_text(body)}, ensure_ascii=False).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self._cors()
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        out = json.dumps({"ok": True, "calls": COUNT["n"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    print("mock AI on :%d" % PORT)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
