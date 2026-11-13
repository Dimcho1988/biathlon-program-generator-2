-- Synthetic data only. Every fixture, claim, publication and deletion rolls back.
begin;
insert into auth.users(id) values('99002000-0000-0000-0000-000000000001');
insert into public.onflows_profiles(user_id,display_name)
 values('99002000-0000-0000-0000-000000000001','Prepared dashboard contract');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('prepared-contract-athlete','prepared-contract-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('prepared-contract-athlete',array[100,120,140,155,170,190],190,'Europe/Sofia');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 values('99002000-0000-0000-0000-000000000001','prepared-contract-athlete',true);
insert into public.onflows_activity_catalog(activity_ref,athlete_alias,provider_activity_key,sport,elapsed_time_s,quality_status) values
 ('act_11111111111111111111111111111111','prepared-contract-athlete',repeat('1',64),'Run',3600,'valid'),
 ('act_22222222222222222222222222222222','prepared-contract-athlete',repeat('2',64),'Run',1800,'valid');
insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
 values('prepared-contract-athlete','PROFILE','management-profile-v1',1,'{}','99002000-0000-0000-0000-000000000001');
insert into public.onflows_training_snapshots(athlete_alias,payload,activated_at)
 values('prepared-contract-athlete',jsonb_build_object('schema_version','athlete-snapshot-v1',
  'training_status',jsonb_build_object('athlete_id','prepared-contract-athlete'),
  'load_history',jsonb_build_object('athlete_id','prepared-contract-athlete')),clock_timestamp());

do $$ declare f regprocedure; r text;
begin
 assert exists(select 1 from onflows_private.dashboard_dirty_events where athlete_alias='prepared-contract-athlete'),
  'New source was not queued before the first application read';
 assert not exists(select 1 from onflows_private.prepared_dashboards where athlete_alias='prepared-contract-athlete'),
  'Input publication touched the shared dashboard cache';
 assert onflows_private.fixed_plan_identity('{"days":[{"sessions":[{"dose":30}],"session":{"dose":30}}]}')=
  onflows_private.fixed_plan_identity('{"_storage":"primary-session-reference-v1","days":[{"sessions":[{"dose":30}],"session":{"$session_ref":0}}]}'),
  'Storage compaction changed fixed-plan identity';
 assert onflows_private.fixed_plan_identity('{"days":[{"sessions":[{"dose":30}],"session":{"dose":30}}]}')<>
  onflows_private.fixed_plan_identity('{"_storage":"primary-session-reference-v1","days":[{"sessions":[{"dose":31}],"session":{"$session_ref":0}}]}'),
  'A changed fixed dose became equivalent';
 foreach f in array array[
  'public.onflows_dashboard_preparation(text,text,uuid,bigint,date,text,jsonb)'::regprocedure,
  'public.onflows_training_plan_update(text,text,uuid)'::regprocedure,
  'public.save_onflows_management_plan_after_training(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid,bigint)'::regprocedure,
  'public.cleanup_onflows_prepared_data(text)'::regprocedure] loop
  assert not (select prosecdef from pg_proc where oid=f);
  assert has_function_privilege('service_role',f,'EXECUTE');
  foreach r in array array['anon','authenticated'] loop assert not has_function_privilege(r,f,'EXECUTE'); end loop;
 end loop;
 foreach r in array array['anon','authenticated','service_role'] loop
  assert not has_table_privilege(r,'onflows_private.prepared_dashboards','SELECT');
  assert not has_table_privilege(r,'onflows_private.training_plan_updates','SELECT');
  assert not has_table_privilege(r,'onflows_private.dashboard_dirty_events','SELECT');
  assert not has_function_privilege(r,'onflows_private.flush_dashboard_invalidations(text)','EXECUTE');
  assert not has_function_privilege(r,'onflows_private.dashboard_preparation_core_v1(text,text,uuid,bigint,date,text,jsonb)','EXECUTE');
 end loop;
 assert (select relrowsecurity from pg_class where oid='onflows_private.prepared_dashboards'::regclass);
 assert (select relrowsecurity from pg_class where oid='onflows_private.training_plan_updates'::regclass);
 assert (select relrowsecurity from pg_class where oid='onflows_private.dashboard_dirty_events'::regclass);
end $$;

do $$ declare a constant text:='prepared-contract-athlete'; key1 text:=repeat('a',64); key2 text:=repeat('b',64);
state jsonb; payload jsonb; token1 uuid:=gen_random_uuid(); token2 uuid:=gen_random_uuid(); claim jsonb; old_revision bigint;
begin
 select to_jsonb(s) into payload from public.active_onflows_analysis(a) s;
 state:=public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key1);
 assert state->>'ready'='false';
 assert public.onflows_dashboard_preparation('STORE',a,p_revision=>(state->>'revision')::bigint,
  p_day=>(state->>'day')::date,p_model_key=>key1,p_envelope=>payload)->>'stored'='true';
 assert public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key1)->'envelope'=payload;
 -- Repeated input writes append one private event per transaction without
 -- changing a ready queue row. The next consumer advances its revision once.
 old_revision:=(state->>'revision')::bigint;
 update public.onflows_athlete_settings set hrmax_bpm=hrmax_bpm+1 where athlete_alias=a;
 update public.onflows_athlete_settings set hrmax_bpm=hrmax_bpm+1 where athlete_alias=a;
 assert (select requested_revision from onflows_private.prepared_dashboards where athlete_alias=a)=old_revision,
  'Input writer changed the shared dashboard queue';
 assert (select count(*) from onflows_private.dashboard_dirty_events where athlete_alias=a)=1,
  'One transaction did not coalesce its dashboard events';
 state:=public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key1);
 assert state->>'ready'='false' and (state->>'revision')::bigint<>old_revision,
  'Dirty dashboard remained readable';
 assert not exists(select 1 from onflows_private.dashboard_dirty_events where athlete_alias=a),
  'Captured dashboard events were not consumed';
 assert public.onflows_dashboard_preparation('STORE',a,p_revision=>old_revision,
  p_day=>(state->>'day')::date,p_model_key=>key1,p_envelope=>payload)->>'stored'='false',
  'Consumed dirty events did not fence the old calculation';
 assert public.onflows_dashboard_preparation('STORE',a,p_revision=>(state->>'revision')::bigint,
  p_day=>(state->>'day')::date,p_model_key=>key1,p_envelope=>payload)->>'stored'='true';
 old_revision:=(state->>'revision')::bigint;
 delete from public.onflows_training_snapshots where athlete_alias=a;
 assert public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key1) is null,'Deleted source remained readable';
 insert into public.onflows_training_snapshots(athlete_alias,payload,activated_at)
  values(a,payload->'snapshot_payload',(payload->>'activated_at')::timestamptz);
 state:=public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key1);
 assert (state->>'revision')::bigint<>old_revision,'Recreated cache reused the deleted revision';
 assert public.onflows_dashboard_preparation('STORE',a,p_revision=>old_revision,
  p_day=>(state->>'day')::date,p_model_key=>key1,p_envelope=>payload)->>'stored'='false','Deleted/recreated source accepted an old calculation';
 assert public.onflows_dashboard_preparation('STORE',a,p_revision=>(state->>'revision')::bigint,
  p_day=>(state->>'day')::date,p_model_key=>key1,p_envelope=>payload)->>'stored'='true';
 update public.onflows_athlete_settings set hrmax_bpm=191 where athlete_alias=a;
 assert public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key1)->>'ready'='false';
 assert public.onflows_dashboard_preparation('STORE',a,p_revision=>(state->>'revision')::bigint,
  p_day=>(state->>'day')::date,p_model_key=>key1,p_envelope=>payload)->>'stored'='false','Changed settings reused';
 claim:=public.onflows_dashboard_preparation('CLAIM',a,token1,p_model_key=>key1);
 assert claim is not null;
 assert public.onflows_dashboard_preparation('CLAIM',a,token2,p_model_key=>key1) is null;
 assert public.onflows_dashboard_preparation('RENEW',a,token2)->>'state'='LOST';
 assert public.onflows_dashboard_preparation('STORE',a,token2,(claim->>'revision')::bigint,
  (claim->>'day')::date,key1,payload)->>'state'='LOST';
 assert public.onflows_dashboard_preparation('STORE',a,token1,(claim->>'revision')::bigint,
  (claim->>'day')::date,key1,payload)->>'stored'='true';
 -- A new release is prepared even when the daily timer is tomorrow.
 claim:=public.onflows_dashboard_preparation('CLAIM',a,token2,p_model_key=>key2);
 assert claim is not null,'New build waited until tomorrow';
 assert public.onflows_dashboard_preparation('STORE',a,token2,(claim->>'revision')::bigint,
  (claim->>'day')::date,key2,payload)->>'stored'='true';
 update onflows_private.prepared_dashboards set evaluated_on=evaluated_on-1,available_at=clock_timestamp() where athlete_alias=a;
 assert public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>key2)->>'ready'='false','Yesterday Recovery reused';
 claim:=public.onflows_dashboard_preparation('CLAIM',a,token1,p_model_key=>key2);
 assert claim is not null;
 update public.onflows_athlete_settings set timezone='UTC' where athlete_alias=a;
 assert public.onflows_dashboard_preparation('STORE',a,token1,(claim->>'revision')::bigint,
  (claim->>'day')::date,key2,payload)->>'stored'='false','Timezone change published';
 assert not exists(select 1 from onflows_private.training_plan_updates where athlete_alias=a),'Settings/day created a plan task';
