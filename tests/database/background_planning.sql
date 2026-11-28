-- Synthetic fixtures only; every mutation rolls back. Safe to rerun in staging.
begin;
insert into auth.users(id) values ('99001000-0000-0000-0000-000000000001'),('99001000-0000-0000-0000-000000000002'),('99001000-0000-0000-0000-000000000003');
insert into public.onflows_profiles(user_id,display_name) values
 ('99001000-0000-0000-0000-000000000001','Background contract owner'),
 ('99001000-0000-0000-0000-000000000002','Background contract coach'),
 ('99001000-0000-0000-0000-000000000003','Background contract outsider');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('async-contract-athlete','async-contract-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('async-contract-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 values('99001000-0000-0000-0000-000000000001','async-contract-athlete',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,edit_plan,view_plan)
 values('99001000-0000-0000-0000-000000000001','99001000-0000-0000-0000-000000000002',true,true);

do $$
declare f regprocedure; r text;
begin
 foreach f in array array[
  'public.onflows_planning_job(text,text,uuid,jsonb,uuid,uuid,text,jsonb)'::regprocedure,
  'public.onflows_load_projection(text,text,jsonb)'::regprocedure] loop
  assert not (select prosecdef from pg_proc where oid=f), 'Public RPC must be security invoker';
  assert has_function_privilege('service_role',f,'EXECUTE');
  foreach r in array array['anon','authenticated'] loop
   assert not has_function_privilege(r,f,'EXECUTE'), 'Browser role can execute protected RPC';
  end loop;
 end loop;
 foreach r in array array['anon','authenticated','service_role'] loop
  assert not has_table_privilege(r,'onflows_private.planning_jobs','SELECT');
  assert not has_table_privilege(r,'onflows_private.load_projection_cache','SELECT');
 end loop;
 assert (select relrowsecurity from pg_class where oid='onflows_private.planning_jobs'::regclass);
 assert (select relrowsecurity from pg_class where oid='onflows_private.load_projection_cache'::regclass);
end $$;

set local role service_role;
do $$
declare a constant text := 'async-contract-athlete';
 owner_id constant uuid := '99001000-0000-0000-0000-000000000001';
 coach_id constant uuid := '99001000-0000-0000-0000-000000000002';
 outsider_id constant uuid := '99001000-0000-0000-0000-000000000003';
 req jsonb; j jsonb; claim jsonb; saved jsonb; old_job uuid; token1 uuid:=gen_random_uuid(); token2 uuid:=gen_random_uuid(); denied boolean;
 payload jsonb;
begin
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{}',0,owner_id);
 req := jsonb_build_object('start_date',(clock_timestamp() at time zone 'UTC')::date,
  'expected_profile_revision',1,'expected_draft_revision',0);
 denied:=false;
 begin perform public.onflows_planning_job('ENQUEUE',a,outsider_id,req);
 exception when insufficient_privilege then denied:=true; end;
 assert denied,'Outsider enqueued a plan';
 denied:=false;
 begin perform public.onflows_planning_job('ENQUEUE',a,owner_id,req||'{"start_date":null}');
 exception when invalid_parameter_value then denied:=true; end;
 assert denied,'Null date accepted';
 j:=public.onflows_planning_job('ENQUEUE',a,coach_id,req);
 assert j->>'status'='QUEUED'; old_job:=(j->>'job_id')::uuid;
 assert public.onflows_planning_job('ENQUEUE',a,coach_id,req)->>'job_id'=old_job::text,'Duplicate enqueue';
 assert public.onflows_planning_job('ENQUEUE',a,owner_id,req)->>'reason'='JOB_IN_PROGRESS';
 assert public.onflows_planning_job('STATUS',a,coach_id)->'job'->>'job_id'=old_job::text;
 claim:=public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token1);
 assert claim->>'job_id'=old_job::text;
 assert public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token2) is null,'Live lease stolen';
 assert public.onflows_planning_job('RENEW',p_job=>old_job,p_token=>token2)->>'state'='LOST';
 payload:=jsonb_build_object('start_date',req->>'start_date','source',jsonb_build_object('generation_id',null),
   'input_snapshot',jsonb_build_object('response_revisions','[]'::jsonb));
 assert public.onflows_planning_job('COMPLETE',p_job=>old_job,p_token=>token2,p_payload=>payload)->>'state'='LOST';
 assert public.onflows_planning_job('RENEW',p_job=>old_job,p_token=>token1,p_stage=>'SEARCH')->>'stage'='SEARCH';
 saved:=public.onflows_planning_job('COMPLETE',p_job=>old_job,p_token=>token1,p_payload=>payload);
 assert saved->>'status'='SUCCEEDED' and saved->>'result_revision'='1';
 assert public.onflows_planning_job('COMPLETE',p_job=>old_job,p_token=>token1,p_payload=>payload)=saved,'Completion retry changed receipt';
 assert public.onflows_planning_job('ENQUEUE',a,coach_id,req)->>'job_id'=old_job::text,'Lost HTTP response duplicated publication';
 assert public.onflows_planning_job('FAIL',p_job=>old_job,p_token=>token1,p_stage=>'COMPUTATION_FAILED')->>'status'='SUCCEEDED';
 req:=req||'{"expected_draft_revision":1}';
 j:=public.onflows_planning_job('ENQUEUE',a,coach_id,req);
 assert j->>'job_id'<>old_job::text;
 old_job:=(j->>'job_id')::uuid;
 perform public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token2);
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"changed":true}',1,owner_id);
 saved:=public.onflows_planning_job('COMPLETE',p_job=>old_job,p_token=>token2,p_payload=>payload);
 assert saved->>'status'='FAILED' and saved->>'error_code'='INPUT_CHANGED','Stale profile published';
 req:=req||'{"expected_profile_revision":2}';
 j:=public.onflows_planning_job('ENQUEUE',a,coach_id,req);
 old_job:=(j->>'job_id')::uuid;
 perform public.onflows_planning_job('CLAIM',p_alias=>a,p_token=>token1);
 -- Settings can change independently of the management profile revision.
 reset role;
 update public.onflows_athlete_settings set hrmax_bpm=191 where athlete_alias=a;
 set local role service_role;
 saved:=public.onflows_planning_job('COMPLETE',p_job=>old_job,p_token=>token1,p_payload=>payload);
 assert saved->>'status'='FAILED' and saved->>'error_code'='INPUT_CHANGED','Changed settings published';
