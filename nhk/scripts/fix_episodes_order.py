#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 修 episodes.js 的**排序**（用户看到「停更」的元凶）

现象：首页第一条是旧的一期 ⇒ 用户以为没自动更新。
根因：排序键是 time 的**原始字符串** ⇒ 格式不统一时被字符串比较坑了
      （详见 ep_sort.py 的说明）。build_data.py 已修；这个脚本修**存量**。

为什么用「打补丁」而不是重跑 build_data.py：
    build_data.py 是从 data/episodes/ 全量重建的 ⇒ 本工程有 4 期的源数据不在本地
    ⇒ 重跑会**把那 4 期悄悄丢掉** ⇒ 只能外科手术式地重排。

用法： python3 scripts/fix_episodes_order.py [--app site/app] [--check]
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ep_sort  # noqa: E402


def load(app):
    p = os.path.join(app, "data", "episodes.js")
    raw = open(p, encoding="utf-8").read()
    m = re.search(r"=\s*(\[.*?\]);", raw, re.S)
    if not m:
        sys.exit("❌ 读不出 data/episodes.js 里的数组")
    return raw, m, json.loads(m.group(1))


def order_ok(eps):
    keys = [ep_sort.field_key(e) for e in eps]
    return all(keys[i] >= keys[i + 1] for i in range(len(keys) - 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(ROOT, "site", "app"))
    ap.add_argument("--check", action="store_true", help="只查不写")
    a = ap.parse_args()
    app = os.path.normpath(a.app)
    raw, m, eps = load(app)
    n = len(eps)
    ids_before = [e["id"] for e in eps]
    nos_before = [e.get("no") for e in eps]

    print("== 排序检查 ==")
    print("   期数：%d" % n)
    print("   当前第一条：%s %s" % (eps[0]["id"], eps[0].get("date")))
    print("   当前最后一条：%s %s" % (eps[-1]["id"], eps[-1].get("date")))
    ok = order_ok(eps)
    nos_ok = nos_before == list(range(1, n + 1))
    print("   日期倒序：%s ｜ no 连续：%s" % ("✅" if ok else "❌", "✅" if nos_ok else "❌"))
    if ok and nos_ok:
        print("✅ 顺序没问题，用不着改")
        return 0
    if a.check:
        return 1

    new = sorted(eps, key=ep_sort.field_key, reverse=True)
    for i, e in enumerate(new, 1):
        e["no"] = i
    assert [e["id"] for e in new] and set(e["id"] for e in new) == set(ids_before), "期号集合变了！"
    assert len(new) == n, "期数变了！"
    assert not order_ok(new) is False, "排完还不对"

    out = raw[:m.start(1)] + json.dumps(new, ensure_ascii=False, indent=1) + raw[m.end(1):]
    open(os.path.join(app, "data", "episodes.js"), "w", encoding="utf-8").write(out)
    print("✅ 已重排并重编 no（1..%d）" % n)
    print("   新第一条：%s %s %s" % (new[0]["id"], new[0].get("date"), new[0].get("time")))
    print("   新最后一条：%s %s %s" % (new[-1]["id"], new[-1].get("date"), new[-1].get("time")))
    print("   日期序列（去重后按出现顺序）：")
    seen = []
    for e in new:
        if e["date"] not in seen:
            seen.append(e["date"])
    print("     " + " → ".join(seen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
