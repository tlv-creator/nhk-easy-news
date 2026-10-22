#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新「每日听力记录」的加速镜像地址（canonical 在 GitHub Pages，永不变）

为什么需要这个脚本：
  canonical = https://tlv-creator.github.io/nhk-easy-news/listening/   ← 永久，学生用这个
  mirror    = https://<mcode域>/                                      ← 国内加速，**重建就换域名**
  ⇒ 重建镜像后，必须同步改两处：
      ① listening/version.json  的 mirror 字段
      ② listening/index.html   的「国内加速通道」链接
  ⇒ 靠人记 = 下次重建就忘了 ⇒ **做成一条命令**。

用法：
    python3 tools/update_listen_mirror.py https://新域名.space.mcode.cn
    python3 tools/update_listen_mirror.py --show
"""
import base64, json, os, re, sys, time, urllib.request

REPO = "tlv-creator/nhk-easy-news"
BASE = "https://api.github.com/repos/%s/contents" % REPO
RAW = "https://raw.githubusercontent.com/%s/main" % REPO
TOKEN = os.environ.get("GH_TOKEN", "")


def gh(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "token " + TOKEN,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def sha_of(path):
    code, body = gh("GET", BASE + "/" + path)
    return body.get("sha") if code == 200 and isinstance(body, dict) else None


def get_text(path):
    """读仓库文件：优先 Contents API（更稳），失败再走 raw"""
    for attempt in range(3):
        try:
            code, body = gh("GET", BASE + "/" + path)
            if code == 200 and isinstance(body, dict) and body.get("content"):
                return base64.b64decode(body["content"]).decode("utf-8")
            break
        except Exception:
            pass
        time.sleep(1.5)
    with urllib.request.urlopen(RAW + "/" + path, timeout=30) as r:
        return r.read().decode("utf-8")


def put(path, content):
    """409 = 别人刚推过 → 重取 sha 再重试（不是错误，是并发编辑的正常结果）"""
    data = base64.b64encode(content.encode("utf-8")).decode()
    for attempt in range(4):
        body = {"message": "update " + path, "content": data, "branch": "main"}
        s = sha_of(path)
        if s:
            body["sha"] = s
        code, resp = gh("PUT", BASE + "/" + path, body)
        if code in (200, 201):
            print("  PUT %-26s → %s ✅" % (path, code))
            return True
        print("  PUT %-26s → %s（重取 sha 再试）" % (path, code))
        time.sleep(2)
    print("  PUT %-26s ❌ %s" % (path, str(resp)[:160]))
    return False


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if sys.argv[1] == "--show":
        print(json.dumps(json.loads(get_text("listening/version.json")), ensure_ascii=False, indent=1))
        return 0

    mirror = sys.argv[1].strip().rstrip("/")
    if not re.match(r"^https://[a-z0-9]{4,}\.space\.mcode\.cn/?$", mirror):
        print("❌ 镜像地址格式不对：%s\n   应形如 https://xxxxxxxx.space.mcode.cn" % mirror)
        return 2
    if not TOKEN:
        print("❌ 没有 GH_TOKEN")
        return 2

    print("=== 把镜像改成 %s ===" % mirror)

    # ① version.json
    vj = json.loads(get_text("listening/version.json"))
    old = vj.get("mirror")
    vj["mirror"] = mirror
    vj["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    ok = put("listening/version.json", json.dumps(vj, ensure_ascii=False, indent=1))

    # ② index.html 的加速链接
    html = get_text("listening/index.html")
    html2 = re.sub(r'https://[a-z0-9]{4,}\.space\.mcode\.cn/?', mirror, html)
    if html2 != html:
        ok = put("listening/index.html", html2) and ok
    else:
        print("  index.html 里的链接没变（可能本来就是它）")

    # ③ 校验
    time.sleep(1)
    v2 = json.loads(get_text("listening/version.json"))
    print("  回读 mirror =", v2.get("mirror"), "✅" if v2.get("mirror") == mirror else "❌")
    print("  旧地址 =", old)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
