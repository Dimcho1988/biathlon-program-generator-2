-- Real-schema HR calibration contract. Synthetic rows and queued work roll back.
begin;
set local lock_timeout='3s';
set local statement_timeout='30s';
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('sync-progress-contract','sync-progress-provider','synthetic-not-a-token','CONNECTED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('sync-progress-contract',array[86,137,150,169,179,190],190,'UTC');
insert into public.onflows_sync_jobs(job_id,athlete_alias,job_kind,idempotency_key,request_sequence,status)
 values('99009000-0000-0000-0000-000000000001','sync-progress-contract','FULL_SYNC',repeat('a',64),1,'SUCCEEDED');
insert into public.onflows_analysis_generations(generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
 base_revision,activity_set_generation_id,activated_revision,status,snapshot_schema_version,snapshot_hash,snapshot_payload,
 period_start,period_end,as_of,provenance,activity_count,ready_at,activated_at)
 values('99009000-0000-0000-0000-000000000002','sync-progress-contract','99009000-0000-0000-0000-000000000001',1,'FULL_SYNC',1,
 0,'99009000-0000-0000-0000-000000000002',1,'ACTIVE','athlete-snapshot-v1',repeat('b',64),
 '{"schema_version":"athlete-snapshot-v1","training_status":{"athlete_id":"sync-progress-contract"},"load_history":{"athlete_id":"sync-progress-contract"}}',
 current_date,current_date,current_date,'{}',0,clock_timestamp(),clock_timestamp());
insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision,request_sequence)
 values('sync-progress-contract','99009000-0000-0000-0000-000000000002',1,1);

do $$
declare j constant uuid:='99009000-0000-0000-0000-000000000001';
 g constant uuid:='99009000-0000-0000-0000-000000000002';
 token constant uuid:='99009000-0000-0000-0000-000000000003';
begin
 update public.onflows_sync_jobs set status='RUNNING',current_generation_id=g,
  lease_token=token,lease_owner='progress-test',lease_expires_at=now()+interval '5 minutes',started_at=now() where job_id=j;
 assert public.report_onflows_sync_progress(j,g,token,'ACTIVITIES:12:30',36);
 assert (select progress_stage='ACTIVITIES:12:30' from public.onflows_sync_jobs where job_id=j);
 assert not public.report_onflows_sync_progress(j,g,gen_random_uuid(),'MODELS',90),'Stale token changed progress';
 update public.onflows_sync_jobs set lease_expires_at=now()-interval '1 second' where job_id=j;
 assert not public.report_onflows_sync_progress(j,g,token,'MODELS',90),'Expired lease changed progress';
 update public.onflows_sync_jobs set status='SUCCEEDED',lease_token=null,lease_owner=null,lease_expires_at=null where job_id=j;
 assert not public.report_onflows_sync_progress(j,g,token,'MODELS',90),'Terminal work changed progress';
 assert not has_function_privilege('anon','public.report_onflows_sync_progress(uuid,uuid,uuid,text,integer)','EXECUTE');
 assert not has_function_privilege('authenticated','public.report_onflows_sync_progress(uuid,uuid,uuid,text,integer)','EXECUTE');
end $$;
rollback;
