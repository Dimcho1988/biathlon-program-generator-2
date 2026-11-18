-- Real-schema HR calibration contract. Synthetic rows and queued work roll back.
begin;
set local lock_timeout='3s';
set local statement_timeout='30s';
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('hr-history-contract','hr-history-provider','synthetic-not-a-token','CONNECTED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('hr-history-contract',array[86,137,150,169,179,190],190,'UTC');
insert into public.onflows_sync_jobs(job_id,athlete_alias,job_kind,idempotency_key,request_sequence,status)
 values('99009000-0000-0000-0000-000000000001','hr-history-contract','FULL_SYNC',repeat('a',64),1,'SUCCEEDED');
insert into public.onflows_analysis_generations(generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
 base_revision,activity_set_generation_id,activated_revision,status,snapshot_schema_version,snapshot_hash,snapshot_payload,
 period_start,period_end,as_of,provenance,activity_count,ready_at,activated_at)
 values('99009000-0000-0000-0000-000000000002','hr-history-contract','99009000-0000-0000-0000-000000000001',1,'FULL_SYNC',1,
 0,'99009000-0000-0000-0000-000000000002',1,'ACTIVE','athlete-snapshot-v1',repeat('b',64),
 '{"schema_version":"athlete-snapshot-v1","training_status":{"athlete_id":"hr-history-contract"},"load_history":{"athlete_id":"hr-history-contract"}}',
 current_date,current_date,current_date,'{}',0,clock_timestamp(),clock_timestamp());
insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision,request_sequence)
 values('hr-history-contract','99009000-0000-0000-0000-000000000002',1,1);

do $$ declare a constant text:='hr-history-contract'; prefs jsonb; revised jsonb; revision text; denied boolean;
begin
 select model_preferences into prefs from public.onflows_athlete_settings where athlete_alias=a;
 update public.onflows_athlete_settings set hrmax_bpm=hrmax_bpm where athlete_alias=a;
 assert (select model_preferences=prefs from public.onflows_athlete_settings where athlete_alias=a),
  'No-op added calibration metadata';
 assert onflows_private.hr_analysis_current(a),'Legacy analysis was invalidated without a correction';
 update public.onflows_athlete_settings set hrmax_bpm=200,hr_zone_bounds=array[90,144,158,178,188,200] where athlete_alias=a;
 select model_preferences into revised from public.onflows_athlete_settings where athlete_alias=a;
 revision:=revised->>'hr_settings_revision';
 assert revised#>>'{hr_history,0,hrmax_bpm}'='190' and revised#>>'{hr_history,1,hrmax_bpm}'='200';
 assert (revised#>>'{hr_history,1,from}')::date=current_date-89;
 assert not onflows_private.hr_analysis_current(a),'Old analysis was labelled with the new HR calibration';
 assert exists(select 1 from public.onflows_sync_jobs where athlete_alias=a and status='QUEUED'
  and request_payload->>'reason'='HR_CALIBRATION_CHANGED'),'Correction did not enqueue a durable rebuild';
 -- A manual/provider request behind an HR correction must still import data.
 perform public.enqueue_onflows_sync_job(a,'FULL_SYNC',repeat('d',64),'{"scope":"FULL"}');
 assert exists(select 1 from public.onflows_sync_jobs where athlete_alias=a and status='QUEUED'
  and request_payload->>'reason'='COALESCED_FULL_REFRESH'),'Coalescing lost the provider refresh';
 perform public.enqueue_onflows_sync_job(a,'FULL_SYNC',repeat('e',64),'{"reason":"HR_CALIBRATION_CHANGED"}');
 assert exists(select 1 from public.onflows_sync_jobs where athlete_alias=a and status='QUEUED'
  and request_payload->>'reason'='COALESCED_FULL_REFRESH'),'HR correction downgraded the provider refresh';
 assert public.onflows_dashboard_preparation('READ',a,p_model_key=>repeat('c',64))->>'reason'='HR_CALIBRATION_CHANGED';
 assert public.onflows_learning_preparation('READ',a,p_model_key=>repeat('c',64))->>'reason'='HR_CALIBRATION_CHANGED';
 assert public.onflows_prepared_view('READ',a,'trainability',p_model_key=>repeat('c',64))->>'reason'='HR_CALIBRATION_CHANGED';
 assert public.onflows_prepared_view('STORE',a,'trainability',p_model_key=>repeat('c',64),p_payload=>'{}')->>'stored'='false';
 update public.onflows_athlete_settings set model_preferences='{"leading_indicator":"SPEED"}' where athlete_alias=a;
 assert (select model_preferences->>'hr_settings_revision'=revision from public.onflows_athlete_settings where athlete_alias=a),
  'Unrelated save discarded authoritative HR history';
 -- A numeric reversal remains a new revision until raw analysis is rebuilt.
 update public.onflows_athlete_settings set hrmax_bpm=190,hr_zone_bounds=array[86,137,150,169,179,190],
  model_preferences=model_preferences||jsonb_build_object('hrmax_apply_from',(current_date-10)::text) where athlete_alias=a;
 select model_preferences into revised from public.onflows_athlete_settings where athlete_alias=a;
 assert revised->>'hr_settings_revision'<>revision;
 assert jsonb_array_length(revised->'hr_history')=3 and (revised#>>'{hr_history,2,from}')::date=current_date-10;
 assert not onflows_private.hr_analysis_current(a),'Reversal revived an obsolete generation';
 denied:=false;
 begin
  update public.onflows_athlete_settings set hrmax_bpm=201,
   model_preferences=model_preferences||jsonb_build_object('hrmax_apply_from',(current_date+1)::text) where athlete_alias=a;
 exception when invalid_parameter_value then denied:=true;
 end;
 assert denied and (select model_preferences=revised from public.onflows_athlete_settings where athlete_alias=a),
  'Rejected date changed persisted settings';
 -- Simulate a worker publication against the real generation trigger.
 update public.onflows_analysis_generations set status='READY' where athlete_alias=a;
 denied:=false;
 begin
  update public.onflows_analysis_generations set status='ACTIVE' where athlete_alias=a;
 exception when serialization_failure then denied:=true;
 end;
 assert denied,'Obsolete worker generation activated';
 update public.onflows_analysis_generations set snapshot_payload=jsonb_set(snapshot_payload,
  '{load_history,hr_settings_revision}',to_jsonb(revised->>'hr_settings_revision')) where athlete_alias=a;
 update public.onflows_analysis_generations set status='ACTIVE' where athlete_alias=a;
 assert onflows_private.hr_analysis_current(a),'Current worker generation was rejected';
end $$;
rollback;
