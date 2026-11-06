-- Reproducible local/staging contracts. All synthetic state rolls back.
begin;
set local statement_timeout='30s';
insert into auth.users(id) values('99006000-0000-0000-0000-000000000001');
insert into public.onflows_profiles(user_id,display_name) values('99006000-0000-0000-0000-000000000001','Prepared views contract');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('prepared-views-contract','prepared-views-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('prepared-views-contract',array[100,120,140,155,170,190],190,'Europe/Sofia');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 values('99006000-0000-0000-0000-000000000001','prepared-views-contract',true);
insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
 values('prepared-views-contract','PROFILE','management-profile-v1',1,'{"adaptation_mode":"AUTO"}','99006000-0000-0000-0000-000000000001');
insert into public.onflows_sync_jobs(job_id,athlete_alias,job_kind,idempotency_key,request_sequence)
 values('99006000-0000-0000-0000-000000000002','prepared-views-contract','FULL_SYNC',repeat('a',64),1);
insert into public.onflows_analysis_generations(generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
 base_revision,activity_set_generation_id,activated_revision,status,snapshot_schema_version,snapshot_hash,snapshot_payload,
 period_start,period_end,as_of,provenance,activity_count,ready_at,activated_at)
 values('99006000-0000-0000-0000-000000000003','prepared-views-contract','99006000-0000-0000-0000-000000000002',1,'FULL_SYNC',1,
 0,'99006000-0000-0000-0000-000000000003',1,'ACTIVE','athlete-snapshot-v1',repeat('b',64),
 '{"schema_version":"athlete-snapshot-v1","training_status":{"athlete_id":"prepared-views-contract"},"load_history":{"athlete_id":"prepared-views-contract"}}',
 current_date,current_date,current_date,'{}',0,clock_timestamp(),clock_timestamp());
insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision)
 values('prepared-views-contract','99006000-0000-0000-0000-000000000003',1);


insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
 values('prepared-views-contract',1,'ACTIVATE','99006000-0000-0000-0000-000000000001',
  jsonb_build_object('mode','AUTO','evaluated_on',current_date));
insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by)
 values('prepared-views-contract',1,'ACTIVE','99006000-0000-0000-0000-000000000001');
do $$ declare a constant text:='prepared-views-contract'; prior uuid:='99006000-0000-0000-0000-000000000003';
 g uuid; snap jsonb; n integer; expected bigint;
begin
 select snapshot_payload into snap from public.onflows_analysis_generations where generation_id=prior;
 for n in 2..6 loop
  g:=gen_random_uuid();
  if n in (3,6) then snap:=jsonb_set(snap,'{wellness_calendar}',jsonb_build_array(jsonb_build_object('date',current_date,'resting_hr',50+n))); end if;
  if n=5 then snap:=jsonb_set(snap,'{load_history,recorded_minutes}','22'); end if;
  if n=6 then update public.onflows_management_plan_state set status='PAUSED' where athlete_alias=a; end if;
  update public.onflows_analysis_generations set status='SUPERSEDED',superseded_at=clock_timestamp() where generation_id=prior;
  insert into public.onflows_analysis_generations(generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
   base_revision,activity_set_generation_id,activated_revision,status,snapshot_schema_version,snapshot_hash,snapshot_payload,
   period_start,period_end,as_of,provenance,activity_count,ready_at,activated_at)
   values(g,a,'99006000-0000-0000-0000-000000000002',n,'FULL_SYNC',1,0,'99006000-0000-0000-0000-000000000003',n,
    'ACTIVE','athlete-snapshot-v1',repeat('b',64),snap,current_date,current_date,current_date,'{}',0,clock_timestamp(),clock_timestamp());
  update public.onflows_athlete_analysis_state set active_generation_id=g,active_revision=n where athlete_alias=a;
  expected:=case when n=2 then 0 when n<=4 then 1 else 2 end;
  assert coalesce((select requested_revision from onflows_private.training_plan_updates where athlete_alias=a),0)=expected,
   'An unchanged source re-planned, a material observation was missed, or a paused plan was queued';
  prior:=g;
 end loop;
end $$;
rollback;
