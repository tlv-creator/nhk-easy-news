#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日听力记录站 · **浏览器级闭环验收**（打线上 Pages，每步都回读数据库）

为什么必须是浏览器级：
  🔴 shim（api.js 的 realRpc）把 8 个逻辑 RPC 翻译成 REST 调用
     ⇒ **DB 通 ≠ UI 通**（shim 少一个分支 / 传错字段，UI 就静默失败）
  🔴 三个删除按钮走的是 **裸 DELETE** ⇒ PostgREST 删不掉也返回 200/204
     ⇒ 必须**回读数据库**确认行真的没了

覆盖：学生登录 → 交一条 → 库里在 → 学生删自己的 → 库里没了
      教师登录 → 明细看得到 → 删一条 → 库里没了 → 清空全部 → 库里空
用法：
    python3 browser_e2e.py [BASE_URL]
    BASE_URL 默认 = 线上永久入口
"""
import sys, os, re, json, time, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else
        "https://tlv-creator.github.io/nhk-easy-news/listening/app").rstrip("/")
SU = "https://lszwxcmvcvbraqtrtgon.supabase.co"
AK = os.environ.get("SB_KEY", "sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo")
SID, NAME = "26270133", "王曦娴"
MARK = "浏览器闭环测试"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

fails = []


def ck(n, c, e=""):
    print(("  PASS " if c else "  FAIL ") + n + (("  | " + str(e)[:200]) if e and not c else ""))
    if not c:
        fails.append(n)
    return c


def api(method, path, body=None):
    h = {"apikey": AK, "Authorization": "Bearer " + AK, "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(SU + "/rest/v1/" + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            t = resp.read().decode("utf-8", "replace")
            return json.loads(t) if t.strip().startswith(("[", "{")) else t
    except urllib.error.HTTPError as e:
        return {"__http": e.code, "__body": e.read().decode("utf-8", "replace")[:200]}


def db_rows():
    r = api("GET", "vocab_history_logs?select=id,student_id,action&class_key=eq.LISTEN")
    return r if isinstance(r, list) else []


def db_mine():
    r = api("GET", "vocab_history_logs?select=id,action&class_key=eq.LISTEN&student_id=eq." + SID)
    return r if isinstance(r, list) else []


def cleanup():
    rows = db_rows()
    for r in rows:
        api("DELETE", "vocab_history_logs?id=eq.%s&class_key=eq.LISTEN" % r["id"])
    return len(rows)


print("=== 每日听力记录站 · 浏览器级闭环（%s）===" % BASE)
print("-- 清理历史测试数据：清掉 %d 条" % cleanup())

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=CHROME,
                           args=["--no-sandbox", "--ignore-certificate-errors", "--ignore-ssl-errors"])
    pg = br.new_page(viewport={"width": 414, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append("c:" + m.text) if m.type == "error" else None)
    pg.on("dialog", lambda d: d.accept())          # confirm() 一律确认

    print("\n-- 学生端 --")
    pg.goto(BASE + "/student.html", wait_until="networkidle", timeout=90000)
    pg.fill("#sid", SID); pg.fill("#sname", NAME)
    pg.click("#btnLogin"); pg.wait_for_timeout(3000)
    ck("学生登录进主页", pg.is_visible("#viewHome"), pg.inner_text("#loginMsg"))

    pg.click("#btnAdd"); pg.wait_for_timeout(400)
    pg.fill("#fContent", MARK)
    pg.click("#minChips button:nth-child(3)")
    pg.click("#catChips button:nth-child(1)")
    pg.click("#btnSave"); pg.wait_for_timeout(3500)
    ck("提交成功（UI 提示/列表出现）", MARK in pg.inner_text("#myLogs"), pg.inner_text("#addMsg"))

    rows = db_mine()
    ck("① 库里确实写进去了（回读）", len(rows) >= 1, rows)
    ck("① 写进去的 action 带标记", any(MARK in (r.get("action") or "") for r in rows), rows)

    # ② 学生删自己的 —— UI 按钮
    n_before = len(db_mine())
    btns = pg.query_selector_all("#myLogs button")
    ck("列表里有删除按钮", len(btns) >= 1, len(btns))
    if btns:
        btns[0].click(); pg.wait_for_timeout(3000)
        rows2 = db_mine()
        ck("② 学生删自己的 → 库里真的少了（回读，不看 HTTP 码）", len(rows2) < n_before,
           "删前 %d → 删后 %d" % (n_before, len(rows2)))

    print("\n-- 教师端 --")
    pg2 = br.new_page(viewport={"width": 1280, "height": 900})
    pg2.on("pageerror", lambda e: errs.append("T:" + str(e)))
    pg2.on("dialog", lambda d: d.accept())
    pg2.goto(BASE + "/teacher.html", wait_until="networkidle", timeout=90000)
    pg2.fill("#tid", "2013"); pg2.fill("#tpwd", "781108")
    pg2.click("#btnLogin"); pg2.wait_for_timeout(4000)
    ck("教师登录进看板", pg2.is_visible("#viewDash"), pg2.inner_text("#loginMsg"))

    # 造一条给老师删
    api("POST", "vocab_history_logs", {"class_key": "LISTEN", "class_name": "浏览器闭环测试",
                                        "student_id": SID, "timestamp": "2099/01/01 00:00:00",
                                        "duration_sec": 600, "action": "【听力】测试 | " + MARK})
    pg2.click("#tabLog") if pg2.query_selector("#tabLog") else None
    pg2.wait_for_timeout(2500)
    # 🔴 判据不能是「行数 > 0」：读被挡时表格也会渲染一行「没有记录」⇒ 假绿
    # ⇒ 必须看**内容**（我那条记录的内容真的出现在表里）
    ck("明细表里能看到刚才那条（看内容，不看行数）", MARK in pg2.inner_text("#tblLog"),
       "表格文本前 120 字: %r" % pg2.inner_text("#tblLog")[:120])

    n_before = len(db_rows())
    delbtns = pg2.query_selector_all("#tblLog tbody button")
    ck("明细表里有删除按钮", len(delbtns) >= 1, len(delbtns))
    if delbtns:
        delbtns[0].click(); pg2.wait_for_timeout(3000)
        n_after = len(db_rows())
        ck("③ 教师删一条 → 库里真的少了（回读）", n_after < n_before,
           "删前 %d → 删后 %d" % (n_before, n_after))

    # ④ 清空全部
    n_before = len(db_rows())
    if pg2.query_selector("#btnClear"):
        # 🔴 空跑也是绿 ⇒ 必须先确认「确实有东西可清」，否则这条断言没有意义
        if ck("清空前库里确实有 %d 条（有东西可清）" % n_before, n_before >= 1, n_before):
            pg2.click("#btnClear"); pg2.wait_for_timeout(3500)
            n_after = len(db_rows())
            ck("④ 教师清空全部 → 库里真的空了（回读）", n_after == 0,
               "清空前 %d → 清除后 %d" % (n_before, n_after))
        else:
            ck("④ 教师清空全部（跳过：本来就没数据可清）", False, "没有可清的记录")
    else:
        ck("④ 清空按钮存在", False, "没找到 #btnClear")

    pg.screenshot(path="/workspace/listen-log/build/shots/e2e_student.png", full_page=True)
    pg2.screenshot(path="/workspace/listen-log/build/shots/e2e_teacher.png", full_page=True)
    br.close()

print("\n-- 清理 --")
n = cleanup()
left = db_rows()
ck("测试数据已删干净", len(left) == 0, left)
real = [e for e in errs if "favicon" not in e.lower()]
ck("无 JS 报错", len(real) == 0, real[:3])

print("\n结果: %d 项失败" % len(fails))
for f in fails:
    print("   -", f)
sys.exit(1 if fails else 0)
