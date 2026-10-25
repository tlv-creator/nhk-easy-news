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
        and student_id = nullif(nullif(current_setting('request.headers', true), '')::json->>'x-student-id', '')
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
        and student_id = nullif(nullif(current_setting('request.headers', true), '')::json->>'x-student-id', '')
        -- 🔴 行令牌 = 提交时由浏览器随机生成、藏在 action 末尾（#tk=…）
        --    ⇒ 光知道学号**删不掉**（拿不到那一行的令牌）
        --    ⇒ 令牌先过白名单正则（只允许 16~64 位字母数字）⇒
        --       杜绝把 LIKE 通配符（% / _）塞进令牌来「匹配任意行」
        and nullif(nullif(current_setting('request.headers', true), '')::json->>'x-row-token', '') ~ '^[A-Za-z0-9]{16,64}$'
        -- 🔴🔴🔴 库里只存 **SHA-256(原始令牌)**，比对时算哈希
        --    为什么不存明文：action 列对 anon **可读**（老师看板要读）⇒ 明文令牌人人可读
        --    ⇒ 而名册也在 anon 可读的 vocab_states 里 ⇒ 任何学生都能拿到同学的学号 + 令牌
        --    ⇒ 能把某个同学的记录逐条删光 ⇒ 那就不是真隔离
        --    ⇒ 改成「库里只有摘要、原始令牌只留提交者本机」⇒ 读到 action 也反推不出原始令牌
        --    （SHA-256 浏览器端 crypto.subtle、库端 PG 内置 sha256() ⇒ 零 DDL、不动表结构）
        -- 🔴🔴 下面这行**必须加括号**：PostgreSQL 里 `||` 与 `->>` **同优先级且左结合**
        --    ⇒ 不加括号会解析成 (('%#tk=' || text::json) ->> 'x-row-token') || '%'
        --    ⇒ 报「operator does not exist: text ->> unknown」（本地 PG 实测踩到）
        and action like ('%#tk=' || encode(sha256(convert_to(nullif(nullif(current_setting('request.headers', true), '')::json->>'x-row-token', ''), 'utf8')), 'hex') || '%')
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
    # 🔴 幂等：基底可能是「协作方的原版」，也可能是「我上一轮生成后推回仓库的那份」
    #    ⇒ 两种都要能生成**同一结果**（否则就成了循环引用：输出当基底 → 基底变输出 → 漂移）
    #    ⇒ 做法：用**正则匹配整块 policy**（任一变体都能命中）⇒ 替换成同一份 NEW_POLICY
    # 🔴 幂等：基底可能是「协作方的原版」，也可能是「我上一轮生成后推回仓库的那份」
    #    ⇒ 两种都要能生成**同一结果**（否则就成了循环引用：输出当基底 → 基底变输出 → 漂移）
    if OLD_POLICY in s:
        s = s.replace(OLD_POLICY, NEW_POLICY)
    elif 'x-row-token' not in s:
        print("❌ 基底里找不到令牌 policy ⇒ 基底变了 ⇒ 先看清楚再改")
        return 1
    # 🔴 空 request.headers 会抛 JSON 解析错 ⇒ 统一包一层 nullif（任一变体都能命中 ⇒ 幂等）
    s = s.replace("current_setting('request.headers', true)::json",
                  "nullif(current_setting('request.headers', true), '')::json")
    if OLD_CMT in s:
        s = s.replace(OLD_CMT, NEW_CMT)
    elif '只靠学号是不够的' not in s:
        print("❌ 基底里既没有旧注释也没有新注释 ⇒ 基底变了")
        return 1

    # 🔴🔴 自检 ③ 改成「一眼能看懂」的汇总（协作方提的：老师看 dump 容易漏）
    #    目标：老师一眼看到「anon 每张表只剩一条收紧的」+「老师侧那几条都还在」
    old3 = """-- ③ 现在每张表的 DELETE policy（应该每张表只剩一条收紧的）
select tablename, policyname, cmd, roles::text, qual
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
 order by tablename;"""
    new3 = """-- ③ 一眼看懂：每张表各有多少条 **anon** 的 DELETE policy、名字是什么
--    👉 期望：vocab_history_logs = 1 条（p_anon_delete_logs）、vocab_online_status = 1 条（p_anon_delete_online）
--    ⚠️ 出现 **2 条以上** ⇒ 收尾没生效（旧的宽松 policy 还在 ⇒ 那是能一键清空全班的那条）
select '③ ' || tablename || ' 的 anon DELETE policy：' || count(*) || ' 条' || case when count(*) = 1 then '  ✅' else '  ⛔ 还有多余的！' end
       || '  → ' || coalesce(string_agg(policyname, ' / '), '（无）') as x
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
   and ('anon' = any(roles) or roles = ARRAY['public']::name[])
 group by tablename
 order by tablename;
--
-- ③b 老师侧的删除权限**一根都不许少**（收紧只动 anon）
--     👉 期望：vocab_history_logs = 2 条、vocab_online_status = 2 条（各一张 FOR ALL + 一张 FOR DELETE）
select '③b 老师侧(authenticated) DELETE/ALL policy：' || tablename || ' = ' || count(*) || ' 条'
       || case when count(*) >= 2 then '  ✅ 还在' else '  ⛔ 少了！' end as x
  from pg_policies
 where schemaname = 'public' and tablename in ('vocab_history_logs','vocab_online_status')
   and 'authenticated' = any(roles)
 group by tablename
 order by tablename;"""
    if old3 in s:
        s = s.replace(old3, new3)
    elif '③ 一眼看懂' not in s:
        print("❌ 基底里的自检③变了 ⇒ 先看清楚再改")
        return 1

    # 🔴 混合型 policy（roles 同时含 anon 与 authenticated）：
    #    DO 块直接 drop 会把**老师那一半**也带走 ⇒ 老师权限被削 ⇒ 而收紧只该动 anon
    #    ⇒ 改成：drop 之前，若 roles 里也有 authenticated ⇒ 先按原判据重建一条只给 authenticated 的
    for _tbl in ('vocab_history_logs', 'vocab_online_status'):
        _old_do = (
            "  for r in\n"
            "    select policyname\n"
            "      from pg_policies\n"
            "     where schemaname = 'public'\n"
            "       and tablename   = '%s'\n"
            "       and cmd         = 'DELETE'\n"
            "       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )\n"
            "  loop\n"
            "    execute format('drop policy %%I on public.%s', r.policyname);" % (_tbl, _tbl))
        _new_do = (
            "  for r in\n"
            "    select policyname, roles, qual\n"
            "      from pg_policies\n"
            "     where schemaname = 'public'\n"
            "       and tablename   = '%s'\n"
            "       and cmd         = 'DELETE'\n"
            "       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )\n"
            "  loop\n"
            "    -- 🔴 若这条 policy **同时**含 anon 之外的角色（authenticated / service_role …）\n"
            "    --    ⇒ 先按**原判据**重建一条只给那些角色的（`_teacher` 副本）\n"
            "    --    （直接 drop 会把老师/云函数那一半也带走 ⇒ 权限被削 ⇒ 而这不是收紧该做的事）\n"
            "    if coalesce(array_length(array_remove(array_remove(r.roles::text[], 'anon'), 'public'), 1), 0) > 0 then\n"
            "      execute format('drop policy if exists %%I on public.%s', r.policyname || '_teacher');\n"
            "      execute format('create policy %%I on public.%s for delete to %%s using (%%s)',\n"
            "                     r.policyname || '_teacher',\n"
            "                     array_to_string(array_remove(array_remove(r.roles::text[], 'anon'), 'public'), ', '),\n"
            "                     coalesce(r.qual, 'true'));\n"
            "    end if;\n"
            "    execute format('drop policy %%I on public.%s', r.policyname);" % (_tbl, _tbl, _tbl, _tbl))
        if _old_do in s:
            s = s.replace(_old_do, _new_do)
        elif "_teacher" not in s:
            print("❌ 基底里 %s 的 DO 块变了 ⇒ 先看清楚再改" % _tbl)
            return 1

    # 🔴 自检基线要更新成「事故后的**下限**」：
    #    · 数字只增不减（学生天天在练）⇒ 现在比基线多 = 正常，比基线少 = ⛔
    #    · 基线写得太高 ⇒ 老师会误以为数据被锁死 SQL 弄丢了（假警报）
    #    · LISTEN 现在是**补交中**（老师决定走「学生重交」）⇒ 它的下限就是 0
    old_base = "  values ('JR1',746),('CW3',582),('WL_CW',280),('XQ1',839),('LISTEN',0),('CET4',6),('JR1|u1',0),('JR1|u2',0)"
    new_base = "  values ('JR1',746),('CW3',591),('WL_CW',280),('XQ1',839),('LISTEN',0),('CET4',6),('JR1|u1',0),('JR1|u2',0)"
    if old_base in s:
        s = s.replace(old_base, new_base)
    elif new_base not in s:
        print("❌ 基底里的自检基线行既不是旧值也不是新值 ⇒ 先看清楚再改")
        return 1
    s = s.replace("-- ② 总条数：只判「没丢」，不判「等于」（基线 2453；学生练习会自然增长，看到比基线多是对的）",
                  "-- ② 总条数：只判「没丢」，不判「等于」（基线 2462；学生练习会自然增长，看到比基线多是对的）")
    s = s.replace("select '② 总数 ' || count(*) || ' / 基线 2453 → '",
                  "select '② 总数 ' || count(*) || ' / 基线 2462 → '")
    s = s.replace("|| case when count(*) >= 2453 then '✅ 没丢数据' else '⛔ 少了数据！' end as x",
                  "|| case when count(*) >= 2462 then '✅ 没丢数据（比基线多是正常的：学生在补交/在练）' else '⛔ 少了数据！' end as x")
    s = s.replace("--   ⚠️ **数字会自己涨**（学生天天在练）⇒ 判据是「不许比基线少」，不是「必须等于基线」",
                  "--   ⚠️ **数字会自己涨**（学生天天在练 + 现在正在补交 10/22-24）\n"
                  "--   ⇒ 判据是「不许比基线少」，**不是**「必须等于基线」\n"
                  "--   ⇒ 基线 = 事故之后的**下限**（只增不减）⇒ 现在比基线**多**是正常的，别慌")
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
    if n_roles < 2:
        print("❌ 基底里「只删 anon 生效的」过滤只有 %d 处（至少要 2 处：history_logs + online_status）" % n_roles)
        return 1

    # 断言：结果必须包含关键片段，且**不能**同时出现旧写法
    must = ["x-row-token", "'^[A-Za-z0-9]{16,64}$'", "encode(sha256(convert_to(nullif(nullif(current_setting('request.headers', true), '')::json->>'x-row-token', ''), 'utf8')), 'hex')",
            "① 探针行", "只靠学号是不够的", "'anon' = any(roles)", "sha256(convert_to(nullif("]
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
