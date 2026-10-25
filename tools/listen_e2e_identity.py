#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日听力记录站 · E2E 脚本的**共用底座**（唯一权威实现）

🔴 **为什么抽出来**：
  之前 `check_listen_browser.py` 和 `listen_browser_e2e.py` 各存了一份**逐字相同**的
  loader + 守卫（2146 B × 2）⇒ 今天是一样，但**以后改一处忘了另一处就会漂移**
  （而且不会报错，只是某个脚本莫名其妙 exit 9）。
  ⇒ 收敛成**一份** ⇒ 漂移在结构上就不可能发生。

🔴 **这里不写死任何真实学生的学号/姓名**（仓库是 public，Pages 公开可读）：
  ① 环境变量 `E2E_SID` / `E2E_NAME` 优先
  ② 否则取随站发布的静态名册（本地仓库版 → 线上版）
     （名册本来就对 anon 可读，不算新增暴露；取到的还是同一个学生 ⇒ 行为完全不变）
  ③ 都拿不到 ⇒ `sys.exit(9)`（**环境没搭起来**，不是断言红）

⚠️ 只给 `E2E_SID` 时**必须去名册里查这个学号对应的姓名**：
  原来会拿「名册第一条的姓名」去配它 ⇒ 登录必然报「姓名与名册不一致」，
  而人只会以为自己名字打错了 ⇒ 白排查半天。

落盘目录：默认写**系统临时目录**（快照里是真实学生记录 ⇒ 绝不能落在仓库里被人 commit）
  ⇒ `E2E_WORKDIR=/your/dir` 可改。

自测（不碰生产库、只抽函数）：
    python3 tools/listen_e2e_identity.py
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

ROSTER_URL = ("https://tlv-creator.github.io/nhk-easy-news/listening/app/data/roster.js")

WORKDIR = os.environ.get("E2E_WORKDIR") or os.path.join(tempfile.gettempdir(), "listen-e2e")
SHOTS = os.path.join(WORKDIR, "shots")
SNAPS = os.path.join(WORKDIR, "snapshots")


def roster_entries():
    """取静态名册（本地仓库版 → 线上版）⇒ 返回 list；取不到返回 None"""
    here = os.path.dirname(os.path.abspath(__file__))
    texts = []
    for p in (os.path.join(here, "..", "listening", "app", "data", "roster.js"),
              os.path.join(here, "roster.js")):
        try:
            texts.append(open(p, encoding="utf-8").read())
        except Exception:
            pass
    if not texts:
        # 🔴 线上兜底**必须重试**（2026-10-25 实测）：
        #    沙箱/部分网络的 DNS 是**间歇整段挂**（ndots:6 + 多个 search 域 ⇒
        #    任一个 search 域回 SERVFAIL ⇒ glibc 整次解析放弃 ⇒ 单次失败）
        #    ⇒ 单次 urlopen 失败就 return None ⇒ 报「名册彻底拿不到」exit 9
        #    ⇒ 那是**环境抖动**，不是「名册没了」⇒ 宁可多等几秒重试，
        #      也别把一次抖动说成事实（单次失败 ≠ 东西没了）
        for _try in range(3):
            try:
                with urllib.request.urlopen(ROSTER_URL, timeout=20) as r:
                    texts.append(r.read().decode("utf-8", "replace"))
                break
            except Exception:
                if _try < 2:
                    time.sleep(1.5)
    for txt in texts:
        m = re.search(r'window\.LISTEN_ROSTER_FALLBACK\s*=\s*(\[.*?\])\s*;', txt, re.S)
        if not m:
            continue
        try:
            arr = json.loads(m.group(1))
        except Exception:
            continue
        if arr:
            return arr
    return None


def first_roster_entry():
    """名册第一条（默认就演这名学生）"""
    arr = roster_entries()
    return arr[0] if arr else None


