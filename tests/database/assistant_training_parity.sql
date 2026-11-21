-- Disposable database only: fixtures, triggers and writes are rolled back.
begin;
insert into auth.users(id) values
 ('99130000-0000-4000-8000-000000000001'),('99130000-0000-4000-8000-000000000002'),
 ('99130000-0000-4000-8000-000000000003'),('99130000-0000-4000-8000-000000000004');
insert into public.onflows_profiles(user_id,display_name) values
 ('99130000-0000-4000-8000-000000000001','Training parity athlete'),
 ('99130000-0000-4000-8000-000000000002','Training parity editor'),
 ('99130000-0000-4000-8000-000000000003','Training parity recovery viewer'),
 ('99130000-0000-4000-8000-000000000004','Training parity plan viewer');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
values('assistant-training-test','assistant-training-provider','synthetic-not-a-token','CONNECTED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
values('assistant-training-test',array[90,120,140,160,180,200],200,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
values('99130000-0000-4000-8000-000000000001','assistant-training-test',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,view_plan,view_recovery,edit_plan) values
 ('99130000-0000-4000-8000-000000000001','99130000-0000-4000-8000-000000000002',true,false,true),
 ('99130000-0000-4000-8000-000000000001','99130000-0000-4000-8000-000000000003',false,true,false),
 ('99130000-0000-4000-8000-000000000001','99130000-0000-4000-8000-000000000004',true,false,false);
create function onflows_private.assistant_test_fail_receipt() returns trigger language plpgsql as $$
begin
 if new.operation_id='99130000-0000-4000-8000-000000000099' then
  raise exception 'Synthetic receipt failure';
 end if;
 return new;
end $$;
create trigger assistant_training_fail_receipt before insert on onflows_private.assistant_operations
 for each row execute function onflows_private.assistant_test_fail_receipt();
set local role service_role;
do $$
declare
 a constant text := 'assistant-training-test';
 owner_id constant uuid := '99130000-0000-4000-8000-000000000001';
 editor_id constant uuid := '99130000-0000-4000-8000-000000000002';
 recovery_id constant uuid := '99130000-0000-4000-8000-000000000003';
 viewer_id constant uuid := '99130000-0000-4000-8000-000000000004';
 profile jsonb := '{"schema_version":"planning-profile-v1","season_start":"2026-01-01","season_end":"2026-12-31","annual_target_hours":600,"sessions_per_week":8,"rest_days":[0],"double_session_days":[2,5],"long_session_day":6,"intensity_days":[2,5],"strength_days":[1,4],"max_key_sessions_per_week":3,"mesocycle_anchor_date":"2026-01-01","mesocycle_length_weeks":4,"camp_default_accent_limit":2,"double_threshold_enabled":false,"double_threshold_day":2,"double_threshold_components":["Z3","Z4"]}';
 prefs jsonb := '{"schema_version":"mesocycle-accent-preferences-v1","accent_mode":"HYBRID","accent_limit":3,"manual_components":["Z2"]}';
 sync jsonb := '{"job_kind":"FULL_SYNC","request_payload":{"schema_version":"sync-request-v1","scope":"FULL","as_of":"2026-11-12"}}';
 result jsonb; first_result jsonb; denied boolean; count_before integer;
begin
 result := public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000010',repeat('a',64),'PLANNING_PROFILE','planning-profile-v1',profile,0);
 assert result->>'forbidden'='true', 'Recovery viewer changed planning profile';
 first_result := public.save_onflows_assistant_app_operation(a,editor_id,'99130000-0000-4000-8000-000000000010',repeat('a',64),'PLANNING_PROFILE','planning-profile-v1',profile,0);
 assert first_result->>'saved'='true' and first_result->>'revision'='1', 'Plan-only editor cannot edit legacy profile';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99130000-0000-4000-8000-000000000010',repeat('a',64),'PLANNING_PROFILE','planning-profile-v1',profile,0);
 assert result=first_result, 'Profile replay changed receipt';
 result := public.onflows_assistant_operation_status(a,editor_id,'99130000-0000-4000-8000-000000000010');
 assert result=first_result, 'Plan-only editor cannot recover legacy receipt';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99130000-0000-4000-8000-000000000011',repeat('b',64),'MESOCYCLE_PREFERENCES','mesocycle-accent-preferences-v1',prefs,1);
 assert result->>'saved'='true' and result->>'revision'='2', 'Meso preferences failed';
 update public.onflows_athlete_settings set planning_profile=jsonb_set(planning_profile,'{annual_target_hours}','650') where athlete_alias=a;
 assert (select settings_revision from public.onflows_athlete_settings where athlete_alias=a)=3, 'Form profile edit did not invalidate preview';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99130000-0000-4000-8000-000000000012',repeat('c',64),'MESOCYCLE_PREFERENCES','mesocycle-accent-preferences-v1',prefs,2);
 assert result->>'status'='CONFLICT', 'Stale legacy change overwrote form';
 update public.onflows_athlete_settings set mesocycle_accent_preferences=jsonb_set(mesocycle_accent_preferences,'{accent_limit}','4') where athlete_alias=a;
 assert (select settings_revision from public.onflows_athlete_settings where athlete_alias=a)=4, 'Form meso edit did not invalidate preview';
 result := public.save_onflows_assistant_app_operation(a,viewer_id,'99130000-0000-4000-8000-000000000020',repeat('d',64),'SYNC_DATA','intervals-sync',sync,0);
 assert result->>'forbidden'='true', 'Plan-only viewer could enqueue sync';
 first_result := public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000020',repeat('d',64),'SYNC_DATA','intervals-sync',sync,0);
 assert first_result->>'saved'='true' and first_result->>'queued'='true', 'Recovery-only viewer cannot refresh as app permits';
 assert first_result#>>'{sync,state}'='QUEUED' and first_result#>>'{sync,scope}'='FULL', 'Public sync result differs from canonical service';
 result := public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000020',repeat('d',64),'SYNC_DATA','intervals-sync',sync,0);
 assert result=first_result, 'Sync replay changed receipt';
 assert (select count(*) from public.onflows_sync_jobs where athlete_alias=a)=1, 'Sync replay duplicated queue job';
 result := public.onflows_assistant_operation_status(a,recovery_id,'99130000-0000-4000-8000-000000000020');
 assert result=first_result, 'Recovery-only viewer cannot read own sync receipt';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99130000-0000-4000-8000-000000000021',repeat('e',64),'SYNC_DATA','intervals-sync',sync,0);
 assert result->>'saved'='true' and result#>>'{sync,coalesced}'='true', 'Plan-only editor cannot refresh or coalescing lost';
 assert result#>>'{sync,job_id}'=first_result#>>'{sync,job_id}', 'Coalesced queue identity changed';
 update public.onflows_sync_jobs set status='SUCCEEDED' where athlete_alias=a;
 result := public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000020',repeat('d',64),'SYNC_DATA','intervals-sync',sync,0);
 assert result=first_result, 'Replay after completion enqueued instead of recovering receipt';
 result := public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000022',repeat('f',64),'SYNC_DATA','intervals-sync',sync,0);
 assert result#>>'{sync,job_id}'<>first_result#>>'{sync,job_id}', 'New refresh after completion reused old intent';
 update public.onflows_sync_jobs set status='SUCCEEDED' where athlete_alias=a;
 select count(*) into count_before from public.onflows_sync_jobs where athlete_alias=a;
 denied := false;
 begin
  perform public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000099',repeat('9',64),'SYNC_DATA','intervals-sync',sync,0);
 exception when raise_exception then denied:=true; end;
 assert denied, 'Receipt failure fixture did not execute';
 assert (select count(*) from public.onflows_sync_jobs where athlete_alias=a)=count_before, 'Queue survived failed atomic receipt';
 assert not exists(select 1 from onflows_private.assistant_operations where athlete_alias=a and operation_id='99130000-0000-4000-8000-000000000099'), 'Failed operation retained receipt';
 denied := false;
 begin
  perform public.save_onflows_assistant_app_operation(a,editor_id,'99130000-0000-4000-8000-000000000023',repeat('1',64),'SYNC_DATA','intervals-sync',sync||'{"job_kind":"WELLNESS_SYNC"}',0);
 exception when invalid_parameter_value then denied:=true; end;
 assert denied, 'Mismatched canonical sync kind accepted';
 update public.onflows_sharing_grants set view_recovery=false,view_plan=true where owner_user_id=owner_id and viewer_user_id=recovery_id;
 result := public.onflows_assistant_operation_status(a,recovery_id,'99130000-0000-4000-8000-000000000020');
 assert result->>'forbidden'='true', 'Revoked sync scope exposed receipt';
 result := public.save_onflows_assistant_app_operation(a,recovery_id,'99130000-0000-4000-8000-000000000020',repeat('d',64),'SYNC_DATA','intervals-sync',sync,0);
 assert result->>'forbidden'='true', 'Revoked scope replayed sync request';
end $$;
reset role;
rollback;
