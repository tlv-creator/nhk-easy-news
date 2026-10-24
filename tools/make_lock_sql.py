#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「协作方的基底」重新生成收尾 SQL（可复现 ⇒ 不会被手改/mutation 带偏）

基底 = 共享仓库里的 tools/listen_lock_anon_delete.sql（协作方改过自检 ①② 的那版）
我的增量 = 「行令牌」机制（学生只能删自己那一条 ⇒ 学号 + 令牌两个都要对上）
"""
import os
import re
import sys

BASE = "/workspace/listen-log/build/repo_scan/listen_lock_now.sql"
OUT = "/workspace/listen-log/deploy/sql/05_lock_anon_delete.sql"

OLD_POLICY = """create policy p_anon_delete_logs
    on public.vocab_history_logs
    for delete to anon
    using (
      student_id = '__rls_probe__'
      or (
        class_key = 'LISTEN'
        and student_id = nullif(current_setting('request.headers', true)::json->>'x-student-id', '')
      )
    );"""

NEW_POLICY = """-- 🔴 显式 drop 同名 policy：**别让「重名」把整段 SQL 打断**
--    （DO 块漏掉同名那条时，create 会报 policy already exists ⇒ 老师端整段回滚，白跑一趟）
drop policy if exists p_anon_delete_logs on public.vocab_history_logs;
create policy p_anon_delete_logs
    on public.vocab_history_logs
    for delete to anon
    using (
      -- ① 探针行：验收脚本的清理口子（任何班都行）
      student_id = '__rls_probe__'
      or (
        -- ② 学生删**自己那一条**：学号 + **行令牌** 两个都要对得上
        class_key = 'LISTEN'
        and student_id = nullif(current_setting('request.headers', true)::json->>'x-student-id', '')
        -- 🔴 行令牌 = 提交时由浏览器随机生成、藏在 action 末尾（#tk=…）
        --    ⇒ 光知道学号**删不掉**（拿不到那一行的令牌）
        --    ⇒ 令牌先过白名单正则（只允许 16~64 位字母数字）⇒
        --       杜绝把 LIKE 通配符（% / _）塞进令牌来「匹配任意行」
        and nullif((current_setting('request.headers', true)::json->>'x-row-token'), '') ~ '^[A-Za-z0-9]{16,64}$'
        -- 🔴🔴 下面这行**必须加括号**：PostgreSQL 里 `||` 与 `->>` **同优先级且左结合**
        --    ⇒ 不加括号会解析成 (('%#tk=' || text::json) ->> 'x-row-token') || '%'
        --    ⇒ 报「operator does not exist: text ->> unknown」（本地 PG 实测踩到）
        and action like ('%#tk=' || (current_setting('request.headers', true)::json->>'x-row-token') || '%')
      )
    );"""

OLD_CMT = """--     ② class_key = 'LISTEN' 且 student_id = 请求头 x-student-id
--        ⇒ **学生只能删自己那一条**；老师的「删一条 / 清空全部」不走这条路
--
-- 【⚠ 为什么学生的删除要带 x-student-id 头】
--   PostgREST 会把请求头放进 current_setting('request.headers')（键名一律小写）
--   ⇒ RLS 里能读到「这次请求自称是谁」。
--   ⚠ 这个头是**自称**的，能被伪造 ⇒ 学生仍可能删掉同班同学的**单条**记录
--     （要逐条删、还要知道学号和行 id；比「一键清空全班」难得多，但不是零）
--     ⇒ 真正的隔离要靠「每人一个口令」（157 个学生要发口令，代价大），先记着这条路。
--     ⇒ 这一版比现在强的地方是：**一键清空全班彻底没了**。"""

NEW_CMT = """--     ② class_key = 'LISTEN' 且 student_id = 请求头 x-student-id
--        **且** action 里有该行自己的「行令牌」（请求头 x-row-token）
--        ⇒ **学生只能删自己那一条**；老师的「删一条 / 清空全部」不走这条路
--
-- 【⚠ 为什么学生的删除要带两个头】
--   PostgREST 会把请求头放进 current_setting('request.headers')（键名一律小写）
--   ⇒ RLS 里能读到「这次请求自称是谁」。
--   🔴 **但只靠学号是不够的**：那个头是**自报**的，能被伪造
--      ⇒ 只靠学号的话，**任何人知道同学学号就能删掉那一条**（本地 PG 实测过：确实能删）
--   ⇒ 所以提交时浏览器给每一行生成一枚**随机令牌**，写在 action 末尾（#tk=…）
--      删除时必须**同时**出示「学号 + 这一行的令牌」
--      ⇒ 别人拿不到令牌 ⇒ 删不掉
--   ⇒ 令牌在列表 / 明细表 / CSV 里都被**剥掉**（前端 rowToLog 统一处理）⇒ 师生都看不到
--   ⚠ 残留：能删自己**刚刚**提交的行（令牌在自己的浏览器里）—— 这本来就是这个功能的目的
--     ⇒ 真正要防的是「删别人的」⇒ 令牌已经挡住了
--   ⇒ 这一版相对之前强在：**一键清空全班没了** + **逐条删别人的也没了**。"""



# ③ 🔴 把「DO 块只删对 anon 生效的」这层保险**补回去**
#    （协作方那版是从我早先的版本改的 ⇒ 这层丢了 ⇒ 老师的 FOR DELETE policy 会被误删）
OLD_DROP = """       and cmd         = 'DELETE'
  loop"""
NEW_DROP = """       and cmd         = 'DELETE'
       -- 🔴 只删**对 anon 生效**的：roles 含 anon 的，或 roles={public} 的
       --    ⇒ 老师侧（roles={authenticated}，不管 cmd 是 ALL 还是 DELETE）一根都不动
       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )
  loop"""

def main():
    if not os.path.exists(BASE):
        print("❌ 找不到基底：%s（先从共享仓库拉一份）" % BASE)
        return 1
    s = open(BASE, encoding="utf-8").read()
    if OLD_POLICY not in s:
        print("❌ 基底里找不到那段 policy ⇒ 基底变了 ⇒ 先看清楚再改")
        return 1
    if OLD_CMT not in s:
        print("❌ 基底里找不到那段注释 ⇒ 基底变了")
        return 1
    s = s.replace(OLD_POLICY, NEW_POLICY).replace(OLD_CMT, NEW_CMT)
    # online_status 那张表也一样：显式 drop 同名 policy（否则重名报错 ⇒ 整段回滚）
    old_online = "create policy p_anon_delete_online"
    if old_online not in s:
        print("❌ 基底里找不到 p_anon_delete_online")
        return 1
    s = s.replace(old_online,
                  "-- 🔴 同上：显式 drop 同名 policy\n"
                  "drop policy if exists p_anon_delete_online on public.vocab_online_status;\n"
                  + old_online, 1)
    # 🔴 断言基底里**带着**「只删对 anon 生效的」这层保险
    #    （协作方那版是从我早先的版本改的 ⇒ 这层一旦丢了，老师的 FOR DELETE policy 会被误删）
    n_roles = s.count("'anon' = any(roles)")
    if n_roles != 2:
        print("❌ 基底里「只删 anon 生效的」过滤只有 %d 处（期望 2 处）⇒ 基底变了，先看清楚再改" % n_roles)
        return 1

    # 断言：结果必须包含关键片段，且**不能**同时出现旧写法
    must = ["x-row-token", "'^[A-Za-z0-9]{16,64}$'", "action like ('%#tk=' || (current_setting",
            "① 探针行", "只靠学号是不够的", "'anon' = any(roles)"]
    for m in must:
        if m not in s:
            print("❌ 生成结果里缺：%s" % m)
            return 1
    if re.search(r"current_setting\('request\.headers', true\)\s*::\s*json->>'x-row-token'\)\s*\)?\s*\n\s*and action like\('%#tk=' \|\| current_setting", s):
        print("❌ 生成结果里还有没加括号的写法")
        return 1
    open(OUT, "w", encoding="utf-8").write(s)
    print("✅ 重新生成：%s（%d B）" % (OUT, len(s.encode())))
    print("   关键片段齐了：%s" % "、".join(must[:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
