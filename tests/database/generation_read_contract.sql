begin;

do $$
declare
  permission text;
  writer record;
  writer_count integer := 0;
begin
  if not has_table_privilege('service_role', 'public.onflows_analysis_generations', 'SELECT') then
    raise exception 'Generation reads must remain available';
  end if;
  foreach permission in array array['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] loop
    if has_table_privilege('service_role', 'public.onflows_analysis_generations', permission) then
      raise exception 'Direct generation % must remain unavailable', permission;
    end if;
  end loop;
  for writer in
    select p.oid, p.proname, p.prosecdef, pg_get_userbyid(p.proowner) as owner
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in (
      'claim_onflows_sync_job', 'stage_onflows_analysis_generation',
      'activate_onflows_analysis_generation', 'fail_onflows_sync_job',
      'rollback_onflows_analysis_generation', 'prune_onflows_analysis_generations')
  loop
    writer_count := writer_count + 1;
    if not writer.prosecdef or writer.owner='service_role'
       or not has_function_privilege('service_role', writer.oid, 'EXECUTE') then
      raise exception 'Supported generation writer % is unavailable', writer.proname;
    end if;
  end loop;
  if writer_count <> 6 then
    raise exception 'Expected all six supported generation writers';
  end if;
  -- Exercise SQL privilege checking without touching any historical row.
  begin
    execute 'set local role service_role';
    execute 'update public.onflows_analysis_generations set snapshot_payload=snapshot_payload where false';
    raise exception 'Direct generation writes unexpectedly succeeded';
  exception when insufficient_privilege then
    execute 'reset role';
  end;
end;
$$;

rollback;
