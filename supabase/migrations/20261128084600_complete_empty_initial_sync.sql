-- Finish the first successful empty enumeration without fabricating a snapshot.
create function public.complete_onflows_empty_initial_sync(
  p_job_id uuid, p_generation_id uuid, p_lease_token uuid
) returns table(outcome text)
language plpgsql security definer set search_path = ''
as $$
declare
  job public.onflows_sync_jobs%rowtype;
begin
  select * into job from public.onflows_sync_jobs where job_id = p_job_id for update;
  if not found or job.job_kind <> 'FULL_SYNC' or job.status <> 'RUNNING'
     or job.current_generation_id is distinct from p_generation_id
     or job.lease_token is distinct from p_lease_token or job.lease_expires_at < now() then
    return query select 'LEASE_LOST'::text;
    return;
  end if;
  perform 1 from public.onflows_analysis_generations g
  where g.generation_id = p_generation_id and g.job_id = p_job_id
    and g.athlete_alias = job.athlete_alias and g.status = 'BUILDING'
    and g.base_generation_id is null and g.base_revision = 0 for update;
  if not found then
    return query select 'LEASE_LOST'::text;
    return;
  end if;
  perform 1 from public.onflows_athlete_analysis_state s
  where s.athlete_alias = job.athlete_alias and s.active_generation_id is null and s.active_revision = 0 for update;
  if not found then
    return query select 'LEASE_LOST'::text;
    return;
  end if;
  update public.onflows_analysis_generations
    set status = 'SUPERSEDED', superseded_at = now() where generation_id = p_generation_id;
  update public.onflows_sync_jobs
    set status = 'SUCCEEDED', progress_stage = 'NO_ACTIVITIES', progress_percent = 100,
        error_code = null, completed_at = now(), updated_at = now(), current_generation_id = null,
        lease_token = null, lease_owner = null, lease_expires_at = null
    where job_id = p_job_id;
  return query select 'NO_ACTIVITIES'::text;
end;
$$;
revoke all on function public.complete_onflows_empty_initial_sync(uuid,uuid,uuid) from public, anon, authenticated;
grant execute on function public.complete_onflows_empty_initial_sync(uuid,uuid,uuid) to service_role;
