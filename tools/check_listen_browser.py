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
from datetime import datetime
from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else
        "https://tlv-creator.github.io/nhk-easy-news/listening/app").rstrip("/")
# 🔴 两种模式，判据不一样：
#   过渡期（默认）：anon 还能整班删 LISTEN（老师的按钮还靠 anon 兜底）
#   --locked      ：收尾 SQL 已跑 ⇒ **anon 整班删必须被拒**，老师按钮必须走服务端端点
LOCKED = "--locked" in sys.argv
if LOCKED:
    sys.argv = [a for a in sys.argv if a != "--locked"]
SU = "https://lszwxcmvcvbraqtrtgon.supabase.co"
AK = os.environ.get("SB_KEY", "sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo")
SID, NAME = "26270133", "王曦娴"
MARK = "浏览器闭环测试"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

fails = []

PROBE_SID = "__rls_probe_browser__"

# 🔴 收尾 SQL 跑完后，anon 删不动任何真记录 ⇒ 测试行也删不掉了
#    ⇒ 清理必须走**老师的特权端点**（不然测试行会留在生产库里）
OPS_URLS = [
    "https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai/vocab-ops",
    "https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/vocab-ops",
]


def teacher_hash():
    import hashlib
    return hashlib.sha256("vocab-unit23-teacher-2026|2013|781108".encode("utf-8")).hexdigest()


