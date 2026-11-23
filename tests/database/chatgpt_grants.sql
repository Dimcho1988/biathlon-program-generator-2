-- Exercise only synthetic rows and roll back the transaction.
begin;
insert into auth.users(id) values ('8db64221-0a72-4ffa-8564-907a25689b25');
insert into public.onflows_profiles(user_id,display_name)
values ('8db64221-0a72-4ffa-8564-907a25689b25','ChatGPT integration test');
insert into public.onflows_user_athletes(user_id,athlete_alias)
values ('8db64221-0a72-4ffa-8564-907a25689b25','chatgpt-authorization-test');
insert into auth.users(id) values ('158a241a-b0ba-4224-bbd7-ad094148de98');
insert into public.onflows_profiles(user_id,display_name)
values ('158a241a-b0ba-4224-bbd7-ad094148de98','Second ChatGPT test athlete');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
values ('158a241a-b0ba-4224-bbd7-ad094148de98','chatgpt-second-test',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,view_plan)
values ('158a241a-b0ba-4224-bbd7-ad094148de98','8db64221-0a72-4ffa-8564-907a25689b25',true);
set local role service_role;
do $$
declare
  coach uuid := '8db64221-0a72-4ffa-8564-907a25689b25';
  rights text[] := array['plan.read','calendar.write'];
  token_rpc regprocedure := 'public.exchange_onflows_chatgpt_token(text,text,text,text,text)'::regprocedure;
begin
  assert not public.create_onflows_chatgpt_roster(coach,array['chatgpt-authorization-test','unknown'],true,'qa-denied','c');
  assert public.create_onflows_chatgpt_roster(coach,array['chatgpt-authorization-test','chatgpt-second-test'],true,'qa-roster','c');
  assert (select athlete_alias is null and scopes=rights from public.onflows_chatgpt_grants where code_hash='qa-roster');
  assert (select count(*)=2 and bool_and(allow_write=(athlete_alias='chatgpt-authorization-test'))
          from public.onflows_chatgpt_athletes where grant_id=(select id from public.onflows_chatgpt_grants where code_hash='qa-roster'));
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
