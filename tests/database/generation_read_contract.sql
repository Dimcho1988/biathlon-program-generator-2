begin;

do $$
declare
  permission text;
  writer record;
  writer_count integer := 0;
  prune record;
  denied_role text;
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
      'rollback_onflows_analysis_generation')
  loop
    writer_count := writer_count + 1;
    if not writer.prosecdef or writer.owner='service_role'
       or not has_function_privilege('service_role', writer.oid, 'EXECUTE') then
      raise exception 'Supported generation writer % is unavailable', writer.proname;
    end if;
  end loop;
  if writer_count <> 5 then
    raise exception 'Expected all five supported generation writers';
  end if;
  -- Pruning is suspended until immutable management-history references are
  -- protected. Keep the function available to its owner without granting an
  -- application role a path around the preservation guard.
  select p.oid, p.prosecdef, pg_get_userbyid(p.proowner) as owner
  into strict prune
  from pg_proc p
  where p.oid = 'public.prune_onflows_analysis_generations(text,integer,interval,integer)'::regprocedure;
  if not prune.prosecdef or prune.owner = 'service_role'
     or not has_function_privilege(prune.owner, prune.oid, 'EXECUTE') then
    raise exception 'Suspended pruning function must remain available to its owner';
  end if;
  foreach denied_role in array array['service_role', 'anon', 'authenticated'] loop
    if has_function_privilege(denied_role, prune.oid, 'EXECUTE') then
      raise exception 'Suspended pruning must not be executable by %', denied_role;
    end if;
    execute format('set local role %I', denied_role);
    begin
      -- Invalid retention also prevents writes if execution is accidentally
      -- restored. The expected failure must occur before entering the body.
      perform * from public.prune_onflows_analysis_generations(
        'generation-read-contract-no-pruning', 0, interval '30 days', 1);
      raise exception 'Suspended pruning unexpectedly executed as %', denied_role;
    exception when insufficient_privilege then
      null;
    end;
    execute 'reset role';
  end loop;
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
