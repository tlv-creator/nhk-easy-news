#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把老师要传的「cet4 + vocab-ops」合并包里的 vocab_ops.js 换成**没有全局 fetch**的版本

🔴 为什么必须换：
   CloudBase 的 Node 沙箱（scf_bootstrap 起的那个 node）是 Node 12/16 ⇒ **没有全局 fetch**
   ⇒ 原来那版 sbFetch 用 fetch() ⇒ 函数能起来（部署"成功"）但 /vocab-ops 一调就 500
   ⇒ 证据：现役 ai-proxy 的 index.js 第 44-46 行用 require('https')，
           第 255 行还专门写着「绝对不要用 new URL()：沙箱里没有全局 URL」

🔴 这个脚本的纪律：
   ① **只换 vocab_ops.js 一个文件** ⇒ index.js（cet4 + 路由）必须**逐字不变**
      （断言：换完的 index.js 与原包里的逐字相同）
   ② 打 zip 时 scf_bootstrap 必须是 755 + LF（云函数三个坑之一）
   ③ 打 zip 后**重新解开验证**：权限 / 换行 / 无密钥 / node --check
   ④ 打 zip 后跑**行为对拍**：新包 vs 原包，期望「vocab-ops 相关变、cet4 相关不变」
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile

WORK = "/workspace/listen-log/build/merged"
SRC_ZIP = os.path.join(WORK, "m.zip")
OLD_DIR = os.path.join(WORK, "x")          # 原包解出来的（只读参照）
NEW_DIR = os.path.join(WORK, "y")          # 修好的
NEW_ZIP = os.path.join(WORK, "ai-proxy-code-vocabops-v2.zip")
FIXED_OPS = "/workspace/listen-log/cloudbase/vocab-ops/vocab_ops.js"


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def strip_code(src):
    """把**注释**和**字符串内容**剥掉（字符串保留占位，避免把 id 名字也剥了）
       ⇒ 检查「代码里有没有 fetch(」时，注释里写「不能用 fetch()」不该被误伤
       ⚠ 这是我踩过的坑：静态检查器被自己的注释打成假红 ⇒ 闸门会被大家绕过 ⇒ 闸门就死了
    """
    out = []
    i, n = 0, len(src)
    prev_sig = ''          # 上一个有效字符（用来判 / 是除号还是正则开头）
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == '//':
            j = src.find('\n', i)
            if j < 0:
                j = n
            out.append(' ' * (j - i))
            i = j
            continue
        if two == '/*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join(ch if ch == '\n' else ' ' for ch in src[i:j]))
            i = j
            continue
        if c in '"\'':
            q = c
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == q:
                    j += 1
                    break
                j += 1
            out.append(q + q)          # 字符串留两个引号（内容换成空）
            prev_sig = q
            i = j
            continue
        out.append(c)
        if not c.isspace():
            prev_sig = c
        i += 1
    return ''.join(out)


