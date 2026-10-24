-- ⚠️ 本文件 = tools/fix_history_logs_rls.sql **v2（模式匹配版）** 的副本（内容完全一致）
--    存在的唯一理由：防止老师点错文件跑了旧版 v1（v1 的 DELETE 只放行探针 ⇒ 删不掉真实数据）
--    自检：bash tools/pg_rls_harness.sh tools/fix_history_logs_rls.sql   （本地 PostgreSQL 真跑一遍）
--    反向：bash tools/pg_rls_harness.sh tools/rls_v1_snapshot.sql        （v1 ⇒ 删不掉/upsert 失败）
--    反向：bash tools/pg_rls_harness.sh tools/rls_v2_enum_bug.sql       （精确枚举版 ⇒ |uN 后缀被挡）
-- =====================================================================
-- =====================================================================
-- RLS 修复 v2（三站一次 Run）· 2026-10-24
-- Supabase 控制台 → SQL Editor → 新建 → 粘贴 → Run
-- 幂等（可重复执行）· 不改表结构 · 不动任何真实数据 · 不含凭据
-- =====================================================================
--
-- 【实测基线（2026-10-24 我用 anon key 直连 Supabase 实测，不是转述）】
--   表                    读                          写
--   vocab_history_logs    0 行（全表/JR1/LISTEN/CET4 都一样）   401 42501
--   vocab_submissions     0 行                                    401 42501
--   vocab_online_status   0 行                                    401 42501
--   vocab_states          ✅ 206 / 20 行                          ✅ 通
--   ⇒ **三个站现在全哑**：词汇站教师端导不出日志、四级站交不上去、听力站交不上去
--
-- 【三个站真实的需求（全部从线上 live 代码读出来的，不是推测）】
--   站                表                      读                    写                    删                      class_key
--   词汇站 Unit1+2+3  vocab_history_logs      anon .eq(class_key)  anon insert          —                      JR1/CW3/WL_CW/XQ1
--                     vocab_submissions       anon select(*)       anon **upsert**      —                      （表里没有此列）
--                     vocab_online_status     anon select          anon **upsert**      —                      有此列
--                     vocab_states            anon select          anon upsert          —                      名册 / 班
--   听力记录站        vocab_history_logs      anon .eq(LISTEN)     anon insert          **3 条 DELETE**        LISTEN
--                     vocab_states            anon select(roster)  —                    —                      __roster_v1
--   四级精读日报      vocab_history_logs      anon .eq(CET4)       anon insert          —                      CET4
--                     vocab_states            anon select          anon upsert          —                      __cet4_*
--
-- 【v1（fix_history_logs_rls.sql 旧版）的三个洞 —— 为什么不能直接跑旧版】
--   ① **DELETE 只放行 class_key='__rls_probe__'** ⇒ 听力站三条删除路径全部被挡
--      ⇒ PostgREST 删不掉也返回 200/204 + 空数组 ⇒ **老师点「删掉了」，数据还在**（最阴的形态）
--   ② **select using (true)** ⇒ anon 能读到词汇站那 2418 行（含练习内容）
--      ⇒ 但词汇站教师端**就是用 anon 读日志的** ⇒ 「让那个页面能用」与「那 2418 行对 anon 关闭」
--        **互斥**（除非把那个页面挪到后端）⇒ v2 拆成两条 policy，见下面的「开关」
--   ③ 🔴 **没有 UPDATE policy** ⇒ `upsert` = INSERT … ON CONFLICT DO UPDATE，PostgREST 走 upsert
--      必须同时有 insert + update policy ⇒ 词汇站「标记已交卷 / 写在线状态」会继续报 42501
--      （v1 没写 update，v2 补上了）
--
-- 【🔴 class_key 用「模式」而不是「枚举」——这是踩过的坑】
--   词汇站**第一/二课**写的不是 'JR1'，是 **'JR1|u1' / 'WL_CW|u2'** 这种带后缀的
--   （源码：function stateKeyFor(unit){ if (unit===3) return currentClassKey;
--            return currentClassKey + '|u' + unit; }  ← 第三课才用原始 key）
--   ⇒ 我第一版用 `class_key in ('JR1','CW3','WL_CW','XQ1','LISTEN','CET4')` **精确枚举**
--      ⇒ **第一/二课的学生会全部提交不上去**（而且 PostgREST 不报错 ⇒ 静默失效）
--   ⇒ 而且老师以后加一个班级，枚举型白名单**当场又坏一次**
--   ⇒ 所以统一用一条模式：**大写字母开头的 token，后面可带 |uN 后缀**
--      class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$'
--      · JR1 / CW3 / WL_CW / XQ1 / WL1 / CWZT1 / LISTEN / CET4        ✅ 放行
--      · JR1|u1 / WL_CW|u2                                        ✅ 放行
--      · __rls_probe__ / __rls_neg_probe__（下划线开头）              ❌ 拒绝
--      · 空字符串 / 乱码                                            ❌ 拒绝
--      ⇒ **新班级自动生效**（不用回来改 SQL），同时**探针与垃圾行照旧被挡**
--
-- 【本文件的原则】
--   · **只加 `to anon` 的 policy** —— 老师侧（authenticated）已经跑好的那套一个字都不动
--   · **按 class_key 收窄**（vocab_submissions 除外：那张表没有 class_key 列）
--   · **vocab_states 现在就是通的 ⇒ 一行都不改**（不碰已经好的东西）
--
-- =====================================================================
-- 【开关：要不要让 anon 读到「词汇站那 2418 行」】
--   下面有两条 select policy：
--     p_anon_select_logs       → class_key ~ '^(LISTEN|CET4|CET4TEST)$'              ← 听力站 + 四级站
--     p_anon_select_logs_vocab → class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$'  ← 词汇站教师端 CSV 导出用
--   · 默认**两条都建** ⇒ 谁都不坏（词汇站那个导出页面照样能用）
--   · 如果老师平时用 VocabLog（CloudBase 那版）看数据、不用词汇站页面里那个「所有班级导出」
--     ⇒ 想把词汇站那 2418 行对 anon 关掉，执行这一句即可（**只关读取，不影响学生写入**）：
--
--       drop policy if exists p_anon_select_logs_vocab on public.vocab_history_logs;
--
--   ⇒ 关掉后：词汇站学生照常交记录（insert 不变），只有那个页面的导出变空。
-- =====================================================================