end $$;

-- Activate six synthetic generations: empty, new activity, identical import,
-- wellness-only import, an unavailable activity, then its available recording.
do $$ declare a constant text:='prepared-contract-athlete'; job uuid:=gen_random_uuid(); g uuid; prior uuid;
 activity_set uuid; n integer; snapshot jsonb; payload jsonb; state jsonb; claim jsonb; token uuid:=gen_random_uuid(); denied boolean;
begin
 select s.payload into snapshot from public.onflows_training_snapshots s where s.athlete_alias=a;
 insert into public.onflows_sync_jobs(job_id,athlete_alias,job_kind,idempotency_key,request_sequence)
  values(job,a,'FULL_SYNC',repeat('d',64),1);
 for n in 1..6 loop
  g:=gen_random_uuid(); activity_set:=case when n=4 then prior else g end;
  if prior is not null then update public.onflows_analysis_generations set status='SUPERSEDED',superseded_at=clock_timestamp() where generation_id=prior; end if;
  insert into public.onflows_analysis_generations(generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
   base_revision,activity_set_generation_id,activated_revision,status,snapshot_schema_version,snapshot_hash,snapshot_payload,
   period_start,period_end,as_of,provenance,activity_count,ready_at,activated_at)
   values(g,a,job,n,'FULL_SYNC',1,0,activity_set,n,'ACTIVE','athlete-snapshot-v1',repeat('e',64),snapshot,
    current_date,current_date,current_date,'{}',case when n=1 then 0 else 1 end,clock_timestamp(),clock_timestamp());
  if n<>4 and n>=2 then
   insert into public.onflows_analysis_generation_activities(generation_id,athlete_alias,activity_ref,catalog_payload,payload_hash)
    values(g,a,'act_11111111111111111111111111111111','{"sport":"Run","elapsed_time_s":3600,"quality_status":"valid"}',repeat('f',64));
  end if;
  if n>=5 then
   insert into public.onflows_analysis_generation_activities(generation_id,athlete_alias,activity_ref,catalog_payload,payload_hash)
    values(g,a,'act_22222222222222222222222222222222',jsonb_build_object('sport','Run','elapsed_time_s',1800,
     'quality_status',case when n=5 then 'provider_missing' else 'valid' end),repeat('a',64));
  end if;
  insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision)
   values(a,g,n) on conflict(athlete_alias) do update set active_generation_id=excluded.active_generation_id,active_revision=excluded.active_revision;
  if n=1 then assert not exists(select 1 from onflows_private.training_plan_updates where athlete_alias=a);
  else assert (select requested_revision from onflows_private.training_plan_updates where athlete_alias=a)=case when n=6 then 2 else 1 end,
    'Unchanged/missing/wellness import duplicated a training event'; end if;
  prior:=g;
 end loop;
 claim:=public.onflows_training_plan_update('CLAIM',a,token);
 assert claim->>'revision'='2';
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null;
 assert public.save_onflows_management_plan_after_training(a,'{}',0,'99002000-0000-0000-0000-000000000001',
  'REVIEW',1,'{}',true,gen_random_uuid(),2)->>'conflict'='true','Lost worker published';
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,1,'ACTIVATE','99002000-0000-0000-0000-000000000001','{"mode":"REVIEW","plan":{"marker":"approved"}}');
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by)
  values(a,1,'ACTIVE','99002000-0000-0000-0000-000000000001');
 -- A REVIEW-mode change now reaches the canonical writer. Actual validation and
 -- current publication are exercised by automatic_current_training_plan.sql.
 update onflows_private.training_plan_updates set requested_revision=3 where athlete_alias=a;
 assert public.save_onflows_management_plan_after_training(a,'{}',1,'99002000-0000-0000-0000-000000000001',
  'REVIEW',1,'{}',true,token,2)->>'conflict'='true','New training did not fence the old calculation';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert (select completed_revision<requested_revision from onflows_private.training_plan_updates where athlete_alias=a);
 claim:=public.onflows_training_plan_update('CLAIM',a,token);
 assert claim->>'revision'='3','Latest coalesced task lost';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null,'No new activity still planned';
