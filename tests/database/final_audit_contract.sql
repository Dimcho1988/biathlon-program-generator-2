-- Run against a migrated local database: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f this-file.
-- Fixtures and every mutation are rolled back. No real athlete records are read.
begin;

insert into auth.users(id) values
 ('99000000-0000-0000-0000-000000000001'),
 ('99000000-0000-0000-0000-000000000002'),
 ('99000000-0000-0000-0000-000000000003');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000001','Checkpoint test athlete'),
 ('99000000-0000-0000-0000-000000000002','Checkpoint test coach'),
 ('99000000-0000-0000-0000-000000000003','Checkpoint test outsider');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('checkpoint-test-athlete','checkpoint-test-provider','synthetic-not-a-token','REVOKED'),
 ('checkpoint-test-other','checkpoint-test-other-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('checkpoint-test-athlete',array[100,120,140,155,170,190],190,'UTC'),
 ('checkpoint-test-other',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000001','checkpoint-test-athlete',true),
 ('99000000-0000-0000-0000-000000000003','checkpoint-test-other',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,edit_plan,view_plan) values
 ('99000000-0000-0000-0000-000000000001','99000000-0000-0000-0000-000000000002',true,true);

set local role service_role;
do $$
declare
 athlete constant text := 'checkpoint-test-athlete';
 actor constant uuid := '99000000-0000-0000-0000-000000000001';
 token1 constant uuid := '99000000-0000-0000-0000-000000000011';
 token2 constant uuid := '99000000-0000-0000-0000-000000000012';
 checkpoint jsonb; payload jsonb; result jsonb; denied boolean;
begin
 perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1','{}',0,actor);
 checkpoint := public.onflows_management_checkpoint(athlete);
 payload := jsonb_build_object('status','ACTIVE','mode','AUTO','approved_settings_fingerprint','same-rules',
   'evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,'approved_by',actor,'approved_profile_revision',1,
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,
     'readiness_known',false,'planning_history_supported',true,'readiness_basis','ESTIMATED_LOAD',
     'planning_history',jsonb_build_object('supported',true,'estimated',true,'policy','RECORDED_DURATION_EXPERT_Q_WITH_RECOVERY'))));
 result := public.save_onflows_management_plan(athlete,payload,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->>'saved'='true', 'Supported estimated history must activate';
 result := public.save_onflows_management_plan(athlete,payload,1,actor,'ADAPT',1,checkpoint,true);
 assert result->>'saved'='true', 'Supported estimate must adapt under the same approved rules';
 result := public.save_onflows_management_plan(athlete,payload,1,actor,'ADAPT',1,checkpoint,true);
 assert result->>'reason'='REVISION_CHANGED', 'Duplicate publication must still conflict';
 denied := false;
 begin
  perform public.save_onflows_management_plan(athlete,
    jsonb_set(payload,'{plan,source,planning_history,supported}','false'),2,actor,'ADAPT',1,checkpoint,true);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Unsupported estimated history activated';
 denied := false;
 begin
  perform public.save_onflows_management_plan(athlete,
    jsonb_set(payload,'{plan,source,readiness_basis}','"UNKNOWN"'),2,actor,'ADAPT',1,checkpoint,true);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Unknown history activated';
 assert not onflows_private.management_plan_eligible('{"activation_eligible":"true","source":{"readiness_known":true}}'), 'String boolean accepted';
 denied := false;
 begin
 result := public.save_onflows_management_plan(athlete,
    jsonb_set(payload,'{plan,source,generation_id}','"stale"'),2,actor,'ADAPT',1,checkpoint,true);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Stale generation activated';
end $$;

do $$
declare athlete text := 'checkpoint-test-athlete'; t1 uuid := '99000000-0000-0000-0000-000000000011';
 t2 uuid := '99000000-0000-0000-0000-000000000012'; r jsonb;
begin
 assert not has_table_privilege('service_role','onflows_private.planning_computations','UPDATE');
 assert not has_function_privilege('authenticated','public.onflows_planning_computation(text,text,uuid,text,jsonb)','EXECUTE');
 assert not (select prosecdef from pg_proc where oid='public.onflows_planning_computation(text,text,uuid,text,jsonb)'::regprocedure);
 r := public.onflows_planning_computation(athlete,repeat('a',64),t1,'CLAIM');
 assert r->>'state'='OWNED';
 assert public.onflows_planning_computation(athlete,repeat('a',64),t2,'CLAIM')->>'state'='BUSY';
 assert public.onflows_planning_computation(athlete,repeat('b',64),t2,'CLAIM')->>'state'='BUSY';
 assert public.onflows_planning_computation(athlete,repeat('a',64),t2,'COMPLETE','{}')->>'state'='LOST';
 assert public.onflows_planning_computation(athlete,repeat('a',64),t1,'COMPLETE','{"review_required":true}')->>'state'='READY';
 r := public.onflows_planning_computation(athlete,repeat('a',64),t2,'CLAIM');
 assert r#>>'{result,review_required}'='true';
 assert public.onflows_planning_computation(athlete,repeat('b',64),t2,'CLAIM')->>'state'='OWNED';
 assert public.onflows_planning_computation(athlete,repeat('a',64),t1,'COMPLETE','{}')->>'state'='LOST';
 assert public.onflows_planning_computation(athlete,repeat('b',64),t2,'FAIL')->>'state'='FAILED';
 assert public.onflows_planning_computation(athlete,repeat('b',64),t1,'CLAIM')->>'state'='OWNED';
end $$;
reset role;
-- Simulate a dead process, then prove the expired token is fenced.
update onflows_private.planning_computations set lease_until=clock_timestamp()-interval '1 second'
 where athlete_alias='checkpoint-test-athlete';
set local role service_role;
do $$
begin
 assert public.onflows_planning_computation('checkpoint-test-athlete',repeat('b',64),
  '99000000-0000-0000-0000-000000000012','CLAIM')->>'state'='OWNED';
 assert public.onflows_planning_computation('checkpoint-test-athlete',repeat('b',64),
  '99000000-0000-0000-0000-000000000011','COMPLETE','{}')->>'state'='LOST';
end $$;
rollback;
