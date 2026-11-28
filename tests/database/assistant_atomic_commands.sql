-- Run only on a disposable migrated database. Every synthetic row rolls back.
begin;
insert into auth.users(id) values
 ('99110000-0000-4000-8000-000000000001'),('99110000-0000-4000-8000-000000000002');
insert into public.onflows_profiles(user_id,display_name) values
 ('99110000-0000-4000-8000-000000000001','Assistant SQL athlete'),
 ('99110000-0000-4000-8000-000000000002','Assistant SQL plan viewer');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('assistant-sql-athlete','assistant-sql-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('assistant-sql-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99110000-0000-4000-8000-000000000001','assistant-sql-athlete',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,view_plan) values
 ('99110000-0000-4000-8000-000000000001','99110000-0000-4000-8000-000000000002',true);

do $$
declare rpc regprocedure; role_name text;
begin
 foreach rpc in array array[
  'public.onflows_assistant_access(text,uuid)'::regprocedure,
  'public.onflows_assistant_operation_status(text,uuid,uuid)'::regprocedure,
  'public.save_onflows_assistant_operation(text,uuid,uuid,text,text,text,jsonb,date,integer,text)'::regprocedure
 ] loop
  assert not (select prosecdef from pg_proc where oid=rpc),'Exposed RPC must use invoker rights';
  assert has_function_privilege('service_role',rpc,'EXECUTE'),'Missing service grant';
  foreach role_name in array array['anon','authenticated'] loop
   assert not has_function_privilege(role_name,rpc,'EXECUTE'),'Public execution leaked';
  end loop;
 end loop;
 assert (select relrowsecurity from pg_class where oid='onflows_private.assistant_operations'::regclass),'Receipt RLS disabled';
 assert not has_table_privilege('authenticated','onflows_private.assistant_operations','SELECT'),'Receipt store exposed';
end $$;
set local role service_role;
do $$
declare
 athlete constant text := 'assistant-sql-athlete';
 owner_id constant uuid := '99110000-0000-4000-8000-000000000001';
 viewer_id constant uuid := '99110000-0000-4000-8000-000000000002';
 operation_id constant uuid := '99110000-0000-4000-8000-000000000010';
 second_id constant uuid := '99110000-0000-4000-8000-000000000011';
 first_receipt jsonb; result jsonb; rights jsonb;
begin
 rights := public.onflows_assistant_access(athlete,viewer_id);
 assert (rights->>'can_view_plan')::boolean and not (rights->>'can_view_recovery')::boolean,'Plan sharing widened';
 result := public.save_onflows_assistant_operation(athlete,viewer_id,operation_id,repeat('a',64),'DAILY','2026-11-11',
  '{"day":"2026-11-11","fatigue":4}','2026-11-11',0);
 assert (result->>'forbidden')::boolean,'Read-only viewer wrote self report';
 first_receipt := public.save_onflows_assistant_operation(athlete,owner_id,operation_id,repeat('a',64),'DAILY','2026-11-11',
  '{"day":"2026-11-11","fatigue":4}','2026-11-11',0);
 assert (first_receipt->>'saved')::boolean and (first_receipt->>'revision')::int=1,'Observation not saved';
 result := public.save_onflows_assistant_operation(athlete,owner_id,operation_id,repeat('a',64),'DAILY','2026-11-11',
  '{"day":"2026-11-11","fatigue":4}','2026-11-11',0);
 assert result=first_receipt,'Receipt replay changed';
 assert (select count(*) from public.onflows_response_entries where athlete_alias=athlete)=1,'Duplicate operation wrote twice';
 result := public.save_onflows_assistant_operation(athlete,owner_id,second_id,repeat('b',64),'DAILY','2026-11-11',
  '{"day":"2026-11-11","fatigue":2}','2026-11-11',0);
 assert result->>'status'='CONFLICT','Stale revision overwrote new value';
 result := public.onflows_assistant_operation_status(athlete,viewer_id,operation_id);
 assert result->>'status'='NOT_FOUND','Receipt crossed actor scope';
 result := public.save_onflows_assistant_operation(athlete,owner_id,operation_id,repeat('c',64),'DAILY','2026-11-11',
  '{"day":"2026-11-11","fatigue":1}','2026-11-11',0);
 assert result->>'reason'='OPERATION_REUSED','Operation identifier accepted a different write';
 result := public.save_onflows_assistant_operation(athlete,owner_id,'99110000-0000-4000-8000-000000000012',repeat('d',64),
  'CALENDAR_EVENT','race-test-0001','{"schema_version":"planning-calendar-v1","events":[]}',null,0);
 assert (result->>'revision')::int=1,'Calendar CAS did not advance';
 update public.onflows_athlete_settings set planning_calendar='{"schema_version":"planning-calendar-v1","events":[{"event_id":"race-test-0001"}]}'
  where athlete_alias=athlete;
 assert (select calendar_revision from public.onflows_athlete_settings where athlete_alias=athlete)=2,'Legacy edit bypassed calendar version';
 result := public.save_onflows_assistant_operation(athlete,owner_id,'99110000-0000-4000-8000-000000000013',repeat('e',64),
  'CALENDAR_EVENT','race-test-0001','{"schema_version":"planning-calendar-v1","events":[]}',null,1);
 assert result->>'status'='CONFLICT','Calendar race overwrote UI edit';
end $$;
rollback;
