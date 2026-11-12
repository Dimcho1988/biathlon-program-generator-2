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

do $$ declare a constant text:='prepared-views-contract'; role_name text; key1 text:=repeat('c',64); key2 text:=repeat('d',64);
 token uuid:=gen_random_uuid(); claim jsonb; state jsonb; payload jsonb:='{"value":12.345,"unchanged":true}'; d date; r bigint;
begin
 assert (select count(*) from onflows_private.prepared_views where athlete_alias=a)=12,'Default views were not queued before opening';
 -- The stable catalog row can have no date or sport metadata. The active
 -- recording is authoritative, including a corrected classification.
 insert into public.onflows_activity_catalog(activity_ref,athlete_alias,provider_activity_key)
  values('act_99006000000000000000000000000001',a,repeat('9',64));
 insert into public.onflows_analysis_generation_activities(generation_id,athlete_alias,activity_ref,local_date,catalog_payload,payload_hash)
  values('99006000-0000-0000-0000-000000000003',a,'act_99006000000000000000000000000001',
  (now() at time zone 'Europe/Sofia')::date,'{"sport":"NordicSki","activity_type":"Walk"}',repeat('8',64));
 perform onflows_private.seed_prepared_views(a);
 assert (select count(*) from onflows_private.prepared_views where athlete_alias=a and is_default
    and parameters='{"sport":"NordicSki"}' and kind in ('speed','speed-history','speed-load'))=3,
  'A sport tab waited for its first visit despite an active recording';
 foreach role_name in array array['anon','authenticated','service_role'] loop
  assert not has_table_privilege(role_name,'onflows_private.prepared_views','SELECT');
  assert not has_table_privilege(role_name,'onflows_private.prepared_view_sources','UPDATE');
  assert not has_table_privilege(role_name,'onflows_private.prepared_view_events','SELECT');
  assert not has_table_privilege(role_name,'onflows_private.activity_derivations','SELECT');
 end loop;
 foreach role_name in array array['anon','authenticated'] loop
  assert not has_function_privilege(role_name,'public.onflows_prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb)','EXECUTE');
  assert not has_function_privilege(role_name,'public.onflows_activity_derivations(text,text,text[],jsonb)','EXECUTE');
 end loop;
 assert not (select prosecdef from pg_proc where oid='public.onflows_prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb)'::regprocedure);
 delete from public.onflows_analysis_generation_activities where athlete_alias=a;
 delete from onflows_private.prepared_views where athlete_alias=a;
 state:=public.onflows_prepared_view('READ',a,'trainability',p_model_key=>key1);
 assert state->>'ready'='false';
 delete from onflows_private.prepared_views where athlete_alias=a and kind<>'trainability';
 claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
 assert claim->>'kind'='trainability';
 d:=(claim->>'day')::date; r:=(claim->>'revision')::bigint;
 assert public.onflows_prepared_view('STORE',a,'trainability','{}',gen_random_uuid(),r,d,key1,payload)->>'state'='LOST';
 assert public.onflows_prepared_view('STORE',a,'trainability','{}',token,r,d,key1,payload)->>'stored'='true';
 state:=public.onflows_prepared_view('READ',a,'trainability',p_model_key=>key1);
 assert state->>'ready'='true' and state->'payload'=payload;
 assert public.onflows_prepared_view('READ',a,'trainability',jsonb_build_object('period_start',d-89,'period_end',d),p_model_key=>key1)->'payload'=payload,
  'The standard date selector missed its prepared default';
 assert (select count(*) from onflows_private.prepared_views where athlete_alias=a)=1;
 assert public.onflows_prepared_view('READ',a,'trainability',p_model_key=>key2)->>'ready'='false','A prior build escaped its fence';
 -- Responses change stress/planning, but never invalidate the paired index.
 perform onflows_private.request_prepared_views(a,false);
 assert public.onflows_prepared_view('READ',a,'trainability',p_model_key=>key1)->'payload'=payload;
 update public.onflows_athlete_settings set hrmax_bpm=191 where athlete_alias=a;
 assert public.onflows_prepared_view('READ',a,'trainability',p_model_key=>key1)->>'ready'='false';
 delete from onflows_private.prepared_views where athlete_alias=a and kind<>'trainability';
 claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
 update public.onflows_athlete_settings set hrmax_bpm=192 where athlete_alias=a;
 assert public.onflows_prepared_view('STORE',a,'trainability','{}',token,(claim->>'revision')::bigint,d,key1,payload)->>'stored'='false',
  'An in-flight result survived a source change';
 delete from onflows_private.prepared_views where athlete_alias=a and kind<>'trainability';
 claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
 assert public.onflows_prepared_view('STORE',a,'trainability','{}',token,(claim->>'revision')::bigint,d,key1,payload)->>'stored'='true';
 update onflows_private.prepared_views set evaluated_on=d-1,claimed_day=d-1 where athlete_alias=a;
 assert public.onflows_prepared_view('READ',a,'trainability',p_model_key=>key1)->>'ready'='false','Yesterday was marked current';
 claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
 assert claim is not null,'The calendar did not independently schedule preparation';
 assert public.onflows_prepared_view('STORE',a,'trainability','{}',token,(claim->>'revision')::bigint,d-1,key1,payload)->>'stored'='false';

 -- Immutable per-activity reuse survives readers and is isolated by kind/athlete.
 perform public.onflows_activity_derivations(a,'SPEED_Q',p_values=>jsonb_build_object(key1,payload));
 assert public.onflows_activity_derivations(a,'SPEED_Q',array[key1])->key1=payload;
 assert public.onflows_activity_derivations(a,'UNIFIED_Q',array[key1])='{}';
 assert public.onflows_activity_derivations('another-athlete','SPEED_Q',array[key1])='{}';
 update onflows_private.activity_derivations set expires_at=clock_timestamp()-interval '1 second' where athlete_alias=a;
 assert public.onflows_activity_derivations(a,'SPEED_Q',array[key1])='{}';
