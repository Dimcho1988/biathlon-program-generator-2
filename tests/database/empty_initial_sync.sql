-- An empty first import completes without publishing a physiological analysis.
begin;
do $$
declare
  athlete text := 'test-empty-' || gen_random_uuid()::text;
  j uuid := gen_random_uuid();
  g uuid := gen_random_uuid();
  token uuid := gen_random_uuid();
  result text;
begin
  insert into public.onflows_intervals_connections(athlete_alias, provider_athlete_id, encrypted_access_token, status)
    values (athlete, athlete, 'synthetic', 'CONNECTED');
  insert into public.onflows_athlete_analysis_state(athlete_alias, request_sequence) values (athlete, 1);
  insert into public.onflows_sync_jobs(job_id, athlete_alias, job_kind, idempotency_key, request_sequence)
    values (j, athlete, 'FULL_SYNC', repeat('a', 64), 1);
  insert into public.onflows_analysis_generations(generation_id, athlete_alias, job_id, attempt_no,
    job_kind, request_sequence, base_revision) values (g, athlete, j, 1, 'FULL_SYNC', 1, 0);
  update public.onflows_sync_jobs set status='RUNNING', current_generation_id=g, started_at=now(),
    lease_token=token, lease_owner='test', lease_expires_at=now()+interval '1 minute' where job_id=j;
  select outcome into result from public.complete_onflows_empty_initial_sync(j,g,gen_random_uuid());
  if result is distinct from 'LEASE_LOST' then raise exception 'Invalid lease accepted'; end if;
  select outcome into result from public.complete_onflows_empty_initial_sync(j,g,token);
  if result is distinct from 'NO_ACTIVITIES' then raise exception 'Empty sync did not finish'; end if;
  if not exists(select 1 from public.onflows_sync_state(athlete)
    where status='SUCCEEDED' and progress_stage='NO_ACTIVITIES' and error_code is null
      and active_generation_id is null and active_revision=0)
    then raise exception 'Incorrect public empty sync state'; end if;
  if has_function_privilege('authenticated','public.complete_onflows_empty_initial_sync(uuid,uuid,uuid)','execute')
    or has_function_privilege('anon','public.complete_onflows_empty_initial_sync(uuid,uuid,uuid)','execute')
    then raise exception 'Unexpected client permission'; end if;
end;
$$;
rollback;
