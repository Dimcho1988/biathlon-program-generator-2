-- Real trigger execution with synthetic records only; every change rolls back.
-- Clearing each prior event simulates worker consumption in this one transaction.
begin;
set local lock_timeout='3s';
set local statement_timeout='30s';
insert into auth.users(id) values('99003100-0000-0000-0000-000000000001');
insert into public.onflows_profiles(user_id,display_name)
 values('99003100-0000-0000-0000-000000000001','Learning trigger contract');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('learning-trigger-contract','learning-trigger-provider','synthetic-not-a-token','REVOKED');
-- This table has no kind field: INSERT itself exercises the regression.
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('learning-trigger-contract',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_training_snapshots(athlete_alias,payload,activated_at)
 values('learning-trigger-contract',jsonb_build_object('schema_version','athlete-snapshot-v1',
  'training_status',jsonb_build_object('athlete_id','learning-trigger-contract'),
  'load_history',jsonb_build_object('athlete_id','learning-trigger-contract')),clock_timestamp());

do $$
declare a constant text:='learning-trigger-contract';
 actor constant uuid:='99003100-0000-0000-0000-000000000001';
 previous_revision bigint; initial_cache_revision bigint;
begin
 assert exists(select 1 from onflows_private.learning_dirty_events where athlete_alias=a),
  'Real snapshot INSERT failed to append learning invalidation';
 perform public.onflows_learning_preparation('READ',a,p_model_key=>repeat('a',64));
 select requested_revision into initial_cache_revision from onflows_private.prepared_learning_views where athlete_alias=a;

 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 update public.onflows_athlete_settings set hrmax_bpm=191 where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Settings UPDATE did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 update public.onflows_athlete_settings set hrmax_bpm=hrmax_bpm where athlete_alias=a;
 assert (select coalesce(max(id),0)=previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'No-op UPDATE unnecessarily invalidated learning';

 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
  values(a,'RECOVERY','recovery-v2',1,'{}',actor);
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Model INSERT did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 update public.onflows_model_entries set payload='{"enabled":true}' where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Model UPDATE did not invalidate learning';

 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 insert into public.onflows_response_entries(athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id)
  values(a,'DAILY','2026-11-03','2026-11-03',1,'{}',actor);
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Response INSERT did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 update public.onflows_response_entries set payload='{"fatigue":2}' where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Response UPDATE did not invalidate learning';

 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
  values(a,'PROFILE','management-profile-v1',1,'{}',actor);
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Profile INSERT did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
  values(a,'DRAFT','2026-11-03',1,'{"start_date":"2026-11-03"}',actor);
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Draft INSERT did not invalidate learning';

 -- Plan revisions and analysis-state rows have no kind field either.
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,1,'ACTIVATE',actor,'{"mode":"REVIEW","plan":{"marker":"approved"}}');
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Plan memory INSERT did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision)
  values(a,null,0);
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Analysis state INSERT did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 update public.onflows_athlete_analysis_state set request_sequence=request_sequence+1 where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Analysis state UPDATE did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 update public.onflows_training_snapshots set payload=payload||'{"marker":1}'::jsonb where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Legacy snapshot UPDATE did not invalidate learning';

 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from public.onflows_management_plan_revisions where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Plan memory DELETE did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from public.onflows_athlete_analysis_state where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Analysis state DELETE did not invalidate learning';
 select coalesce(max(id),0) into previous_revision from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from onflows_private.learning_dirty_events where athlete_alias=a;
 delete from public.onflows_response_entries where athlete_alias=a;
 delete from public.onflows_model_entries where athlete_alias=a;
 delete from public.onflows_management_entries where athlete_alias=a;
 assert (select coalesce(max(id),0)>previous_revision from onflows_private.learning_dirty_events where athlete_alias=a),
  'Observation/model/profile DELETE did not invalidate learning';
 assert (select requested_revision=initial_cache_revision from onflows_private.prepared_learning_views where athlete_alias=a),
  'Input triggers mutated the shared cache row';
 delete from public.onflows_training_snapshots where athlete_alias=a;
 perform public.onflows_learning_preparation('CLAIM',a,gen_random_uuid(),p_model_key=>repeat('a',64));
 assert not exists(select 1 from onflows_private.prepared_learning_views where athlete_alias=a),
  'Deleted last source retained a ready projection';
 assert not exists(select 1 from onflows_private.learning_dirty_events where athlete_alias=a),
  'No-source worker cleanup retained consumed events';
 delete from public.onflows_athlete_settings where athlete_alias=a;
end $$;
rollback;
