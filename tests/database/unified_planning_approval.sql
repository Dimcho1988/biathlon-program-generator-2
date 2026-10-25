-- Real publication with synthetic data; the entire transaction rolls back.
begin;

do $$
declare policy text; plan jsonb; path text[];
begin
 assert onflows_private.management_plan_eligible(
   '{"activation_eligible":true,"source":{"readiness_known":true}}');
 assert not onflows_private.management_plan_eligible(null);
 foreach policy in array array['RECORDED_DURATION_EXPERT_Q_WITH_RECOVERY',
   'UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES'] loop
   plan := jsonb_build_object('activation_eligible',true,'source',jsonb_build_object(
     'readiness_known',false,'readiness_basis','ESTIMATED_LOAD','planning_history_supported',true,
     'planning_history',jsonb_build_object('supported',true,'estimated',true,'policy',policy)));
   assert onflows_private.management_plan_eligible(plan), 'Supported estimate rejected: ' || policy;
   foreach path slice 1 in array array[
     array['activation_eligible',null,null],array['source','planning_history_supported',null],
     array['source','planning_history','supported'],array['source','planning_history','estimated']
   ] loop
     path := array_remove(path,null);
     assert not onflows_private.management_plan_eligible(jsonb_set(plan,path,'false'));
     assert not onflows_private.management_plan_eligible(jsonb_set(plan,path,'"true"'));
     assert not onflows_private.management_plan_eligible(plan #- path);
   end loop;
   assert not onflows_private.management_plan_eligible(jsonb_set(plan,'{source,readiness_basis}','"UNKNOWN"'));
   assert not onflows_private.management_plan_eligible(jsonb_set(plan,'{source,planning_history,policy}','"UNRECOGNIZED"'));
 end loop;
 assert not has_function_privilege('anon','onflows_private.management_plan_eligible(jsonb)','EXECUTE');
 assert not has_function_privilege('authenticated','onflows_private.management_plan_eligible(jsonb)','EXECUTE');
 assert has_function_privilege('service_role','onflows_private.management_plan_eligible(jsonb)','EXECUTE');
 assert not (select prosecdef from pg_proc where oid='onflows_private.management_plan_eligible(jsonb)'::regprocedure);
end $$;

insert into auth.users(id) values ('99000000-0000-0000-0000-000000000041');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000041','Synthetic unified approval owner');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('unified-approval-test-athlete','unified-approval-test-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('unified-approval-test-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000041','unified-approval-test-athlete',true);

set local role service_role;
do $$
declare
 athlete constant text := 'unified-approval-test-athlete';
 actor constant uuid := '99000000-0000-0000-0000-000000000041';
 checkpoint jsonb; fixed jsonb; pending jsonb; approved jsonb; proposal jsonb; result jsonb; denied boolean;
begin
 perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1',
   '{"adaptation_mode":"REVIEW"}',0,actor);
 checkpoint := public.onflows_management_checkpoint(athlete);
 fixed := jsonb_build_object('status','ACTIVE','mode','REVIEW','approved_settings_fingerprint','fixed-rules',
   'evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,'approved_by',actor,'approved_profile_revision',1,
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object(
     'generation_id',null,'readiness_known',true)));
 result := public.save_onflows_management_plan_compact(athlete,fixed,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->>'saved'='true' and result->>'revision'='1';

 perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1',
   '{"adaptation_mode":"AUTO"}',1,actor);
 checkpoint := public.onflows_management_checkpoint(athlete);
 proposal := jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,
   'readiness_known',false,'planning_history_supported',true,'readiness_basis','ESTIMATED_LOAD',
   'planning_history',jsonb_build_object('supported',true,'estimated',true,
     'policy','UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES')));
 pending := fixed || jsonb_build_object('status','REVIEW_REQUIRED','proposal',proposal,'proposal_mode','AUTO');
 result := public.save_onflows_management_plan_compact(athlete,pending,1,actor,'REVIEW',2,checkpoint,false);
 assert result->>'saved'='true' and result->>'revision'='2';
 assert (select payload->>'mode'='REVIEW' from public.onflows_management_plan_revisions
   where athlete_alias=athlete and revision=2), 'Pending mode changed the approved week';

 approved := pending || jsonb_build_object('status','ACTIVE','mode','AUTO','plan',proposal,'proposal',null,
   'approved_profile_revision',2,'approved_settings_fingerprint','adaptive-rules');
 denied := false;
 begin
   perform public.save_onflows_management_plan_compact(athlete,
     jsonb_set(approved,'{plan,source,planning_history,supported}','false'),2,actor,'APPROVE',2,checkpoint,false);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Unsupported estimate was approved';
 result := public.save_onflows_management_plan_compact(athlete,approved,2,actor,'APPROVE',2,
   checkpoint || '{"generation_id":"changed-input"}'::jsonb,false);
 assert result->>'reason'='INPUTS_CHANGED', 'Changed inputs were accepted';

 result := public.save_onflows_management_plan_compact(athlete,approved,2,actor,'APPROVE',2,checkpoint,false);
 assert result->>'saved'='true' and result->>'revision'='3', 'Compact approval rejected the unified estimate';
 assert not result ? 'payload', 'Compact acknowledgement echoed private plan data';
 assert (select payload=approved from public.onflows_management_plan_revisions where athlete_alias=athlete and revision=3);
 assert (select payload=fixed from public.onflows_management_plan_revisions where athlete_alias=athlete and revision=1);
 result := public.save_onflows_management_plan(athlete,approved,3,actor,'ADAPT',2,checkpoint,true);
 assert result->>'saved'='true' and result->'payload'=approved, 'Full save disagrees with compact save';
 assert (select status='ACTIVE' and revision=4 from public.onflows_management_plan_state where athlete_alias=athlete);
end $$;
reset role;
rollback;
