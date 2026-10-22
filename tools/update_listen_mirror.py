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
    python3 tools/update_listen_mirror.py https://新域名... --no-rendered

🔴 三个必须分清的东西（2026-10-23 pony 提醒；我原样也踩过）：
  ① **源端**（仓库 Contents API）= 真相；写进去的东西以它为准
  ② **渲染端**（GitHub Pages）= 有构建/缓存延迟，**慢一拍是正常的**
  ③ **「我看到的旧内容」有两种可能**：① 真被覆盖了 ② 还没刷新
     ⇒ **复核必须回源**，不能看渲染端；渲染端只用来「报告」，不参与判定
  （旧版在 PUT 前现取 sha ⇒ 409 几乎永不发生 ⇒ 并发改动被静默覆盖，已修）

退出码：0=源端复核通过 / 1=失败 / 2=环境问题（没 token、拿不到 sha、拉不到源端 → 不写）
"""
import base64, json, os, re, sys, time, urllib.request, urllib.error

REPO = os.environ.get("LISTEN_REPO", "tlv-creator/nhk-easy-news")
BASE = os.environ.get("LISTEN_GH_BASE", "https://api.github.com/repos/%s/contents" % REPO)
RAW = os.environ.get("LISTEN_GH_RAW", "https://raw.githubusercontent.com/%s/main" % REPO)
PAGES = os.environ.get("LISTEN_PAGES_BASE", "https://tlv-creator.github.io/nhk-easy-news")
TOKEN = os.environ.get("GH_TOKEN", "")

MCODE_RE = re.compile(r'https://[a-z0-9]{4,}\.space\.mcode\.cn/?')


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
    except Exception as e:
        return 0, str(e)[:300]


# ---------- 源端读取（复核只认这一端） ----------

def get_source(path):
    """读仓库文件 → (text, sha, src_label)
    顺序：Contents API（权威，带 sha）→ raw（仍是源端，只是拿不到 sha）
    ⚠️ 绝不用 Pages 做复核 —— Pages 是渲染端，会慢一拍。"""
    code, body = gh("GET", BASE + "/" + path)
    if code == 200 and isinstance(body, dict) and body.get("content"):
        try:
            return base64.b64decode(body["content"]).decode("utf-8"), body.get("sha"), "contents-api"
        except Exception:
            pass
    try:
        with urllib.request.urlopen(RAW + "/" + path, timeout=30) as r:
            return r.read().decode("utf-8"), None, "raw"
    except Exception as e:
        raise RuntimeError("读不到源端文件 %s（%s）：%s" % (path, BASE, e))


def put(path, content, sha):
    """用**读时拿到的 sha** 写入。
    ⇒ 别人在这期间推过 = 409（真信号）→ 重读重做，不覆盖别人的改动。
    ⚠️ 旧版在 PUT 前现取 sha ⇒ 409 几乎永不发生 ⇒ 并发改动被静默覆盖（已修）。"""
    data = base64.b64encode(content.encode("utf-8")).decode()
    body = {"message": "update " + path, "content": data, "branch": "main", "sha": sha}
    return gh("PUT", BASE + "/" + path, body)


# ---------- 页面处理 ----------

def domains_of(html):
    """页面里出现的 mcode 域名集合（去尾斜杠，忽略大小写）"""
    return {u.rstrip("/") for u in MCODE_RE.findall(html or "")}


def pick_mirror_slot(html, old_mirror):
    """页面里那个「加速链接」是哪一个？
    规则：优先 = 与 version.json 当前 mirror 同域的那个；
          找不到但页面只有一个 mcode 域 → 就用它（并说明）；
          有多个且都对不上 → 交给人判断。"""
    doms = domains_of(html)
    if not doms:
        return None, "页面里没有 mcode 链接"
    old_dom = domains_of(old_mirror or "")
    if old_dom and old_dom <= doms:
        return list(old_dom)[0], "与 version.json 的 mirror 对得上"
    if len(doms) == 1:
        return list(doms)[0], "version.json 对不上，但页面只有一个 mcode 链接 → 按它处理"
    return None, "页面里有多个 mcode 链接（%s），不敢猜是哪个" % ", ".join(sorted(doms))


def replace_mirror(html, slot_domain, new_mirror):
    """**只**替换那一个「加速链接」域名的所有出现（精确串），不是「页面上所有 mcode 域名」
    ⇒ 以后页面上加了「备用加速通道 B」也不会被误伤。"""
    out = html
    for suffix in ("", "/"):
        cand = slot_domain + suffix
        if cand in out:
            out = out.replace(cand, new_mirror.rstrip("/") + (suffix if suffix else ""))
    return out


# ---------- 渲染端（只报告，不判定） ----------

def rendered_status(mirror, tries=None, gap=None):
    tries = int(os.environ.get("LISTEN_RENDERED_TRIES", "3"))
    gap = float(os.environ.get("LISTEN_RENDERED_GAP", "4"))
    """渲染端状态：**只报告，不影响退出码**（源端才是真相）。
    ⚠️ 渲染端慢一拍是正常的（Pages 构建 + CDN 缓存）⇒ 拿它判定 = 假红灯。"""
    last = "拉不到"
    for i in range(tries):
        try:
            req = urllib.request.Request(PAGES + "/listening/index.html",
                                         headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            if mirror.rstrip("/") in html:
                return True, "✅ 渲染端已是新地址"
            last = "⏳ 渲染端还是旧内容（Pages 构建/缓存延迟，源端已更新）"
        except Exception as e:
            last = "⚠️ 渲染端拉不到（%s）" % type(e).__name__
        time.sleep(gap)
    return False, last


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    # 🔴 顺序有讲究：--show 必须**先于**「没有参数就打印帮助」这一支
    #    （--show 以 "--" 开头 ⇒ args 为空 ⇒ 早退 ⇒ 走成打印帮助。踩过一次。）
    if "--show" in flags:
        text, sha, src = get_source("listening/version.json")
        print("（源端 %s）" % src)
        print(json.dumps(json.loads(text), ensure_ascii=False, indent=1))
        return 0
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    mirror = args[0].strip().rstrip("/")
    if not re.match(r"^https://[a-z0-9]{4,}\.space\.mcode\.cn$", mirror):
        print("❌ 镜像地址格式不对：%s\n   应形如 https://xxxxxxxx.space.mcode.cn" % mirror)
        return 2
    if not TOKEN:
        print("❌ 没有 GH_TOKEN")
        return 2

    print("=== 把镜像改成 %s ===" % mirror)
    wrote = False
    old = ""
    for rnd in range(1, 5):
        try:
            vj_text, vj_sha, vj_src = get_source("listening/version.json")
            html_text, html_sha, html_src = get_source("listening/index.html")
        except RuntimeError as e:
            print("❌ %s" % e)
            return 2
        if not vj_sha or not html_sha:
            print("❌ 拿不到 sha，不敢写（怕覆盖别人的改动）")
            return 2

        old = json.loads(vj_text).get("mirror", "")
        vj_new = json.loads(vj_text)
        vj_new["mirror"] = mirror
        vj_new["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        vj_new_txt = json.dumps(vj_new, ensure_ascii=False, indent=1)

        slot, why = pick_mirror_slot(html_text, old)
        if slot is None:
            print("❌ %s ⇒ 拒绝写（宁可不动，也别改坏页面）" % why)
            return 1
        if slot != mirror.rstrip("/"):
            html_new = replace_mirror(html_text, slot, mirror)
            before = domains_of(html_text)
            after = domains_of(html_new)
            expected = (before - {slot}) | {mirror.rstrip("/")}
            if after != expected:
                print("❌ 替换后页面里的 mcode 域名集合与预期不符\n   实际=%s\n   预期=%s\n   ⇒ 拒绝写"
                      % (sorted(after), sorted(expected)))
                return 1
        else:
            html_new = html_text
        print("  加速链接槽位 = %s（%s）" % (slot, why))

        c1, r1 = put("listening/version.json", vj_new_txt, vj_sha)
        if c1 == 409:
            print("  ⚠️ version.json 有人同时改了（409）→ 重读、重新合，不覆盖 TA 的改动")
            time.sleep(2)
            continue
        if c1 not in (200, 201):
            print("❌ 写 version.json 失败：%s %s" % (c1, str(r1)[:160]))
            return 1
        print("  PUT listening/version.json → %s ✅" % c1)

        if html_new != html_text:
            c2, r2 = put("listening/index.html", html_new, html_sha)
            if c2 == 409:
                print("  ⚠️ index.html 有人同时改了（409）→ 重读、重新合，不覆盖 TA 的改动")
                time.sleep(2)
                continue
            if c2 not in (200, 201):
                print("❌ 写 index.html 失败：%s %s" % (c2, str(r2)[:160]))
                return 1
            print("  PUT listening/index.html → %s ✅" % c2)
        else:
            print("  index.html 无需改动（已是新地址）")

        wrote = True
        break

    if not wrote:
        print("❌ 连续 4 轮都在跟别人并发改同一个文件 ⇒ 停下来，先跟对方说一声")
        return 1

    # ---------- 复核：回源（不是渲染端） ----------
    try:
        v2_text, v2_sha, v2_src = get_source("listening/version.json")
        h2_text, _, h2_src = get_source("listening/index.html")
    except RuntimeError as e:
        print("❌ 回源复核失败：%s\n   ⇒ **不能算成功**（复核失败 ≠ 没写成功，但也别当成功）" % e)
        return 2
    v2 = json.loads(v2_text).get("mirror")
    ok_ver = (v2 == mirror)
    ok_html = (mirror.rstrip("/") in h2_text) and (old.rstrip("/") not in h2_text if old else True)
    print("  回源复核（源端 %s / %s，不是渲染端）：" % (v2_src, h2_src))
    print("    version.json.mirror = %s %s" % (v2, "✅" if ok_ver else "❌ 不是我写的那个"))
    print("    index.html 新地址在=%s 旧地址还在=%s %s"
          % (mirror.rstrip("/") in h2_text, bool(old) and old.rstrip("/") in h2_text,
             "✅" if ok_html else "❌"))
    if not (ok_ver and ok_html):
        print("❌ 回源复核不通过 ⇒ 退出码非 0（不把「写没写对」只靠打印告诉你）")
        return 1
    print("  旧地址 =", old)

    # ---------- 渲染端：只报告 ----------
    if "--no-rendered" not in flags:
        okr, msg = rendered_status(mirror)
        print("  渲染端（Pages，可能慢一拍，不参与判定）：%s" % msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
