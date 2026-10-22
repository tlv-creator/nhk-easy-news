#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：给 update_listen_mirror.py / check_sites_alive.py 造反例去撞

为什么要有这个文件：
  「每写完一个工具，立刻造一个反例去撞它」—— 不是可选项。
  而且**光说「旧版有 bug」不算证据** ⇒ 这里用**旧版二进制**（fixtures/old_update_listen_mirror.py，
  字节级来自仓库）在同一个 mock 上跑一遍，正反两向对拍。

跑法：
    python3 tools/test_listen_tools.py
退出码：0 = 全过 / 1 = 有失败
"""
import base64, json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.join(HERE, "update_listen_mirror.py")
ALIVE = os.path.join(HERE, "check_sites_alive.py")
OLD = os.path.join(HERE, "fixtures", "old_update_listen_mirror.py")

OLD_MIRROR = "https://oldoldoldold.space.mcode.cn"
NEW_MIRROR = "https://newnewnewnew.space.mcode.cn"

VERSION_JSON = json.dumps({
    "app": "每日听力记录",
    "mirror": OLD_MIRROR,
    "permanent_entry": "https://tlv-creator.github.io/nhk-easy-news/listening/",
    "updated": "2026-01-01 00:00:00",
}, ensure_ascii=False, indent=1)

INDEX_HTML = """<!DOCTYPE html>
<html><head><title>每日听力记录</title></head><body>
<div class="note"><p><b>加载慢？</b>用国内加速通道：<a href="%s">%s</a></p></div>
</body></html>
""" % (OLD_MIRROR, OLD_MIRROR)

# 另一个 mcode 链接（备用通道）—— 选择式替换必须放它一马
INDEX_HTML_TWO = """<!DOCTYPE html>
<html><head><title>每日听力记录</title></head><body>
<div class="note"><p><b>加载慢？</b>用国内加速通道：<a href="%s">%s</a></p></div>
<div class="note"><p>备用加速通道 B：<a href="https://backupbkbkbkbk.space.mcode.cn">备用</a></p></div>
</body></html>
""" % (OLD_MIRROR, OLD_MIRROR)


# ---------------- mock 仓库 ----------------

class Repo(object):
    def __init__(self):
        self.files = {}
        self.rendered = {}
        self.hooks = {}
        self.n = 0
        self.hits = 0          # ← 断言「真的打到了 mock」，防脚本打到真 GitHub
        self.lock = threading.Lock()

    def newsha(self):
        self.n += 1
        return "sha%04d" % self.n

    def set(self, path, text):
        with self.lock:
            self.files[path] = {"text": text, "sha": self.newsha()}

    def get(self, path):
        with self.lock:
            return self.files.get(path)

    def put(self, path, text, sha):
        with self.lock:
            for h in self.hooks.get(path, []):
                res = h(self, path, text, sha)
                if res is not None:
                    return res
            cur = self.files.get(path)
            if cur and sha != cur["sha"]:
                return (409, {"message": "sha mismatch"})
            self.files[path] = {"text": text, "sha": self.newsha()}
            return (200, {"content": {"sha": self.files[path]["sha"]}})


REPO = Repo()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        REPO.hits += 1
        p = self.path
        if "/contents/" in p:
            path = p.split("/contents/", 1)[1]
            f = REPO.get(path)
            if not f:
                return self._send(404, {"message": "Not Found"})
            return self._send(200, {"sha": f["sha"],
                                    "content": base64.b64encode(f["text"].encode()).decode()})
        if p.startswith("/raw/"):
            f = REPO.get(p[len("/raw/"):])
            if not f:
                return self._send(404, b"nope", "text/plain")
            return self._send(200, f["text"].encode(), "text/plain")
        if p.startswith("/pages/"):
            t = REPO.rendered.get(p[len("/pages/"):])
            if t is None:
                return self._send(404, b"nope", "text/plain")
            return self._send(200, t.encode(), "text/html")
        return self._send(404, {"message": "Not Found"})

    def do_PUT(self):
        REPO.hits += 1
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode())
        path = self.path.split("/contents/", 1)[1]
        text = base64.b64decode(body["content"]).decode()
        code, resp = REPO.put(path, text, body.get("sha"))
        return self._send(code, resp)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def run(script, args, port, extra_env=None):
    env = dict(os.environ)
    env.update({
        "LISTEN_GH_BASE": "http://127.0.0.1:%d/repos/o/r/contents" % port,
        "LISTEN_GH_RAW": "http://127.0.0.1:%d/raw" % port,
        "LISTEN_PAGES_BASE": "http://127.0.0.1:%d/pages" % port,
        "LISTEN_LIST_API": "http://127.0.0.1:%d/repos/o/r/contents/protected-sites.json" % port,
        "LISTEN_LIST_RAW": "http://127.0.0.1:%d/raw/protected-sites.json" % port,
        "LISTEN_LIST_PAGES": "http://127.0.0.1:%d/pages/protected-sites.json" % port,
        "GH_TOKEN": "fake-token-for-test",
    })
    if extra_env:
        env.update(extra_env)
    REPO.hits = 0
    p = subprocess.run([sys.executable, script] + args, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    out = p.stdout.decode("utf-8", "replace")
    if REPO.hits == 0:
        # 🔴 一条请求都没到 mock ⇒ 这个脚本没认 env 覆盖 ⇒ 它打到了**真 GitHub**
        #    ⇒ 这次的「结果」不是我们要验的那个，必须当场判失败
        raise RuntimeError("脚本没有访问 mock（hits=0）⇒ 它打到了真 GitHub，测试无效！")
    return p.returncode, out


# ---------------- 测试框架 ----------------

TESTS = []
FAIL = []


def test(fn):
    TESTS.append(fn)
    return fn


def check(cond, msg):
    if cond:
        print("   ✅ %s" % msg)
    else:
        print("   ❌ %s" % msg)
        FAIL.append(msg)
    return cond


def reset(version=VERSION_JSON, html=INDEX_HTML):
    REPO.files.clear()
    REPO.rendered.clear()
    REPO.hooks.clear()
    REPO.set("listening/version.json", version)
    REPO.set("listening/index.html", html)
    # 渲染端先跟源端一致（测试「渲染端慢一拍」时再单独改）
    REPO.rendered["listening/index.html"] = html


# ---------------- 用例 ----------------

@test
def t1_正向_改镜像():
    """正向：新镜像写进去，退出码 0，页面链接跟着换"""
    srv, port = start_server()
    try:
        reset()
        code, out = run(NEW, [NEW_MIRROR, "--no-rendered"], port)
        check(code == 0, "退出码 = 0（实际 %d）" % code)
        vj = json.loads(REPO.get("listening/version.json")["text"])
        check(vj.get("mirror") == NEW_MIRROR, "version.json.mirror = 新地址")
        html = REPO.get("listening/index.html")["text"]
        check(NEW_MIRROR in html, "index.html 里有新地址")
        check(OLD_MIRROR not in html, "index.html 里旧地址没了")
        check("回源复核" in out, "输出明确写了「回源复核」")
    finally:
        srv.shutdown()


@test
def t2_并发_不覆盖别人的改动():
    """反向 A：读之后别人推过 → 必须 409 → 重读重做，**别人的改动不能被覆盖**"""
    srv, port = start_server()
    try:
        reset()
        fired = {"n": 0}

        def conflict(repo, path, text, sha):
            if fired["n"] == 0:
                fired["n"] += 1
                # 模拟「另一个人在同一秒推了」：文件内容变了，sha 也变了
                cur = repo.files[path]
                j = json.loads(cur["text"])
                j["_other_person"] = "KEEP-ME"
                repo.files[path] = {"text": json.dumps(j, ensure_ascii=False, indent=1),
                                    "sha": repo.newsha()}
            return None   # 继续走正常 sha 比对 → 409

        REPO.hooks["listening/version.json"] = [conflict]
        code, out = run(NEW, [NEW_MIRROR, "--no-rendered"], port)
        vj = json.loads(REPO.get("listening/version.json")["text"])
        check(vj.get("_other_person") == "KEEP-ME", "别人的改动还在（没被覆盖）")
        check(vj.get("mirror") == NEW_MIRROR, "我的改动也写进去了（重读后重做）")
        check("409" in out, "输出里有 409（说明冲突被识别了）")
        check(code == 0, "最终退出码 = 0（实际 %d）" % code)
    finally:
        srv.shutdown()


@test
def t3_回读判定_必须影响退出码():
    """反向 B：写进去的内容不对 → 回源复核必须**不通过且退出码非 0**（不能只打印 ❌ 然后退 0）"""
    srv, port = start_server()
    try:
        reset()

        def corrupt(repo, path, text, sha):
            cur = repo.files[path]
            repo.files[path] = {"text": json.dumps({"mirror": "https://wrongwrongwrong.space.mcode.cn"},
                                                    ensure_ascii=False), "sha": repo.newsha()}
            return (200, {"content": {"sha": repo.files[path]["sha"]}})   # 假装写成功

        REPO.hooks["listening/version.json"] = [corrupt]
        code, out = run(NEW, [NEW_MIRROR, "--no-rendered"], port)
        check(code != 0, "退出码非 0（实际 %d）—— 旧版这里是 0 = 假绿灯" % code)
        check("回源复核不通过" in out, "输出说了「回源复核不通过」")
    finally:
        srv.shutdown()


@test
def t4_选择式替换_不误伤别的链接():
    """反向 C：页面上有两个 mcode 链接 → 只换「加速通道」那个，另一个原样保留"""
    srv, port = start_server()
    try:
        reset(html=INDEX_HTML_TWO)
        code, out = run(NEW, [NEW_MIRROR, "--no-rendered"], port)
        html = REPO.get("listening/index.html")["text"]
        check("https://backupbkbkbkbk.space.mcode.cn" in html, "备用通道 B 原样保留（没被误伤）")
        check(NEW_MIRROR in html, "加速通道换成新地址")
        check(OLD_MIRROR not in html, "旧加速地址没了")
        check(code == 0, "退出码 = 0（实际 %d）" % code)
    finally:
        srv.shutdown()


@test
def t5_渲染端慢一拍_不影响判定():
    """反向 D：渲染端还是旧内容 → 源端是真相 → **退出码仍 0**，但必须**说出来**"""
    srv, port = start_server()
    try:
        reset()
        REPO.rendered["listening/index.html"] = INDEX_HTML   # 渲染端永远不更新
        # 这一条就是要测渲染端 → 不关它，但把重试预算调小（别让测试自己变慢）
        code, out = run(NEW, [NEW_MIRROR], port,
                        extra_env={"LISTEN_RENDERED_TRIES": "2", "LISTEN_RENDERED_GAP": "1"})
        check(code == 0, "退出码 = 0（源端才是真相；实际 %d）" % code)
        check("渲染端" in out and ("旧内容" in out or "还没" in out),
              "输出明确指出渲染端还没更新")
    finally:
        srv.shutdown()


@test
def t6_旧版二进制_并发时确实会覆盖():
    """**用旧版二进制证明 bug 是真的**（不是我的推测）：同样条件下旧版会静默覆盖别人的改动"""
    if not os.path.exists(OLD):
        print("   ⚠️ 跳过：没有旧版 fixture（%s）" % OLD)
        return
    srv, port = start_server()
    try:
        reset()
        fired = {"n": 0}

        def conflict(repo, path, text, sha):
            if fired["n"] == 0:
                fired["n"] += 1
                cur = repo.files[path]
                j = json.loads(cur["text"])
                j["_other_person"] = "KEEP-ME"
                repo.files[path] = {"text": json.dumps(j, ensure_ascii=False, indent=1),
                                    "sha": repo.newsha()}
            return None

        REPO.hooks["listening/version.json"] = [conflict]
        code, out = run(OLD, [NEW_MIRROR], port)
        vj = json.loads(REPO.get("listening/version.json")["text"])
        check(vj.get("_other_person") != "KEEP-ME",
              "旧版确实把别人的改动覆盖掉了（证明新版的 409 保护不是多余的）")
        check(code == 0, "旧版还退出 0（**静默丢失** —— 这就是最阴的形态）")
    finally:
        srv.shutdown()


@test
def t7_旧版二进制_回读不一致也退0():
    """旧版的第二个洞：回读打印 ❌ 但退出码仍是 0（假绿灯）"""
    if not os.path.exists(OLD):
        print("   ⚠️ 跳过：没有旧版 fixture")
        return
    srv, port = start_server()
    try:
        reset()

        def corrupt(repo, path, text, sha):
            repo.files[path] = {"text": json.dumps({"mirror": "https://wrongwrongwrong.space.mcode.cn"},
                                                    ensure_ascii=False), "sha": repo.newsha()}
            return (200, {"content": {"sha": repo.files[path]["sha"]}})

        REPO.hooks["listening/version.json"] = [corrupt]
        code, out = run(OLD, [NEW_MIRROR], port)
        check(code == 0, "旧版退出码 = 0，尽管回读是错的（实际 %d）—— 假绿灯坐实" % code)
        check("❌" in out, "旧版确实打印了 ❌（即：只靠打印告诉你，没挡住）")
    finally:
        srv.shutdown()


@test
def t8_清单_优先读源端():
    """check_sites_alive.py：清单必须从**源端**读（渲染端给旧清单时也要用源端那份）"""
    srv, port = start_server()
    try:
        src_list = {"protected_sites": [
            {"domain": "aaaaaaaaaaaa", "project": "src-only"},
            {"domain": "bbbbbbbbbbbb", "project": "deleted", "status": "DELETED"},
        ]}
        old_list = {"protected_sites": [
            {"domain": "aaaaaaaaaaaa", "project": "src-only"},
            {"domain": "bbbbbbbbbbbb", "project": "deleted"},   # 渲染端那版没标 DELETED
        ]}
        REPO.set("protected-sites.json", json.dumps(src_list, ensure_ascii=False))
        REPO.rendered["protected-sites.json"] = json.dumps(old_list, ensure_ascii=False)
        code, out = run(ALIVE, [], port)
        check("源端" in out, "输出说明了清单来源是源端")
        check("已标记 DELETED" in out, "源端那版里的 DELETED 条目被识别了（渲染端那版没有）")
        check("渲染端" not in out.split("清单来源")[1].split("\n")[0], "没有退到渲染端")
    finally:
        srv.shutdown()


@test
def t9_清单拉不到_必须拒绝放行():
    """闸门找不到东西必须 FAIL（exit 2），不是「跳过 + 放行」"""
    srv, port = start_server()
    try:
        # 🔴 上一版我忘了清 mock → 清单还在 → 测的其实不是「拉不到」这一条
        REPO.files.clear(); REPO.rendered.clear(); REPO.hooks.clear()
        code, out = run(ALIVE, [], port)
        check(code == 2, "退出码 = 2（实际 %d）" % code)
        check("不许放行" in out, "输出说了「不许放行」")
    finally:
        srv.shutdown()


@test
def t10_show_必须打印JSON():
    """--show 不能走成「打印帮助」—— 刚写完就踩过（--show 被当成 flag 过滤掉了）"""
    srv, port = start_server()
    try:
        reset()
        code, out = run(NEW, ["--show"], port)
        check(code == 0, "退出码 = 0（实际 %d）" % code)
        check("每日听力记录" in out, "打印的是 version.json 内容（不是帮助文本）")
        check("源端" in out, "说明了是从源端读的")
        check("用法：" not in out, "没有打印帮助文本")
    finally:
        srv.shutdown()


def main():
    print("=== 自检：update_listen_mirror.py / check_sites_alive.py ===")
    for fn in TESTS:
        print("\n▶ %s" % fn.__doc__.strip().split("\n")[0])
        try:
            fn()
        except Exception as e:
            print("   ❌ 用例崩了：%s: %s" % (type(e).__name__, e))
            FAIL.append("用例 %s 崩了" % fn.__name__)
    print("\n" + "=" * 60)
    if FAIL:
        print("❌ 失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("   - %s" % f)
        return 1
    print("✅ 全部通过（%d 个用例）" % len(TESTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
