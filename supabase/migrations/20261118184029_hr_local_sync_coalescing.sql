-- Preserve provider imports when a queued HR correction is coalesced.
begin;
set local lock_timeout='3s';
create or replace function public.enqueue_onflows_sync_job(
  p_athlete_alias text,
  p_job_kind text,
  p_idempotency_key text,
  p_request_payload jsonb
) returns table (
  job_id uuid,
  athlete_alias text,
  job_kind text,
  status text,
  request_sequence bigint,
  deduplicated boolean
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  state_row public.onflows_athlete_analysis_state%rowtype;
  existing_job public.onflows_sync_jobs%rowtype;
  inserted_job public.onflows_sync_jobs%rowtype;
begin
  if p_job_kind not in ('FULL_SYNC', 'WELLNESS_SYNC', 'RECOVERY_RESTORE')
     or length(p_idempotency_key) <> 64
     or jsonb_typeof(p_request_payload) <> 'object' then
    raise exception 'invalid sync job request';
  end if;

  if not exists (
    select 1 from public.onflows_intervals_connections c
    where c.athlete_alias = p_athlete_alias and c.status = 'CONNECTED'
  ) then
    raise exception 'athlete connection is unavailable';
  end if;

  insert into public.onflows_athlete_analysis_state (athlete_alias)
  values (p_athlete_alias)
  on conflict on constraint onflows_athlete_analysis_state_pkey do nothing;

  select * into state_row
  from public.onflows_athlete_analysis_state s
  where s.athlete_alias = p_athlete_alias
  for update;

  select * into existing_job
  from public.onflows_sync_jobs j
  where j.athlete_alias = p_athlete_alias
    and j.job_kind = p_job_kind
    and (
      j.idempotency_key = p_idempotency_key
      or j.status in ('QUEUED', 'RETRY_WAIT')
    )
  order by
    case when j.idempotency_key = p_idempotency_key then 0 else 1 end,
    j.requested_at desc
  limit 1;

  if found then
    -- An explicit provider refresh coalesced behind a local HR correction still
    -- needs its import. HR-only requests may never downgrade a full refresh.
    if p_job_kind = 'FULL_SYNC' and existing_job.status in ('QUEUED', 'RETRY_WAIT')
       and existing_job.request_payload->>'reason' = 'HR_CALIBRATION_CHANGED'
       and coalesce(p_request_payload->>'reason', '') <> 'HR_CALIBRATION_CHANGED' then
      update public.onflows_sync_jobs j
      set request_payload = existing_job.request_payload || p_request_payload
            || jsonb_build_object('reason', 'COALESCED_FULL_REFRESH'), updated_at = now()
      where j.job_id = existing_job.job_id
      returning * into existing_job;
    end if;
    return query select existing_job.job_id, existing_job.athlete_alias,
      existing_job.job_kind, existing_job.status,
      existing_job.request_sequence, true;
    return;
  end if;

  update public.onflows_athlete_analysis_state s
  set request_sequence = s.request_sequence + 1,
      updated_at = now()
  where s.athlete_alias = p_athlete_alias
  returning * into state_row;

  insert into public.onflows_sync_jobs (
    athlete_alias, job_kind, idempotency_key, request_sequence, request_payload
  ) values (
    p_athlete_alias, p_job_kind, p_idempotency_key,
    state_row.request_sequence, p_request_payload
  ) returning * into inserted_job;

  return query select inserted_job.job_id, inserted_job.athlete_alias,
    inserted_job.job_kind, inserted_job.status,
    inserted_job.request_sequence, false;
end;
$$;


commit;