end $$;

reset role;
do $$ begin
 assert (select count(*) from onflows_private.planning_jobs where athlete_alias='async-contract-athlete')=1;
 assert (select count(*) from public.onflows_management_entries where athlete_alias='async-contract-athlete' and kind='DRAFT')=1;
end $$;
set local role service_role;
do $$
declare req jsonb; j jsonb; job_id uuid; token uuid:=gen_random_uuid(); key1 text:=repeat('a',64); key2 text:=repeat('b',64); payload jsonb;
begin
 req:=jsonb_build_object('start_date',(clock_timestamp() at time zone 'UTC')::date,
  'expected_profile_revision',2,'expected_draft_revision',1);
 j:=public.onflows_planning_job('ENQUEUE','async-contract-athlete','99001000-0000-0000-0000-000000000002',req);
 job_id:=(j->>'job_id')::uuid;
 perform public.onflows_planning_job('CLAIM',p_alias=>'async-contract-athlete',p_token=>token);
 perform public.onflows_planning_job('FAIL',p_job=>job_id,p_token=>token,p_stage=>'TEMPORARILY_UNAVAILABLE');
 assert public.onflows_planning_job('STATUS','async-contract-athlete','99001000-0000-0000-0000-000000000001')->'job'->>'status'='QUEUED';
 assert public.onflows_planning_job('CLAIM',p_alias=>'async-contract-athlete',p_token=>gen_random_uuid()) is null,'Retry backoff ignored';
 payload:=jsonb_build_object('load_selection',jsonb_build_object('fingerprint',key1),'activities','[]'::jsonb);
 assert public.onflows_load_projection('async-contract-athlete',key1) is null;
 assert public.onflows_load_projection('async-contract-athlete',key2,payload)->>'stored'='false';
 assert public.onflows_load_projection('async-contract-athlete',key1,payload)->>'stored'='true';
 assert public.onflows_load_projection('async-contract-athlete',key1)=payload;
 assert public.onflows_load_projection('async-contract-athlete',key2) is null,'Wrong cache key reused';
 payload:=jsonb_set(payload,'{load_selection,fingerprint}',to_jsonb(key2));
 perform public.onflows_load_projection('async-contract-athlete',key2,payload);
 assert public.onflows_load_projection('async-contract-athlete',key1) is null,'Old cache version retained';
end $$;
reset role;
update onflows_private.planning_jobs set status='RUNNING',attempts=1,lease_until=clock_timestamp()-interval '1 second'
 where athlete_alias='async-contract-athlete';
do $$
declare token uuid:=gen_random_uuid(); j jsonb; job uuid; payload jsonb;
begin
 j:=public.onflows_planning_job('CLAIM',p_alias=>'async-contract-athlete',p_token=>token);
 assert j is not null,'Expired claim not recovered'; job:=(j->>'job_id')::uuid;
 update public.onflows_sharing_grants set edit_plan=false where owner_user_id='99001000-0000-0000-0000-000000000001'
  and viewer_user_id='99001000-0000-0000-0000-000000000002';
 payload:=jsonb_build_object('start_date',j->'request'->>'start_date','source',jsonb_build_object('generation_id',null));
 j:=public.onflows_planning_job('COMPLETE',p_job=>job,p_token=>token,p_payload=>payload);
 assert j->>'status'='FAILED' and j->>'error_code'='ACCESS_CHANGED','Revoked coach published';
 assert (select count(*) from onflows_private.load_projection_cache where athlete_alias='async-contract-athlete')=1;
 update onflows_private.load_projection_cache set expires_at=clock_timestamp()-interval '1 second' where athlete_alias='async-contract-athlete';
 assert public.onflows_load_projection('async-contract-athlete',repeat('b',64)) is null,'Expired cache reused';
end $$;
rollback;
