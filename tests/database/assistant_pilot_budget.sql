-- Disposable migrated database only. Synthetic accounts and reservations roll back.
begin;
insert into onflows_private.assistant_budget_accounts(budget_id,limit_nano_usd,enabled)
 values ('assistant-budget-sql',100,true),('assistant-budget-service',100,true);

do $$
declare
 result jsonb;
 req1 constant uuid := '99130000-0000-4000-8000-000000000001';
 req2 constant uuid := '99130000-0000-4000-8000-000000000002';
 req3 constant uuid := '99130000-0000-4000-8000-000000000003';
begin
 assert not (select prosecdef from pg_proc
  where oid='public.onflows_assistant_budget(text,text,uuid,bigint)'::regprocedure),'Budget RPC must use invoker';
 assert not has_function_privilege('anon','public.onflows_assistant_budget(text,text,uuid,bigint)','EXECUTE'),'Anon budget admission exposed';
 assert not has_function_privilege('authenticated','public.onflows_assistant_budget(text,text,uuid,bigint)','EXECUTE'),'Authenticated budget admission exposed';
 assert not has_table_privilege('authenticated','onflows_private.assistant_budget_accounts','SELECT'),'Budget account exposed';
 assert not has_table_privilege('anon','onflows_private.assistant_budget_reservations','SELECT'),'Budget reservations exposed';
 assert not has_column_privilege('service_role','onflows_private.assistant_budget_accounts','limit_nano_usd','UPDATE'),'Runtime can refill credit';
 assert not has_column_privilege('service_role','onflows_private.assistant_budget_accounts','enabled','UPDATE'),'Runtime can resume credit';
 assert not has_table_privilege('service_role','onflows_private.assistant_budget_accounts','INSERT'),'Runtime can create another allowance';
 assert not has_table_privilege('service_role','onflows_private.assistant_budget_reservations','DELETE'),'Runtime can delete unknown holds';
 assert (select bool_and(relrowsecurity) from pg_class where oid in
  ('onflows_private.assistant_budget_accounts'::regclass,'onflows_private.assistant_budget_reservations'::regclass)),'Budget RLS disabled';
 assert exists(select 1 from onflows_private.assistant_budget_accounts where
  budget_id='onflows-staging-luna-pilot-v1' and not enabled and limit_nano_usd=0),'Migration funded the pilot';

 result := public.onflows_assistant_budget('RESERVE','missing-pilot',req1,1);
 assert result->>'error'='budget_unavailable','Missing ledger admitted traffic';
 result := public.onflows_assistant_budget('RESERVE','onflows-staging-luna-pilot-v1',req1,1);
 assert result->>'error'='budget_exhausted','Disabled zero-credit pilot admitted traffic';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',req1,80);
 assert result->>'reserved'='true','Initial reservation rejected';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',req1,80);
 assert result->>'error'='invalid_budget','Same UUID authorized a second HTTP request';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',req2,21);
 assert result->>'error'='budget_exhausted','Reservation exceeded available allowance';
 assert (select reserved_nano_usd=80 and charged_nano_usd=0 from
  onflows_private.assistant_budget_accounts where budget_id='assistant-budget-sql'),'Rejected call changed allowance';
 result := public.onflows_assistant_budget('SETTLE','assistant-budget-sql',req1,81);
 assert result->>'error'='invalid_budget','Oversize settlement released unknown hold';
 result := public.onflows_assistant_budget('SETTLE','assistant-budget-sql',req1,60);
 assert result->>'settled'='true','Known usage did not settle';
 result := public.onflows_assistant_budget('SETTLE','assistant-budget-sql',req1,60);
 assert result->>'settled'='true','Same known usage was not idempotent';
 result := public.onflows_assistant_budget('SETTLE','assistant-budget-sql',req1,0);
 assert result->>'error'='invalid_budget','Settled charge could be refunded by retry';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',req2,40);
 assert result->>'reserved'='true','Unused hold was not returned after known settlement';

 -- A timeout/unknown result has no settlement. Crossing day, month or year
 -- boundaries must never release this money or reset cumulative known spend.
 update onflows_private.assistant_budget_reservations set created_at='2025-01-01'
  where budget_id='assistant-budget-sql' and request_id=req2;
 update onflows_private.assistant_budget_accounts set created_at='2025-01-01',updated_at='2025-01-01'
  where budget_id='assistant-budget-sql';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',req3,1);
 assert result->>'error'='budget_exhausted','Unknown hold expired or allowance reset';
 assert (select charged_nano_usd=60 and reserved_nano_usd=40 from
  onflows_private.assistant_budget_accounts where budget_id='assistant-budget-sql'),'Unknown/known spend lost';

 -- Only an operator changes the lifetime total after explicit top-up.
 update onflows_private.assistant_budget_accounts set limit_nano_usd=150 where budget_id='assistant-budget-sql';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',req3,50);
 assert result->>'reserved'='true','Explicit added allowance did not resume traffic';
 update onflows_private.assistant_budget_accounts set enabled=false where budget_id='assistant-budget-sql';
 result := public.onflows_assistant_budget('SETTLE','assistant-budget-sql',req3,10);
 assert result->>'settled'='true','Disabling traffic prevented settling already-paid usage';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',gen_random_uuid(),1);
 assert result->>'error'='budget_exhausted','Disabled allowance admitted traffic';
 assert (select charged_nano_usd=70 and reserved_nano_usd=40 from
  onflows_private.assistant_budget_accounts where budget_id='assistant-budget-sql'),'Known charge or unknown hold changed incorrectly';

 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',null,1);
 assert result->>'error'='invalid_budget','Null request accepted';
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-sql',gen_random_uuid(),0);
 assert result->>'error'='invalid_budget','Zero reservation accepted';
 result := public.onflows_assistant_budget('REFILL','assistant-budget-sql',gen_random_uuid(),1);
 assert result->>'error'='invalid_budget','Runtime refill action exposed';
end $$;

set local role service_role;
do $$
declare result jsonb; req constant uuid := '99130000-0000-4000-8000-000000000004';
begin
 result := public.onflows_assistant_budget('RESERVE','assistant-budget-service',req,100);
 assert result->>'reserved'='true','Real service role could not reserve';
 result := public.onflows_assistant_budget('SETTLE','assistant-budget-service',req,0);
 assert result->>'settled'='true','Real service role could not settle a confirmed free request';
end $$;
rollback;
