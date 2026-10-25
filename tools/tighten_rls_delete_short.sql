begin;

                                                                        
do $$
declare
  r record;
  saved_qual text;
  saved_name text;
begin
  for r in
    select policyname, qual, roles, tablename
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_history_logs'
       and cmd         = 'DELETE'
       and 'anon' = any(roles)                             
  loop
    saved_name := r.policyname;
    saved_qual := r.qual;
    if 'authenticated' = any(r.roles) then
                                                       
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      execute format('create policy %I on public.%I for delete to authenticated, service_role using (%s)',
                     r.policyname || '_teacher', r.tablename, saved_qual);
      raise notice '已拆分 %（anon 收紧 / authenticated 原样保留）', saved_name;
    else
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      raise notice '已删除 anon 侧 DELETE policy: %', saved_name;
    end if;
  end loop;
end $$;

create policy p_anon_delete_logs on public.vocab_history_logs
    for delete to anon
    using (student_id = '__rls_probe__' or class_key = 'LISTEN');

                                                                      
                                                             

                                                                   
do $$
declare
  r record;
  saved_qual text;
  saved_name text;
begin
  for r in
    select policyname, qual, roles, tablename
      from pg_policies
     where schemaname = 'public'
       and tablename   = 'vocab_online_status'
       and cmd         = 'DELETE'
       and 'anon' = any(roles)
  loop
    saved_name := r.policyname;
    saved_qual := r.qual;
    if 'authenticated' = any(r.roles) then
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      execute format('create policy %I on public.%I for delete to authenticated, service_role using (%s)',
                     r.policyname || '_teacher', r.tablename, saved_qual);
      raise notice '已拆分 %（anon 收紧 / authenticated 原样保留）', saved_name;
    else
      execute format('drop policy %I on public.%I', r.policyname, r.tablename);
      raise notice '已删除 anon 侧 DELETE policy: %', saved_name;
    end if;
  end loop;
end $$;

create policy p_anon_delete_online on public.vocab_online_status
    for delete to anon
    using (student_id = '__rls_probe__');
                                  
                                

                                                  
delete from public.vocab_history_logs where student_id = '__rls_probe__';
delete from public.vocab_online_status where student_id = '__rls_probe__';
                                                                                     
delete from public.vocab_states where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1');

commit;

                                                                        
                                        
                                                                        
                                               
select '① ' || tablename || ' | ' || policyname || ' | roles=' || roles::text || ' | ' || left(qual, 60) as x
  from pg_policies
 where schemaname = 'public' and cmd = 'DELETE'
 order by tablename;
  
                                   
                                                                         
                                           
select '② ' || tablename || ' | ' || policyname || ' | cmd=' || cmd || ' | roles=' || roles::text as x
  from pg_policies
 where schemaname = 'public'
   and cmd in ('ALL','DELETE')
   and tablename in ('vocab_history_logs','vocab_online_status')
   and ('authenticated' = any(roles) or 'service_role' = any(roles))
 order by tablename, cmd;
  
                                   
                                                  
                                             
with base(class_key, n) as (
  values ('JR1',746),('CW3',582),('WL_CW',280),('XQ1',839),('LISTEN',0),('CET4',6),('JR1|u1',0),('JR1|u2',0)
), cur as (
  select class_key, count(*) n from public.vocab_history_logs group by class_key
)
select '③ ' || b.class_key || ' 现在=' || coalesce(c.n,0) || ' 基线=' || b.n
       || case when coalesce(c.n,0) < b.n then '  ⛔ 少了！' else '  ✅' end as x
  from base b left join cur c on c.class_key = b.class_key
 order by b.class_key;
  
                                                     
select '④ 总数 ' || count(*) || ' / 基线 2453 → '
       || case when count(*) >= 2453 then '✅ 没丢数据' else '⛔ 少了数据！' end as x
  from public.vocab_history_logs;
  
                      
select '⑤ 残留探针 logs=' || (select count(*) from public.vocab_history_logs where student_id = '__rls_probe__')
     || ' online=' || (select count(*) from public.vocab_online_status where student_id = '__rls_probe__')
     || ' states=' || (select count(*) from public.vocab_states
                        where class_key in ('__rls_probe__','ZZPROBE','PROBE_V1')) as x;
