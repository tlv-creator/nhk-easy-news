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
    #   --mark 的 STATUS 只认 DELETED / SUPERSEDED / ALIVE（不写 status = 现役）
    #   ⇒ 写第四种取值会被**直接拒绝**（闸门 tools/check_sites_alive.py 也只认这几种）
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

# 🔴 status 的合法取值（闸门 tools/check_sites_alive.py commit 3ffa6f14a4b8 只认这些）
#    DELETED    = 我们说它已经删了 ⇒ 期望它死
#    SUPERSEDED = 被新站取代、可删 ⇒ 墓碑，不判死活
#    ALIVE      = 显式写出来的「现役」⇒ 期望它活着
#    （不写 status）= 现役 ⇒ 期望它活着
#    ⇒ **写入端也必须收严**：闸门遇到未知取值会 exit 2 不放行，
#      如果这里放行第五种取值，就等于「写进去就再也跑不了闸门」⇒ 缺口在写入端
STATUS_VALUES = ("DELETED", "SUPERSEDED", "ALIVE")


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


# 🔴 2026-10-27（跨会话修复）：清单里条目数组的键名有两个
#    · `sites`            —— **线上权威键**（清单现在就是这个结构）
#    · `protected_sites`  —— 老代码在用，但**线上从来就没有这个键**
#    ⇒ 只认旧键的实现一律读到**空列表**：
#      清理脚本的「冻结保护集」为空 ⇒ 可能误删现役站；
#      闸门则变成「校验代码在、校验对象是空的」⇒ **恒绿**。
#    ⇒ 两个键都认（收进词汇表，不是把线上的改回去）；
#      写端**必须写进线上实际在用的那个键**，否则又写进没人读的数组。
def _sites_key(obj):
    for _k in ("sites", "protected_sites"):
        if isinstance(obj.get(_k), list):
            return _k
    return "sites"          # 两个都没有 ⇒ 建 `sites`（与线上结构一致）


def _sites_of(obj):
    """取条目数组（并保证写回时用的是同一个键）。"""
    _k = _sites_key(obj)
    _v = obj.get(_k)
    if not isinstance(_v, list):
        _v = []
        obj[_k] = _v
    return _v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--add", action="append", default=[],
                    help="domain:project:reason（可带 :frozen / :role=xxx）")
    ap.add_argument("--mark", action="append", default=[],
                    help="domain:STATUS:note ⇒ STATUS 只认 DELETED / SUPERSEDED / ALIVE，其它一律拒绝")
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
        sites = _sites_of(obj)          # 🔴 写进线上实际在用的键（见 _sites_key）
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
            if len(parts) < 2:
                raise SystemExit("❌ --mark 的格式是 domain:STATUS[:note]，收到：%r" % spec)
            dom, status = parts[0], parts[1].strip().upper()
            if status not in STATUS_VALUES:
                raise SystemExit(
                    "❌ status=%r 不认识 ⇒ **拒绝写入**\n"
                    "   只认这三个：DELETED（期望它死）/ SUPERSEDED（墓碑，不判死活）/ ALIVE（现役）\n"
                    "   （**不写 status** 本身就是「现役」）\n"
                    "   ⇒ 闸门 tools/check_sites_alive.py 遇到未知取值会 exit 2 不放行\n"
                    "   ⇒ 别写第五种取值：写进去清单就再也跑不了闸门了" % status)
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
    mine = [e for e in _sites_of(obj) if e.get("project") == args.project]
    print("🟢 复核：%s 项目 %d 条" % (args.project, len(mine)))
    for e in mine:
        print("   %-14s %-10s %s" % (e.get("domain"), e.get("status", "alive"),
                                     str(e.get("reason"))[:70]))


if __name__ == "__main__":
    main()