end $$;

do $$ declare a constant text:='prepared-contract-athlete'; owner_id uuid:='99002000-0000-0000-0000-000000000001';
 r jsonb; payload jsonb; state jsonb;
begin
 insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id,recorded_at) values
  (a,'DRAFT','2026-01-01',1,'{"start_date":"2026-01-01"}',owner_id,clock_timestamp()-interval '40 days'),
  (a,'DRAFT','2026-01-02',1,'{"start_date":"2026-01-02","parameters":{"individual_learning":{"memory":{"episodes":[]}}}}',owner_id,clock_timestamp()-interval '40 days'),
  (a,'DRAFT','2026-01-03',1,'{"start_date":"2026-01-03","parameters":{"load_progression":{"anchor":{"day":"2026-01-01"}}}}',owner_id,clock_timestamp()-interval '40 days'),
  (a,'DRAFT','2026-01-04',1,'{"start_date":"2026-01-04"}',owner_id,clock_timestamp()-interval '40 days'),
  (a,'DRAFT',current_date::text,1,jsonb_build_object('start_date',current_date),owner_id,clock_timestamp());
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,2,'REVIEW',owner_id,'{"origin":{"start_date":"2026-01-04","draft_revision":1},"plan":{"marker":"approved"}}');
 select to_jsonb(s) into payload from public.active_onflows_analysis(a) s;
 state:=public.onflows_dashboard_preparation('READ',p_alias=>a,p_model_key=>repeat('b',64));
 perform public.onflows_dashboard_preparation('STORE',a,p_revision=>(state->>'revision')::bigint,
  p_day=>(state->>'day')::date,p_model_key=>repeat('b',64),p_envelope=>payload);
 r:=public.cleanup_onflows_prepared_data(a);
 assert r->>'drafts'='1','Cleanup did not preserve only necessary history';
 assert not exists(select 1 from public.onflows_management_entries where athlete_alias=a and entry_key='2026-01-01');
 assert (select count(*) from public.onflows_management_entries where athlete_alias=a and kind='DRAFT')=4;
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=a)=2,'Approved history deleted';
 assert public.cleanup_onflows_prepared_data(a)->>'drafts'='0','Daily cleanup repeated';
end $$;
set constraints all immediate;
rollback;
