#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_protected.py —— 更新共享仓库的 protected-sites.json（**只动本项目的条目**）

🔴 protected-sites.json 是**所有 agent 共用**的文件 ⇒
    · 只改自己项目的条目（--project shun）
    · **先读 sha 再 PUT**（409 = 别人刚推过 ⇒ 重读合并，别覆盖）
    · 推完回源复核

用法：
    python3 tools/update_protected.py --project shun \
        --add 2aixd037sd8fk:shun:现役内容站（含 APK + 字幕/词库/音频） \
        --mark w94oj2uij89x0:DELETED:本次发版的中间站（已被取代）
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

OWNER = "tlv-creator"
REPO = "nhk-easy-news"
PATH = "protected-sites.json"
API = "https://api.github.com/repos/%s/%s/contents/%s" % (OWNER, REPO, PATH)


def api(method, url, token, payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "mavis-shun-protected")
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--add", action="append", default=[],
                    help="domain:project:reason（可带 :frozen / :role=xxx）")
    ap.add_argument("--mark", action="append", default=[],
                    help="domain:STATUS:note")
    ap.add_argument("--note", action="append", default=[],
                    help="domain:要追加到 reason 里的说明")
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise SystemExit("❌ 没有 GH_TOKEN")

    def load():
        st, d = api("GET", API, token)
        if st != 200:
            raise SystemExit("❌ 读不到 %s：%s" % (PATH, str(d)[:200]))
        text = base64.b64decode(d["content"]).decode("utf-8")
        return json.loads(text), d["sha"]

    def save(obj, sha):
        payload = {"message": "chore(protected): 更新 %s 的站点清单" % args.project,
                   "content": base64.b64encode(
                       json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
                   ).decode("ascii")}
        if sha:
            payload["sha"] = sha
        st, d = api("PUT", API, token, payload)
        return st, d

    for attempt in range(4):
        obj, sha = load()
        sites = obj.setdefault("protected_sites", [])
        by_dom = {e.get("domain"): e for e in sites}

        for spec in args.add:
            parts = spec.split(":", 2)
            dom, proj, reason = parts[0], parts[1], parts[2]
            entry = by_dom.get(dom)
            if entry is None:
                entry = {"domain": dom, "project": proj, "reason": reason}
                sites.append(entry)
                by_dom[dom] = entry
            else:
                entry["reason"] = reason
                entry["project"] = proj
            if "frozen" in reason:
                entry["frozen"] = True
            for kv in reason.split():
                if kv.startswith("role="):
                    entry["role"] = kv[5:]

        for spec in args.mark:
            parts = spec.split(":", 2)
            dom, status = parts[0], parts[1]
            note = parts[2] if len(parts) > 2 else ""
            entry = by_dom.get(dom)
            if entry is None:
                sites.append({"domain": dom, "project": args.project,
                              "reason": note or "（无说明）", "status": status})
            else:
                entry["status"] = status
                if note:
                    entry["note"] = note

        for spec in args.note:
            dom, extra = spec.split(":", 1)
            entry = by_dom.get(dom)
            if entry is not None:
                entry["reason"] = (entry.get("reason", "") + " " + extra).strip()

        obj["updated"] = time.strftime("%Y-%m-%d")
        st, d = save(obj, sha)
        if st in (200, 201):
            print("✅ 已更新 protected-sites.json")
            break
        if st == 409:
            print("  ↻ 409（有人刚推过）⇒ 重读合并再推")
            time.sleep(2)
            continue
        raise SystemExit("❌ 推失败：%s %s" % (st, str(d)[:300]))
    else:
        raise SystemExit("❌ 连续冲突，放弃")

    obj, _ = load()
    mine = [e for e in obj["protected_sites"] if e.get("project") == args.project]
    print("🟢 复核：%s 项目 %d 条" % (args.project, len(mine)))
    for e in mine:
        print("   %-14s %-10s %s" % (e.get("domain"), e.get("status", "alive"),
                                     str(e.get("reason"))[:70]))


if __name__ == "__main__":
    main()
