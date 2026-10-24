#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行为对拍：本地起 patched 的 index.js，逐路径跟**线上现役**逐字比

为什么要做（协作方的纪律）：文本 diff 只能证明「我插了一段东西」，
**行为对拍才能证明「现役的三条路由一个字都没坏」**。

⚠️ 诚实的边界：
  · POST /ai 带真 ja 的那条**没对拍**（会真调 MiniMax ⇒ 烧钱）⇒ 差异未完全排除
  · GET /ai/quota 必然不同（本地没有 MM_API_KEY 环境变量）⇒ 属预期差异
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "/workspace/listen-log/build/aiproxy"
PATCHED = os.path.join(BASE, "patched")
LIVE = "https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com"
PORT = 9099
LOCAL = "http://127.0.0.1:%d" % PORT

CASES = [
    ("GET",  "/ai",            None,                         "现役默认信息"),
    ("GET",  "/ai/prompt",     None,                         "真 prompt（顺带证明基底 == 现役）"),
    ("GET",  "/ai/audio-config", None,                       "音频地址表"),
    ("GET",  "/ai/quota",      None,                         "配额（**预期不同**：本地没 MM_API_KEY）"),
    ("GET",  "/nope",          None,                         "未知路径"),
    ("POST", "/ai",            {"scene": "x"},               "AI 分支：缺 ja ⇒ bad_request（不烧额度）"),
    ("POST", "/nope",          {"foo": 1},                   "未知路径 POST"),
    ("GET",  "/vocab-ops",     None,                         "新端点 GET（本地 405 / 线上默认信息）"),
    ("POST", "/vocab-ops",     {"action": "ping"},           "新端点 ping"),
    ("POST", "/vocab-ops",     {"action": "delete_log", "teacher_id": "2013",
                                "teacher_hash": "x", "id": 1, "class_key": "LISTEN"}, "新端点 delete_log（无 env ⇒ 如实报错）"),
]

EXPECT_DIFF = {
    ("GET", "/ai/quota"): "配额要真调上游 ⇒ 本地是黑洞（upstream_error），线上是真数据",
    ("GET", "/ai/audio-config"): "🔴 **现役线上那份比 zip 旧**（没有 audio-config 分支）⇒ 传上去会顺带补上",
    ("GET", "/vocab-ops"): "线上还没这个路由 ⇒ 走默认信息；本地 405",
    ("POST", "/vocab-ops"): "线上还没这个路由 ⇒ 走 AI 分支；本地是我的端点",
    ("GET", "/nope"): "线上 404 INVALID_PATH 是**网关**挡的（网关只映射 /ai/*）⇒ 本地直连函数当然不一样",
    ("POST", "/nope"): "同上（网关挡的）",
}


def fetch(base, method, path, body):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, "ERR:" + str(e)


def main():
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    # 塞一个**假 key** + 把上游指向黑洞 ⇒ 上游调用立刻失败（不烧额度、不等超时）
    # ⇒ 这样「本地 vs 线上」的差异就只剩**代码**差异
    env["MM_API_KEY"] = "sk-cp-dummy-for-local-diff"
    env["MM_BASE_URL"] = "http://127.0.0.1:1"
    env.pop("SB_URL", None)
    env.pop("SB_SERVICE_ROLE", None)
    srv = subprocess.Popen(["node", "index.js"], cwd=PATCHED, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            time.sleep(0.25)
            try:
                urllib.request.urlopen(LOCAL + "/ai", timeout=3).read()
                break
            except Exception:
                if srv.poll() is not None:
                    print("❌ 本地服务起不来：")
                    print(srv.stdout.read().decode("utf-8", "replace")[:800])
                    return 1

        same = diff = expected = 0
        for method, path, body, note in CASES:
            ls, lb = fetch(LOCAL, method, path, body)
            rs, rb = fetch(LIVE, method, path, body)
            key = (method, path)
            if lb == rb and ls == rs:
                same += 1
                print("  ✅ %-5s %-16s 逐字相同（%s）" % (method, path, note))
            elif key in EXPECT_DIFF:
                expected += 1
                print("  ⚠️  %-5s %-16s 预期不同（%s）\n        本地 %s %s\n        线上 %s %s"
                      % (method, path, EXPECT_DIFF[key], ls, lb[:110], rs, rb[:110]))
            else:
                diff += 1
                print("  ❌ %-5s %-16s **意外不同**（%s）\n        本地 %s %s\n        线上 %s %s"
                      % (method, path, note, ls, lb[:300], rs, rb[:300]))
        print("\n================ 结果 ================")
        print("逐字相同 %d / 预期不同 %d / 意外不同 %d" % (same, expected, diff))
        if diff:
            return 1
        print("✅ 现役路由没被改坏（意外不同 0）")
        return 0
    finally:
        srv.kill()


if __name__ == "__main__":
    sys.exit(main())
