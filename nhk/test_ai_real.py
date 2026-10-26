#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真接口模式的 5 项实测适配测试（2026-10-22 中转实测结果驱动）

对应中转端跑出来的真实情况：
  ① 响应约 15~16 秒 → 超时 75 秒（中转上游 60s + 余量）+ 「思考中… 已用 N 秒」计数（不做流式）
  ② 模型标的假名**不可信**（实测把「女の子」的「子」标成「このこ」）→ 以 App 查表为准
  ③ 模型可能输出 ```text 围栏的代码块 → 要能渲染，不能原样吐围栏
  ④ refs 要是干净的本剧真实台词（不带内嵌读音括号）
  ⑤ 中转的错误码 → 前端显示对应文案

用法： python3 test_ai_real.py [base_url]
（需要 mock： python3 mock_ai.py 8898）

⚠️ **只能在本地 http 站跑**：线上是 https，访问 http://127.0.0.1:8898 的 mock 会被
CORS 拦（loopback 跨源）→ 线上跑必然全红，**那是测试环境限制，不是产品 bug**。
线上要验的是「代码已部署」→ 直接 grep 线上 app.js 有没有 AI_TIMEOUT=75000 / aiFixReading 等。
⇒ 真中转部署后**必须带 `Access-Control-Allow-Origin`**（App 站点与云函数不同源）。
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "BASE", "http://127.0.0.1:8899/")
MOCK = os.environ.get("MOCK", "http://127.0.0.1:8898")
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + "  " + name +
          (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


# ══════════════════════════════════════════════════════════════════
# 【0 组】静态断言：前端 prompt === 中转 prompt（**逐字**，不是比长度）
# ══════════════════════════════════════════════════════════════════
# 🔴 为什么必须逐字而不是比长度：
#   2026-10-22 踩过 —— 手改 prompt 时把「直**说**不确定」打成「直**而**不确定」，
#   两个字长度一样 → 字符数 573 恰好匹配 → **没被发现**。后来靠逐字 diff 揪出来。
#   ⇒ 「长度对上」≠「内容对上」。这条断言就是防这个的。
import re as _re
import os.path as _osp

_APP_JS = "/workspace/nhk/site/app/app.js"
_FIXTURE = "/workspace/nhk/work/backend_system_prompt.txt"


def _frontend_prompt():
    """从 app.js 里抠出 AI_SYSTEM_PROMPT 的**实际值**。
    ⚠️ 必须能还原 \n 换行（prompt 是多行的，每行一个 JS 字符串字面量）。"""
    src = _open(_APP_JS, encoding="utf-8").read()
    m = _re.search(r"var AI_SYSTEM_PROMPT\s*=\s*(.*?);\n", src, _re.S)
    if not m:
        return ""
    parts = _re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))
    return ("".join(parts).replace("\\n", "\n").replace("\\'", "'").strip())


def _open(p, **kw):
    return open(p, **kw)


if _osp.exists(_FIXTURE) and _osp.exists(_APP_JS):
    _fix = _open(_FIXTURE, encoding="utf-8").read().strip()
    _fe = _frontend_prompt()
    ck("【0】前端 prompt 与中转基准逐字一致",
       _fe == _fix and len(_fix) > 0,
       ("前端 %d / 基准 %d" % (len(_fe), len(_fix))) if _fe != _fix else "%d 字符" % len(_fix))
    ck("【0】中转 prompt 基准是 1036 字符（新答题模式 + 可选例句）", len(_fix) == 1036, len(_fix))
    # 🔴 手滑错字黑名单（长度一样但内容错 → 只有逐字比对才抓得到）
    for _typo, _why in [("直而不确定", "手滑：直说→直而"),
                        ("例句优先用上下文里的真实台词", "旧版那行，已被逐字取自上下文取代"),
                        ("包括在内", "占位符残留")]:
        ck("【0】prompt 里没有「%s」（%s）" % (_typo, _why),
           _typo not in _fix)
    _ai_js = _open(_APP_JS, encoding="utf-8").read()
    ck("【0】前端超时是 75000ms（中转上游 60s + 余量）",
       "AI_TIMEOUT = 75000" in _ai_js)
    # 2026-10-22：peer 把「例句优先取上一句…」那行删了（与「不许把当前句重复当例句」冲突）
    ck("【0】prompt 含「例句是**可选**的」规则",
       "例句是**可选**的" in _fix)
    ck("【0】prompt 含「不许把当前句重复当例句」",
       "不许把当前句重复当例句" in _fix)
    ck("【0】前端 prompt 里的例句规则没被手抄走样",
       "      - 一旦要写，必须逐字取自上下文" in _fe and
       "      - 引用例句时只写日文原文" in _fe)
else:
    print("  SKIP  【0】组（找不到 %s 或 %s）" % (_APP_JS, _FIXTURE))


def open_study(pg):
    pg.evaluate("document.querySelector('.ep-item').click()")
    pg.wait_for_timeout(1800)
    pg.evaluate("""()=>{
        var cs = document.querySelectorAll('#transcript .cue');
        if (cs[1]) cs[1].click();
    }""")
    pg.wait_for_timeout(1200)


with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME,
                            args=["--no-sandbox", "--ignore-certificate-errors",
                                  "--autoplay-policy=no-user-gesture-required"])
    pg = br.new_page(viewport={"width": 412, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(800)

    # ══ ① 秒数计时（不流式）══
    print("[①] 响应慢时的反馈")
    pg.evaluate("(u)=>{window.CFG=window.CFG||{}; window.CFG.AI_ENDPOINT=u;}", MOCK + "/ai-delay")
    open_study(pg)
    pg.locator("[data-act='ai-sentence']").first.click()
    pg.wait_for_timeout(2500)
    txt = pg.locator("#aiBox").inner_text()
    ck("等待时显示秒数", "已用" in txt and "秒" in txt, txt[:40])
    ck("提示是「思考中…」", "思考中" in txt, txt[:20])
    ck("没有逐字流式（等完才一次性出结果）",
       "逐词拆解" not in txt, txt[:40])
    pg.wait_for_timeout(2500)   # mock 延迟 4 秒，这里要等它真的返回
    ck("请求完成后正常渲染",
       "逐词拆解" in pg.locator("#aiBox").inner_text())

    # ══ ② 模型假名不可信 → 以查表为准 ══
    print("\n[②] 模型假名纠正")
    pg.evaluate("(u)=>{window.CFG.AI_ENDPOINT=u;}", MOCK + "/ai-badread")
    open_study(pg)
    pg.locator("[data-act='ai-sentence']").first.click()
    pg.wait_for_timeout(1800)
    html = pg.evaluate("document.querySelector('#aiBox').innerHTML")
    txt = pg.locator("#aiBox").inner_text()
    ck("模型的错误假名「（このこ）」被纠正了", "このこ" not in txt, txt[:60])
    ck("用查表读音「（おんなのこ）」", "おんなのこ" in txt, txt[:60])
    ck("原文那行用 App 的 ruby", "<ruby" in html)
    # 同一处不出现两套假名：模型那套已被替换成 App 那套
    ck("没有两套假名并存",
       txt.count("おんなのこ") == txt.count("このこ") + txt.count("おんなのこ"))

    # ══ ③ markdown 代码块 ══
    print("\n[③] markdown 代码块")
    # 代码块的断言放到 ③b（用带围栏的 /ai-table 端点）

    # ══ ③b 真实 M3 输出的分段 + 表格渲染（回归）══
    print("\n[③b] 真实 M3 输出：分段 + 表格")
    # 表格版本单独用 /ai-table 端点测
    pg.evaluate("(u)=>{window.CFG.AI_ENDPOINT=u;}", MOCK + "/ai-table")
    open_study(pg)
    pg.locator("[data-act='ai-sentence']").first.click()
    pg.wait_for_timeout(2000)
    html2 = pg.evaluate("document.querySelector('#aiBox').innerHTML")
    txt2 = pg.locator("#aiBox").inner_text()
    # 🔴 回归：表格表头含「语法功能」→ 旧逻辑会把它当标题 → 逐词拆解整段丢失
    ck("逐词拆解段非空（表格表头没被误当标题）",
       "逐词拆解" in txt2 and len(txt2) > 500, len(txt2))
    ck("逐词拆解段里有实际词条（宮崎県 → 宫崎县）",
       "宫崎县" in txt2 and "地名" in txt2)
    ck("表格没原样吐出竖线", "|" not in txt2, txt2[:60])
    ck("表格转成了行式（词 → 释义）", "→" in txt2)
    ck("代码块渲染成 <pre class=\"ai-code\">", 'class="ai-code"' in html2)
    ck("没有原样吐出 ``` 围栏", "```" not in txt2, txt2[-60:])
    ck("代码块里有结构内容", "主语" in txt2 or "谓语" in txt2)
    ck("语法说明段还在（### 小标题不误判）",
       "列举助词" in txt2)
    # 五段都在
    for t in ("原文", "中文翻译", "逐词拆解", "语法说明", "整句结构框架"):
        ck("五段标题都在：%s" % t, t in txt2)

    # ══ ③c 真中转格式（「N. 标题：内容」行内标题）══
    print("\n[③c] 真中转格式：行内标题")
    pg.evaluate("(u)=>{window.CFG.AI_ENDPOINT=u;}", MOCK + "/ai")
    open_study(pg)
    pg.locator("[data-act='ai-sentence']").first.click()
    pg.wait_for_timeout(2000)
    segs = pg.evaluate("""()=>{var o={};[].forEach.call(document.querySelectorAll('#aiBox .ai-sec'),
        function(s){var h=s.querySelector('.ai-h').textContent.replace(/[▲▼]\\s*$/,'').trim();
        o[h]=s.querySelector('.ai-b').innerText.trim().length;});return o;}""")
    for k in ("原文", "中文翻译", "逐词拆解", "语法说明", "整句结构框架"):
        ck("真中转格式下「%s」段非空" % k, segs.get(k, 0) > 0, segs.get(k))

    # ══ ④ refs 干净 ══
    print("\n[④] refs 干净")
    body = pg.evaluate("window.__AI.lastBody")
    bad = [r["ja"] for r in body["refs"]
           if any(ch in (r["ja"] or "") for ch in "ぁ-んァ-ヶ")
           and "（" in (r["ja"] or "")]
    ck("refs 不含内嵌读音括号", not bad, bad[:2])
    # 括号里是纯假名的那种（读音）才要查；内容里的括号（如气温）不算
    import re
    reading_like = []
    for r in body["refs"]:
        for m in re.finditer(r"[（(]\s*([ぁ-んァ-ヶー]+)\s*[）)]", r["ja"] or ""):
            reading_like.append(m.group(0))
    ck("refs 没有「（假名）」这种读音括号", not reading_like, reading_like[:3])

    # ══ ⑤ 错误码 ══
    print("\n[⑤] 中转错误码 → 前端文案")
    CASES = [
        ("not_configured", "AI 未配置"),
        ("unauthorized", "AI 未配置"),
        ("too_many", "今天用得有点多"),
        ("quota_exceeded", "額度用完了"),
        ("timeout", "網絡失敗，請稍後重試"),
        ("upstream_params", "AI 暫時不可用"),
        ("upstream_error", "AI 暫時不可用"),
        ("upstream_auth", "AI 暫時不可用"),
    ]
    for code, want in CASES:
        pg.evaluate("(u)=>{window.CFG.AI_ENDPOINT=u;}", MOCK + "/ai-err/" + code)
        open_study(pg)
        pg.locator("[data-act='ai-sentence']").first.click()
        pg.wait_for_timeout(1200)
        t = pg.locator("#aiBox").inner_text()
        ck("错误码 %s → 「%s」" % (code, want), want in t, t[:30])

    # ══ 未知错误码兜底 ══
    pg.evaluate("(u)=>{window.CFG.AI_ENDPOINT=u;}", MOCK + "/ai-fail")
    open_study(pg)
    pg.locator("[data-act='ai-sentence']").first.click()
    pg.wait_for_timeout(1200)
    t = pg.locator("#aiBox").inner_text()
    ck("未知错误兜底为「AI 暫時不可用」", "AI 暫時不可用" in t, t[:30])

    # 错误码测试故意触发 4xx/5xx → 浏览器会记 console error，这是预期的
    real_errs = [e for e in errs
                 if "404" not in e and "favicon" not in e
                 and "Failed to load resource" not in e]
    ck("无 JS 错误", len(real_errs) == 0, real_errs[:3])
    br.close()

print("\n== 失败 %d 项 ==" % len(fails))
sys.exit(1 if fails else 0)
