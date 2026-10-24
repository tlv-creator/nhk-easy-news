-- =====================================================================
-- RLS 挡写修复 · **每日听力记录站（listening）专属，按 class_key 收窄**
-- 2026-10-24 · 可重复执行（幂等）· 不改表结构 · 不动任何真实数据
-- Supabase SQL Editor → 新建 → 粘贴 → Run
-- =====================================================================
-- 🔴 为什么单独一个文件（而不是并进通用修复）：
--   通用修复用 `using (true)` / `with check (true)` 放开**整张表**
--   ⇒ anon 能读到/写到**别人站的数据**（比如词汇站那 2400+ 行学生练习记录）
--   ⇒ 把老师刚锁上的「anon 直读表被拒」又打开了。
--
--   而听力记录站**只碰 class_key='LISTEN' 的行**（实测：api.js 的 CFG.LOG_KEY='LISTEN'）
--   ⇒ **三个按 class_key='LISTEN' 收窄的 policy 就够**，且**对词汇站/四级站零影响**。
--
--   三个 policy 分别对应我的三条删除/写入路径：
--     ① 学生交一条记录   → INSERT  (class_key='LISTEN')
--     ② 学生看自己的记录 → SELECT  (class_key='LISTEN')
--     ③ 学生删自己的记录 → DELETE  (class_key='LISTEN')
--     ④ 教师删一条       → DELETE  (class_key='LISTEN')
--     ⑤ 教师清空全部     → DELETE  (class_key='LISTEN')
-- =====================================================================
begin;

-- ① INSERT：学生交一条听力记录
drop policy if exists p_anon_insert_listen_logs on public.vocab_history_logs;
create policy p_anon_insert_listen_logs on public.vocab_history_logs
    for insert to anon, authenticated
    with check (class_key = 'LISTEN');

-- ② SELECT：学生看自己的 + 教师看板读全部听力记录
drop policy if exists p_anon_select_listen_logs on public.vocab_history_logs;
create policy p_anon_select_listen_logs on public.vocab_history_logs
    for select to anon, authenticated
    using (class_key = 'LISTEN');

-- ③ DELETE：学生删自己的 + 教师删一条 + 教师清空全部
--    ⚠️ 少了这条，PostgREST 会返回 **200/204 + 空数组、不报错**
--    ⇒ 老师点「删掉了」其实**一行没删**（最阴的静默失败）
drop policy if exists p_anon_delete_listen_logs on public.vocab_history_logs;
create policy p_anon_delete_listen_logs on public.vocab_history_logs
    for delete to anon, authenticated
    using (class_key = 'LISTEN');

commit;

-- 自检（跑完看这两行）
select count(*) as 听力记录条数_should_be_0_or_more from public.vocab_history_logs where class_key = 'LISTEN';
select count(*) as 词汇站老数据_should_be_2418 from public.vocab_history_logs where class_key <> 'LISTEN';
