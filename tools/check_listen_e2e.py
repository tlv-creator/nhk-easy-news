#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日听力记录站 · anon 读写闭环验收（RLS 修复后跑这个）

为什么要有这个：
  🔴 **PostgREST 删不掉也返回 200/204 + 空数组** ⇒ 「看着成功了」不等于「真删了」
  🔴 **PostgREST 对不存在的列做过滤会忽略** ⇒ 空结果可能是「被 RLS 挡了」，不是「没数据」
  ⇒ 所以本脚本**每一步都回读确认**，**不信 HTTP 码**。

用法：
    export SB_KEY=<supabase publishable key>
    python3 tools/check_listen_e2e.py            # 闭环验收（会写一行探针再删掉）
    python3 tools/check_listen_e2e.py --read-only # 只读体检（不写任何东西）

退出码：0 = 全通 ｜1 = 有环节不通
"""
import json, os, sys, time, urllib.request, urllib.error

SB = os.environ.get("SB_URL", "https://lszwxcmvcvbraqtrtgon.supabase.co")
KEY = os.environ.get("SB_KEY", "")
LOG_KEY = "LISTEN"                 # 听力记录的 class_key（api.js 的 CFG.LOG_KEY）
PROBE_SID = "__rls_probe_listen__"
PROBE_TS = "2099/01/01 00:00:00"
READ_ONLY = "--read-only" in sys.argv
FAIL = []


def check(cond, msg):
    print("   %s %s" % ("✅" if cond else "❌", msg))
    if not cond:
        FAIL.append(msg)
    return cond


def rest(path, method="GET", body=None, prefer=None):
    if method == "GET" and not prefer:
        prefer = "count=exact"      # 🔴 不加这个，Content-Range 是 "*/*"（总数未知）⇒ 判据不成立
    url = "%s/rest/v1/%s" % (SB, path)
    data = json.dumps(body).encode() if body is not None else None
    h = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode("utf-8", "replace")
            cr = r.headers.get("Content-Range", "")
            return r.status, t, cr
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Content-Range", "")
    except Exception as e:
        return 0, str(e), ""


def rows(path):
    st, t, cr = rest(path)
    try:
        return st, json.loads(t) if t.strip() else [], cr
    except Exception:
        return st, [], cr


def main():
    if not KEY:
        print("❌ 没设 SB_KEY（Supabase publishable key）\n   export SB_KEY=<key>  再跑")
        return 2
    print("=== 每日听力记录站 · anon 读写闭环验收 ===")

    # ① 读通道：SELECT 能不能看到 LISTEN 的行
    st, rs, cr = rows("vocab_history_logs?select=id,student_id&class_key=eq.%s" % LOG_KEY)
    print("\n① 读通道")
    check(st == 200, "SELECT HTTP %s（要 200）" % st)
    check("42501" not in rs and "row-level security" not in str(rs),
          "没有 RLS 报错（42501 = 被 policy 挡）")
    # 🔴 判据必须落在**真实总数**上：`*/0` = anon 看到 0 行（= 被 policy 挡了，或真的没记录）
    total = cr.split("/")[-1] if "/" in cr else cr
    check(total not in ("0", "*"), "anon 能看到 LISTEN 的行（content-range=%r，总数=%s）" % (cr, total))

    # ② 名册（老师端看板的底）
    st2, rs2, cr2 = rows("vocab_states?select=class_key&class_key=eq.__roster_v1")
    print("\n② 名册（教师看板用）")
    check(st2 == 200 and len(rs2) >= 1, "vocab_states 能读到名册行（HTTP %s，%d 行）" % (st2, len(rs2)))

    if READ_ONLY:
        print("\n（--read-only：到此为止，没写任何东西）")
        return 1 if FAIL else 0

    # ③ 写：插一行探针
    print("\n③ 写通道（插一行探针，随后会删掉）")
    st3, t3, _ = rest("vocab_history_logs", "POST",
                      {"class_key": LOG_KEY, "class_name": "RLS探针", "student_id": PROBE_SID,
                       "timestamp": PROBE_TS, "duration_sec": 60, "action": "【探针】rls e2e"},
                      prefer="return=representation")
    check(st3 in (200, 201), "INSERT HTTP %s（要 200/201；401+42501 = 被 RLS 挡）%s"
          % (st3, (" " + t3[:120]) if st3 not in (200, 201) else ""))
    if st3 not in (200, 201):
        return 1

    # ④ 回读：行真的在吗（不信 HTTP 码）
    st4, rs4, _ = rows("vocab_history_logs?select=id&class_key=eq.%s&student_id=eq.%s" % (LOG_KEY, PROBE_SID))
    check(st4 == 200 and len(rs4) == 1, "回读：探针行在库里（HTTP %s，命中 %d 行）" % (st4, len(rs4)))

    # ⑤ 删：这条 policy 现在放行 class_key='LISTEN' 的删除
    st5, t5, _ = rest("vocab_history_logs?class_key=eq.%s&student_id=eq.%s" % (LOG_KEY, PROBE_SID), "DELETE")
    check(st5 in (200, 204), "DELETE HTTP %s（要 200/204）%s" % (st5, (" " + t5[:120]) if st5 not in (200, 204) else ""))

    # ⑥ 🔴 回读确认真的删掉了（PostgREST 删不掉也返回 200/204！）
    time.sleep(0.5)
    st6, rs6, _ = rows("vocab_history_logs?select=id&class_key=eq.%s&student_id=eq.%s" % (LOG_KEY, PROBE_SID))
    check(st6 == 200 and len(rs6) == 0, "回读确认：探针行真的没了（还剩 %d 行）" % len(rs6))
    if rs6:
        print("      🔴 删了但还在 ⇒ DELETE policy 没放行 class_key='LISTEN'（PostgREST 不会报错！只是没删）")

    print()
    if FAIL:
        print("❌ 失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("   -", f)
        return 1
    print("✅ 闭环全通：读 ✅ 写 ✅ 删 ✅（且都回读确认过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