def ops_delete(log_id):
    """走老师的特权端点删一条（收尾 SQL 之后的唯一合法路径）"""
    body = json.dumps({"action": "delete_log", "teacher_id": "2013",
                       "teacher_hash": teacher_hash(), "id": log_id,
                       "class_key": "LISTEN"}).encode("utf-8")
    last = None
    for u in OPS_URLS:
        try:
            req = urllib.request.Request(u, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
    return {"ok": False, "error": "endpoint_unreachable: %s" % (last,)}


def snapshot():
    """破坏性操作（清空全部）前**必须快照** ⇒ 测完原样恢复"""
    r = api("GET", "vocab_history_logs?select=*&class_key=eq.LISTEN&order=id")
    return r if isinstance(r, list) else []


def restore(rows):
    """把快照写回去（id 让它自增；本表没有外键引用 id ⇒ 安全）"""
    n = 0
    for r in rows:
        body = {k: v for k, v in r.items() if k != "id"}
        if not body.get("class_key"):
            continue
        res = api("POST", "vocab_history_logs", body)
        if isinstance(res, dict) and res.get("__http"):
            print("      🔴 恢复失败：", res)
        else:
            n += 1
    return n


def fingerprint(rows):
    """行内容指纹（忽略 id ⇒ 恢复后 id 会变）"""
    import hashlib
    proj = sorted("%s|%s|%s|%s|%s" % (r.get("class_key"), r.get("class_name"), r.get("student_id"),
                                       r.get("timestamp"), r.get("action")) for r in rows)
    return hashlib.md5("\n".join(proj).encode("utf-8")).hexdigest()


def goto(pg, url, tries=3):
    """🔴 网络会抖 ⇒ goto 必须重试；且**不用 networkidle**（它等「网络安静」，一挂就假超时）"""
    import time as _t
    last = None
    for i in range(tries):
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            return True
        except Exception as e:
            last = e; print("   ↻ 第 %d 次打开失败，重试：%s" % (i + 1, str(e)[:80])); _t.sleep(3)
    raise last


def api(method, path, body=None):
    """🔴 回读也要抗抖动：网络抖一次就报「产品坏了」是最冤的假红"""
    import time as _t
    last = None
    for i in range(3):
        try:
            return _api_once(method, path, body)
        except Exception as e:
            last = e; _t.sleep(2)
    raise last


def _api_once(method, path, body=None):
    h = {"apikey": AK, "Authorization": "Bearer " + AK, "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(SU + "/rest/v1/" + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=40) as resp:
        t = resp.read().decode("utf-8", "replace")
        return json.loads(t) if t.strip().startswith(("[", "{")) else t



def ck(n, c, e=""):
    print(("  PASS " if c else "  FAIL ") + n + (("  | " + str(e)[:200]) if e and not c else ""))
    if not c:
        fails.append(n)
    return c


def api_raw(method, path, body=None):
    h = {"apikey": AK, "Authorization": "Bearer " + AK, "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(SU + "/rest/v1/" + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            t = resp.read().decode("utf-8", "replace")
            return json.loads(t) if t.strip().startswith(("[", "{")) else t
    except urllib.error.HTTPError as e:
        return {"__http": e.code, "__body": e.read().decode("utf-8", "replace")[:200]}


def roster_student():
    """从库里取这个学生的真 class_name（🔴 手写 class_name ⇒ 会被老师的班级过滤筛掉 ⇒ 假红）"""
    r = api("GET", "vocab_states?select=state_val&class_key=eq.__roster_v1")
    try:
        rows = r if isinstance(r, list) else []
        for row in rows:
            for st in json.loads(row.get("state_val") or "[]"):
                if str(st.get("id")) == SID:
                    return st.get("class_name") or "", st.get("class_key") or ""
    except Exception:
        pass
    return "", ""


def db_rows():
    r = api("GET", "vocab_history_logs?select=id,student_id,action&class_key=eq.LISTEN")
    return r if isinstance(r, list) else []


def db_mine():
    r = api("GET", "vocab_history_logs?select=id,action&class_key=eq.LISTEN&student_id=eq." + SID)
    return r if isinstance(r, list) else []


def cleanup():
    """🔴🔴 只删**测试行** ⇒ 绝不能按 class_key 整表删
       （上一版整表删 ⇒ 把班上 35 条真实记录一起删了 ⇒ 那是我的事故）"""
    rows = db_rows()
    n = 0
    for r in rows:
        blob = "%s %s" % (r.get("action") or "", r.get("student_id") or "")
        if MARK in blob or "探针" in blob or "rls" in blob.lower() or r.get("student_id") == PROBE_SID:
            if LOCKED:
                # 收尾后 anon 删不动 ⇒ 走老师端点
                res = ops_delete(r["id"])
                if not (res and res.get("ok")):
                    print("      ⚠️ 端点删除失败：", res)
                else:
                    n += 1
            else:
                api("DELETE", "vocab_history_logs?id=eq.%s&class_key=eq.LISTEN" % r["id"])
                n += 1
    return n


print("=== 每日听力记录站 · 浏览器级闭环（%s）===" % BASE)
# 🔴 快照必须取在**任何测试动作之前** ⇒ 它就是「测试前的真实状态」
SNAP = snapshot()
FP_BEFORE = fingerprint(SNAP)
print("   测试前 LISTEN 状态：%d 行（指纹 %s）" % (len(SNAP), FP_BEFORE[:10]))
print("-- 清理历史测试数据：清掉 %d 条" % cleanup())

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=CHROME,
                           args=["--no-sandbox", "--ignore-certificate-errors", "--ignore-ssl-errors"])
    pg = br.new_page(viewport={"width": 414, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append("c:" + m.text) if m.type == "error" else None)
    # 🔴 confirm() 一律确认；prompt() 必须**输入「清空」**（老师端的护栏：手打两个字才执行）
    #    ⇒ 只 accept() 会确认成空字符串 ⇒ 护栏正确地取消操作 ⇒ 脚本会误报「清空失败」
    pg.on("dialog", lambda d: d.accept("清空"))

    print("\n-- 学生端 --")
    goto(pg, BASE + "/student.html")
    pg.wait_for_selector("#sid", timeout=30000)
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

    # ②b 再交一条，**留给老师看**（UI 提交的才有真 class_name / 真日期 ⇒ 老师端默认筛选能命中）
    pg.click("#btnAdd"); pg.wait_for_timeout(400)
    pg.fill("#fContent", MARK + "（给老师看）")
    pg.click("#minChips button:nth-child(3)")
    pg.click("#catChips button:nth-child(1)")
    pg.click("#btnSave"); pg.wait_for_timeout(3500)
    ck("②b 学生再交一条（留给老师端看）", (MARK + "（给老师看）") in pg.inner_text("#myLogs"),
       pg.inner_text("#addMsg"))

    print("\n-- 教师端 --")
    print("   （清空全部前会恢复测试前快照：%d 行）" % len(SNAP))
    pg2 = br.new_page(viewport={"width": 1280, "height": 900})
    pg2.on("pageerror", lambda e: errs.append("T:" + str(e)))
    pg2.on("dialog", lambda d: d.accept("清空"))   # 🔴 同上：prompt 要输入「清空」
    goto(pg2, BASE + "/teacher.html")
    pg2.wait_for_selector("#tid", timeout=30000)
    pg2.fill("#tid", "2013"); pg2.fill("#tpwd", "781108")
    pg2.click("#btnLogin"); pg2.wait_for_timeout(4000)
    ck("教师登录进看板", pg2.is_visible("#viewDash"), pg2.inner_text("#loginMsg"))

    # 造一条给老师删
    # 🔴 日期必须落在老师端的默认日期区间内（默认区间末端 = 今天）
    #    ⇒ 用 2099 会让明细表「理应为空」⇒ 那是**我的样本错**，不是产品坏
    _n = datetime.now()
    _ts = "%d/%d/%d %02d:%02d:00" % (_n.year, _n.month, _n.day, _n.hour, _n.minute)
    _cname, _ckey = roster_student()
    api("POST", "vocab_history_logs", {"class_key": "LISTEN", "class_name": _cname or "互联网金融",
                                        "student_id": SID, "timestamp": _ts,
                                        "duration_sec": 600, "action": "【听力】测试 | " + MARK})
    pg2.click("#tabLog") if pg2.query_selector("#tabLog") else None
    pg2.wait_for_timeout(2500)
    # 🔴 判据不能是「行数 > 0」：读被挡时表格也会渲染一行「没有记录」⇒ 假绿
    # ⇒ 必须看**内容**（我那条记录的内容真的出现在表里）
    _tbl = pg2.inner_text("#tblLog")
    ck("明细表里能看到学生刚交的那条（看内容，不看行数）", (MARK + "（给老师看）") in _tbl,
       "表格文本前 160 字: %r" % _tbl[:160])
    ck("明细表里能看到探针那条（真 class_name 才不被班级过滤筛掉）", MARK in _tbl,
       "表格文本前 160 字: %r" % _tbl[:160])

    n_before = len(db_rows())
    delbtns = pg2.query_selector_all("#tblLog tbody button")
    ck("明细表里有删除按钮", len(delbtns) >= 1, len(delbtns))
    if delbtns:
        delbtns[0].click(); pg2.wait_for_timeout(3000)
        n_after = len(db_rows())
        ck("③ 教师删一条 → 库里真的少了（回读）", n_after < n_before,
           "删前 %d → 删后 %d" % (n_before, n_after))

    # ④-b 🔴 关键判据：拿 anon key **直接 curl** 整班删，必须被拒
    #      （PostgREST 挡 DELETE 也返回 200/204 ⇒ 必须回读，不能看返回码）
    #      ⇒ 放在「清空全部」**之前**（那时库里还有行，判据才不是空跑）
    if LOCKED:
        _n0 = len(db_rows())
        api("DELETE", "vocab_history_logs?class_key=eq.LISTEN")
        _n1 = len(db_rows())
        ck("④-b anon 直接 curl 整班删 → **被拒**（行还在）", _n1 == _n0,
           "删前 %d → 删后 %d（行少了 ⇒ 洞没关上）" % (_n0, _n1))
        _rows = db_rows()
        if _rows:
            _id = _rows[0].get("id")
            api("DELETE", "vocab_history_logs?id=eq.%s&class_key=eq.LISTEN" % _id)
            _n2 = len(db_rows())
            ck("④-c anon 直接 curl 删单条 → **被拒**（行还在）", _n2 == _n1,
               "删前 %d → 删后 %d" % (_n1, _n2))
        else:
            ck("④-c anon 直接 curl 删单条（跳过：本来就没数据）", False, "没有可测的行")
    else:
        print("   （过渡期：anon 整班删还开着，这是预期的；收尾 SQL 跑完后加 --locked 重跑）")

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

print("\n-- 恢复快照（把清空测试弄掉的真实记录放回去）--")
restored = restore(SNAP)
after = snapshot()
ck("真实记录已恢复（行数一致）", len(after) == len(SNAP), "测试前 %d → 恢复后 %d" % (len(SNAP), len(after)))
ck("真实记录内容一致（指纹相同）", fingerprint(after) == FP_BEFORE,
   "指纹 %s vs %s" % (FP_BEFORE[:10], fingerprint(after)[:10]))
ck("测试行没混进来", not any(MARK in (r.get("action") or "") for r in after),
   [r.get("action") for r in after if MARK in (r.get("action") or "")][:3])

print("\n-- 清理（只删测试行）--")
n = cleanup()
left = db_rows()
ck("测试数据已删干净（真实记录不受影响）", not any(MARK in (r.get("action") or "") for r in left),
   "库里还剩 %d 行（真实记录）" % len(left))
# 🔴 必须用**同一个取数函数**比（db_rows 只 select 3 列，snapshot select 全部列
#    ⇒ 两套列的指纹当然不同 ⇒ 那是我的判据写错，不是数据出问题）
_final = snapshot()
ck("🔴 最终：LISTEN 与测试前**逐字一致**（行数+指纹）", len(_final) == len(SNAP) and fingerprint(_final) == FP_BEFORE,
   "测试前 %d 行/指纹 %s → 现在 %d 行/指纹 %s" % (len(SNAP), FP_BEFORE[:10], len(_final), fingerprint(_final)[:10]))
real = [e for e in errs if "favicon" not in e.lower()]
ck("无 JS 报错", len(real) == 0, real[:3])

print("\n结果: %d 项失败" % len(fails))
for f in fails:
    print("   -", f)
sys.exit(1 if fails else 0)
