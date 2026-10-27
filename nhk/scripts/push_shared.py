#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推共享仓库：① protected-sites.json 加新站/标旧站 ② 把音频工具放进 scripts/

🔴 纪律（今晚被同一件事坑了两次）：
  · 基线永远取**线上最新**（不拿本地副本）⇒ 409 时重取重试，不覆盖别人
  · 回读要**逐字段对账**（目标字段 + 条目数 + 「对方的特征还在」）
  · 只动我要动的条目，别整块重写
"""
import base64
import json
import os
import sys
import time
import subprocess
import urllib.request

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
BRANCH = "main"
API = "https://api.github.com"
TOK = open("/workspace/.secrets/gh_token").read().strip()

NEW_SITE = "sn61770y6nxvr"
OLD_SITE = "4r0zazix2auz0"
TOOLS = ["audio_health.py", "loudnorm_fix.py", "repair_audio.py",
         "refetch_audio.py", "dex_probe.py"]


def api(method, path, body=None, raw=False):
    """🔴 用 curl 而不是 urllib —— 沙箱里 urllib 连 api.github.com 会被网关打成 400
       （老规矩：外部访问优先 curl）。"""
    url = API + path
    cmd = ["curl", "-s", "-m", "60", "-X", method,
           "-H", "Authorization: Bearer " + TOK,
           "-H", "Accept: application/vnd.github+json",
           "-H", "X-GitHub-Api-Version: 2022-11-28",
           "-H", "User-Agent: nhk-easy-news-updater",
           url]                       # 🔴 漏了 URL 会得到 `curl: no URL specified!`
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        p = subprocess.run(cmd, input=json.dumps(body).encode(), capture_output=True, timeout=90)
    else:
        p = subprocess.run(cmd, capture_output=True, timeout=90)
    if p.returncode != 0:
        # 🔴 失败必须**说理由**（stderr），不能只看到一个空 stdout
        raise RuntimeError("curl 失败 rc=%s：%s" %
                           (p.returncode, p.stderr.decode("utf-8", "replace")[:200]))
    b = p.stdout.decode("utf-8", "replace")
    if b.startswith("{") and '"message"' in b and '"documentation_url"' in b:
        try:
            err = json.loads(b)
            raise RuntimeError("GitHub API %s %s → %s %s" %
                               (method, path, err.get("message"), err.get("status")))
        except ValueError:
            pass
    return b if raw else (json.loads(b) if b.strip() else {})


def get_file(path):
    d = api("GET", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path))
    return d["sha"], base64.b64decode(d["content"]).decode("utf-8")


def put_file(path, text, sha, message):
    body = {"message": message, "content": base64.b64encode(text.encode()).decode(),
            "branch": BRANCH, "sha": sha}
    for i in range(4):
        try:
            api("PUT", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path), body)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 409 and i < 3:
                # 🔴 409 = 别人在我 GET 之后推了一次 ⇒ 重取基线重做，**不覆盖对方**
                print("   409（并发改同一文件）→ 重取基线重试 %d/3" % (i + 1))
                time.sleep(1.5)
                sha, text2 = get_file(path)
                body["sha"] = sha
                body["content"] = base64.b64encode(text2.encode()).decode()
                continue
            print("   PUT 失败：%s %s" % (e.code, e.read().decode()[:200]))
            return False
    return False


def update_protected():
    sha, text = get_file("protected-sites.json")
    j = json.loads(text)
    # 🔴 2026-10-26：线上权威键是 `sites`；`protected_sites` 线上从来不存在
    #    ⇒ 老写法 j["protected_sites"] 直接 **KeyError** ⇒ 这个脚本一直是哑的
    _key = "sites" if isinstance(j.get("sites"), list) else "protected_sites"
    before = len(j[_key])
    doms_before = [s.get("domain") for s in j[_key]]

    # ① 旧站标 DELETED（不删条目 —— 清单放永久事实）
    changed = False
    for s in j[_key]:
        if s.get("domain") == OLD_SITE and s.get("status") != "DELETED":
            s["status"] = "DELETED"
            s["note"] = ("2026-10-23 已被 v1.26 取代并删除（curl 404 验过）；"
                         "现役内容站以引导页 version.json 的 site 字段为准")
            changed = True
    if OLD_SITE not in doms_before:
        j[_key].append({
            "domain": OLD_SITE, "project": "nhk", "status": "DELETED",
            "reason": "NHK 内容站 v1.25（已被 4r0zazix2auz0 取代）",
            "note": "2026-10-23 已删（curl 404 验过）"})
        changed = True

    # ② 新站登记（现役内容站：域名每次发版都换 ⇒ 以引导页为准）
    if NEW_SITE not in doms_before:
        j[_key].append({
            "domain": NEW_SITE, "project": "nhk", "role": "current_site",
            "reason": "NHK 内容站 v1.27（17 期坏帧已修 + 音频四维体检全过 0 削平 0 过冲 + 字幕同步修好）",
            "note": "现役内容站：域名每次发版都换 ⇒ 以引导页 version.json 的 site 字段为准；"
                    "更新内容 = 新建站 + 改引导页 + 才删旧的",
            "frozen": False})
        changed = True

    if not changed:
        print("  清单没需要改的地方")
        return True
    new_text = json.dumps(j, ensure_ascii=False, indent=2)
    ok = put_file("protected-sites.json", new_text, sha,
                  "update: nhk 内容站 %s（v1.27）+ 旧站 %s 标 DELETED" % (NEW_SITE, OLD_SITE))
    if not ok:
        return False

    # ── 回读逐字段对账 ──
    time.sleep(1.5)
    _sha2, back = get_file("protected-sites.json")
    j2 = json.loads(back)
    _key2 = "sites" if isinstance(j2.get("sites"), list) else "protected_sites"
    doms2 = [s.get("domain") for s in j2[_key2]]
    ok2 = True
    print("   回读：条数 %d → %d" % (before, len(j2[_key2])))
    if len(j2[_key2]) != before + (1 if OLD_SITE not in doms_before else 0) + \
       (1 if NEW_SITE not in doms_before else 0):
        print("   ❌ 条目数不对"); ok2 = False
    if NEW_SITE not in doms2:
        print("   ❌ 新站没进去"); ok2 = False
    old = [s for s in j2[_key2] if s.get("domain") == OLD_SITE]
    if not old or old[0].get("status") != "DELETED":
        print("   ❌ 旧站没标 DELETED"); ok2 = False
    # 对方的特征还在吗（清单里有别的 agent 的条目）
    others = [d for d in doms2 if d in ("bo8u69hdw58t4", "iujsgx3ci73dp", "zp67qm11jonui",
                                        "tcw0ti0k7p0hd", "irk7ibwekq8se")]
    if len(others) < 4:
        print("   ❌ 别人的条目少了：%s" % others); ok2 = False
    else:
        print("   别人的条目还在：%s" % others)
    print("   %s" % ("✅ 对账通过" if ok2 else "❌ 对账失败"))
    return ok2


def push_tools():
    ok_all = True
    for name in TOOLS:
        src = "/workspace/nhk/scripts/%s" % name
        if not os.path.exists(src):
            print("  跳过（没有 %s）" % name); continue
        try:
            sha, _ = get_file("scripts/%s" % name)
        except Exception:
            sha = None
        text = open(src, encoding="utf-8").read()
        if sha:
            ok = put_file("scripts/%s" % name, text, sha, "add scripts/%s（音频体检/修复工具）" % name)
        else:
            body = {"message": "add scripts/%s（音频体检/修复工具）" % name,
                    "content": base64.b64encode(text.encode()).decode(), "branch": BRANCH}
            try:
                api("PUT", "/repos/%s/%s/contents/scripts/%s" % (OWNER, REPO, name), body)
                ok = True
            except Exception as e:
                print("   %s PUT 失败：%s" % (name, e)); ok = False
        print("  %s scripts/%s" % ("✅" if ok else "❌", name))
        ok_all = ok_all and ok
        time.sleep(0.4)
    return ok_all


if __name__ == "__main__":
    print("== ① protected-sites.json ==")
    a = update_protected()
    print("== ② 音频工具进共享仓库 ==")
    b = push_tools()
    print("\n%s" % ("✅ 全部完成" if (a and b) else "❌ 有失败"))
    sys.exit(0 if (a and b) else 1)
