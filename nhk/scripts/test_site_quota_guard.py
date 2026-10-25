#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 站点配额守卫的**自测**（四道闸门逐条验）

为什么要有自测：
    「删站」是**不可逆**的真副作用 ⇒ 闸门写错了 = 删掉别人（别的 APK）依赖的站
    ⇒ 每条闸门都要有一��**能红的**判据 ⇒ 改完跑一遍，红了就说明闸门被改坏了。

用法：python3 scripts/test_site_quota_guard.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import site_quota_guard as G  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("   ✅ %s" % name)
    else:
        print("   ❌ %s %s" % (name, detail))
        FAILED.append(name)


def main():
    boot = {"site": "https://em8hjp6jskxyb.space.mcode.cn",
            "apk": "https://em8hjp6jskxyb.space.mcode.cn/NHK.apk",
            "ver": 30, "apkVer": 20}
    cur = "em8hjp6jskxyb"
    fb = "vkwwuh9wgkzo0"

    print("════ 闸门① 保护清单 status=ACTIVE ⇒ 不删 ════")
    prot = {cur: {"status": "ACTIVE"}, fb: {"status": None, "frozen": True}}
    ok, res = G.gates({"domain": cur}, boot, cur, fb, prot)
    check("现役站（保护清单 ACTIVE）被拦下", not ok)
    check("① 的理由写清楚了",
          any("ACTIVE" in w for t, g, w in res if t == "①" and not g),
          str(res))

    print("════ 闸门② frozen=true ⇒ 不删 ════")
    ok, res = G.gates({"domain": "frozentest"}, boot, cur, fb,
                       {"frozentest": {"frozen": True}})
    check("frozen 站被拦下", not ok)
    check("② 的理由写清楚了",
          any("frozen" in w for t, g, w in res if t == "②" and not g), str(res))

    print("════ 闸门③ 现役 APK 里写着这个域名 ⇒ 不删 ════")
    # 真数据：兜底站 vkwwuh9wgkzo0 确实写在 classes.dex 里
    hits = G.apk_mentions(fb)
    check("兜底站确实能在 APK 的 classes.dex 里查到", bool(hits), str(hits))
    ok, res = G.gates({"domain": fb}, boot, cur, fb, prot)
    check("APK 引用站被拦下（即使 ①② 放行）", not ok)
    check("③ 的理由点名了 APK 里的位置",
          any("APK" in w for t, g, w in res if t == "③" and not g), str(res))

    print("════ 闸门④ 引导页 / 站点源码里还有引用 ⇒ 不删 ════")
    ok, res = G.gates({"domain": cur}, boot, cur, fb, {})   # 引导页 site 字段写着它
    check("引导页当前指向的站被拦下", not ok)
    # 源码引用：临时在 site/app 里造一个引用再查
    probe = os.path.join(G.SITE_SRC, "data", "_gate_probe.js")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("// probe: https://proberefsite.space.mcode.cn/x\n")
    try:
        hits = G.src_mentions("proberefsite")
        check("站点源码里的域名能查到", bool(hits), str(hits))
        ok, res = G.gates({"domain": "proberefsite"}, boot, cur, fb, {})
        check("源码引用站被拦下", not ok)
        check("④ 的理由点名了引用位置",
              any("引用" in w for t, g, w in res if t == "④" and not g), str(res))
    finally:
        os.unlink(probe)

    print("════ 四道闸门全过 ⇒ 判为「可删」 ════")
    ok, res = G.gates({"domain": "cleanoldsite"}, boot, cur, fb, {})
    check("干净的老站判为可删", ok, str(res))
    check("四条闸门都有结论", len(res) == 4, str(res))

    print("════ 登记表读写 ════")
    reg = G.load_reg()
    check("登记表读得到", isinstance(reg.get("sites"), list))
    fb_dom = reg.get("fallback_domain")
    check("登记表里有兜底站", fb_dom == fb, str(fb_dom))
    live = {s.get("domain") for s in reg.get("sites", [])}
    check("现役站已登记", cur in live, str(live))
    cur_entry = [s for s in reg["sites"] if s.get("domain") == cur]
    check("现役站有 node_id（不是 PENDING）",
          bool(cur_entry) and cur_entry[0].get("node_id")
          and cur_entry[0]["node_id"] != "PENDING", str(cur_entry))

    print()
    if FAILED:
        print("❌ %d 项没过：%s" % (len(FAILED), FAILED))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
