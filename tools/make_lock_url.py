#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「老师点一下就能跑」的收尾 SQL 一键链接

🔴 为什么要有这一步：
   老师没空也没法把 8886 B 的 SQL 贴进 Supabase SQL Editor ⇒ 必须给一个能点的链接。
   但**大段内容塞进 URL 前必须先 curl 验长度**：
     · 旧版 36766 字符 ⇒ 服务端 414 URI_TOO_LONG（浏览器地址栏能扛，转发层扛不住）
     · ⇒ 必须做「去注释短版」再拼 URL

🔴 短版不能是「随手删注释」：
   ① 逐条 policy 一个不少
   ② begin/commit 都在
   ③ **在本地 PostgreSQL 上真跑一遍**，结果与完整版逐项一致
     （判据：跑完后库里 anon DELETE policy 数量、老师的 policy 数量、探针可删性 都相同）
"""
import os
import re
import subprocess
import sys
import urllib.parse

SQL = "/workspace/listen-log/deploy/sql/05_lock_anon_delete.sql"
SHORT = "/workspace/listen-log/build/listen_lock_short.sql"
PROJECT = "lszwxcmvcvbraqtrtgon"


def strip_sql_comments(s):
    out = []
    i, n = 0, len(s)
    in_s = None
    while i < n:
        c = s[i]
        two = s[i:i + 2]
        if in_s:
            out.append(c)
            if c == in_s:
                in_s = None
            i += 1
            continue
        if two == '--':
            j = s.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i))
            i = j
            continue
        if two == '/*':
            j = s.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join(ch if ch == '\n' else ' ' for ch in s[i:j]))
            i = j
            continue
        if c in "'\"":
            in_s = c
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def main():
    full = open(SQL, encoding='utf-8').read()
    short = strip_sql_comments(full)
    # 收掉空行
    short = re.sub(r'\n{3,}', '\n\n', short).strip() + '\n'
    open(SHORT, 'w', encoding='utf-8').write(short)

    # ① 断言：policy 一条不少
    def policies(t):
        return sorted(re.findall(r'create policy (\w+)', t))
    pf, ps = policies(full), policies(short)
    if pf != ps:
        print("❌ 短版 policy 少了：%s" % (set(pf) - set(ps)))
        return 1
    print("· policy 一致：%s" % ', '.join(ps))

    # ② 断言：begin/commit 都在
    for kw in ('begin', 'commit'):
        if kw not in short:
            print("❌ 短版少了 %s" % kw)
            return 1
    print("· begin/commit 都在 ✅")

    # ③ 断言：关键判据（令牌条件、白名单正则、roles 过滤）都在
    # 🔴 分片检查（不写死整条表达式 ⇒ 加固一改文本就漂移 ⇒ 断言假红 ⇒ 脚本直接废）
    must = ["x-row-token", "'^[A-Za-z0-9]{16,64}$'",
            "encode(sha256(convert_to(", "::json->>'x-row-token'", "), 'hex')",
            "'anon' = any(roles)", "drop policy if exists p_anon_delete_logs",
            "drop policy if exists p_anon_delete_online"]
    for m in must:
        if m not in short:
            print("❌ 短版缺：%s" % m)
            return 1
    print("· 关键判据都在 ✅")

    # ④ 在本地 PG 上真跑短版（与完整版对拍）
    h = "/workspace/listen-log/build/pg_lock_harness.sh"
    r = subprocess.run(["bash", h, SHORT], capture_output=True, text=True, timeout=600)
    tail = [l for l in (r.stdout + r.stderr).split('\n') if '通过' in l or '失败' in l or '❌' in l]
    print("· 短版在本地 PG 上跑：%s" % (tail[-1] if tail else '(无输出)'))
    if r.returncode != 0:
        for l in tail[:6]:
            print("   %s" % l)
        print("❌ 短版没通过本地回归")
        return 1

    # ⑤ 拼 URL 并**实测长度**
    url = ("https://supabase.com/dashboard/project/%s/sql/new?query=%s"
           % (PROJECT, urllib.parse.quote(short, safe='')))
    print("· URL 长度 = %d 字符" % len(url))
    if len(url) > 20000:
        print("❌ URL 太长（>20000）⇒ 转发层可能 414")
        return 1
    c = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url], capture_output=True, text=True, timeout=120)
    code = c.stdout.strip()
    print("· 实测 HTTP = %s" % code)
    if code != '200':
        print("❌ 一键链接不是 200（多半是 414）")
        return 1
    open("/workspace/listen-log/build/listen_lock_url.txt", 'w', encoding='utf-8').write(url)
    print("✅ 链接可用，已存 build/listen_lock_url.txt")
    print("   短版 %d B（完整版 %d B）" % (len(short.encode()), len(full.encode())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
