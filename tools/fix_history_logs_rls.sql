-- =====================================================================
-- 紧急修复：RLS 把三个站的写入挡住了
-- 执行方式：Supabase 控制台 → SQL Editor → 新建 → 粘贴 → Run
-- 幂等：可重复执行（先 drop policy if exists）
-- 不含任何凭据；不改表结构；不动任何真实数据
-- =====================================================================
--
-- 【根因（2026-10-24 实测，不是推测）】
--   RLS 方案只给 authenticated（教师侧）配了 policy，三个站的学生端全是 anon 直写：
--     表                        anon 读              anon 写
--     vocab_history_logs        🔴 0 行（被过滤）      🔴 42501
--     vocab_submissions         ✅                     🔴 42501
--     vocab_online_status       ✅                     🔴 42501
--     vocab_states              ✅                     ✅ 通（但 DELETE 被挡）
--
-- 【影响面比最初报告的更大】
--   ① 三个站的学生记录全丢：四级站(练习记录) / 听力站(在线状态) /
--      **词汇站(平时练习记录 + 正式考试交卷)**
--   ② 🔴 **三个站的教师后台也读不到任何行**（SELECT 返回 0 行，不报错）
--      ⇒ 老师打开记录表看到的是空的
--
-- 【本脚本做什么】
--   给 anon / authenticated 补「读 + 写」policy，覆盖三张被挡的表。
--   DELETE 只放行 class_key/student_id = '__rls_probe__' 的探针行
--   ⇒ 验收脚本能真写一条探针再删掉，**不留垃圾**，而且**谁都删不掉真实记录**。
--
-- 【代价（必须说清）】
--   这三张表的隐私保护**回到装 RLS 之前的状态**：anon key 是公开的 ⇒
--   任意人可读全表、可灌数据。**这不是比装之前更差，是回到原点。**
--   真正的收紧要靠「学生端改走后端（service_role 写入）」，
--   那是三个站的前端改造，不是今天能落地的。等改造完再重新收紧。
-- =====================================================================

begin;

-- ─────────────────────────────────────────────────────────────
-- 1) vocab_history_logs（三站共用；词汇站的平时练习记录也在这）
-- ─────────────────────────────────────────────────────────────
drop policy if exists p_anon_write_logs on public.vocab_history_logs;
create policy p_anon_write_logs
    on public.vocab_history_logs
    for insert to anon, authenticated
    with check (true);

drop policy if exists p_anon_read_logs on public.vocab_history_logs;
create policy p_anon_read_logs
    on public.vocab_history_logs
    for select to anon, authenticated
    using (true);

drop policy if exists p_anon_delete_probe_logs on public.vocab_history_logs;
create policy p_anon_delete_probe_logs
    on public.vocab_history_logs
    for delete to anon, authenticated
    using (class_key = '__rls_probe__');

-- ─────────────────────────────────────────────────────────────
-- 2) vocab_submissions（正式考试交卷；三站共用）
-- ─────────────────────────────────────────────────────────────
drop policy if exists p_anon_write_subs on public.vocab_submissions;
create policy p_anon_write_subs
    on public.vocab_submissions
    for insert to anon, authenticated
    with check (true);

drop policy if exists p_anon_read_subs on public.vocab_submissions;
create policy p_anon_read_subs
    on public.vocab_submissions
    for select to anon, authenticated
    using (true);

drop policy if exists p_anon_delete_probe_subs on public.vocab_submissions;
create policy p_anon_delete_probe_subs
    on public.vocab_submissions
    for delete to anon, authenticated
    using (student_id = '__rls_probe__');

-- ─────────────────────────────────────────────────────────────
-- 3) vocab_online_status（每日听力记录站在用）
-- ─────────────────────────────────────────────────────────────
drop policy if exists p_anon_write_online on public.vocab_online_status;
create policy p_anon_write_online
    on public.vocab_online_status
    for insert to anon, authenticated
    with check (true);

drop policy if exists p_anon_read_online on public.vocab_online_status;
create policy p_anon_read_online
    on public.vocab_online_status
    for select to anon, authenticated
    using (true);

drop policy if exists p_anon_delete_probe_online on public.vocab_online_status;
create policy p_anon_delete_probe_online
    on public.vocab_online_status
    for delete to anon, authenticated
    using (class_key = '__rls_probe__');

-- ─────────────────────────────────────────────────────────────
-- 4) 清理这几轮排查留下的探针行
--    （都是测试数据，不是真实记录；vocab_states 的 DELETE 被 RLS 挡，
--      只能在这里删 —— SQL Editor 是 owner 身份，绕过 RLS）
-- ─────────────────────────────────────────────────────────────
delete from public.vocab_states
 where class_key in ('__probe__', '__probe_states__', '__rls_probe__', '__cet4_probe__');

-- ─────────────────────────────────────────────────────────────
-- 5) 自检：跑完看这两句
-- ─────────────────────────────────────────────────────────────
-- (a) policy 清单：
-- select tablename, policyname, cmd, roles
--   from pg_policies
--  where schemaname='public'
--    and tablename in ('vocab_history_logs','vocab_submissions','vocab_online_status')
--  order by tablename, policyname;
--     期望每张表都能看到 p_anon_read_* / p_anon_write_* / p_anon_delete_probe_*
--
-- (b) 真实数据还在不在（**这一条最关键**）：
-- select count(*) from public.vocab_history_logs;
--     期望 2418（装 RLS 之前我独立复核过的真实条数）
--
-- (c) 探针行清干净了没：
-- select count(*) from public.vocab_states where class_key like '\_\_%probe%';
--     期望 0

commit;

-- =====================================================================
-- 跑完把 (b) 的数字发我，我这边再独立跑一遍验收脚本：
--   python3 tools/check_supabase_access.py
-- 期望：exit 0（四张表 × 读/写 全通）
-- =====================================================================
