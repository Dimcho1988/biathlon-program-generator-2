-- Synthetic publication fixtures only. The complete test transaction rolls back.
begin;
set local plpgsql.check_asserts=on;

insert into auth.users(id) values
 ('99000000-0000-0000-0000-000000000021'),
 ('99000000-0000-0000-0000-000000000022');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000021','Queued day edit test owner'),
 ('99000000-0000-0000-0000-000000000022','Queued day edit test outsider');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('queued-day-test-athlete','queued-day-test-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('queued-day-test-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000021','queued-day-test-athlete',true);

set local role service_role;
do $$
declare
 a constant text := 'queued-day-test-athlete';
 actor constant uuid := '99000000-0000-0000-0000-000000000021';
 outsider constant uuid := '99000000-0000-0000-0000-000000000022';
 day date := (clock_timestamp() at time zone 'UTC')::date;
 checkpoint jsonb; active jsonb; req jsonb; job uuid; token uuid := gen_random_uuid();
 publication jsonb; receipt jsonb; denied boolean := false;
begin
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{}',0,actor);
 checkpoint := public.onflows_management_checkpoint(a);
 active := jsonb_build_object('status','ACTIVE','mode','AUTO','approved_settings_fingerprint','rules',
   'evaluated_on',day,'approved_by',actor,'approved_profile_revision',1,
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,'readiness_known',true)));
 perform public.save_onflows_management_plan_compact(a,active,0,actor,'ACTIVATE',1,checkpoint);
 req := jsonb_build_object('kind','DAY','date',day,'action','REST','note','','expected_revision',1);
 begin perform public.onflows_planning_job('ENQUEUE',a,outsider,req);
 exception when insufficient_privilege then denied := true; end;
 assert denied;
 receipt := public.onflows_planning_job('ENQUEUE',a,actor,req);
 job := (receipt->>'job_id')::uuid;
 assert receipt->>'kind'='DAY' and receipt->>'status'='QUEUED';
 assert public.onflows_planning_job('ENQUEUE',a,actor,req)=receipt;
 assert public.onflows_planning_job('ENQUEUE',a,actor,req||'{"action":"SKIP"}')->>'conflict'='true';
 assert public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token)->'request'=req;
 assert public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>gen_random_uuid()) is null;
 publication := jsonb_build_object('operation','DAY','profile_revision',1,'checkpoint',public.onflows_management_checkpoint(a),
  'day_plan',active || jsonb_build_object('decisions',jsonb_build_object(day::text,jsonb_build_object('action','REST','note',''))));
 assert public.onflows_planning_job('COMPLETE',p_job=>job,p_token=>gen_random_uuid(),p_payload=>publication)->>'state'='LOST';
 receipt := public.onflows_planning_job('COMPLETE',p_job=>job,p_token=>token,p_payload=>publication);
 assert receipt->>'status'='SUCCEEDED' and receipt->>'result_active_revision'='2' and receipt->>'published'='true';
 assert public.onflows_planning_job('COMPLETE',p_job=>job,p_token=>token,p_payload=>publication)=receipt;
 assert public.onflows_planning_job('ENQUEUE',a,actor,req)=receipt;
 assert public.onflows_planning_job('ENQUEUE',a,actor,req||'{"action":"CLEAR"}')->>'conflict'='true';
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=a)=2;
 req := req || '{"action":"CLEAR","expected_revision":2}';
 receipt := public.onflows_planning_job('ENQUEUE',a,actor,req);
 job := (receipt->>'job_id')::uuid;
 perform public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token);
 perform public.save_onflows_management_plan_compact(a,active||'{"status":"PAUSED"}',2,actor,'PAUSE',1,public.onflows_management_checkpoint(a));
 receipt := public.onflows_planning_job('COMPLETE',p_job=>job,p_token=>token,p_payload=>publication);
 assert receipt->>'status'='FAILED' and receipt->>'error_code'='INPUT_CHANGED';
 assert (select revision=3 and status='PAUSED' from public.onflows_management_plan_state where athlete_alias=a);
end $$;
rollback;
