-- ═══════════════════════════════════════════════════════════════
-- tighten_regress 的**断言**部分（各角色对每条删除路径的真实行为）
-- 判读：
--   ② 老师侧删 JR1 后剩余 = 0  ⇒ 老师侧(authenticated)边界保住了 ✅
--   ③ anon 删 JR1 后剩余 = 1     ⇒ anon 删不动词汇站的班 ✅（收紧达成）
--   ④ LISTEN 探针行剩余 = 0 / LISTEN 剩余 = 0 ⇒ 听力站三条路径照常 ✅
-- ═══════════════════════════════════════════════════════════════

\echo === ② 老师侧（authenticated）删 JR1 ===
set role authenticated;
delete from public.vocab_history_logs where class_key = 'JR1';
reset role;
select '  → 老师侧删 JR1 后，JR1 剩余行数 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'JR1';
-- 供下一段测试用：把行插回来（reset role 之后是超级用户 ⇒ 不受 RLS 限制）
insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
  values ('JR1','班','SJR1','2026-10-24 12:00:00',60,'练习')
  on conflict do nothing;
select '  → 插回一行后，JR1 行数 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'JR1';

\echo === ③ 学生侧（anon）删 JR1 ===
set role anon;
delete from public.vocab_history_logs where class_key = 'JR1';
reset role;
select '  → anon 删 JR1 后，JR1 剩余行数 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'JR1';

\echo === ④ 听力站三条删除路径（anon） ===
set role anon;
insert into public.vocab_history_logs (class_key,class_name,student_id,timestamp,duration_sec,action)
  values ('LISTEN','探针','__rls_probe__','t',0,'p') on conflict do nothing;
delete from public.vocab_history_logs where class_key = 'LISTEN' and student_id = '__rls_probe__';
select '  → 路径1 学生删自己：LISTEN 里探针行剩余 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'LISTEN' and student_id = '__rls_probe__';
delete from public.vocab_history_logs where class_key = 'LISTEN';
select '  → 路径3 老师清空全部：LISTEN 剩余 = ' || count(*) as r
  from public.vocab_history_logs where class_key = 'LISTEN';
reset role;

\echo === ⑤ 剩下的 DELETE policy ===
select '  ' || tablename || ' | ' || policyname || ' | roles=' || roles::text || ' | ' || left(qual, 52) as p
  from pg_policies where schemaname = 'public' and cmd = 'DELETE' order by tablename;

\echo === ⑥ 调试：角色 / 权限 / 可见行数 ===
select 'current_user=' || current_user || ' session_user=' || session_user as whoami;
select 'authenticated 有 DELETE 权限 = ' || has_table_privilege('authenticated','public.vocab_history_logs','DELETE') as priv;
select '行数(切角色前) = ' || count(*) as n from public.vocab_history_logs;
select '  policy: '||policyname||' roles='||roles::text||' cmd='||cmd||' qual='||left(qual,40) as p
  from pg_policies where schemaname='public' and tablename='vocab_history_logs';
set role authenticated;
select '  切到 authenticated 后 current_user=' || current_user as cu;
select '  authenticated 看得到 JR1 行数 = ' || count(*) as n from public.vocab_history_logs where class_key='JR1';
do $$
declare n int;
begin
  delete from public.vocab_history_logs where class_key='JR1';
  get diagnostics n = row_count;
  raise notice '  DELETE 影响行数 = %', n;
end $$;
reset role;
select '行数(切回后) = ' || count(*) as n from public.vocab_history_logs;