def pick_identity():
    """返回 (学号, 姓名)。取不到 ⇒ 打印原因并 exit 9。"""
    sid = os.environ.get("E2E_SID", "").strip()
    nm = os.environ.get("E2E_NAME", "").strip()
    if sid and nm:
        return sid, nm
    arr = roster_entries()
    if not arr:
        print("❌ 拿不到名册（本地没有 listening/app/data/roster.js，线上也取不到）")
        print("   ⇒ 没法登录 ⇒ 本次验收**无效**（不是断言红）")
        print("   临时指定：E2E_SID=<学号> E2E_NAME=<姓名> python3 %s" % os.path.basename(__file__))
        sys.exit(9)
    if sid:
        hit = next((s for s in arr if str(s.get("id")) == sid), None)
        if not hit:
            print("❌ 名册里没有学号 %s ⇒ 换个 E2E_SID，或两个都设（E2E_SID + E2E_NAME）" % sid)
            sys.exit(9)
        return sid, str(hit.get("name"))
    if nm:
        hit = next((s for s in arr if str(s.get("name")) == nm), None)
        if not hit:
            print("❌ 名册里没有姓名 %s ⇒ 换个 E2E_NAME，或两个都设（E2E_SID + E2E_NAME）" % nm)
            sys.exit(9)
        return str(hit.get("id")), nm
    first = arr[0]
    return str(first.get("id")), str(first.get("name"))


def self_test():
    """6 条自测（只抽函数，不登录、不碰生产库）"""
    ok = 0
    bad = []

    def ck(name, cond, detail=""):
        nonlocal ok
        if cond:
            ok += 1
            print("  ✅ %s%s" % (name, ("  " + detail) if detail else ""))
        else:
            bad.append(name)
            print("  ❌ %s%s" % (name, ("  ← " + detail) if detail else ""))

    os.environ.pop("E2E_SID", None)
    os.environ.pop("E2E_NAME", None)
    arr = roster_entries()
    ck("名册取得到", bool(arr), "共 %d 人" % (len(arr) if arr else 0))
    if not arr:
        print("\n环境没搭起来 ⇒ 本次自测**无效**")
        return 9
    first = arr[0]

    sid, nm = pick_identity()
    ck("① 都不给 ⇒ 名册第一条", (sid, nm) == (first["id"], first["name"]),
       "%s %s" % (sid, nm))

    # ② 只给 E2E_SID ⇒ 必须配**这条**学号的姓名（故意挑名册**第二条**，不是第一条）
    second = arr[1]
    os.environ["E2E_SID"] = second["id"]
    sid, nm = pick_identity()
    ck("② 只给 E2E_SID ⇒ 配对正确（挑名册第二条，证明不是回第一条）",
       (sid, nm) == (second["id"], second["name"]) and second["id"] != first["id"],
       "%s %s" % (sid, nm))
    os.environ.pop("E2E_SID", None)

    # ③ 只给 E2E_NAME ⇒ 反查学号
    os.environ["E2E_NAME"] = arr[3]["name"]
    sid, nm = pick_identity()
    ck("③ 只给 E2E_NAME ⇒ 反查学号正确", (sid, nm) == (arr[3]["id"], arr[3]["name"]),
       "%s %s" % (sid, nm))
    os.environ.pop("E2E_NAME", None)

    # ④ 两个都给 ⇒ 直接用，不查名册
    os.environ["E2E_SID"] = "90000011"
    os.environ["E2E_NAME"] = "测试同学"
    sid, nm = pick_identity()
    ck("④ 两个都给 ⇒ 直接用", (sid, nm) == ("90000011", "测试同学"), "%s %s" % (sid, nm))
    os.environ.pop("E2E_SID", None)
    os.environ.pop("E2E_NAME", None)

    # ⑤ 只给一个、名册里没有 ⇒ exit 9（不是断言红）
    os.environ["E2E_SID"] = "00000000"
    try:
        pick_identity()
        ck("⑤ 只给一个但名册里没有 ⇒ exit 9", False, "没有退出")
    except SystemExit as e:
        ck("⑤ 只给一个但名册里没有 ⇒ exit 9", e.code == 9, "exit %s" % e.code)
    os.environ.pop("E2E_SID", None)

    # ⑥ 名册彻底拿不到 ⇒ exit 9
    _real = roster_entries
    try:
        globals()["roster_entries"] = lambda: None
        try:
            pick_identity()
            ck("⑥ 名册彻底拿不到 ⇒ exit 9", False, "没有退出")
        except SystemExit as e:
            ck("⑥ 名册彻底拿不到 ⇒ exit 9", e.code == 9, "exit %s" % e.code)
    finally:
        globals()["roster_entries"] = _real

    print("\n通过 %d 条，失败 %d 条" % (ok, len(bad)))
    if bad:
        print("❌ 失败：" + "、".join(bad))
        return 1
    print("🟢 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(self_test())
