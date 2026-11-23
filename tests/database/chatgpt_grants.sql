-- Exercise only synthetic rows and roll back the transaction.
begin;
insert into auth.users(id) values ('8db64221-0a72-4ffa-8564-907a25689b25');
insert into public.onflows_profiles(user_id,display_name)
values ('8db64221-0a72-4ffa-8564-907a25689b25','ChatGPT integration test');
insert into public.onflows_user_athletes(user_id,athlete_alias)
values ('8db64221-0a72-4ffa-8564-907a25689b25','chatgpt-authorization-test');
set local role service_role;
do $$
declare
  rights text[] := array['plan.read','calendar.write'];
  token_rpc regprocedure := 'public.exchange_onflows_chatgpt_token(text,text,text,text,text)'::regprocedure;
begin
  assert not has_function_privilege('authenticated',token_rpc,'execute');
  assert not has_table_privilege('anon','public.onflows_chatgpt_grants','select');
  assert not has_table_privilege('authenticated','public.onflows_chatgpt_refresh','update');
  insert into public.onflows_chatgpt_grants(actor,athlete_alias,scopes,code_hash,challenge)
  values ('8db64221-0a72-4ffa-8564-907a25689b25','chatgpt-authorization-test',rights,'code-test','challenge-test');
  assert public.exchange_onflows_chatgpt_token('authorization_code','code-test','wrong','a0','r0') is null;
  assert public.exchange_onflows_chatgpt_token('authorization_code','code-test','challenge-test','a1','r1')=rights;
  assert public.exchange_onflows_chatgpt_token('authorization_code','code-test','challenge-test','a2','r2') is null;
  assert public.exchange_onflows_chatgpt_token('refresh_token','r1',null,'a2','r2')=rights;
  assert not exists(select 1 from public.onflows_chatgpt_grants where access_hash='a1');
  assert public.exchange_onflows_chatgpt_token('refresh_token','r1',null,'a3','r3') is null;
  assert not exists(select 1 from public.onflows_chatgpt_grants where access_hash='a2');
  assert not exists(select 1 from public.onflows_chatgpt_refresh where digest='r2');
end $$;
rollback;
