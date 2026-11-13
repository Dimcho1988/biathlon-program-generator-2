-- Run only on a disposable migrated database. Every synthetic row rolls back.
begin;
insert into auth.users(id) values
 ('99120000-0000-4000-8000-000000000001'),('99120000-0000-4000-8000-000000000002');
insert into public.onflows_profiles(user_id,display_name) values
 ('99120000-0000-4000-8000-000000000001','Assistant SQL athlete'),
 ('99120000-0000-4000-8000-000000000002','Assistant SQL plan viewer');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('assistant-chat-athlete','assistant-chat-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('assistant-chat-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99120000-0000-4000-8000-000000000001','assistant-chat-athlete',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,view_plan) values
 ('99120000-0000-4000-8000-000000000001','99120000-0000-4000-8000-000000000002',true);

do $$
declare
 athlete constant text := 'assistant-chat-athlete';
 owner_id constant uuid := '99120000-0000-4000-8000-000000000001';
 viewer_id constant uuid := '99120000-0000-4000-8000-000000000002';
 operation_id constant uuid := '99120000-0000-4000-8000-000000000010';
 chat_id uuid; viewer_conversation uuid; result jsonb;
begin
 assert not (select prosecdef from pg_proc where oid='public.onflows_assistant_conversation(text,text,uuid,uuid,uuid,jsonb,integer)'::regprocedure),'Conversation RPC must use invoker';
 assert not has_function_privilege('authenticated','public.onflows_assistant_conversation(text,text,uuid,uuid,uuid,jsonb,integer)','EXECUTE'),'Conversation RPC exposed';
 assert not has_table_privilege('authenticated','public.onflows_assistant_turns','SELECT'),'Conversation history exposed';
 assert (select relrowsecurity from pg_class where oid='public.onflows_assistant_turns'::regclass),'Turn RLS disabled';
 result := public.onflows_assistant_conversation('BEGIN',athlete,owner_id,null,operation_id,'{"message":"Какво тренирам днес?"}');
 chat_id := (result->>'conversation_id')::uuid;
 assert result->>'status'='PENDING' and chat_id is not null,'Turn not created';
 result := public.onflows_assistant_conversation('BEGIN',athlete,owner_id,null,operation_id,'{"message":"Какво тренирам днес?"}');
 assert (result->>'replayed')::boolean,'Lost response caused second execution';
 result := public.onflows_assistant_conversation('BEGIN',athlete,owner_id,null,operation_id,'{"message":"Друго искане"}');
 assert result->>'error'='operation_conflict','UUID reused for another input';
 result := public.onflows_assistant_conversation('BEGIN',athlete,owner_id,chat_id,'99120000-0000-4000-8000-000000000011','{"message":"Друго искане"}');
 assert result->>'error'='busy','Concurrent turn admitted';
 result := public.onflows_assistant_conversation('FINISH',athlete,owner_id,chat_id,operation_id,'{"response":{"message":"Две сесии"},"state":{}}');
 assert result->>'message'='Две сесии','Result not persisted';
 result := public.onflows_assistant_conversation('STATUS',athlete,owner_id,null,operation_id);
 assert result->>'status'='COMPLETED' and result->'response'->>'message'='Две сесии','Receipt status lost';
 result := public.onflows_assistant_conversation('READ',athlete,viewer_id,chat_id);
 assert result->>'error'='not_found','Conversation crossed actor boundary';
 update public.onflows_sharing_grants set view_recovery=true where owner_user_id=owner_id and viewer_user_id=viewer_id;
 result := public.onflows_assistant_conversation('BEGIN',athlete,viewer_id,null,'99120000-0000-4000-8000-000000000012','{"message":"Статус"}');
 viewer_conversation := (result->>'conversation_id')::uuid;
 perform public.onflows_assistant_conversation('FINISH',athlete,viewer_id,viewer_conversation,'99120000-0000-4000-8000-000000000012','{"response":{"message":"Частни резултати"},"state":{}}');
 update public.onflows_sharing_grants set view_recovery=false where owner_user_id=owner_id and viewer_user_id=viewer_id;
 result := public.onflows_assistant_conversation('READ',athlete,viewer_id,viewer_conversation);
 assert result->>'error'='scope_changed','Revoked recovery leaked from chat cache';
 result := public.onflows_assistant_conversation('CLEAR',athlete,owner_id,chat_id);
 assert (result->>'cleared')::boolean,'Clear failed';
 assert not exists(select 1 from public.onflows_assistant_turns t where t.conversation_id=chat_id),'Raw turns survived clear';
end $$;
rollback;
