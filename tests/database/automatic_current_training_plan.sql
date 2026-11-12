-- Synthetic fixtures only. Generated plans, current pointers and jobs roll back.
begin;
set local plpgsql.check_asserts='on';
insert into auth.users(id) values
 ('99003000-0000-0000-0000-000000000001'),('99003000-0000-0000-0000-000000000002'),('99003000-0000-0000-0000-000000000003');
insert into public.onflows_profiles(user_id,display_name) values
 ('99003000-0000-0000-0000-000000000001','Automatic plan owner'),
 ('99003000-0000-0000-0000-000000000002','Automatic plan coach'),
 ('99003000-0000-0000-0000-000000000003','Automatic plan outsider');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('automatic-plan-test','automatic-plan-provider','synthetic-not-a-token','REVOKED'),
 ('automatic-bootstrap-test','automatic-bootstrap-provider','synthetic-not-a-token','REVOKED'),
 ('automatic-daily-test','automatic-daily-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 select a,array[100,120,140,155,170,190],190,'UTC' from unnest(array['automatic-plan-test','automatic-bootstrap-test','automatic-daily-test']) a;
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 select '99003000-0000-0000-0000-000000000001',a,true from unnest(array['automatic-plan-test','automatic-bootstrap-test','automatic-daily-test']) a;
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,edit_plan,view_plan)
 values('99003000-0000-0000-0000-000000000001','99003000-0000-0000-0000-000000000002',true,true);

do $$ declare f regprocedure; r text; begin
 foreach f in array array[
  'public.save_onflows_generated_plan(text,jsonb,integer,uuid,integer,jsonb)'::regprocedure,
  'public.queue_onflows_unpublished_plans(integer)'::regprocedure] loop
  assert not (select prosecdef from pg_proc where oid=f),'Public RPC must be security invoker';
  assert has_function_privilege('service_role',f,'EXECUTE');
  foreach r in array array['anon','authenticated'] loop
   assert not has_function_privilege(r,f,'EXECUTE'),'Browser can invoke a server-only planning RPC';
  end loop;
 end loop;
end $$;

set local role service_role;
do $$
declare a constant text:='automatic-plan-test'; owner_id uuid:='99003000-0000-0000-0000-000000000001';
 coach_id uuid:='99003000-0000-0000-0000-000000000002'; outsider_id uuid:='99003000-0000-0000-0000-000000000003';
 day text:=(clock_timestamp() at time zone 'UTC')::date::text; checkpoint jsonb; draft jsonb; active jsonb;
 publication jsonb; result jsonb; denied boolean; j jsonb; job_id uuid; token uuid:=gen_random_uuid();
