#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /vocab-ops 端点拼进现役 ai-proxy 的 index.js —— 每一步都有断言

纪律（来自协作方的合并器四条 + 我自己的两条）：
  ① **基底必须是现役那份**（zip 里那份，sha256 逐字对上）⇒ 不是记忆里的、不是 GitHub 旧副本
  ② **逐字不变断言**：把插入块删掉后，文件必须与基底**逐字相同**
  ③ 插入点必须**唯一**（多处匹配 ⇒ 拒绝打补丁）
  ④ 注释里的 `*/` 会不会吃掉后面的代码 ⇒ 打完必须 `node --check`
  ⑤ **prompt 指纹必须不变**（SYSTEM_PROMPT 的运行时 sha256）⇒ 这是「没改坏日语端」的唯一证据
  ⑥ 端点模块的**身份标记**必须在（service:'vocab-ops'）⇒ 前端靠它认人
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

BASE = "/workspace/listen-log/build/aiproxy"
SRC = os.path.join(BASE, "src")
OUT = os.path.join(BASE, "patched")
ZIP = os.path.join(BASE, "ai-proxy-code.zip")
EXPECT_ZIP_SHA = "b21784ff2af905c028ee0b91e84fc5674f02c745e56117aa6f2d95bf0f5cef37"
OPS = "/workspace/listen-log/cloudbase/vocab-ops/vocab_ops.js"

INSERT = r'''  /* ==========================================================================
   * 🔴 听力记录站：教师侧特权端点 /vocab-ops（2026-10-24 加）
   * ----------------------------------------------------------------------
   * 为什么加在**现役函数**里、而不是新建一个云函数：
   *   · 新建函数会丢 HTTP 触发器 + 环境变量（MM_API_KEY 那次踩过）⇒ 只能加在现役函数上
   *   · 上传 zip = 整份替换函数代码 ⇒ 这份 zip 必须以**现役 index.js** 为基底
   *   · 本端点**不碰 MiniMax**（不烧额度、不用 AI_TOKEN）⇒ 不影响现役三条路由
   *
   * 🔴 路由识别**不能只看 path**：网关可能把前缀剥掉
   *   （现役 https://…service.tcloudbase.com/ai 进来时 event.path 可能是 '/'）
   *   ⇒ 双重判据：① path 里含 '/vocab-ops'
   *                 ② 请求体的 action 是本端点约定的三个之一
   *   ⇒ AI 端点的请求体是 {scene, ja, zh…} ⇒ 没有 action 字段 ⇒ 绝不会误判
   * ======================================================================= */
  {
    let __act = '';
    try {
      const __raw0 = (event && event.body !== undefined && event.body !== null) ? event.body : event;
      const __o0 = (typeof __raw0 === 'string') ? JSON.parse(__raw0) : __raw0;
      if (__o0 && typeof __o0 === 'object') __act = String(__o0.action || '');
    } catch (e) { __act = ''; }
    const __p0 = String((event && event.path) || '');
    if (__p0.indexOf('/vocab-ops') >= 0 ||
        __act === 'ping' || __act === 'delete_log' || __act === 'clear_class') {
      return require('./vocab_ops').handleVocabOps(event);
    }
  }

'''

ANCHOR = """  /* CORS 预检 */
  if (method === 'OPTIONS') return reply(204, { ok: true });

"""


def sha(b):
    return hashlib.sha256(b).hexdigest()


def fail(msg):
    print("❌ " + msg)
    sys.exit(1)


