#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mavis API 客户端（带 x-signature / yy 签名）——用来直接调平台的 cron 接口。

签名算法（从前端 chunk 反解出来，2026-10-21）：
  H = md5_hex
  bodyStr = "" (GET) 或 json.dumps(body, separators=(',',':'))  [前端用 JSON.stringify]
  fullUrl = path + ("?" or "&") + urlencode(params)
  x-timestamp = <unix 秒>
  x-signature = H(f"{timestamp}I*7Cf%WZ#S&%1RlZJ&C2{bodyStr}")
  yy          = H(f"{quote(fullUrl, safe='')}_{bodyStr}{H(str(time_ms))}ooui")

用法：
  python3 mavis_client.py list
  python3 mavis_client.py create <agent_name> <cron_name> <schedule> <tz> <prompt_file> [session_json]
  python3 mavis_client.py delete <cron_id>
  python3 mavis_client.py show <cron_id>
  python3 mavis_client.py trigger <cron_id>
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://agent.minimax.cn"
CLOUD = "/minimax-cloud/api/v1"
SECRET = "I*7Cf%WZ#S&%1RlZJ&C2"
TOKEN_FILE = Path("/workspace/.secrets/platform_token.json")


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def access_token() -> str:
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["access_token"]


def build_params(now_ms: int) -> dict:
    tz = int(time.timezone) // 3600 if time.daylight == 0 else -(-time.timezone) // 3600
    return {
        "device_platform": "web",
        "biz_id": "3",
        "app_id": "3001",
        "version_code": "22201",
        "unix": str(int(now_ms / 1000)),
        "timezone_offset": str(-60 * -tz),
        "sys_language": "zh",
        "lang": "zh",
        "device_id": "0",
        "os_name": "Linux",
        "browser_name": "Chrome",
        "user_id": "0",
        "screen_width": "1920",
        "screen_height": "1080",
        "client": "web",
        "region": "cn",
        "token": access_token(),
    }


def request(method: str, path: str, body=None, extra_headers=None, use_token=True):
    now_ms = int(time.time() * 1000)
    ts = now_ms // 1000
    params = build_params(now_ms) if use_token else {}
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    full_url = f"{path}?{qs}" if qs else path
    body_str = ""
    if body is not None:
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    headers = {
        "Accept": "application/json",
        "x-timestamp": str(ts),
        "x-signature": md5(f"{ts}{SECRET}{body_str}"),
        "yy": md5(f"{urllib.parse.quote(full_url, safe='')}_{body_str}{md5(str(now_ms))}ooui"),
    }
    if body_str:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    url = BASE + full_url
    data = body_str.encode("utf-8") if body_str else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def jprint(status, txt, n=2000):
    try:
        obj = json.loads(txt)
        print(json.dumps(obj, ensure_ascii=False, indent=2)[:n])
    except Exception:
        print(txt[:n])
    print(f"[HTTP {status}]")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "list":
        st, txt = request("GET", f"{CLOUD}/cron")
        jprint(st, txt, 4000)
    elif cmd == "show":
        st, txt = request("GET", f"{CLOUD}/cron/{sys.argv[2]}")
        jprint(st, txt, 3000)
    elif cmd == "delete":
        st, txt = request("DELETE", f"{CLOUD}/cron/{sys.argv[2]}")
        jprint(st, txt, 2000)
    elif cmd == "trigger":
        st, txt = request("POST", f"{CLOUD}/cron/{sys.argv[2]}/trigger", body={})
        jprint(st, txt, 2000)
    elif cmd == "create":
        agent, cron_name, schedule, tz = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        prompt = Path(sys.argv[6]).read_text(encoding="utf-8")
        session = json.loads(sys.argv[7]) if len(sys.argv) > 7 else {"mode": "new"}
        body = {
            "schedule": schedule,
            "prompt": prompt,
            "timezone": tz,
            "session": session,
            "enabled": True,
        }
        st, txt = request("POST", f"{CLOUD}/agent/{urllib.parse.quote(agent, safe='')}/cron/"
                                   f"{urllib.parse.quote(cron_name, safe='')}", body=body)
        jprint(st, txt, 3000)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
