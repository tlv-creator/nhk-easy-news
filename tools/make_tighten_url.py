#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「第一步收紧 SQL」的一键链接（老师点一下就能跑）

🔴 为什么要有这个脚本（2026-10-25 定规）：
   之前那条 tighten 链接是**手工拼**出来的 ⇒ 只有一个人能复现
   ⇒ 别人拿到共享仓库没法验证「老师手上那条到底对不对」
   ⇒ ⇒ 规矩：**「脚本在仓库里」不等于「链在仓库里」**
      原料（完整版 SQL）+ 生成器（短版 + 链接）+ 验证台（本地 PG）必须都在仓库里。

🔴 本脚本做四件事，缺一不可：
   ① 从**完整版** SQL 机械生成短版（去注释）⇒ 短版不是「随手删的」
   ② 断言：policy 一条不少 / begin+commit 都在 / 关键判据都在
   ③ 在**本地 PostgreSQL** 上真跑短版（verify_tighten.sh，9 条断言）
   ④ 拼 URL 并**实测 HTTP**（大段内容进 URL 必须先 curl 验长度，否则 414）

🔴 路径必须可移植：环境变量 → 相对脚本自身位置的候选 → 绝对候选
   （别人从共享仓库拿到就能跑；写死绝对路径 = 别人一跑就 FileNotFoundError）
   TIGHTEN_SQL       完整版 SQL 路径
   TIGHTEN_SHORT     冻结的短版路径（可选；存在则要求「生成的 == 冻结的」，不一致就响亮失败）
   TIGHTEN_HARNESS   本地 PG 验证脚本路径
   TIGHTEN_UPDATE    设成 1 才允许刷新冻结的短版文件