begin;

-- ─────────────────────────────────────────────────────────────
-- 0) 清掉这 3 张表上的 RESTRICTIVE policy
--    （permissive 之间是 OR；restrictive 是 AND ⇒ 只要残留一条 restrictive，
--      下面写的 permissive 全部白写 ⇒ 必须先清）
--    只动这 3 张表，**vocab_states 不动**（它是通的）
-- ─────────────────────────────────────────────────────────────
do $$
declare r record; has_col boolean := false;
begin
  -- 🔴 这一段必须**绝不让整笔事务回滚**（teacher 点了 Run 却在输出里只看到一句 error、
  --    以为跑成功了，其实一条 policy 都没建 ⇒ 最阴的形态）
  --    ⇒ 先确认 pg_policies 有没有 permissive 列（PG 12+ 才有），再包一层 exception
  select exists (select 1 from information_schema.columns
                  where table_schema = 'pg_catalog' and table_name = 'pg_policies'
                    and column_name = 'permissive') into has_col;
  if not has_col then
    raise notice 'pg_policies 没有 permissive 列（PG < 12）⇒ 跳过 restrictive 清理';
    return;
  end if;
  for r in
    select tablename, policyname from pg_policies
     where schemaname = 'public'
       and tablename in ('vocab_history_logs','vocab_submissions','vocab_online_status')
       and upper(permissive) = 'RESTRICTIVE'
  loop
    raise notice '已删除 restrictive policy: % . %', r.tablename, r.policyname;
    execute format('drop policy %I on public.%I', r.policyname, r.tablename);
  end loop;
exception when others then
  raise notice '⚠️ 清理 restrictive policy 失败（**不影响后面的语句**）：%', sqlerrm;
end $$;

-- ─────────────────────────────────────────────────────────────
-- 1) vocab_history_logs（三站共用的活动/练习记录表）
--    白名单 = 三站真实在用的 class_key（从线上代码读出来的）
-- ─────────────────────────────────────────────────────────────
drop policy if exists p_anon_insert_logs on public.vocab_history_logs;
create policy p_anon_insert_logs
    on public.vocab_history_logs
    for insert to anon
    with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_select_logs on public.vocab_history_logs;
create policy p_anon_select_logs
    on public.vocab_history_logs
    for select to anon
    using (class_key ~ '^(LISTEN|CET4|CET4TEST)$');

drop policy if exists p_anon_select_logs_vocab on public.vocab_history_logs;
create policy p_anon_select_logs_vocab
    on public.vocab_history_logs
    for select to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