begin
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"adaptation_mode":"REVIEW"}',0,owner_id);
 checkpoint:=public.onflows_management_checkpoint(a);
 draft:=jsonb_build_object('start_date',day,'activation_eligible',true,'review_required',false,
  'source',jsonb_build_object('generation_id',null,'readiness_known',true),
  'input_snapshot',jsonb_build_object('response_revisions','[]'::jsonb));
 active:=jsonb_build_object('status','ACTIVE','mode','REVIEW','approved_settings_fingerprint','initial-rules',
  'evaluated_on',day,'approved_by',owner_id,'approved_profile_revision',1,'plan',draft,
  'origin',jsonb_build_object('start_date',day,'draft_revision',1),
  'decisions',jsonb_build_object('today','rest'),'outcomes',jsonb_build_array(jsonb_build_object('date','2000-01-01','completed',true)));
 publication:=jsonb_build_object('payload',active,'expected_revision',0,'checkpoint',checkpoint);
 result:=public.save_onflows_generated_plan(a,draft,0,owner_id,1,publication);
 assert result->>'saved'='true' and result->>'revision'='1' and result->>'active_revision'='1';
 assert result->>'published'='true','A valid generated plan still requires approval';
 assert (select status='ACTIVE' from public.onflows_management_plan_state where athlete_alias=a);
 result:=public.save_onflows_generated_plan(a,draft,0,owner_id,1,publication);
 assert result->>'saved'='true' and result->>'active_revision'='1','Lost-response retry failed';
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=a)=1,'Retry duplicated current history';
 assert (select count(*) from public.onflows_management_entries where athlete_alias=a and kind='DRAFT')=1,'Retry duplicated draft history';

 -- Saving a draft and then losing the active CAS must roll back both writes.
 draft:=draft||'{"test_variant":2}'; active:=active||jsonb_build_object('plan',draft,
  'origin',jsonb_build_object('start_date',day,'draft_revision',2));
 publication:=publication||jsonb_build_object('payload',active);
 result:=public.save_onflows_generated_plan(a,draft,1,owner_id,1,publication);
 assert result->>'reason'='REVISION_CHANGED';
 assert (select count(*) from public.onflows_management_entries where athlete_alias=a and kind='DRAFT')=1,'Failed active CAS left a draft';
 publication:=publication||'{"expected_revision":1}';
 result:=public.save_onflows_generated_plan(a,draft,1,owner_id,1,
  jsonb_set(publication,'{checkpoint,generation_id}','"changed-input"'));
 assert result->>'reason'='INPUTS_CHANGED';
 assert (select count(*) from public.onflows_management_entries where athlete_alias=a and kind='DRAFT')=1,'Failed input fence left a draft';
 denied:=false;
 begin perform public.save_onflows_generated_plan(a,draft,1,outsider_id,1,publication);
 exception when insufficient_privilege then denied:=true; end;
 assert denied,'Outsider generated a current program';
 denied:=false;
 begin perform public.save_onflows_generated_plan(a,draft,1,owner_id,1,null);
 exception when invalid_parameter_value then denied:=true; end;
 assert denied,'Valid generated plan silently remained a draft';
 denied:=false;
 begin perform public.save_onflows_generated_plan(a,draft,1,owner_id,1,
  jsonb_set(publication,'{payload,origin,draft_revision}','99'));
 exception when invalid_parameter_value then denied:=true; end;
 assert denied,'Generated program claimed a different draft origin';
 denied:=false;
 begin perform public.save_onflows_generated_plan(a,jsonb_set(draft,'{activation_eligible}','false'),1,owner_id,1,
  jsonb_set(publication,'{payload,plan,activation_eligible}','false'));
 exception when invalid_parameter_value then denied:=true; end;
 assert denied,'Ineligible generated plan became active';
 assert (select count(*) from public.onflows_management_entries where athlete_alias=a and kind='DRAFT')=1;

 -- A newly authorized coach may replace a current plan and own later refreshes.
 active:=active||jsonb_build_object('approved_by',coach_id);
 publication:=publication||jsonb_build_object('payload',active);
 result:=public.save_onflows_generated_plan(a,draft,1,coach_id,1,publication);
 assert result->>'active_revision'='2';
 assert (select approved_by=coach_id from public.onflows_management_plan_state where athlete_alias=a);
 assert (select payload->'decisions'=active->'decisions' and payload->'outcomes'=active->'outcomes'
  from public.onflows_management_plan_revisions where athlete_alias=a and revision=2),'Coach history was reset';

 -- Current validated settings can update either mode without a second approval.
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',1,coach_id);
 checkpoint:=public.onflows_management_checkpoint(a);
 active:=active||jsonb_build_object('mode','AUTO','approved_profile_revision',2,'approved_settings_fingerprint','updated-rules');
 result:=public.save_onflows_management_plan_compact(a,active,2,coach_id,'ADAPT',2,checkpoint,true);
 assert result->>'saved'='true' and result->>'revision'='3','Current saved profile required repeat approval';
 assert (select payload->>'approved_settings_fingerprint'='initial-rules'
  from public.onflows_management_plan_revisions where athlete_alias=a and revision=1),'Past revision rewritten';

 -- Explicit pauses cannot be removed by generation, nor by automatic refresh.
 active:=active||'{"status":"PAUSED"}';
 perform public.save_onflows_management_plan_compact(a,active,3,coach_id,'PAUSE',2,checkpoint,false);
 result:=public.save_onflows_generated_plan(a,draft,2,coach_id,2,null);
 assert result->>'saved'='true' and result->>'published'='false';
 assert (select status='PAUSED' and revision=4 from public.onflows_management_plan_state where athlete_alias=a);
 active:=active||'{"status":"ACTIVE"}'; publication:=jsonb_build_object('payload',
  active||jsonb_build_object('origin',jsonb_build_object('start_date',day,'draft_revision',4)),'expected_revision',4,'checkpoint',checkpoint);
 result:=public.save_onflows_generated_plan(a,draft,3,coach_id,2,publication);
 assert result->>'reason'='PLAN_PAUSED';
 denied:=false;
 begin perform public.save_onflows_management_plan_compact(a,active,4,coach_id,'ADAPT',2,checkpoint,true);
 exception when insufficient_privilege then denied:=true; end;
 assert denied,'Automatic work resumed a pause';
 perform public.save_onflows_management_plan_compact(a,active,4,coach_id,'RESUME',2,checkpoint,false);
 active:=active||'{"status":"COMPLETED"}';
 perform public.save_onflows_management_plan_compact(a,active,5,coach_id,'COMPLETE',2,checkpoint,false);
 active:=active||'{"status":"ACTIVE"}';
 denied:=false;
 begin perform public.save_onflows_management_plan_compact(a,active,6,coach_id,'ADAPT',2,checkpoint,true);
 exception when insufficient_privilege then denied:=true; end;
 assert denied,'Automatic work restarted a completed program';
 active:=active||jsonb_build_object('origin',jsonb_build_object('start_date',day,'draft_revision',4));
 publication:=jsonb_build_object('payload',active,'expected_revision',6,'checkpoint',checkpoint);
 result:=public.save_onflows_generated_plan(a,draft,3,coach_id,2,publication);
 assert result->>'active_revision'='7','Explicit new valid program could not replace completed program';

 -- Durable completion uses the same atomic helper and reports current revision.
 j:=public.onflows_planning_job('ENQUEUE',a,coach_id,jsonb_build_object('start_date',day,
  'expected_profile_revision',2,'expected_draft_revision',4)); job_id:=(j->>'job_id')::uuid;
 perform public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token);
 draft:=draft||'{"test_variant":3}'; active:=active||jsonb_build_object('plan',draft,
  'origin',jsonb_build_object('start_date',day,'draft_revision',5));
 publication:=jsonb_build_object('payload',active,'expected_revision',7,'checkpoint',checkpoint);
 denied:=false;
 begin perform public.onflows_planning_job('COMPLETE',p_job=>job_id,p_token=>token,
  p_payload=>jsonb_build_object('draft',draft||jsonb_build_object('start_date',(day::date+1)::text),'publication',publication));
 exception when invalid_parameter_value then denied:=true; end;
 assert denied,'Worker completed a different requested date';
 assert (select count(*) from public.onflows_management_entries where athlete_alias=a and kind='DRAFT')=4,
  'Wrong-day completion wrote an artifact';

 result:=public.onflows_planning_job('COMPLETE',p_job=>job_id,p_token=>token,
  p_payload=>jsonb_build_object('draft',draft,'publication',publication));
 assert result->>'status'='SUCCEEDED' and result->>'result_revision'='5'
  and result->>'result_active_revision'='8' and result->>'published'='true';
 assert public.onflows_planning_job('COMPLETE',p_job=>job_id,p_token=>token,
  p_payload=>jsonb_build_object('draft',draft,'publication',publication))=result,'Job replay changed receipt';