def main():
    # ── ① 基底核对 ──────────────────────────────────────────────
    if not os.path.exists(ZIP):
        fail("找不到 zip：%s" % ZIP)
    got = sha(open(ZIP, "rb").read())
    if got != EXPECT_ZIP_SHA:
        fail("zip 的 sha256 不对\n   期望 %s\n   实际 %s" % (EXPECT_ZIP_SHA, got))
    print("· 基底 zip sha256 逐字对上 ✅ %s" % got[:16])

    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(OUT)
    base_index = open(os.path.join(OUT, "index.js"), "rb").read()
    print("· 基底 index.js %d B sha=%s" % (len(base_index), sha(base_index)[:16]))

    # ── ② 打补丁（插入点必须唯一）───────────────────────────────
    s = base_index.decode("utf-8")
    n = s.count(ANCHOR)
    if n != 1:
        fail("插入点命中 %d 次（必须恰好 1 次）⇒ 拒绝打补丁" % n)
    s2 = s.replace(ANCHOR, ANCHOR + INSERT)

    # ── ③ 逐字不变断言：删掉插入块后必须与基底逐字相同 ──────────
    s3 = s2.replace(ANCHOR + INSERT, ANCHOR)
    if s3 != s:
        fail("逐字不变断言失败：删掉插入块后与基底不一致")
    print("· 逐字不变断言 ✅（插入块可原样摘除）")

    open(os.path.join(OUT, "index.js"), "w", encoding="utf-8", newline="").write(s2)
    shutil.copy(OPS, os.path.join(OUT, "vocab_ops.js"))

    # ── ④ 语法检查（注释缺 */ 会一路吃掉文件末尾）────────────────
    for f in ("index.js", "vocab_ops.js"):
        r = subprocess.run(["node", "--check", os.path.join(OUT, f)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail("node --check 失败：%s\n%s" % (f, r.stderr[:800]))
    print("· node --check 两个文件都过 ✅")

    # ── ⑤ prompt 指纹必须不变（把 index.js 在沙箱里跑一遍取运行时值）──
    def prompt_sha_of(path):
        r = subprocess.run(["node", os.path.join(BASE, "prompt_sha.js"), path],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            fail("算 prompt 指纹失败：%s" % (r.stderr[-500:] or r.stdout[-500:]))
        return r.stdout.strip()

    a = prompt_sha_of(os.path.join(SRC, "index.js"))
    b = prompt_sha_of(os.path.join(OUT, "index.js"))
    print("· 基底 prompt：%s" % a)
    print("· 打完 prompt：%s" % b)
    if a != b:
        fail("❌ prompt 指纹变了 ⇒ 我改坏了日语端，绝不能给老师")
    print("· prompt 指纹逐字相同 ✅（没改坏日语端）")
    EXP = "1036 b62731f9d93d94fbb0cab505c6a1ae5f77bdd0b8407c7c7ca7123ecde8a66731"
    if a != EXP:
        print("⚠️ 指纹与线上记录的不一致（线上 %s）⇒ 继续但要人工确认" % EXP[:24])

    # ── ⑥ 端点模块必须带身份标记 + 必须在「没有全局 URL」的环境里能跑 ──
    ops = open(os.path.join(OUT, "vocab_ops.js"), encoding="utf-8").read()
    if "service: 'vocab-ops'" not in ops:
        fail("端点模块没有 service 身份标记 ⇒ 前端认不出它")
    print("· 端点模块：有身份标记 ✅")

    # 🔴 判据用**行为**而不是文本扫描：
    #   云函数沙箱里没有全局 URL ⇒ 把 globalThis.URL 删掉再跑一遍
    #   （文本扫描会把自己的注释判成违规 —— 我今天刚踩过这个「裁判兼球员」）
    probe = """
    delete globalThis.URL;
    if (typeof URL !== 'undefined') { console.log('CANNOT_DELETE_URL'); process.exit(4); }
    const m = require(process.argv[process.argv.length - 1]);
    m.handleVocabOps({ path: '/vocab-ops', httpMethod: 'POST', body: JSON.stringify({ action: 'ping' }) })
      .then(function (r) {
        const b = JSON.parse(r.body);
        if (r.statusCode !== 200 || b.service !== 'vocab-ops') { console.log('BAD:' + r.body); process.exit(5); }
        console.log('OK service=' + b.service + ' status=' + r.statusCode);
      })
      .catch(function (e) { console.log('THROW:' + ((e && e.message) || e)); process.exit(6); });
    """
    r = subprocess.run(["node", "-e", probe, os.path.join(OUT, "vocab_ops.js")],
                       capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "OK service=vocab-ops" not in out:
        fail("端点模块在「没有全局 URL」的环境里跑不起来 ⇒ 云函数上会挂\n%s" % out[:600])
    print("· 端点模块：删掉 globalThis.URL 后仍能跑 ✅（云函数沙箱安全）")

    # ── ⑦ 打 zip（scf_bootstrap 必须 755 + LF）──────────────────
    outzip = os.path.join(BASE, "ai-proxy-with-vocab-ops.zip")
    if os.path.exists(outzip):
        os.remove(outzip)
    with zipfile.ZipFile(outzip, "w", zipfile.ZIP_DEFLATED) as z:
        for name, mode in (("index.js", 0o644), ("vocab_ops.js", 0o644),
                           ("package.json", 0o644), ("scf_bootstrap", 0o755)):
            data = open(os.path.join(OUT, name), "rb").read()
            if b"\r\n" in data:
                fail("%s 里有 CRLF 换行 ⇒ scf_bootstrap 会挂" % name)
            zi = zipfile.ZipInfo(name)
            zi.external_attr = (mode & 0xFFFF) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    print("· 打了 zip：%s（%d B，sha256 %s）" %
          (outzip, os.path.getsize(outzip), sha(open(outzip, "rb").read())[:16]))

    with zipfile.ZipFile(outzip) as z:
        for i in z.infolist():
            print("   · %-16s %7d B  mode=%o" % (i.filename, i.file_size, (i.external_attr >> 16) & 0o7777))
        for i in z.infolist():
            if re_key(i.filename):
                fail("zip 里出现了不该有的文件名：%s" % i.filename)
    print("✅ 全部断言通过")


def re_key(name):
    return any(k in name.lower() for k in (".jks", ".keystore", "key", "secret", ".env"))


if __name__ == "__main__":
    main()
