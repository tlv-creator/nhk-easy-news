-- =====================================================================
-- 🔴🔴 本文件**只能对本地空库**跑（先跑 setup.sql 建好形状）
--    学号全是**合成占位**（90000001/90000002/90000003），不是真人学号
--      ⇒ 只会碰到 setup.sql 自己造的那 3 行，**不会删到任何真学生的记录**
-- ⚠️ T8/T9 是**真删**（老师侧删整班）—— 但它们包在 begin…rollback 里 ⇒ 跑完会回滚
-- ⚠️ 唯独 T13 之前那条 insert 在**事务外** ⇒ 对着生产库跑会**留下 1 行垃圾**（不会删数据）
-- =====================================================================
\set ON_ERROR_STOP on
do $$ begin perform set_config('request.headers','{}',false); end $$;
-- 场景 1：anon 删词汇站整班（JR1）—— 期望删 0 行
begin; set role anon;
delete from public.vocab_history_logs where class_key='JR1';
select 'T1 anon删JR1整班 剩余=' || count(*) as r from public.vocab_history_logs where class_key='JR1';
rollback;

-- 场景 2：anon 删 LISTEN 整班（不带头）—— 期望删 0 行
begin; set role anon;
delete from public.vocab_history_logs where class_key='LISTEN';
select 'T2 anon删LISTEN整班(无头) 剩余=' || count(*) as r from public.vocab_history_logs where class_key='LISTEN';
rollback;

-- 场景 3：🔴 攻击者偷看 action 列拿到的是 sha256 摘要，当作 x-row-token 送来 —— 期望删 0 行
begin; set role anon;
do $$ begin perform set_config('request.headers',
  json_build_object('x-student-id','90000001','x-row-token',
    (select split_part(action,'#tk=',2) from public.vocab_history_logs
      where class_key='LISTEN' and student_id='90000001' limit 1))::text, false); end $$;
delete from public.vocab_history_logs where class_key='LISTEN' and student_id='90000001';
select 'T3 偷到的摘要当令牌 剩余=' || count(*) as r from public.vocab_history_logs
  where class_key='LISTEN' and student_id='90000001';
rollback;

-- 场景 4：学生本人带**原始令牌**（只存在本机）—— 期望删掉自己那 1 条
begin; set role anon;
do $$ begin perform set_config('request.headers',
  '{"x-student-id":"90000001","x-row-token":"a1b2c3d4e5f60718293a4b5c6d7e8f90"}', false); end $$;
delete from public.vocab_history_logs where class_key='LISTEN' and student_id='90000001';
select 'T4 本人带原始令牌 剩余=' || count(*) as r from public.vocab_history_logs
  where class_key='LISTEN' and student_id='90000001';
rollback;

-- 场景 5：拿 A 的原始令牌 + 冒充 B 的学号 —— 期望删 0 行
begin; set role anon;
do $$ begin perform set_config('request.headers',
  '{"x-student-id":"90000002","x-row-token":"a1b2c3d4e5f60718293a4b5c6d7e8f90"}', false); end $$;
delete from public.vocab_history_logs where class_key='LISTEN' and student_id='90000002';
select 'T5 拿A令牌冒充B 剩余=' || count(*) as r from public.vocab_history_logs
  where class_key='LISTEN' and student_id='90000002';
rollback;

-- 场景 6：anon 删探针行 —— 期望能删（清理口子必须留着）
begin; set role anon;
delete from public.vocab_history_logs where student_id='__rls_probe__';
select 'T6 探针行剩余=' || count(*) as r from public.vocab_history_logs where student_id='__rls_probe__';
rollback;

-- 场景 7：🔴 LIKE 通配符注入（x-row-token 里带 %）—— 期望删 0 行
begin; set role anon;
do $$ begin perform set_config('request.headers',
  '{"x-student-id":"90000001","x-row-token":"%"}', false); end $$;
delete from public.vocab_history_logs where class_key='LISTEN' and student_id='90000001';
select 'T7 通配符注入 剩余=' || count(*) as r from public.vocab_history_logs
  where class_key='LISTEN' and student_id='90000001';
rollback;

-- 场景 8：老师侧 authenticated 删词汇站整班 —— 期望成功（剩余 0）
begin; set role authenticated;
delete from public.vocab_history_logs where class_key='JR1';
select 'T8 老师删JR1整班 剩余=' || count(*) as r from public.vocab_history_logs where class_key='JR1';
rollback;

-- 场景 9：老师侧 authenticated 删 LISTEN 整班（老师端「清空全部」）—— 期望成功（剩余 0）
begin; set role authenticated;
delete from public.vocab_history_logs where class_key='LISTEN';
select 'T9 老师删LISTEN整班 剩余=' || count(*) as r from public.vocab_history_logs where class_key='LISTEN';
rollback;

-- 场景 10：老师侧 authenticated 删单条（带令牌形状）—— 期望成功
begin; set role authenticated;
delete from public.vocab_history_logs where id=(select id from public.vocab_history_logs
  where class_key='LISTEN' and student_id='90000001' limit 1);
select 'T10 老师删单条 LISTEN剩余=' || count(*) as r from public.vocab_history_logs where class_key='LISTEN';
rollback;

-- 场景 11：vocab_states 探针行 —— 期望 0（收尾 SQL 已清）
select 'T11 states探针剩余=' || count(*) as r from public.vocab_states
  where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1');

-- 场景 12：混合型 policy 拆分结果 —— 期望 authenticated 侧原样保留
select 'T12 ' || policyname || ' | cmd=' || cmd || ' | roles=' || roles::text || ' | ' || left(qual,52) as r
  from pg_policies where schemaname='public' and cmd='DELETE' order by tablename, policyname;

-- 场景 13：🔴 哈希以 0 开头（最易丢前导零）+ 本人带原始令牌 ⇒ 期望删掉
-- 造一条「哈希以 0 开头」的真实行
insert into public.vocab_history_logs (class_key, class_name, student_id, duration_sec, action)
values ('LISTEN','听力','90000003',600, '【听力】前导零测试 | #tk=091a2cfded201d1f6aa540ada1addfbaa578663675bcc232467cde25c7b201ba');
begin; set role anon;
do $$ begin perform set_config('request.headers',
  '{"x-student-id":"90000003","x-row-token":"00000000000000007"}', false); end $$;
delete from public.vocab_history_logs where class_key='LISTEN' and student_id='90000003';
select 'T13 哈希以0开头 → 本人带原始令牌 剩余=' || count(*) as r
  from public.vocab_history_logs where student_id='90000003';
rollback;
