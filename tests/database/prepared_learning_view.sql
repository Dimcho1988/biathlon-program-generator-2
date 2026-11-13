-- Synthetic local/staging regression check. Every fixture/cache change rolls back.
begin;
set local lock_timeout='3s';
set local statement_timeout='30s';
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('prepared-learning-contract','prepared-learning-contract-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('prepared-learning-contract',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_training_snapshots(athlete_alias,payload,activated_at)
 values('prepared-learning-contract',jsonb_build_object('schema_version','athlete-snapshot-v1',
  'training_status',jsonb_build_object('athlete_id','prepared-learning-contract'),
  'load_history',jsonb_build_object('athlete_id','prepared-learning-contract')),clock_timestamp());
do $$
declare alias constant text:='prepared-learning-contract'; key1 text:=repeat('a',64); key2 text:=repeat('b',64);
 token uuid:='6c6021ad-c796-4eb7-9c84-ab1d3e9ca34a';
 claim jsonb; state jsonb; stored jsonb; payload jsonb;
begin
 if has_function_privilege('anon','public.onflows_learning_preparation(text,text,uuid,bigint,date,text,jsonb)','execute')
  or has_function_privilege('authenticated','public.onflows_learning_preparation(text,text,uuid,bigint,date,text,jsonb)','execute')
  or has_table_privilege('authenticated','onflows_private.prepared_learning_views','select') then
  raise exception 'Learning cache exposed to browser roles'; end if;
 if not exists(select 1 from public.active_onflows_analysis(alias)) then raise exception 'Synthetic source missing'; end if;
 perform onflows_private.request_learning_preparation(alias);
 state:=public.onflows_learning_preparation('READ',alias,p_model_key=>key1);
 if (state->>'ready')::boolean then raise exception 'Cold cache marked ready'; end if;
 claim:=public.onflows_learning_preparation('CLAIM',alias,token,p_model_key=>key1);
 if claim is null then raise exception 'Warmup not queued'; end if;
 payload:=jsonb_build_object('athlete_alias',alias,'report',jsonb_build_object(
  'schema_version','recovery-learning-view-v1','as_of',claim->>'day','series','[]'::jsonb));
 -- A response/profile/analysis event between read and write supersedes the lease.
 -- Simulate a separate input transaction (this rollback script has one txid).
 insert into onflows_private.learning_dirty_events(athlete_alias,transaction_id)
  values(alias,txid_current()+1000000000);
 stored:=public.onflows_learning_preparation('STORE',alias,token,(claim->>'revision')::bigint,
  (claim->>'day')::date,key1,payload);
 if stored->>'stored'<>'false' then raise exception 'Superseded input was published'; end if;
 claim:=public.onflows_learning_preparation('CLAIM',alias,token,p_model_key=>key1);
 -- The active source identity is fenced independently of invalidation triggers.
 update onflows_private.prepared_learning_views set source_revision=source_revision-1 where athlete_alias=alias;
 stored:=public.onflows_learning_preparation('STORE',alias,token,(claim->>'revision')::bigint,
  (claim->>'day')::date,key1,payload);
 if stored->>'stored'<>'false' then raise exception 'Wrong source was published'; end if;
 claim:=public.onflows_learning_preparation('CLAIM',alias,token,p_model_key=>key1);
 stored:=public.onflows_learning_preparation('STORE',alias,token,(claim->>'revision')::bigint,
  (claim->>'day')::date,key1,payload);
 if stored->>'stored'<>'true' then raise exception 'Valid result not stored'; end if;
 state:=public.onflows_learning_preparation('READ',alias,p_model_key=>key1);
 if state->>'ready'<>'true' or state#>>'{payload,athlete_alias}'<>alias then raise exception 'Ready cache invalid'; end if;
 state:=public.onflows_learning_preparation('READ',alias,p_model_key=>key2);
 if state->>'ready'<>'false' then raise exception 'Old model reused'; end if;
 update onflows_private.prepared_learning_views set evaluated_on=evaluated_on-1 where athlete_alias=alias;
 state:=public.onflows_learning_preparation('READ',alias,p_model_key=>key1);
 if state->>'ready'<>'false' then raise exception 'Old local day reused'; end if;
end $$;
rollback;