end $$;

-- Legacy draft-only profiles get fresh work once, never blind promotion or loops.
do $$ declare a constant text:='automatic-bootstrap-test'; actor uuid:='99003000-0000-0000-0000-000000000001';
 day text:=(clock_timestamp() at time zone 'UTC')::date::text; draft jsonb; result jsonb; job_id uuid; token uuid:=gen_random_uuid();
begin
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{}',0,actor);
 draft:=jsonb_build_object('start_date',day,'activation_eligible',false,
  'source',jsonb_build_object('generation_id',null,'readiness_known',false),
  'input_snapshot',jsonb_build_object('response_revisions','[]'::jsonb));
 perform public.save_onflows_management_entry(a,'DRAFT',day,draft,0,actor,1,null,true,'[]');
 assert public.queue_onflows_unpublished_plans()->>'queued'='1','Legacy draft was not queued for fresh computation';
 assert public.queue_onflows_unpublished_plans()->>'queued'='0','In-progress bootstrap duplicated';
 result:=public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token); job_id:=(result->>'job_id')::uuid;
 result:=public.onflows_planning_job('COMPLETE',p_job=>job_id,p_token=>token,
  p_payload=>jsonb_build_object('draft',draft,'publication',null));
 assert result->>'status'='SUCCEEDED' and result->>'published'='false' and result->'result_active_revision'='null';
 assert not exists(select 1 from public.onflows_management_plan_state where athlete_alias=a),'Invalid bootstrap became current';
 assert public.queue_onflows_unpublished_plans()->>'queued'='0','Unchanged invalid result caused endless regeneration';
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"new_input":true}',1,actor);
 assert public.queue_onflows_unpublished_plans()->>'queued'='1','New profile did not permit fresh bootstrap';
end $$;
-- Daily and input-change refreshes are reachable in fixed-horizon mode too.
do $$ declare a constant text:='automatic-daily-test'; actor uuid:='99003000-0000-0000-0000-000000000001';
 day text:=(clock_timestamp() at time zone 'UTC')::date::text; active jsonb; checkpoint jsonb; result jsonb; token uuid:=gen_random_uuid();
begin
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"adaptation_mode":"REVIEW"}',0,actor);
 checkpoint:=public.onflows_management_checkpoint(a);
 active:=jsonb_build_object('status','ACTIVE','mode','REVIEW','approved_settings_fingerprint','fixed',
  'evaluated_on',day,'approved_by',actor,'approved_profile_revision',1,
  'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,'readiness_known',true)));
 perform public.save_onflows_management_plan_compact(a,active,0,actor,'ACTIVATE',1,checkpoint,false);
 reset role;
 insert into onflows_private.training_plan_updates(athlete_alias,requested_revision,completed_revision,last_evaluated_on)
  values(a,1,1,day::date-1);
 set local role service_role;
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result->>'revision'='2','Fixed-horizon plan did not get its next-day evaluation';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null,'Unchanged day requeued itself';
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"adaptation_mode":"REVIEW","new_goal":true}',1,actor);
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result->>'revision'='3','Profile edit did not request canonical refresh';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 reset role;
 update public.onflows_athlete_settings set hrmax_bpm=191 where athlete_alias=a;
 set local role service_role;
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result->>'revision'='4','Settings edit did not request canonical refresh';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 checkpoint:=public.onflows_management_checkpoint(a);
 active:=active||jsonb_build_object('status','PAUSED','approved_profile_revision',2);
 perform public.save_onflows_management_plan_compact(a,active,1,actor,'PAUSE',2,checkpoint,false);
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',2,actor);
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null,'Input edit restarted paused preparation';
end $$;
reset role;
set constraints all immediate;
rollback;
