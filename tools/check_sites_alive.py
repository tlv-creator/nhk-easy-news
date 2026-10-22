#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受保护站点存活体检（清单的唯一配套工具）

用法：
    python3 tools/check_sites_alive.py                 # 查清单里每个域还活着吗
    python3 tools/check_sites_alive.py --json          # 机器可读
    python3 tools/check_sites_alive.py --local file.json

规则：
  · 清单里只写**事实**（域名/项目/为什么不能删/写在哪个文件里）
  · **「现在还活着吗」一律现查** —— 状态不写进清单（会过期，骗人）
  · status == "DELETED" 的条目是「已误删但要记住」→ 期望它死，不算异常
    （「已删」是**永久事实** —— 它不会活回来 ⇒ 适合写进清单；
      而「现在还活着吗」会过期 ⇒ 只能现查。两者不矛盾。）
  · 有活着的域死了 → exit 1（清理前必须先处理）

🔴 清单本身从**源端**读（仓库 API → raw），**不是从 Pages 读**（2026-10-23 pony 提醒）：
  「我看到的旧内容」有两种可能 —— ① 真被覆盖了 ② 渲染端还没刷新。
  ⇒ 拿渲染端当真相 = 拿到旧清单 → 把已经标了 DELETED 的域报成「活的但坏了」（假红灯）。
  ⇒ 源端拿不到才退回 Pages，并**明确说**用的是渲染端。
"""
import argparse, concurrent.futures, json, os, ssl, sys, time
import urllib.request, urllib.error

REPO = os.environ.get("LISTEN_REPO", "tlv-creator/nhk-easy-news")
LIST_API = os.environ.get("LISTEN_LIST_API",
                           "https://api.github.com/repos/%s/contents/protected-sites.json" % REPO)
LIST_RAW = os.environ.get("LISTEN_LIST_RAW",
                           "https://raw.githubusercontent.com/%s/main/protected-sites.json" % REPO)
LIST_PAGES = os.environ.get("LISTEN_LIST_PAGES",
                             "https://tlv-creator.github.io/nhk-easy-news/protected-sites.json")


def _get(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_list():
    """读清单 → (data, source_label)
    顺序：**源端**（Contents API → raw）→ 渲染端（Pages，最后兜底并明确说）"""
    token = os.environ.get("GH_TOKEN", "")
    try:
        raw = _get(LIST_API, headers={"Accept": "application/vnd.github+json",
                                      "Authorization": "token " + token} if token else {})
        j = json.loads(raw.decode("utf-8"))
        if isinstance(j, dict) and j.get("content"):
            import base64
            j = json.loads(base64.b64decode(j["content"]).decode("utf-8"))
        if isinstance(j, dict) and "protected_sites" in j:
            return j, "源端(contents-api)"
    except Exception:
        pass
    try:
        j = json.loads(_get(LIST_RAW).decode("utf-8"))
        if isinstance(j, dict) and "protected_sites" in j:
            return j, "源端(raw)"
    except Exception:
        pass
    try:
        j = json.loads(_get(LIST_PAGES).decode("utf-8"))
        if isinstance(j, dict) and "protected_sites" in j:
            return j, "⚠️ 渲染端(Pages，可能慢一拍)"
    except Exception as e:
        raise RuntimeError("拉不到受保护清单（源端和渲染端都试过了）：%s" % e)
    raise RuntimeError("拉到的清单里没有 protected_sites 字段")


def head(domain, timeout=8):
    url = "https://%s.space.mcode.cn/" % domain
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # 🔴 网络抖动（URLError）≠ 站点死了 → 重试一次再下结论
    last = (0, url)
    for attempt in range(2):
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.status, url
        except urllib.error.HTTPError as e:
            return e.code, url
        except Exception as e:
            last = (0, "%s (%s)" % (url, type(e).__name__))
            time.sleep(1.5)
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="用本地清单文件代替线上地址")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--timeout", type=int, default=8)
    args = ap.parse_args()

    if args.local:
        data = json.load(open(args.local, encoding="utf-8"))
        src = "本地文件(%s)" % args.local
    else:
        try:
            data, src = fetch_list()
        except RuntimeError as e:
            print("❌ %s\n   ⇒ **不许放行删除操作**（清单拉不到 = 保护失效）" % e)
            return 2
    print("清单来源：%s" % src)

    entries = data.get("protected_sites", [])
    todo = []
    for e in entries:
        dom = e.get("domain")
        if not dom:
            continue
        if str(e.get("status", "")).upper() == "DELETED":
            todo.append((dom, e, None))          # 期望死的
        else:
            todo.append((dom, e, "check"))
    if args.local:
        todo = [t for t in todo if t[2] == "check"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(head, d, args.timeout): (d, e, mode) for d, e, mode in todo}
        results = []
        for fut in concurrent.futures.as_completed(futs):
            d, e, mode = futs[fut]
            code, url = fut.result()
            results.append((d, e, mode, code, url))

    results.sort(key=lambda r: (r[2] == "check", r[0]))
    dead = []
    if args.json:
        print(json.dumps([{"domain": d, "project": e.get("project"), "code": c,
                           "url": u, "source": src} for d, e, m, c, u in results],
                         ensure_ascii=False, indent=1))
    else:
        for d, e, mode, c, u in results:
            if mode == "check":
                if c == 200:
                    print("  %-18s %-22s ✅ 200" % (d, (e.get("project") or "")[:22]))
                elif c == 0:
                    # 网络错误（不是 404）→ 不算死，单独提示，避免误报
                    print("  %-18s %-22s ⚠️  网络错误（不是站点死）%s" % (d, (e.get("project") or "")[:22], u))
                else:
                    print("  %-18s %-22s 🔴 %s  %s" % (d, (e.get("project") or "")[:22], c, u))
                    dead.append(d)
            else:
                print("  %-18s %-22s %s" % (d, (e.get("project") or "")[:22], "⚪ 已标记 DELETED（期望它死）"))

    print("\n共 %d 个待查域名，其中 %d 个查不到。" % (len(results), len(dead)))
    if dead:
        print("🔴 这几个域名现在是死的：**清单该更新，或者站被误删了**")
        for d in dead:
            print("     -", d)
        return 1
    print("✅ 全部活着")
    return 0


if __name__ == "__main__":
    sys.exit(main())