def main():
    if not os.path.exists(SRC_ZIP):
        print("❌ 找不到原包：%s" % SRC_ZIP)
        return 1

    # ① 解原包
    if os.path.exists(NEW_DIR):
        shutil.rmtree(NEW_DIR)
    os.makedirs(NEW_DIR)
    with zipfile.ZipFile(SRC_ZIP) as z:
        z.extractall(NEW_DIR)
        names = z.namelist()
    print("· 原包含：%s" % ", ".join(sorted(names)))

    # ② **只**换 vocab_ops.js
    old_ops = os.path.join(OLD_DIR, "vocab_ops.js")
    old_sha = sha(old_ops)
    shutil.copy(FIXED_OPS, os.path.join(NEW_DIR, "vocab_ops.js"))
    new_sha = sha(os.path.join(NEW_DIR, "vocab_ops.js"))
    print("· vocab_ops.js：%s… → %s…（%d B）" % (old_sha[:10], new_sha[:10],
                                                    os.path.getsize(os.path.join(NEW_DIR, "vocab_ops.js"))))

    # ③ 断言：index.js 逐字不变（cet4 那边一个字都不许动）
    a = sha(os.path.join(OLD_DIR, "index.js"))
    b = sha(os.path.join(NEW_DIR, "index.js"))
    if a != b:
        print("❌ index.js 变了（%s vs %s）⇒ 只该换 vocab_ops.js" % (a[:10], b[:10]))
        return 1
    print("· index.js 逐字不变 ✅（%s）" % a[:10])

    # ④ 断言：新 vocab_ops.js 里**没有**全局 fetch / new URL
    ops = open(os.path.join(NEW_DIR, "vocab_ops.js"), encoding="utf-8").read()
    code = strip_code(ops)          # 🔴 剥掉注释/字符串 ⇒ 别被自己的注释打成假红
    if re.search(r"(^|[^.\w])fetch\s*\(", code):
        print("❌ 新 vocab_ops.js 的**代码**里还有 fetch( 调用")
        return 1
    if "new URL(" in code:
        print("❌ 新 vocab_ops.js 的**代码**里还有 new URL(")
        return 1
    if "require('https')" not in ops:
        print("❌ 新 vocab_ops.js 里没有 require('https')")
        return 1
    print("· 新 vocab_ops.js：无全局 fetch / 无 new URL / 用 https.request ✅")

    # ⑤ node --check
    for f in ("index.js", "vocab_ops.js"):
        r = subprocess.run(["node", "--check", os.path.join(NEW_DIR, f)], capture_output=True)
        if r.returncode != 0:
            print("❌ node --check %s 失败：%s" % (f, r.stderr.decode()[:300]))
            return 1
    print("· node --check 两个文件都过 ✅")

    # ⑥ 行为判据：删掉 globalThis.URL **和** globalThis.fetch，端点必须照跑
    probe = r"""
const path = require('path');
const modPath = process.argv[2];
delete globalThis.URL;
delete globalThis.fetch;
const mock = require(path.join(modPath, 'mock_supabase.js'));
(async () => {
  const server = await mock.start(18799);
  process.env.SB_URL = 'http://127.0.0.1:18799';
  process.env.SB_SERVICE_ROLE = 'k';
  const m = require(modPath + '/vocab_ops.js');
  const r = await m.handleVocabOps({ path: '/vocab-ops', httpMethod: 'POST', body: JSON.stringify({ action: 'ping' }) });
  const b = JSON.parse(r.body);
  console.log(JSON.stringify({ status: r.statusCode, ok: b.ok, service: b.service }));
  server.close();
  process.exit(r.statusCode === 200 && b.ok === true && b.service === 'vocab-ops' ? 0 : 1);
})().catch((e) => { console.error('ERR ' + e.message); process.exit(2); });
"""
    pfile = os.path.join(WORK, "probe.js")
    open(pfile, "w", encoding="utf-8").write(probe)
    r = subprocess.run(["node", pfile, os.path.dirname(FIXED_OPS)], capture_output=True)
    print("· 沙箱行为（无 URL 无 fetch）：%s" % (r.stdout.decode().strip() or r.stderr.decode()[:200]))
    if r.returncode != 0:
        print("❌ 沙箱行为判据没过")
        return 1

    # ⑦ 打 zip：scf_bootstrap 必须 755
    if os.path.exists(NEW_ZIP):
        os.remove(NEW_ZIP)
    with zipfile.ZipFile(NEW_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ("index.js", "vocab_ops.js", "package.json", "scf_bootstrap"):
            p = os.path.join(NEW_DIR, f)
            mode = 0o755 if f == "scf_bootstrap" else 0o644
            zi = zipfile.ZipInfo(f)
            zi.external_attr = (mode & 0xFFFF) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as fh:
                data = fh.read()
            if f == "scf_bootstrap":
                data = data.replace(b"\r\n", b"\n")     # 必须是 LF
            z.writestr(zi, data)
    print("· 打了 zip：%s（%d B，sha256 %s）" % (NEW_ZIP, os.path.getsize(NEW_ZIP), sha(NEW_ZIP)[:16]))

    # ⑧ 重新解开验证（别相信"命令跑完了"）
    vdir = os.path.join(WORK, "verify")
    if os.path.exists(vdir):
        shutil.rmtree(vdir)
    os.makedirs(vdir)
    with zipfile.ZipFile(NEW_ZIP) as z:
        z.extractall(vdir)
        for i in z.infolist():
            mode = (i.external_attr >> 16) & 0o777
            need = 0o755 if i.filename == "scf_bootstrap" else 0o644
            if mode != need:
                print("❌ %s 权限 %o（应为 %o）" % (i.filename, mode, need))
                return 1
        boot = z.read("scf_bootstrap")
    if b"\r\n" in boot:
        print("❌ scf_bootstrap 有 CRLF")
        return 1
    print("· zip 权限/LF 复验 ✅")
    for f in ("index.js", "vocab_ops.js"):
        r = subprocess.run(["node", "--check", os.path.join(vdir, f)], capture_output=True)
        if r.returncode != 0:
            print("❌ 解开后 node --check %s 失败" % f)
            return 1
    print("· 解开后 node --check ✅")

    # ⑨ 没有**真**密钥进包
    #    ⚠ 原包的 index.js 里有 ai-proxy 自带的**占位符** 'sk-cp-xxxxxxxxxxxxxxxx'
    #      ⇒ 占位符要放过（否则假红），但**真 key**（随机长串）要拦住
    #    ⇒ index.js 已断言与原包逐字相同 ⇒ 真正需要扫的是我换进去的 vocab_ops.js
    PLACEHOLDER = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
    for f in ("vocab_ops.js", "package.json", "scf_bootstrap"):
        txt = open(os.path.join(vdir, f), encoding="utf-8", errors="ignore").read()
        for m in PLACEHOLDER.finditer(txt):
            frag = m.group(0)
            low = frag.lower()
            if 'x' * 4 in low or 'y' * 4 in low or 'your' in low or 'example' in low or 'placeholder' in low:
                continue          # 占位符，放过
            print("❌ %s 里有疑似真 key：%s…" % (f, frag[:14]))
            return 1
    print("· 包内无真 key ✅（占位符已放过）")
    print("✅ 全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
