-- Synthetic fixtures only. No real history is changed; everything rolls back.
begin;
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values ('history-archive-test','history-archive-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_activity_catalog(activity_ref,athlete_alias,provider_activity_key,start_at_utc)
 values ('act_'||repeat('e',32),'history-archive-test',repeat('e',64),now()-interval '2 years'),
        ('act_'||repeat('f',32),'history-archive-test',repeat('f',64),now()-interval '1 day');
insert into public.onflows_activity_model_inputs
 (input_key,athlete_alias,activity_ref,input_hash,schema_version,input_payload,created_at)
 select repeat('1',64),'history-archive-test','act_'||repeat('e',32),repeat('2',64),'test',
 jsonb_build_object('input_hash',repeat('2',64),'samples',jsonb_agg(jsonb_build_object('elapsed',n,'speed',12.123456789))),
 now()-interval '10 days' from generate_series(1,3000) n;
insert into public.onflows_activity_derived_runs
 (run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload,created_at)
 select repeat('3',64),repeat('1',64),'history-archive-test','act_'||repeat('e',32),repeat('4',64),'test',
 jsonb_build_object('result_hash',repeat('4',64),'timeseries',jsonb_agg(jsonb_build_object('elapsed',n,'speed',12.123456789)),
 'zone_summary',jsonb_build_array(jsonb_build_object('zone','Z3','seconds',900))),
 now()-interval '10 days' from generate_series(1,3000) n;
insert into public.onflows_activity_model_inputs
 (input_key,athlete_alias,activity_ref,input_hash,schema_version,input_payload,created_at)
 select repeat('5',64),'history-archive-test','act_'||repeat('f',32),repeat('6',64),'test',input_payload,created_at
 from public.onflows_activity_model_inputs where input_key=repeat('1',64);
insert into storage.objects(bucket_id,name)
 values ('onflows-history','v1/shadow/'||repeat('3',64)||'/timeseries/'||repeat('7',64)||'.xz'),
        ('onflows-history','v1/input/'||repeat('1',64)||'/samples/'||repeat('7',64)||'.xz');

do $$ declare signature regprocedure; begin
  assert (select not public from storage.buckets where id='onflows-history');
  assert not has_table_privilege('authenticated','public.onflows_history_archive_policy','select');
  assert not has_table_privilege('service_role','public.onflows_history_archive_policy','update');
  assert not has_table_privilege('anon','public.onflows_history_archive_events','select');
  foreach signature in array array[
    'public.read_onflows_history_archive_batch(text,text,integer)'::regprocedure,
    'public.read_onflows_history_storage_hash(text,text)'::regprocedure,
    'public.replace_onflows_history_payload(text,text,text,jsonb,boolean)'::regprocedure
  ] loop
    assert has_function_privilege('service_role',signature,'execute');
    assert not has_function_privilege('anon',signature,'execute');
    assert not has_function_privilege('authenticated',signature,'execute');
  end loop;
end $$;

update public.onflows_history_archive_policy set enabled=false where id;
set local role service_role;
do $$ declare original jsonb; proposed jsonb; denied boolean := false; begin
  select result_payload into original from public.onflows_activity_derived_runs where run_key=repeat('3',64);
  proposed := original || jsonb_build_object('timeseries',jsonb_build_object('codec','onflows-history-object-xz-v1'));
  begin
    perform * from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(original::text),proposed,false);
  exception when raise_exception then denied := true; end;
  assert denied, 'Disabled archive policy was bypassed';
