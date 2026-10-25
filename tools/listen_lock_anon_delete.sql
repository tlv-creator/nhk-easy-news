-- =====================================================================
-- 收尾 SQL：把 anon 的「整班删除」彻底关掉
-- 前提：教师侧特权端点（CloudBase /vocab-ops）已上线并验证过
-- Supabase SQL Editor → 新建 → 粘贴 → Run
-- =====================================================================
--
-- 【为什么是这一步】
--   之前 anon 的 DELETE policy 是：
--       using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
--   ⇒ 任何拿到**公开 anon key** 的人，只要 class_key 长得像班级名，
--     就能删掉那一班的**全部**记录。
--   ⇒ 已实际发生：听力站「教师清空全部」第一次真正生效 ⇒ 34 条真记录没了
--     （之前 RLS 挡着，它是「假成功」；权限一放开，同一个按钮第一次真删）。
--   ⇒ 中间那一步（tools/tighten_rls_delete.sql）先收成「只允许 LISTEN」，
--     因为老师的「删一条 / 清空全部」当时还走 anon REST。
--   ⇒ 现在老师侧已经搬到**服务端特权端点**（service_role + 服务端校验口令），
--     这一版把 LISTEN 这个最后的口子也收掉。
--
-- 【这一版收成什么样】
--   anon 能删的行只剩两类：
--     ① student_id = '__rls_probe__'
--        （验收脚本的探针行，任何班都行 —— 清理探针必须有这个口子）
--     ② class_key = 'LISTEN' 且 student_id = 请求头 x-student-id
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
--   ⇒ 这一版相对之前强在：**一键清空全班没了** + **逐条删别人的也没了**。
--
-- 【不动 authenticated】
--   老师的 p_teach_all_* 是 `FOR ALL TO authenticated`
--   ⇒ 在 pg_policies 里 cmd = 'ALL'（不是 'DELETE'）⇒ 下面只删 cmd='DELETE' 的，
--     **不会碰到它们**。我们自己的 policy 也只写 `to anon`。
-- =====================================================================

begin;

-- ── 1) vocab_history_logs：把这张表上**所有 anon 能用的 DELETE policy** 删光，只留一条收紧的
--    （PERMISSIVE 之间是 OR ⇒ 漏删一条宽松的 = 白收紧）
--    🔴 **只删「对 anon 生效」的**：
--       · roles 里含 anon 的（`to anon`）
--       · 或 roles = {public} 的（没写 to 就是给所有角色）
--    ⇒ 老师侧那条（`FOR ALL TO authenticated` ⇒ roles={authenticated}）**一根都不动**
--    ⇒ 即使老师侧哪天变成 `for delete to authenticated`（cmd='DELETE' 但 roles 不含 anon）
--      也**不会被这个 DO 块误删** ← 这一层保险是同事提醒后补的
do $$
declare r record;
begin
  for r in
    select policyname, roles, qual
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_history_logs'
       and cmd         = 'DELETE'
       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )
  loop
    -- 🔴 若这条 policy **同时**含 anon 之外的角色（authenticated / service_role …）
    --    ⇒ 先按**原判据**重建一条只给那些角色的（`_teacher` 副本）
    --    （直接 drop 会把老师/云函数那一半也带走 ⇒ 权限被削 ⇒ 而这不是收紧该做的事）
    if coalesce(array_length(array_remove(array_remove(r.roles::text[], 'anon'), 'public'), 1), 0) > 0 then
      execute format('drop policy if exists %I on public.vocab_history_logs', r.policyname || '_teacher');
      execute format('create policy %I on public.vocab_history_logs for delete to %s using (%s)',
                     r.policyname || '_teacher',
                     array_to_string(array_remove(array_remove(r.roles::text[], 'anon'), 'public'), ', '),
                     coalesce(r.qual, 'true'));
    end if;
    execute format('drop policy %I on public.vocab_history_logs', r.policyname);
  end loop;
end $$;

-- 🔴 显式 drop 同名 policy：**别让「重名」把整段 SQL 打断**
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
        and nullif((nullif(current_setting('request.headers', true), '')::json->>'x-row-token'), '') ~ '^[A-Za-z0-9]{16,64}$'
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
    );

