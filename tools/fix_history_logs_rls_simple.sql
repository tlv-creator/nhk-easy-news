-- =====================================================================
-- RLS 挡写修复（三站共用表）· 2026-10-24 · 可重复执行
-- Supabase SQL Editor → 新建 → 粘贴 → Run
-- 不改表结构、不动任何真实数据
-- =====================================================================
begin;

-- 1) vocab_history_logs（词汇站平时练习 / 四级站练习记录）
drop policy if exists p_anon_write_logs on public.vocab_history_logs;
create policy p_anon_write_logs on public.vocab_history_logs
    for insert to anon, authenticated with check (true);
drop policy if exists p_anon_read_logs on public.vocab_history_logs;
create policy p_anon_read_logs on public.vocab_history_logs
    for select to anon, authenticated using (true);
drop policy if exists p_anon_delete_probe_logs on public.vocab_history_logs;
create policy p_anon_delete_probe_logs on public.vocab_history_logs
    for delete to anon, authenticated using (class_key = '__rls_probe__');

-- 2) vocab_submissions（正式考试交卷）
drop policy if exists p_anon_write_subs on public.vocab_submissions;
create policy p_anon_write_subs on public.vocab_submissions
    for insert to anon, authenticated with check (true);
drop policy if exists p_anon_read_subs on public.vocab_submissions;
create policy p_anon_read_subs on public.vocab_submissions
    for select to anon, authenticated using (true);
drop policy if exists p_anon_delete_probe_subs on public.vocab_submissions;
create policy p_anon_delete_probe_subs on public.vocab_submissions
    for delete to anon, authenticated using (student_id = '__rls_probe__');

-- 3) vocab_online_status（每日听力记录站在用）
drop policy if exists p_anon_write_online on public.vocab_online_status;
create policy p_anon_write_online on public.vocab_online_status
    for insert to anon, authenticated with check (true);
drop policy if exists p_anon_read_online on public.vocab_online_status;
create policy p_anon_read_online on public.vocab_online_status
    for select to anon, authenticated using (true);
drop policy if exists p_anon_delete_probe_online on public.vocab_online_status;
create policy p_anon_delete_probe_online on public.vocab_online_status
    for delete to anon, authenticated using (class_key = '__rls_probe__');

-- 4) 清掉这几轮排查留下的探针行（测试数据，不是真实记录）
delete from public.vocab_states
 where class_key in ('__probe__','__probe_states__','__rls_probe__','__cet4_probe__');

commit;

-- 自检：跑完看这一句
select count(*) as 真实记录条数 from public.vocab_history_logs;   -- 期望 2418