end $$;
reset role;
update public.onflows_history_archive_policy set enabled=true where id;
set local role service_role;
do $$
declare original jsonb; proposed jsonb; reference jsonb; denied boolean; result text; stamp timestamptz;
begin
  assert (select count(*)=1 from public.read_onflows_history_archive_batch('shadow','',5));
  assert (select count(*)=1 from public.read_onflows_history_archive_batch('input','',5)), 'Hot activity became a candidate';
  denied := false;
  begin
    perform * from public.read_onflows_history_archive_batch('shadow','',null);
  exception when raise_exception then denied := true; end;
  assert denied, 'NULL archive batch limit accepted';
  select result_payload,created_at into original,stamp from public.onflows_activity_derived_runs where run_key=repeat('3',64);
  reference := jsonb_build_object('codec','onflows-history-object-xz-v1','bucket','onflows-history',
    'path','v1/shadow/'||repeat('3',64)||'/timeseries/'||repeat('7',64)||'.xz',
    'sha256',repeat('7',64),'json_sha256',repeat('8',64),'bytes',100,'json_bytes',200000);
  proposed := original || jsonb_build_object('timeseries',reference);
  denied := false;
  begin
    perform * from public.replace_onflows_history_payload('shadow',repeat('3',64),null,proposed,false);
  exception when raise_exception then denied := true; end;
  assert denied, 'NULL storage hash accepted';
  select outcome into result from public.replace_onflows_history_payload('shadow',repeat('3',64),repeat('0',32),proposed,false);
  assert result='CONFLICT', 'Stale payload overwritten';
  denied := false;
  begin
    perform * from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(original::text),proposed||'{"zone_summary":[]}',false);
  exception when raise_exception then denied := true; end;
  assert denied, 'Scientific summary changed';
  denied := false;
  begin
    perform * from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(original::text),proposed-'timeseries',false);
  exception when raise_exception then denied := true; end;
  assert denied, 'Series removed';
  denied := false;
  begin
    perform * from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(original::text),
      original||jsonb_build_object('timeseries',reference||jsonb_build_object('sha256',repeat('9',64),
        'path','v1/shadow/'||repeat('3',64)||'/timeseries/'||repeat('9',64)||'.xz')),false);
  exception when raise_exception then denied := true; end;
  assert denied, 'Missing Storage object accepted';
  select outcome into result from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(original::text),proposed,false);
  assert result='ARCHIVED';
  assert (select result_payload=proposed and created_at=stamp and result_hash=repeat('4',64)
    from public.onflows_activity_derived_runs where run_key=repeat('3',64)), 'Identity or timestamp drift';
  assert (select zone_summary=original->'zone_summary' from public.onflows_activity_run_summaries where run_key=repeat('3',64)),
    'Read projection changed';
  assert (select count(*)=0 from public.read_onflows_history_archive_batch('shadow','',5)), 'Archived record selected again';
  select outcome into result from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(proposed::text),proposed,false);
  assert result='UNCHANGED';
  select outcome into result from public.replace_onflows_history_payload('shadow',repeat('3',64),md5(proposed::text),original,true);
  assert result='RESTORED';
  assert (select result_payload=original from public.onflows_activity_derived_runs where run_key=repeat('3',64));

  select input_payload,created_at into original,stamp from public.onflows_activity_model_inputs where input_key=repeat('1',64);
  reference := reference||jsonb_build_object('path','v1/input/'||repeat('1',64)||'/samples/'||repeat('7',64)||'.xz');
  proposed := original||jsonb_build_object('samples',reference);
  select outcome into result from public.replace_onflows_history_payload('input',repeat('1',64),md5(original::text),proposed,false);
  assert result='ARCHIVED';
  assert (select created_at=stamp and input_hash=repeat('2',64) from public.onflows_activity_model_inputs where input_key=repeat('1',64));
  select outcome into result from public.replace_onflows_history_payload('input',repeat('1',64),md5(proposed::text),original,true);
  assert result='RESTORED';
  denied := false;
  select input_payload into original from public.onflows_activity_model_inputs where input_key=repeat('5',64);
  begin
    perform * from public.replace_onflows_history_payload('input',repeat('5',64),md5(original::text),
      original||jsonb_build_object('samples',reference),false);
  exception when raise_exception then denied := true; end;
  assert denied, 'Recent activity archived';
  assert (select count(*)=4 from public.onflows_history_archive_events where entity_key in (repeat('1',64),repeat('3',64)));
end $$;
rollback;
