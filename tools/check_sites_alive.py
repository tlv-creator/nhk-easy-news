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


def _req(url, method, timeout):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def head(domain, timeout=8):
    """→ (code, kind)  kind: alive / dead / blocked / neterr
    🔴 **HEAD 不准**：并发压测时中间层会回 403/429/501（实测：NHK 内容站被并发 HEAD 打成 403，
       GET 和浏览器都是 200）⇒ **只要 HEAD 不是 200/404，必须用 GET 复验**。
    ⇒ 「403/429/501」= 被挡/限流，**不是站死了**。"""
    # 🔴 端点可覆盖（为了能拿 mock 做自检 —— 「HEAD 不准」这条必须能测）
    url = (os.environ.get("LISTEN_PROBE_BASE") or "https://%s.space.mcode.cn/") % domain
    code = _req(url, "HEAD", timeout)
    if code == 200:
        return 200, "alive", url
    # 一律用 GET 复验（HEAD 被中间层改写/限流的概率很高）
    g = _req(url, "GET", timeout)
    if g == 200:
        return 200, "alive", url
    if g in (404, 410):
        return g, "dead", url
    if g == 0:
        # 网络错误 ≠ 站点死 → 再试一次
        time.sleep(1.5)
        g = _req(url, "GET", timeout)
        if g == 200:
            return 200, "alive", url
        if g in (404, 410):
            return g, "dead", url
        return 0, "neterr", url
    return g, "blocked", url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="用本地清单文件代替线上地址")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--timeout", type=int, default=8)
    ap.add_argument("--check-dead", action="store_true",
                    help="连标了 DELETED 的条目也检查（默认否 ⇒ 不改变原行为）")
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

    # 🔴🔴 类型归一（2026-10-24 patch，Shun 会话报的洞②）：
    #   「清单被写成 dict」⇒ 迭代出来是 key 字符串 ⇒ e.get() 直接 AttributeError 崩
    #   ⇒ 这里**显式校验**，坏清单必须**报错不放行**（不能崩、也不能静默当空）
    entries = data.get("protected_sites", [])
    if not isinstance(entries, list):
        print("❌ protected_sites 必须是数组，实际是 %s ⇒ **不放行**"
              % type(entries).__name__)
        return 2
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            print("❌ protected_sites 第 %d 条不是对象（%s）⇒ **不放行**"
                  % (i, type(e).__name__))
            return 2
    # 🔴🔴🔴 2026-10-24 补丁（主线会话）：**status 取值必须穷举** ——
    #   **假红的根源就是「默认分支」**：老实现只认 DELETED，其余（含 SUPERSEDED）一律当「现役」
    #   ⇒ 新增一个取值时它会被**默默当成现役** ⇒ 判错 ⇒ 假红/假绿，而且**没人会发现**
    #   ⇒ 现在：**不认识的取值直接报错不放行**（宁可停下来问人，别默默按默认走）
    #   已知取值只有四个：
    #     DELETED    = 我们说它已经删了   ⇒ 期望它死   ⇒ 还活着 ⇒ 🔴 清单该更新
    #     SUPERSEDED = 旧站被取代、可删   ⇒ **不判死活** ⇒ 墓碑（实测 8 条里 4 活 4 死）
    #     ALIVE      = 显式写出来的「现役」⇒ 期望它活着（语义 ≡ 没有 status）
    #     （没有 status）= 现役          ⇒ 期望它活着
    KNOWN = {"": "check", "ALIVE": "check", "DELETED": "check_dead", "SUPERSEDED": "skip"}
    todo = []
    superseded = []
    for i, e in enumerate(entries):
        dom = e.get("domain")
        if not dom:
            continue
        raw = e.get("status", None)
        if raw is None:
            st = ""
        elif isinstance(raw, str):
            st = raw.strip().upper()
        else:
            print("❌ protected_sites 第 %d 条（%s）的 status **类型不对**：%s"
                  % (i, dom, type(raw).__name__))
            print("   status 要么不写，要么写字符串（DELETED / SUPERSEDED / ALIVE）")
            print("   ⇒ **不放行**（类型不对的字段不能被默默当成现役）")
            return 2
        if st not in KNOWN:
            print("❌ protected_sites 第 %d 条（%s）的 status=%r 不认识"
                  % (i, dom, raw))
            print("   只认这四个：DELETED（期望它死）/ SUPERSEDED（墓碑，不判死活）"
                  "/ ALIVE（现役）/ 没有 status（现役）")
            print("   ⇒ **不放行**（新状态不能被默默当成现役 ⇒ 那样只会出假红）")
            return 2
        mode = KNOWN[st]
        if mode == "skip":
            superseded.append(dom)
            continue
        todo.append((dom, e, mode))
    if superseded:
        print('  \u26aa 另有 %d 条标 SUPERSEDED（被新站取代 \u21d2 墓碑条目，不判死活）：%s'
              % (len(superseded), ' '.join(sorted(superseded))))
    if args.local and not args.check_dead:
        # 本地模式 = 「拿本地清单检查活站」⇒ **故意不查 DELETED 条目**
        todo = [t for t in todo if t[2] == "check"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(head, d, args.timeout): (d, e, mode) for d, e, mode in todo}
        results = []
        for fut in concurrent.futures.as_completed(futs):
            d, e, mode = futs[fut]
            code, kind, url = fut.result()
            results.append((d, e, mode, code, kind, url))

    results.sort(key=lambda r: (r[2] == "check", r[0]))
    dead = []
    still_alive = []      # 🔴 清单标 DELETED 但站还活着（清单该更新）
    if args.json:
        print(json.dumps([{"domain": d, "project": e.get("project"), "code": c, "kind": k,
                           "url": u, "source": src} for d, e, m, c, k, u in results],
                         ensure_ascii=False, indent=1))
    else:
        for d, e, mode, c, k, u in results:
            if mode == "check":
                if k == "alive":
                    print("  %-18s %-22s ✅ 200" % (d, (e.get("project") or "")[:22]))
                elif k == "dead":
                    print("  %-18s %-22s 🔴 %s  %s" % (d, (e.get("project") or "")[:22], c, u))
                    dead.append(d)
                elif k == "neterr":
                    print("  %-18s %-22s ⚠️  网络错误（不是站点死）%s" % (d, (e.get("project") or "")[:22], u))
                else:
                    # 被中间层挡/限流（403/429/501）⇒ **不算死**，但要让人看见
                    print("  %-18s %-22s ⚠️  被挡/限流 %s（不是站点死，需人工看）%s"
                          % (d, (e.get("project") or "")[:22], c, u))
            elif mode == "check_dead":
                # 🔴 清单标了 DELETED ⇒ **期望它死** ⇒ 还活着就是「清单该更新」
                if k == "alive":
                    print("  %-18s %-22s 🔴 清单标 DELETED 但**站还活着**（清单该更新）"
                          % (d, (e.get("project") or "")[:22]))
                    still_alive.append(d)
                elif k == "dead":
                    print("  %-18s %-22s ✅ 已删除（符合预期）" % (d, (e.get("project") or "")[:22]))
                elif k == "neterr":
                    print("  %-18s %-22s ⚠️  网络错误（不等于还活着）%s"
                          % (d, (e.get("project") or "")[:22], u))
                else:
                    print("  %-18s %-22s ⚠️  被挡/限流 %s（需人工确认是否真删）"
                          % (d, (e.get("project") or "")[:22], c))
            else:
                print("  %-18s %-22s %s" % (d, (e.get("project") or "")[:22], "⚪ 已标记 DELETED（期望它死）"))

    print("\n共 %d 个待查域名，其中 %d 个查不到。" % (len(results), len(dead)))
    if dead:
        print("🔴 这几个域名现在是死的：**清单该更新，或者站被误删了**")
        for d in dead:
            print("     -", d)
    if still_alive:
        print("🔴 这几个**清单已标 DELETED 但站还活着** ⇒ 清单该更新（别当已删）")
        for d in still_alive:
            print("     -", d)
    if dead or still_alive:
        return 1
    print("✅ 全部活着")
    return 0


if __name__ == "__main__":
    sys.exit(main())