-- 🔴 这一条是 v1 最关键的漏：听力站三条 DELETE 路径全靠它
--    （删自己的 / 老师删一条 / 老师清空全部，都带 class_key=eq.LISTEN）
drop policy if exists p_anon_delete_logs on public.vocab_history_logs;
create policy p_anon_delete_logs
    on public.vocab_history_logs
    for delete to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

-- ─────────────────────────────────────────────────────────────
-- 2) vocab_submissions（词汇站「正式考试交卷」）
--    🔴 这张表**没有 class_key 列**（词汇站源码注释里写明了）
--    ⇒ 没法按班收窄 ⇒ 只能按 student_id 放行写；读给 anon = 所有人可见（见代价说明）
-- ─────────────────────────────────────────────────────────────
drop policy if exists p_anon_insert_subs on public.vocab_submissions;
create policy p_anon_insert_subs
    on public.vocab_submissions
    for insert to anon
    with check (true);

drop policy if exists p_anon_update_subs on public.vocab_submissions;
create policy p_anon_update_subs
    on public.vocab_submissions
    for update to anon
    using (true)
    with check (true);

drop policy if exists p_anon_select_subs on public.vocab_submissions;
create policy p_anon_select_subs
    on public.vocab_submissions
    for select to anon
    using (true);

drop policy if exists p_anon_delete_subs on public.vocab_submissions;
create policy p_anon_delete_subs
    on public.vocab_submissions
    for delete to anon
    using (student_id = '__rls_probe__');

-- ─────────────────────────────────────────────────────────────
-- 3) vocab_online_status（**词汇学习站在用**；听力记录站零引用）
--    有 class_key 列 ⇒ 可以按班收窄
--    词汇站用 upsert（onConflict student_id）⇒ 必须有 update policy
-- ─────────────────────────────────────────────────────────────
drop policy if exists p_anon_insert_online on public.vocab_online_status;
create policy p_anon_insert_online
    on public.vocab_online_status
    for insert to anon
    with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_update_online on public.vocab_online_status;
create policy p_anon_update_online
    on public.vocab_online_status
    for update to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$')
    with check (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_select_online on public.vocab_online_status;
create policy p_anon_select_online
    on public.vocab_online_status
    for select to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

drop policy if exists p_anon_delete_online on public.vocab_online_status;
create policy p_anon_delete_online
    on public.vocab_online_status
    for delete to anon
    using (class_key ~ '^[A-Z][A-Z0-9_]*(\|u[0-9]+)?$');

-- ─────────────────────────────────────────────────────────────
-- 4) 清理这几轮排查留下的探针行
--    （SQL Editor 是 owner 身份，绕过 RLS ⇒ 能删 vocab_states 里被 RLS 挡住的探针）
-- ─────────────────────────────────────────────────────────────
delete from public.vocab_states
 where class_key in ('__probe__','__probe_states__','__rls_probe__','__cet4_probe__');
delete from public.vocab_history_logs where class_key = '__rls_probe__';
delete from public.vocab_submissions  where student_id = '__rls_probe__';
delete from public.vocab_online_status where class_key = '__rls_probe__';

-- ─────────────────────────────────────────────────────────────
-- 5) 自检（跑完看这几句）
-- ─────────────────────────────────────────────────────────────
-- (a) policy 清单（期望：每张表都有 p_anon_insert_/update_/select_/delete_ 四类）
-- select tablename, policyname, cmd, roles, permissive, qual, with_check
--   from pg_policies
--  where schemaname='public'
--    and tablename in ('vocab_history_logs','vocab_submissions','vocab_online_status','vocab_states')
--  order by tablename, cmd, policyname;

-- (b) 🔴 真实数据还在不在（**这一条最关键**）
-- select count(*) from public.vocab_history_logs;
--     期望 2418（装 RLS 之前我独立复核过的真实条数）

-- (c) 按 class_key 数一数（能看出哪个站的记录在）
-- select class_key, count(*) from public.vocab_history_logs group by 1 order by 2 desc;

-- (d) 探针行清干净了没
-- select count(*) from public.vocab_states where class_key like '\_\_%probe%';
--     期望 0

commit;

-- =====================================================================
-- 跑完把 (b) 和 (c) 的数字发我，我这边独立跑验收脚本：
--   python3 tools/check_supabase_access.py
-- 验收脚本做的是**真实闭环**：写入 → 回读确认行在 → 删除 → 回读确认真的没了
-- （PostgREST 删不掉也返回 200/204 ⇒ 只看返回码会拿到假绿）
-- =====================================================================
