#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平台 OAuth 设备码流：起一次授权 → 后台轮询换 token。

用法：
  python3 oauth_device.py start        # 生成授权链接，存到 /workspace/.secrets/oauth_device.json
  python3 oauth_device.py poll         # 轮询换 token（成功写 /workspace/.secrets/platform_token.json）
  python3 oauth_device.py poll --wait  # 同上，但一直等到成功/过期
"""
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SECRETS = Path("/workspace/.secrets")
STATE = SECRETS / "oauth_device.json"
TOKEN = SECRETS / "platform_token.json"
AUTH_BASE = "https://account.minimax.cn"
CLIENT_ID = "mcode_tool"
SCOPE = "agent.mcp.matrix agent.mcp.app"


def pkce():
    v = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def post(path, body):
    req = urllib.request.Request(AUTH_BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": type(e).__name__, "error_description": str(e)}


def cmd_start():
    verifier, challenge = pkce()
    st, data = post("/oauth2/device/code", {
        "client_id": CLIENT_ID, "code_challenge": challenge,
        "code_challenge_method": "S256", "scope": SCOPE})
    if st != 200:
        print("启动失败:", st, data)
        return 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"data": data, "verifier": verifier}), encoding="utf-8")
    print("授权链接（点开 → 登录 → 同意授权）：")
    print(data["verification_uri_complete"])
    print("user_code:", data["user_code"], "  有效期:", data["expires_in"], "秒")
    return 0


def cmd_poll(wait=True):
    if not STATE.exists():
        print("没有 oauth_device.json，先跑 start")
        return 1
    state = json.loads(STATE.read_text(encoding="utf-8"))
    dev, verifier = state["data"], state["verifier"]
    deadline = time.time() + dev.get("expires_in", 300) + 30
    while True:
        st, data = post("/oauth2/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID, "device_code": dev["device_code"],
            "code_verifier": verifier})
        if st == 200 and "access_token" in data:
            TOKEN.parent.mkdir(parents=True, exist_ok=True)
            TOKEN.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(TOKEN, 0o600)
            print("✅ 拿到 token，scope =", data.get("scope"), " expires_in =", data.get("expires_in"))
            return 0
        err = data.get("error", "")
        if err == "authorization_pending":
            if not wait:
                print("还在等用户授权…")
                return 2
            time.sleep(max(3, dev.get("interval", 3)))
            continue
        if err == "slow_down":
            time.sleep(5)
            continue
        print("失败:", st, data)
        return 1
    print("过期")
    return 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        sys.exit(cmd_start())
    elif cmd == "poll":
        sys.exit(cmd_poll("--wait" not in sys.argv))
    else:
        print(__doc__)
        sys.exit(2)