"""
import os
import re
import subprocess
import sys
import urllib.parse

PROJECT = "lszwxcmvcvbraqtrtgon"
HERE = os.path.dirname(os.path.abspath(__file__))


def _find(env_name, rel_candidates, abs_candidates=()):
    """按「环境变量 → 相对脚本的候选路径 → 绝对候选」找文件，返回第一个存在的路径。"""
    env = os.environ.get(env_name)
    if env and os.path.exists(env):
        return os.path.abspath(env)
    for rel in rel_candidates:
        for base in (HERE, os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))):
            p = os.path.normpath(os.path.join(base, rel))
            if os.path.exists(p):
                return p
    for p in abs_candidates:
        if os.path.exists(p):
            return p
    return None


FULL = _find(
    "TIGHTEN_SQL",
    ["tighten_rls_delete.sql",
     os.path.join("tools", "tighten_rls_delete.sql")],
    ["/workspace/listen-log/build/protected_sites_tighten.sql"],
)
FROZEN = _find(
    "TIGHTEN_SHORT",
    ["tighten_rls_delete_short.sql",
     os.path.join("tools", "tighten_rls_delete_short.sql")],
    ["/workspace/work/tighten_short.sql"],
)
HARNESS = _find(
    "TIGHTEN_HARNESS",
    ["verify_tighten.sh",
     os.path.join("tools", "verify_tighten.sh")],
    ["/workspace/work/verify_tighten.sh"],
)
OUT_SHORT = os.path.join(HERE, "tighten_rls_delete_short.sql")
OUT_URL = os.path.join(HERE, "tighten_url.txt")


def strip_sql_comments(s):
    """把 SQL 里的注释替换成等长空格（保持行号/列位置）——与 make_lock_url.py 同款口径。"""
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
    if not FULL:
        print("❌ 找不到完整版 tighten SQL（设 TIGHTEN_SQL 指向它）")
        return 1
    print("· 完整版 SQL = %s" % FULL)
    full = open(FULL, encoding='utf-8').read()
    short = strip_sql_comments(full)
    short = re.sub(r'\n{3,}', '\n\n', short).strip() + '\n'

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

    # ③ 断言：关键判据都在（分片检查，不写死整条表达式 ⇒ SQL 加固后不会假红）
    must = ["'anon' = any(roles)", "for delete to anon",
            "student_id = '__rls_probe__'", "class_key = 'LISTEN'",
            "create policy p_anon_delete_logs",
            "create policy p_anon_delete_online",
            "delete from public.vocab_history_logs where student_id = '__rls_probe__'",
            "delete from public.vocab_states where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1')"]
    for m in must:
        if m not in short:
            print("❌ 短版缺：%s" % m)
            return 1
    print("· 关键判据都在 ✅")

    # ④ 冻结的短版必须 == 生成的短版（防止有人改了完整版却没重生成链接）
    if FROZEN:
        frozen = open(FROZEN, encoding='utf-8').read()
        if frozen != short:
            if os.environ.get("TIGHTEN_UPDATE") == "1":
                print("⚠️ 冻结的短版与生成结果不一致 ⇒ 按 TIGHTEN_UPDATE=1 刷新")
                try:
                    open(FROZEN, 'w', encoding='utf-8').write(short)
                except OSError as e:
                    print("❌ 写冻结文件失败：%s" % e)
                    return 1
            else:
                print("❌ 冻结的短版（%s）与生成结果不一致 ⇒ 完整版改了但短版没重生成" % FROZEN)
                print("   确认无误后用 TIGHTEN_UPDATE=1 重跑本脚本刷新冻结文件")
                return 1
        else:
            print("· 冻结的短版与生成结果逐字一致 ✅")

    # ⑤ 在本地 PG 上真跑短版（真实库形状 + 9 条断言）
    if not HARNESS:
        print("⚠️ 找不到本地 PG 验证脚本 ⇒ **本次没做本地回归**（只做了 HTTP 实测）")
        print("   想补：设 TIGHTEN_HARNESS 指向 verify_tighten.sh")
    else:
        print("· 本地回归脚本 = %s" % HARNESS)
        r = subprocess.run(["bash", HARNESS, short], capture_output=True, text=True,
                           timeout=900, env=dict(os.environ))
        tail = [l for l in (r.stdout + r.stderr).split('\n')
                if '结果' in l or '失败' in l or '❌' in l]
        for l in tail[:8]:
            print("   %s" % l)
        if r.returncode == 9:
            print("❌ 本地回归**环境没搭起来** ⇒ 本次验证无效（不是判据红）")
            return 1
        if r.returncode != 0:
            print("❌ 短版没通过本地回归")
            return 1
        print("· 短版在本地 PG 上跑：通过 9 条，失败 0 条 ✅")

    # ⑥ 拼 URL 并**实测长度 + HTTP**
    url = ("https://supabase.com/dashboard/project/%s/sql/new?query=%s"
           % (PROJECT, urllib.parse.quote(short, safe='')))
    print("· URL 长度 = %d 字符" % len(url))
    if len(url) > 20000:
        print("❌ URL 太长（>20000）⇒ 转发层可能 414")
        return 1
    try:
        c = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
                           capture_output=True, text=True, timeout=120)
        code = c.stdout.strip()
    except Exception as e:  # noqa: BLE001
        code = "curl失败(%s)" % e
    print("· 实测 HTTP = %s" % code)
    if code != '200':
        print("❌ 一键链接不是 200（多半是 414）")
        return 1
    try:
        if not os.path.exists(OUT_SHORT) or open(OUT_SHORT, encoding='utf-8').read() != short:
            open(OUT_SHORT, 'w', encoding='utf-8').write(short)
        open(OUT_URL, 'w', encoding='utf-8').write(url)
        print("✅ 链接可用，已存 %s" % OUT_URL)
    except OSError as e:
        print("⚠️ 写文件失败（%s）⇒ 链接仍打印在下面" % e)
    print("   短版 %d B（完整版 %d B）" % (len(short.encode()), len(full.encode())))
    # 🔴 最后一行**裸打链接** ⇒ 调用方可以直接 `tail -1` 取走，不用解析日志
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