-- ── 2) vocab_online_status：只留探针行
--    （听力/四级站都不删这张表；词汇站也只 insert/upsert/select ⇒ 不影响任何人）
--    🔴 同样**只删对 anon 生效的**（理由同上）
do $$
declare r record;
begin
  for r in
    select policyname, roles, qual
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_online_status'
       and cmd         = 'DELETE'
       and ( 'anon' = any(roles) or roles = ARRAY['public']::name[] )
  loop
    -- 🔴 若这条 policy **同时**含 anon 之外的角色（authenticated / service_role …）
    --    ⇒ 先按**原判据**重建一条只给那些角色的（`_teacher` 副本）
    --    （直接 drop 会把老师/云函数那一半也带走 ⇒ 权限被削 ⇒ 而这不是收紧该做的事）
    if coalesce(array_length(array_remove(array_remove(r.roles::text[], 'anon'), 'public'), 1), 0) > 0 then
      execute format('drop policy if exists %I on public.vocab_online_status', r.policyname || '_teacher');
      execute format('create policy %I on public.vocab_online_status for delete to %s using (%s)',
                     r.policyname || '_teacher',
                     array_to_string(array_remove(array_remove(r.roles::text[], 'anon'), 'public'), ', '),
                     coalesce(r.qual, 'true'));
    end if;
    execute format('drop policy %I on public.vocab_online_status', r.policyname);
  end loop;
end $$;

-- 🔴 同上：显式 drop 同名 policy
drop policy if exists p_anon_delete_online on public.vocab_online_status;
-- 🔴 同上：显式 drop 同名 policy
drop policy if exists p_anon_delete_online on public.vocab_online_status;
create policy p_anon_delete_online
    on public.vocab_online_status
    for delete to anon
    using (student_id = '__rls_probe__');

-- ── 3) 清掉这几轮排查留在 vocab_states 的探针行（真数据不动）
delete from public.vocab_states
 where class_key in ('ZZPROBE', '__rls_probe__', 'PROBE_V1');

-- ── 4) 清掉历史探针行（vocab_history_logs / vocab_online_status）
delete from public.vocab_history_logs where student_id = '__rls_probe__';
delete from public.vocab_online_status where student_id = '__rls_probe__';

commit;

-- =====================================================================
-- 自检：跑完看这三条（数字对不上就别往下走，截图给我）
-- =====================================================================
-- ① 逐班条数 vs 基线（基线 = 2026-10-24 快照）
--   ⚠️ **数字会自己涨**（学生天天在练 + 现在正在补交 10/22-24）
--   ⇒ 判据是「不许比基线少」，**不是**「必须等于基线」
--   ⇒ 基线 = 事故之后的**下限**（只增不减）⇒ 现在比基线**多**是正常的，别慌
--   （写死"总数必须等于 N"是错判据：学生一练习就变少 ⇒ 老师会误以为数据丢了）
with base(class_key, n) as (
  values ('JR1',746),('CW3',591),('WL_CW',280),('XQ1',839),('LISTEN',0),('CET4',6),('JR1|u1',0),('JR1|u2',0)
), cur as (
  select class_key, count(*) n from public.vocab_history_logs group by class_key
)
select '① ' || b.class_key || ' 现在=' || coalesce(c.n,0) || ' 基线=' || b.n
       || case when coalesce(c.n,0) < b.n then '  ⛔ 少了！' else '  ✅' end as x
  from base b left join cur c on c.class_key = b.class_key
 order by b.class_key;
--
-- ② 总条数：只判「没丢」，不判「等于」（基线 2462；学生练习会自然增长，看到比基线多是对的）
select '② 总数 ' || count(*) || ' / 基线 2462 → '
       || case when count(*) >= 2462 then '✅ 没丢数据（比基线多是正常的：学生在补交/在练）' else '⛔ 少了数据！' end as x
  from public.vocab_history_logs;
--
-- ③ 一眼看懂：每张表各有多少条 **anon** 的 DELETE policy、名字是什么
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
 order by tablename;
--
-- ④ 老师的 p_teach_all_* 还在吗（应该都在，cmd='ALL'）
select tablename, policyname, cmd, roles::text
  from pg_policies
 where schemaname = 'public' and policyname like 'p_teach_all%'
 order by tablename;
-- =====================================================================
