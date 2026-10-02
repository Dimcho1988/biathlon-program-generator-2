-- Synthetic fixtures only. All changes are rolled back.
begin;
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values ('storage-compaction-test','storage-compaction-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_activity_model_inputs
 (input_key,athlete_alias,activity_ref,input_hash,schema_version,input_payload)
 values (repeat('a',64),'storage-compaction-test','storage-test-activity',repeat('b',64),'test','{}');
insert into public.onflows_activity_derived_runs
 (run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload)
 values (repeat('c',64),repeat('a',64),'storage-compaction-test','storage-test-activity',repeat('d',64),'test',
 jsonb_build_object('result_hash',repeat('d',64),'timeseries',jsonb_build_array(jsonb_build_object('x',1)),
                   'zone_summary',jsonb_build_array(jsonb_build_object('zone','Z3','seconds',900))));

do $$ declare signature regprocedure; begin
  foreach signature in array array[
    'public.read_onflows_shadow_compaction_batch(text,integer)'::regprocedure,
    'public.compact_onflows_shadow_run(text,text,jsonb)'::regprocedure
  ] loop
    assert has_function_privilege('service_role',signature,'execute');
    assert not has_function_privilege('anon',signature,'execute');
    assert not has_function_privilege('authenticated',signature,'execute');
  end loop;
  -- Existing table privileges are intentionally unchanged by this migration.
end $$;

set local role service_role;
do $$
declare original jsonb; proposed jsonb; digest text; result text; denied boolean; stamp timestamptz;
begin
  select result_payload,created_at into original,stamp from public.onflows_activity_derived_runs where run_key=repeat('c',64);
  digest := md5(original::text);
  proposed := original || jsonb_build_object('timeseries',jsonb_build_object(
    'codec','onflows-shadow-series-columnar-lzma-v2','data','fixture-envelope-validated-in-python',
    'sha256',repeat('e',64),'json_bytes',100,'row_count',1));
  denied := false;
  begin
    perform * from public.read_onflows_shadow_compaction_batch('',null);
  exception when raise_exception then denied := true; end;
  assert denied, 'NULL must not bypass batch limit';
  denied := false;
  begin
    perform * from public.compact_onflows_shadow_run(repeat('c',64),null,proposed);
  exception when raise_exception then denied := true; end;
  assert denied, 'NULL must not bypass compare-and-swap';
  select outcome into result from public.compact_onflows_shadow_run(repeat('c',64),repeat('0',32),proposed);
  assert result='CONFLICT', 'Stale storage representation was overwritten';
  denied := false;
  begin
    perform * from public.compact_onflows_shadow_run(repeat('c',64),digest,proposed || '{"zone_summary":[]}');
  exception when raise_exception then denied := true; end;
  assert denied, 'Scientific summary changed through transport RPC';
  denied := false;
  begin
    perform * from public.compact_onflows_shadow_run(repeat('c',64),digest,proposed - 'timeseries');
  exception when raise_exception then denied := true; end;
  assert denied, 'Series removed through transport RPC';
  select outcome into result from public.compact_onflows_shadow_run(repeat('c',64),digest,proposed);
  assert result='COMPACTED';
  assert (select result_payload=proposed and created_at=stamp and result_hash=repeat('d',64)
    from public.onflows_activity_derived_runs where run_key=repeat('c',64)), 'Identity or timestamp drift';
  assert (select zone_summary=original->'zone_summary' from public.onflows_activity_run_summaries where run_key=repeat('c',64)),
    'Read projection changed';
  select outcome into result from public.compact_onflows_shadow_run(repeat('c',64),md5(proposed::text),proposed);
  assert result='UNCHANGED';
end $$;
rollback;
