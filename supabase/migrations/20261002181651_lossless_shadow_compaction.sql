-- Server-only, bounded conversion of the storage representation. No scientific
-- identity, input, summary, configuration or revision may change through this RPC.
create function public.read_onflows_shadow_compaction_batch(
  p_after_run_key text default '', p_limit integer default 1
) returns table(run_key text, result_hash text, storage_hash text, result_payload jsonb)
language plpgsql security definer set search_path = '' as $$
begin
  if p_limit not between 1 and 5 or p_after_run_key !~ '^([a-f0-9]{64})?$' then
    raise exception 'invalid compaction cursor';
  end if;
  return query
  select d.run_key, d.result_hash, pg_catalog.md5(d.result_payload::text), d.result_payload
  from public.onflows_activity_derived_runs d
  where d.run_key > p_after_run_key
  order by d.run_key limit p_limit;
end;
$$;

create function public.compact_onflows_shadow_run(
  p_run_key text, p_expected_storage_hash text, p_result_payload jsonb
) returns table(outcome text)
language plpgsql security definer set search_path = '' set lock_timeout = '2s' as $$
declare
  original jsonb;
  scientific_hash text;
  field_name text;
  replacement jsonb;
  fields constant text[] := array['timeseries','speed_test_series','segments_15s','hrmod_waves'];
begin
  if p_run_key !~ '^[a-f0-9]{64}$' or p_expected_storage_hash !~ '^[a-f0-9]{32}$'
     or jsonb_typeof(p_result_payload) is distinct from 'object' then
    raise exception 'invalid compaction request';
  end if;
  select d.result_payload, d.result_hash into original, scientific_hash
  from public.onflows_activity_derived_runs d where d.run_key = p_run_key for update;
  if not found then
    return query select 'MISSING'::text;
    return;
  end if;
  if pg_catalog.md5(original::text) <> p_expected_storage_hash then
    return query select 'CONFLICT'::text;
    return;
  end if;
  if p_result_payload = original then
    return query select 'UNCHANGED'::text;
    return;
  end if;
  if (original - fields) is distinct from (p_result_payload - fields)
     or p_result_payload->>'result_hash' is distinct from scientific_hash then
    raise exception 'compaction cannot alter scientific metadata';
  end if;
  foreach field_name in array fields loop
    if (original ? field_name) is distinct from (p_result_payload ? field_name) then
      raise exception 'compaction cannot add or remove series';
    end if;
    if (original->field_name) is distinct from (p_result_payload->field_name) then
      replacement := p_result_payload->field_name;
      if jsonb_typeof(replacement) is distinct from 'object'
         or replacement->>'codec' is distinct from 'onflows-shadow-series-columnar-lzma-v2'
         or jsonb_typeof(replacement->'data') is distinct from 'string'
         or coalesce(replacement->>'sha256','') !~ '^[a-f0-9]{64}$'
         or coalesce(replacement->>'json_bytes','') !~ '^[1-9][0-9]{0,8}$'
         or (replacement->>'json_bytes')::bigint > 268435456
         or coalesce(replacement->>'row_count','') !~ '^[0-9]{1,7}$'
         or (replacement->>'row_count')::bigint > 1000000 then
        raise exception 'invalid compact series envelope';
      end if;
    end if;
  end loop;
  -- The maintenance client verifies exact decoded equality before this CAS and
  -- again after reading the stored value. The primary key/hash stay unchanged.
  update public.onflows_activity_derived_runs
  set result_payload = p_result_payload where run_key = p_run_key;
  return query select 'COMPACTED'::text;
end;
$$;

revoke all on function public.read_onflows_shadow_compaction_batch(text,integer) from public,anon,authenticated;
revoke all on function public.compact_onflows_shadow_run(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.read_onflows_shadow_compaction_batch(text,integer) to service_role;
grant execute on function public.compact_onflows_shadow_run(text,text,jsonb) to service_role;