end $$;

do $$ declare a constant text:='prepared-views-contract'; owner_id uuid:='99006000-0000-0000-0000-000000000001';
 token uuid:=gen_random_uuid(); result jsonb; fixed_before jsonb; n bigint; today_local date:=(clock_timestamp() at time zone 'Europe/Sofia')::date;
begin
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,1,'ACTIVATE',owner_id,jsonb_build_object('mode','AUTO','evaluated_on',today_local-1));
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by) values(a,1,'ACTIVE',owner_id);
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result is not null,'New local day did not extend an adaptive program';
 n:=(result->>'revision')::bigint;
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null,'The day created duplicate concurrent programs';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert (select last_evaluated_on from onflows_private.training_plan_updates where athlete_alias=a)=today_local;
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null,'The same local day ran twice';
 update onflows_private.training_plan_updates set requested_revision=requested_revision+3,attempts=0 where athlete_alias=a;
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert (result->>'revision')::bigint=n+3,'Three concurrent training changes lost the newest input';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 update public.onflows_management_plan_revisions set payload=jsonb_set(payload,'{mode}','"REVIEW"') where athlete_alias=a;
 select payload into fixed_before from public.onflows_management_plan_revisions where athlete_alias=a and revision=1;
 update onflows_private.training_plan_updates set last_evaluated_on=today_local-1 where athlete_alias=a;
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result is not null,'A fixed-horizon program did not receive its daily input check';
 assert (select payload=fixed_before from public.onflows_management_plan_revisions where athlete_alias=a and revision=1),
  'Scheduling a fixed-horizon refresh changed the saved program';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert (select last_evaluated_on from onflows_private.training_plan_updates where athlete_alias=a)=today_local;
 assert public.onflows_training_plan_update('CLAIM',a,gen_random_uuid()) is null,'The fixed program ran twice on the same day';
 update public.onflows_management_plan_revisions set payload=jsonb_set(payload,'{mode}','"AUTO"') where athlete_alias=a;
 update public.onflows_management_plan_state set status='PAUSED' where athlete_alias=a;
 assert public.onflows_training_plan_update('CLAIM',a,token) is null,'A calendar tick resumed a paused program';
 update public.onflows_management_plan_state set status='ACTIVE' where athlete_alias=a;
 begin
  perform public.queue_onflows_plan_refresh(a,gen_random_uuid(),1);
  assert false,'An unrelated actor queued a manual refresh';
 exception when insufficient_privilege then null;
 end;
 assert public.queue_onflows_plan_refresh(a,owner_id,0)->>'conflict'='true','The manual request accepted an obsolete plan revision';
 assert public.queue_onflows_plan_refresh(a,owner_id,1,true)->>'queued'='true';
 assert public.read_onflows_plan_preparation(a)->>'status'='QUEUED';
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result->>'manual'='true' and (result->>'expected_plan_revision')::integer=1;
 assert result->>'record_unchanged_check'='true' and (result->>'actor_id')::uuid=owner_id;
 assert public.read_onflows_plan_preparation(a)->>'status'='RUNNING';
 -- A later request survives completion of this owned claim.
 perform public.queue_onflows_plan_refresh(a,owner_id,1,false);
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.read_onflows_plan_preparation(a)->>'status'='QUEUED';
 result:=public.onflows_training_plan_update('CLAIM',a,token);
 assert result->>'manual'='true' and result->>'record_unchanged_check'='false';
 perform public.onflows_training_plan_update('REJECT',a,token);
 assert public.read_onflows_plan_preparation(a)->>'status'='FAILED';
 assert not (select requested_manual from onflows_private.training_plan_updates where athlete_alias=a);
