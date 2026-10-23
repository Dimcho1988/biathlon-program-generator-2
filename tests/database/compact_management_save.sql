-- Synthetic publication fixtures only. The complete test transaction rolls back.
begin;

insert into auth.users(id) values
 ('99000000-0000-0000-0000-000000000021'),
 ('99000000-0000-0000-0000-000000000022');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000021','Compact publication test owner'),
 ('99000000-0000-0000-0000-000000000022','Compact publication test outsider');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('compact-save-test-athlete','compact-save-test-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('compact-save-test-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000021','compact-save-test-athlete',true);

do $$
declare function_id regprocedure; role_name text;
begin
 foreach function_id in array array[
   'public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean)'::regprocedure,
   'public.save_onflows_management_plan_compact(text,jsonb,integer,uuid,text,integer,jsonb,boolean)'::regprocedure
 ] loop
   assert not (select prosecdef from pg_proc where oid=function_id), 'Public save RPC must remain security invoker';
   assert (select proconfig @> array['search_path=""'] from pg_proc where oid=function_id), 'Public RPC search path is not fixed';
   assert has_function_privilege('service_role',function_id,'EXECUTE'), 'Service cannot save';
   foreach role_name in array array['anon','authenticated'] loop
     assert not has_function_privilege(role_name,function_id,'EXECUTE'), 'Public role can save';
   end loop;
 end loop;
 assert (select count(*) from pg_proc where pronamespace='public'::regnamespace
   and proname='save_onflows_management_plan_compact')=1, 'Ambiguous compact PostgREST overload';
 assert not has_table_privilege('service_role','public.onflows_management_plan_revisions','INSERT'), 'Writer bypass granted';
end $$;

set local role service_role;
do $$
declare
 athlete constant text := 'compact-save-test-athlete';
 actor constant uuid := '99000000-0000-0000-0000-000000000021';
 outsider constant uuid := '99000000-0000-0000-0000-000000000022';
 checkpoint jsonb; active_payload jsonb; review_payload jsonb; result jsonb; denied boolean;
begin
 perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1','{}',0,actor);
 checkpoint := public.onflows_management_checkpoint(athlete);
 active_payload := jsonb_build_object('status','ACTIVE','mode','AUTO','approved_settings_fingerprint','same-rules',
   'evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,'approved_by',actor,'approved_profile_revision',1,
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,'readiness_known',true),
     'audit_evidence',jsonb_build_object('prescribed_seconds',40,'dose',1.0)));
 -- Original signature and full readback remain available to older clients.
 result := public.save_onflows_management_plan(athlete,active_payload,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->'payload'=active_payload and result->'saved'='true'::jsonb, 'Legacy full response changed';
 assert result->>'revision'='1', 'Unexpected initial publication revision';

 review_payload := active_payload || jsonb_build_object('status','REVIEW_REQUIRED',
   'proposal',jsonb_build_object('review_required',true,'audit_evidence',repeat('evidence',25000)));
 -- Omit the defaulted automatic flag just as a legacy-style client may do.
 result := public.save_onflows_management_plan_compact(athlete,review_payload,1,actor,'REVIEW',1,checkpoint);
 assert result->'saved'='true'::jsonb and result->>'revision'='2', 'Compact publication failed';
 assert not result ? 'payload', 'Compact save echoed the immutable payload';
 assert (select count(*) from jsonb_object_keys(result))=3, 'Unexpected compact acknowledgement fields';
 assert (result->>'recorded_at')::timestamptz=(select recorded_at from public.onflows_management_plan_revisions
     where athlete_alias=athlete and revision=2), 'Compact timestamp does not identify the stored revision';
 assert (select payload=review_payload from public.onflows_management_plan_revisions
   where athlete_alias=athlete and revision=2), 'Compact publication changed or lost evidence';
 assert (select revision=2 and status='REVIEW_REQUIRED' from public.onflows_management_plan_state
   where athlete_alias=athlete), 'Compact publication did not update the pointer';
 assert (select r.payload=active_payload from public.onflows_management_plan_revisions r
   where athlete_alias=athlete and revision=1), 'Earlier history was modified';
 assert octet_length(result::text)<200, 'Compact acknowledgement unexpectedly large';

 result := public.save_onflows_management_plan_compact(athlete,review_payload,1,actor,'REVIEW',1,checkpoint);
 assert result='{"conflict":true,"reason":"REVISION_CHANGED"}'::jsonb, 'Stale publication was accepted';
 result := public.save_onflows_management_plan_compact(athlete,review_payload,2,actor,'REVIEW',2,checkpoint);
 assert result='{"conflict":true,"reason":"PROFILE_CHANGED"}'::jsonb, 'Changed profile was accepted';
 result := public.save_onflows_management_plan_compact(athlete,review_payload,2,actor,'REVIEW',1,
   checkpoint || '{"generation_id":"changed-input"}'::jsonb);
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Changed input was accepted';
 result := public.save_onflows_management_plan_compact(athlete,
   jsonb_set(review_payload,'{evaluated_on}','"2000-01-01"'),2,actor,'REVIEW',1,checkpoint);
 assert result='{"conflict":true,"reason":"LOCAL_DATE_CHANGED"}'::jsonb, 'Stale local day was accepted';

 denied := false;
 begin
   perform public.save_onflows_management_plan_compact(athlete,review_payload,2,outsider,'REVIEW',1,checkpoint);
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Unauthorized actor used the compact wrapper';
 denied := false;
 begin
   perform public.save_onflows_management_plan_compact(athlete,
     jsonb_set(review_payload,'{approved_settings_fingerprint}','"changed-rules"'),2,actor,'REVIEW',1,checkpoint,true);
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Automatic compact publication changed approved rules';
 denied := false;
 begin
   perform public.save_onflows_management_plan_compact(athlete,
     jsonb_set(active_payload,'{plan,activation_eligible}','false'),2,actor,'ADAPT',1,checkpoint,true);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Ineligible compact publication became active';
 denied := false;
 begin
   perform public.save_onflows_management_plan_compact(athlete,
     jsonb_set(review_payload,'{proposal,padding}',to_jsonb(repeat('x',4*1024*1024))),
     2,actor,'REVIEW',1,checkpoint);
 exception when check_violation then denied := true; end;
 assert denied, 'Compact publication bypassed the storage size constraint';
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=athlete)=2,
   'Rejected compact publication left a partial revision';
 assert (select revision from public.onflows_management_plan_state where athlete_alias=athlete)=2,
   'Rejected compact publication changed the pointer';

 -- Rolling the application back to the old RPC still works after compact writes.
 result := public.save_onflows_management_plan(athlete,
   review_payload || '{"status":"PAUSED"}'::jsonb,2,actor,'PAUSE',1,checkpoint,false);
 assert result->>'revision'='3' and result->'payload'->>'status'='PAUSED', 'Legacy writer cannot follow compact writer';
end $$;

set local role authenticated;
do $$
declare denied boolean := false;
begin
 begin
   perform public.save_onflows_management_plan_compact('compact-save-test-athlete','{}',0,
     '99000000-0000-0000-0000-000000000021','ACTIVATE',1,'{}');
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Authenticated users can execute compact publication directly';
end $$;
reset role;
rollback;