end $$;
do $$ declare a constant text:='prepared-views-contract'; key1 text:=repeat('c',64); key2 text:=repeat('d',64);
 token uuid:=gen_random_uuid(); claim jsonb; state jsonb; payload jsonb:='{"active":{"revision":7,"plan":{"days":[]}}}';
 d date; r bigint; attempt integer;
begin
 delete from onflows_private.prepared_views where athlete_alias=a;
 state:=public.onflows_prepared_view('READ',a,'management-view',p_model_key=>key1);
 assert state->>'ready'='false' and not (state ? 'saved'),'A missing program was fabricated';
 -- An interactive read takes precedence over unrelated background defaults.
 insert into onflows_private.prepared_views(athlete_alias,kind,parameter_key,parameters,is_default)
  values(a,'management-active',md5('{}'::jsonb::text),'{}',true) on conflict do nothing;
 claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
 assert claim->>'kind'='management-view','A requested page was starved by background defaults';
 delete from onflows_private.prepared_views where athlete_alias=a and (kind<>'management-view' or parameters<>'{}');
 d:=(claim->>'day')::date; r:=(claim->>'revision')::bigint;
 assert public.onflows_prepared_view('STORE',a,'management-view','{}',token,r,d,key1,payload)->>'stored'='true';
 state:=public.onflows_prepared_view('READ',a,'management-view',p_model_key=>key2);
 assert state->>'ready'='false' and state->>'saved'='true' and state->'payload'=payload,
  'A deployment hid the saved program or falsely marked it current';
 assert state->'preparation'->>'status'='UPDATING' and state->'preparation'->>'reason'='MODEL_CHANGED';
 assert state->>'athlete_alias'=a and state->>'kind'='management-view';
 update onflows_private.prepared_views set evaluated_on=d-1 where athlete_alias=a and kind='management-view';
 state:=public.onflows_prepared_view('READ',a,'management-view',p_model_key=>key1);
 assert state->>'ready'='false' and state->>'saved'='true' and state->'payload'=payload
  and state->'preparation'->>'reason'='DAY_CHANGED', 'Local midnight hid a saved program';
 assert state->'preparation'->>'evaluated_on'=(d-1)::text,'Saved-day provenance was silently refreshed';
 update onflows_private.prepared_views set evaluated_on=d where athlete_alias=a and kind='management-view';
 assert public.onflows_prepared_view('READ',a,'management-view','{"view":"overview"}',p_model_key=>key2)->'payload' is null,
  'A different requested page received an unrelated saved payload';
 delete from onflows_private.prepared_views where athlete_alias=a and parameters<>'{}';
 perform onflows_private.request_prepared_views(a,false);
 state:=public.onflows_prepared_view('READ',a,'management-view',p_model_key=>key1);
 assert state->'preparation'->>'reason'='INPUTS_CHANGED' and state->'payload'=payload;
 delete from onflows_private.prepared_views where athlete_alias=a and (kind<>'management-view' or parameters<>'{}');
 -- Transient failures do not exhaust into a permanent pending state. Backoff
 -- remains enforced, so an unavailable dependency cannot spin the worker.
 for attempt in 1..4 loop
  claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
  assert claim is not null,'A retryable page became permanently unclaimable';
  perform public.onflows_prepared_view('FAIL',a,'management-view','{}',token);
  assert public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>gen_random_uuid(),p_model_key=>key1) is null,
   'A failed page bypassed its retry cooldown';
  assert (select available_at>clock_timestamp() and available_at<=clock_timestamp()+interval '5 minutes'
   from onflows_private.prepared_views where athlete_alias=a and kind='management-view');
  update onflows_private.prepared_views set available_at=clock_timestamp()-interval '1 second'
   where athlete_alias=a and kind='management-view';
 end loop;
 claim:=public.onflows_prepared_view('CLAIM',p_alias=>a,p_token=>token,p_model_key=>key1);
 assert public.onflows_prepared_view('STORE',a,'management-view','{}',token,(claim->>'revision')::bigint,d,key1,payload)->>'stored'='true';
 state:=public.onflows_prepared_view('READ',a,'management-view',p_model_key=>key1);
 assert state->>'ready'='true' and not (state ? 'saved'),'A successful refresh retained stale provenance';
 update onflows_private.prepared_views set payload='{"prepared_error":{"status":409,"detail":"old"}}',model_key=key2
  where athlete_alias=a and kind='management-view';
 state:=public.onflows_prepared_view('READ',a,'management-view',p_model_key=>key1);
 assert state->>'ready'='false' and not (state ? 'saved'),'An old error was treated as a usable saved program';
end $$;
rollback;
