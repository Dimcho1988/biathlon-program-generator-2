-- Durable, server-only synchronization queue and immutable analysis generations.
--
-- PostgreSQL is intentionally used as both the queue and the activation ledger:
-- no second broker is required, and the final pointer switch is transactional
-- with the current snapshot projection.  Provider credentials are never stored
-- in jobs or generations; workers resolve them from the encrypted connection.

create extension if not exists pgcrypto with schema extensions;

-- Pending reconnect state is profile-owned when an alias is present.  Preserve
-- anonymous OAuth states, but detach historical aliases whose profile is gone.
alter table public.onflows_oauth_states
  add constraint onflows_oauth_states_athlete_alias_fk
  foreign key (athlete_alias)
  references public.onflows_intervals_connections(athlete_alias)
  on update cascade on delete cascade
  not valid;
update public.onflows_oauth_states state
set athlete_alias = null
where state.athlete_alias is not null
  and not exists (
    select 1 from public.onflows_intervals_connections connection
    where connection.athlete_alias = state.athlete_alias
  );
alter table public.onflows_oauth_states
  validate constraint onflows_oauth_states_athlete_alias_fk;

create table public.onflows_athlete_analysis_state (
  athlete_alias text primary key references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  active_generation_id uuid,
  active_revision bigint not null default 0 check (active_revision >= 0),
  request_sequence bigint not null default 0 check (request_sequence >= 0),
  updated_at timestamptz not null default now()
);

create table public.onflows_sync_jobs (
  job_id uuid primary key default gen_random_uuid(),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  job_kind text not null check (
    job_kind in ('FULL_SYNC', 'WELLNESS_SYNC', 'RECOVERY_RESTORE')
  ),
  idempotency_key text not null check (length(idempotency_key) = 64),
  request_sequence bigint not null check (request_sequence > 0),
  request_payload jsonb not null default '{}'::jsonb
    check (jsonb_typeof(request_payload) = 'object'),
  status text not null default 'QUEUED' check (
    status in ('QUEUED', 'RETRY_WAIT', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SUPERSEDED')
  ),
  priority smallint not null default 0 check (priority between -100 and 100),
  attempt_count smallint not null default 0 check (attempt_count between 0 and 20),
  max_attempts smallint not null default 3 check (max_attempts between 1 and 20),
  available_at timestamptz not null default now(),
  lease_token uuid,
  lease_owner text check (lease_owner is null or length(lease_owner) between 1 and 128),
  lease_expires_at timestamptz,
  current_generation_id uuid,
  progress_stage text check (progress_stage is null or length(progress_stage) <= 64),
  progress_percent smallint check (
    progress_percent is null or progress_percent between 0 and 100
  ),
  error_code text check (
    error_code is null or error_code ~ '^[A-Z0-9_]{1,64}$'
  ),
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  unique (athlete_alias, request_sequence),
  unique (athlete_alias, job_kind, idempotency_key),
  constraint onflows_sync_jobs_lease_state check (
    (status = 'RUNNING' and lease_token is not null and lease_owner is not null
      and lease_expires_at is not null and started_at is not null)
    or
    (status <> 'RUNNING' and lease_token is null and lease_owner is null
      and lease_expires_at is null)
  )
);

-- Each kind has at most one bounded follow-up while another attempt runs.
create unique index onflows_sync_jobs_one_running_per_athlete
  on public.onflows_sync_jobs (athlete_alias)
  where status = 'RUNNING';
create unique index onflows_sync_jobs_one_queued_kind_per_athlete
  on public.onflows_sync_jobs (athlete_alias, job_kind)
  where status in ('QUEUED', 'RETRY_WAIT');
create index onflows_sync_jobs_claim
  on public.onflows_sync_jobs (priority desc, available_at, requested_at, job_id)
  where status in ('QUEUED', 'RETRY_WAIT');
create index onflows_sync_jobs_expired_lease
  on public.onflows_sync_jobs (lease_expires_at)
  where status = 'RUNNING';
create index onflows_sync_jobs_athlete_history
  on public.onflows_sync_jobs (
    athlete_alias, request_sequence desc, requested_at desc, job_id desc
  );

create table public.onflows_analysis_generations (
  generation_id uuid primary key default gen_random_uuid(),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  job_id uuid not null,
  attempt_no smallint not null check (attempt_no > 0),
  job_kind text not null check (
    job_kind in ('FULL_SYNC', 'WELLNESS_SYNC', 'RECOVERY_RESTORE')
  ),
  request_sequence bigint not null check (request_sequence > 0),
  base_generation_id uuid,
  base_revision bigint not null check (base_revision >= 0),
  activity_set_generation_id uuid,
  activated_revision bigint check (
    activated_revision is null or activated_revision > 0
  ),
  status text not null default 'BUILDING' check (
    status in ('BUILDING', 'READY', 'ACTIVE', 'SUPERSEDED', 'FAILED')
  ),
  snapshot_schema_version text,
  snapshot_hash text check (snapshot_hash is null or length(snapshot_hash) = 64),
  snapshot_payload jsonb,
  period_start date,
  period_end date,
  as_of date,
  provenance jsonb,
  activity_count integer check (activity_count is null or activity_count >= 0),
  created_at timestamptz not null default now(),
  ready_at timestamptz,
  activated_at timestamptz,
  superseded_at timestamptz,
  failed_at timestamptz,
  unique (generation_id, athlete_alias),
  unique (job_id, attempt_no),
  constraint onflows_analysis_generations_period check (
    period_start is null or period_end is null or period_start <= period_end
  ),
  constraint onflows_analysis_generations_ready_payload check (
    status not in ('READY', 'ACTIVE')
    or (
      snapshot_schema_version is not null
      and snapshot_hash is not null
      and jsonb_typeof(snapshot_payload) = 'object'
      and period_start is not null
      and period_end is not null
      and as_of is not null
      and jsonb_typeof(provenance) = 'object'
      and activity_count is not null
      and activity_set_generation_id is not null
      and ready_at is not null
    )
  ),
  constraint onflows_analysis_generations_active_fields check (
    status <> 'ACTIVE'
    or (activated_revision is not null and activated_at is not null)
  )
);

alter table public.onflows_analysis_generations
  add constraint onflows_analysis_generations_job_fk
  foreign key (job_id)
  references public.onflows_sync_jobs(job_id)
  on update restrict on delete no action
  deferrable initially deferred;

alter table public.onflows_analysis_generations
  add constraint onflows_analysis_generations_base_generation_fk
  foreign key (base_generation_id)
  references public.onflows_analysis_generations(generation_id)
  on delete set null
  deferrable initially deferred;

alter table public.onflows_analysis_generations
  add constraint onflows_analysis_generations_activity_set_fk
  foreign key (activity_set_generation_id, athlete_alias)
  references public.onflows_analysis_generations(generation_id, athlete_alias)
  deferrable initially deferred;

alter table public.onflows_athlete_analysis_state
  add constraint onflows_athlete_analysis_state_active_generation_fk
  foreign key (active_generation_id, athlete_alias)
  references public.onflows_analysis_generations(generation_id, athlete_alias)
  deferrable initially deferred;

alter table public.onflows_sync_jobs
  add constraint onflows_sync_jobs_current_generation_fk
  foreign key (current_generation_id, athlete_alias)
  references public.onflows_analysis_generations(generation_id, athlete_alias)
  deferrable initially deferred;

create unique index onflows_analysis_generations_one_active_per_athlete
  on public.onflows_analysis_generations (athlete_alias)
  where status = 'ACTIVE';
create index onflows_analysis_generations_athlete_history
  on public.onflows_analysis_generations (athlete_alias, activated_revision desc, created_at desc);
create index onflows_analysis_generations_base
  on public.onflows_analysis_generations (base_generation_id)
  where base_generation_id is not null;
create index onflows_analysis_generations_activity_set
  on public.onflows_analysis_generations (activity_set_generation_id)
  where activity_set_generation_id is not null;

-- Composite keys make it impossible for a generation to pin a run belonging
-- to another athlete or another activity, even through a server-side bug.
create unique index onflows_activity_model_inputs_scoped_key
  on public.onflows_activity_model_inputs (input_key, athlete_alias, activity_ref);
create unique index onflows_activity_derived_runs_scoped_key
  on public.onflows_activity_derived_runs (run_key, athlete_alias, activity_ref);
create unique index onflows_activity_derived_runs_scoped_input_key
  on public.onflows_activity_derived_runs (
    run_key, input_key, athlete_alias, activity_ref
  );
create unique index onflows_activity_canonical_runs_scoped_key
  on public.onflows_activity_canonical_runs (run_key, athlete_alias, activity_ref);

-- Preserve the same referential integrity while allowing a populated athlete
-- graph to disappear atomically through the connection's cascade.
alter table public.onflows_activity_derived_runs
  drop constraint if exists onflows_activity_derived_runs_input_key_fkey;
alter table public.onflows_activity_derived_runs
  add constraint onflows_activity_derived_runs_input_key_fkey
  foreign key (input_key)
  references public.onflows_activity_model_inputs(input_key)
  on update restrict on delete no action
  deferrable initially deferred;

alter table public.onflows_activity_canonical_runs
  drop constraint if exists
    onflows_activity_canonical_runs_athlete_alias_activity_ref_fkey;
alter table public.onflows_activity_canonical_runs
  add constraint onflows_activity_canonical_runs_athlete_alias_activity_ref_fkey
  foreign key (athlete_alias, activity_ref)
  references public.onflows_activity_catalog(athlete_alias, activity_ref)
  on update restrict on delete no action
  deferrable initially deferred;

create table public.onflows_analysis_generation_activities (
  generation_id uuid not null,
  athlete_alias text not null,
  activity_ref text not null,
  start_at_utc timestamptz,
  local_date date,
  catalog_payload jsonb not null check (jsonb_typeof(catalog_payload) = 'object'),
  payload_hash text not null check (length(payload_hash) = 64),
  input_key text,
  canonical_run_key text,
  shadow_run_key text,
  created_at timestamptz not null default now(),
  constraint onflows_generation_activity_shadow_input check (
    shadow_run_key is null or input_key is not null
  ),
  primary key (generation_id, activity_ref),
  foreign key (generation_id, athlete_alias)
    references public.onflows_analysis_generations(generation_id, athlete_alias)
    on update restrict on delete cascade,
  foreign key (athlete_alias, activity_ref)
    references public.onflows_activity_catalog(athlete_alias, activity_ref)
    on update restrict on delete no action
    deferrable initially deferred,
  foreign key (input_key, athlete_alias, activity_ref)
    references public.onflows_activity_model_inputs(input_key, athlete_alias, activity_ref)
    on update restrict on delete no action
    deferrable initially deferred,
  foreign key (canonical_run_key, athlete_alias, activity_ref)
    references public.onflows_activity_canonical_runs(run_key, athlete_alias, activity_ref)
    on update restrict on delete no action
    deferrable initially deferred,
  foreign key (shadow_run_key, input_key, athlete_alias, activity_ref)
    references public.onflows_activity_derived_runs(
      run_key, input_key, athlete_alias, activity_ref
    )
    on update restrict on delete no action
    deferrable initially deferred
);

create index onflows_generation_activities_calendar
  on public.onflows_analysis_generation_activities
    (generation_id, local_date, start_at_utc, activity_ref);
create index onflows_generation_activities_navigation
  on public.onflows_analysis_generation_activities
    (generation_id, start_at_utc, activity_ref);

alter table public.onflows_training_snapshots
  add column generation_id uuid,
  add column revision bigint not null default 0 check (revision >= 0),
  add column activated_at timestamptz;

alter table public.onflows_training_snapshots
  add constraint onflows_training_snapshots_athlete_fk
  foreign key (athlete_alias)
  references public.onflows_intervals_connections(athlete_alias)
  on update cascade on delete cascade
  not valid;
alter table public.onflows_training_snapshots
  add constraint onflows_training_snapshots_generation_fk
  foreign key (generation_id, athlete_alias)
  references public.onflows_analysis_generations(generation_id, athlete_alias)
  deferrable initially deferred;

insert into public.onflows_athlete_analysis_state (athlete_alias)
select athlete_alias from public.onflows_intervals_connections
on conflict (athlete_alias) do nothing;

alter table public.onflows_athlete_analysis_state enable row level security;
alter table public.onflows_sync_jobs enable row level security;
alter table public.onflows_analysis_generations enable row level security;
alter table public.onflows_analysis_generation_activities enable row level security;

revoke all on table public.onflows_athlete_analysis_state from public, anon, authenticated;
revoke all on table public.onflows_sync_jobs from public, anon, authenticated;
revoke all on table public.onflows_analysis_generations from public, anon, authenticated;
revoke all on table public.onflows_analysis_generation_activities from public, anon, authenticated;
revoke all on table public.onflows_athlete_analysis_state from service_role;
revoke all on table public.onflows_sync_jobs from service_role;
revoke all on table public.onflows_analysis_generations from service_role;
revoke all on table public.onflows_analysis_generation_activities from service_role;
grant select on table public.onflows_athlete_analysis_state to service_role;
grant select on table public.onflows_sync_jobs to service_role;
grant select on table public.onflows_analysis_generations to service_role;
grant select on table public.onflows_analysis_generation_activities to service_role;

create function public.enqueue_onflows_sync_job(
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
  on conflict (athlete_alias) do nothing;

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

create function public.claim_onflows_sync_job(
  p_worker_id text,
  p_lease_seconds integer default 300
) returns table (
  job_id uuid,
  athlete_alias text,
  job_kind text,
  request_payload jsonb,
  request_sequence bigint,
  generation_id uuid,
  attempt_no smallint,
  base_generation_id uuid,
  base_revision bigint,
  base_activity_set_hash text,
  lease_token uuid,
  lease_expires_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  expired_job public.onflows_sync_jobs%rowtype;
  selected_job public.onflows_sync_jobs%rowtype;
  state_row public.onflows_athlete_analysis_state%rowtype;
  new_generation public.onflows_analysis_generations%rowtype;
  new_lease uuid;
  lease_until timestamptz;
begin
  if length(p_worker_id) not between 1 and 128
     or p_lease_seconds not between 30 and 3600 then
    raise exception 'invalid worker lease request';
  end if;

  -- Requeue abandoned work before claiming.  The rotated token and failed
  -- attempt generation make a late completion from the old worker harmless.
  for expired_job in
    select * from public.onflows_sync_jobs j
    where j.status = 'RUNNING' and j.lease_expires_at < now()
    order by j.lease_expires_at
    for update skip locked
    limit 20
  loop
    update public.onflows_analysis_generations g
    set status = 'FAILED', failed_at = now()
    where g.generation_id = expired_job.current_generation_id
      and g.status in ('BUILDING', 'READY');

    select * into state_row
    from public.onflows_athlete_analysis_state s
    where s.athlete_alias = expired_job.athlete_alias
    for update;

    if exists (
      select 1 from public.onflows_sync_jobs successor
      where successor.athlete_alias = expired_job.athlete_alias
        and successor.job_kind = expired_job.job_kind
        and successor.job_id <> expired_job.job_id
        and successor.status in ('QUEUED', 'RETRY_WAIT')
    ) then
      update public.onflows_sync_jobs j
      set status = 'SUPERSEDED', error_code = 'LEASE_EXPIRED',
          lease_token = null, lease_owner = null, lease_expires_at = null,
          current_generation_id = null, progress_stage = 'SUPERSEDED',
          progress_percent = null, completed_at = now(), updated_at = now()
      where j.job_id = expired_job.job_id;
    elsif expired_job.attempt_count < expired_job.max_attempts then
      update public.onflows_sync_jobs j
      set status = 'RETRY_WAIT', available_at = now(),
          lease_token = null, lease_owner = null, lease_expires_at = null,
          current_generation_id = null, progress_stage = null,
          progress_percent = null, updated_at = now()
      where j.job_id = expired_job.job_id;
    else
      update public.onflows_sync_jobs j
      set status = 'FAILED', error_code = 'LEASE_EXPIRED',
          lease_token = null, lease_owner = null, lease_expires_at = null,
          current_generation_id = null, completed_at = now(), updated_at = now()
      where j.job_id = expired_job.job_id;
    end if;
  end loop;

  select j.* into selected_job
  from public.onflows_sync_jobs j
  join public.onflows_athlete_analysis_state s
    on s.athlete_alias = j.athlete_alias
  where j.status in ('QUEUED', 'RETRY_WAIT')
    and j.available_at <= now()
    and not exists (
      select 1 from public.onflows_sync_jobs running
      where running.athlete_alias = j.athlete_alias
        and running.status = 'RUNNING'
    )
  order by j.priority desc, j.available_at, j.requested_at, j.job_id
  for update of j, s skip locked
  limit 1;

  if not found then
    return;
  end if;

  select * into state_row
  from public.onflows_athlete_analysis_state s
  where s.athlete_alias = selected_job.athlete_alias;

  new_lease := extensions.gen_random_uuid();
  lease_until := now() + make_interval(secs => p_lease_seconds);

  insert into public.onflows_analysis_generations (
    athlete_alias, job_id, attempt_no, job_kind, request_sequence,
    base_generation_id, base_revision
  ) values (
    selected_job.athlete_alias, selected_job.job_id,
    (selected_job.attempt_count + 1)::smallint,
    selected_job.job_kind, selected_job.request_sequence,
    state_row.active_generation_id, state_row.active_revision
  ) returning * into new_generation;

  update public.onflows_sync_jobs j
  set status = 'RUNNING', attempt_count = selected_job.attempt_count + 1,
      lease_token = new_lease, lease_owner = p_worker_id,
      lease_expires_at = lease_until,
      current_generation_id = new_generation.generation_id,
      progress_stage = 'CLAIMED', progress_percent = 0,
      started_at = coalesce(j.started_at, now()), completed_at = null,
      error_code = null, updated_at = now()
  where j.job_id = selected_job.job_id;

  return query select selected_job.job_id, selected_job.athlete_alias,
    selected_job.job_kind, selected_job.request_payload,
    selected_job.request_sequence, new_generation.generation_id,
    new_generation.attempt_no, new_generation.base_generation_id,
    new_generation.base_revision,
    (
      select g.provenance ->> 'activity_set_hash'
      from public.onflows_analysis_generations g
      where g.generation_id = new_generation.base_generation_id
    ),
    new_lease, lease_until;
end;
$$;

create function public.renew_onflows_sync_lease(
  p_job_id uuid,
  p_generation_id uuid,
  p_lease_token uuid,
  p_lease_seconds integer default 300
) returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  changed integer;
begin
  if p_lease_seconds not between 30 and 3600 then
    return false;
  end if;
  update public.onflows_sync_jobs j
  set lease_expires_at = now() + make_interval(secs => p_lease_seconds),
      updated_at = now()
  where j.job_id = p_job_id
    and j.current_generation_id = p_generation_id
    and j.lease_token = p_lease_token
    and j.status = 'RUNNING'
    and j.lease_expires_at >= now();
  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create function public.stage_onflows_analysis_generation(
  p_job_id uuid,
  p_generation_id uuid,
  p_lease_token uuid,
  p_snapshot_payload jsonb,
  p_snapshot_hash text,
  p_period_start date,
  p_period_end date,
  p_as_of date,
  p_provenance jsonb,
  p_activities jsonb,
  p_inherit_activities boolean default false
) returns table (outcome text, activity_count integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
  locked_job public.onflows_sync_jobs%rowtype;
  locked_generation public.onflows_analysis_generations%rowtype;
  staged_count integer;
  activity_set_id uuid;
  job_found boolean;
  generation_found boolean;
begin
  select * into locked_job from public.onflows_sync_jobs j
  where j.job_id = p_job_id for update;
  job_found := found;
  select * into locked_generation from public.onflows_analysis_generations g
  where g.generation_id = p_generation_id for update;
  generation_found := found;

  if not job_found or not generation_found then
    raise exception 'sync generation lease is unavailable';
  end if;

  if locked_generation.status = 'READY' then
    if locked_generation.snapshot_hash = p_snapshot_hash then
      return query select 'ALREADY_READY'::text, locked_generation.activity_count;
      return;
    end if;
    raise exception 'generation is already staged with different content';
  end if;

  if locked_job.status <> 'RUNNING'
     or locked_job.current_generation_id <> p_generation_id
     or locked_job.lease_token <> p_lease_token
     or locked_job.lease_expires_at < now()
     or locked_generation.job_id <> p_job_id
     or locked_generation.status <> 'BUILDING' then
    raise exception 'sync generation lease is unavailable';
  end if;

  if length(p_snapshot_hash) <> 64
     or p_period_start > p_period_end
     or jsonb_typeof(p_snapshot_payload) <> 'object'
     or p_snapshot_payload ->> 'schema_version' <> 'athlete-snapshot-v1'
     or jsonb_typeof(p_provenance) <> 'object'
     or jsonb_typeof(p_activities) <> 'array' then
    raise exception 'analysis generation payload is invalid';
  end if;

  if coalesce(p_snapshot_payload #>> '{training_status,athlete_id}', '')
       <> locked_generation.athlete_alias
     or coalesce(p_snapshot_payload #>> '{load_history,athlete_id}', '')
       <> locked_generation.athlete_alias
     or coalesce(p_snapshot_payload #>> '{recovery_history,athlete_id}', '')
       <> locked_generation.athlete_alias then
    raise exception 'analysis generation athlete scope is invalid';
  end if;

  if p_inherit_activities then
    if jsonb_array_length(p_activities) <> 0 then
      raise exception 'inherited activity generation must not include rows';
    end if;
    if locked_generation.base_generation_id is not null then
      select coalesce(g.activity_set_generation_id, g.generation_id)
      into activity_set_id
      from public.onflows_analysis_generations g
      where g.generation_id = locked_generation.base_generation_id
        and g.athlete_alias = locked_generation.athlete_alias;
      if activity_set_id is null then
        raise exception 'base activity generation is unavailable';
      end if;
    else
      activity_set_id := p_generation_id;
      insert into public.onflows_analysis_generation_activities (
        generation_id, athlete_alias, activity_ref, start_at_utc, local_date,
        catalog_payload, payload_hash, input_key, canonical_run_key, shadow_run_key
      )
      select p_generation_id, c.athlete_alias, c.activity_ref, c.start_at_utc,
        c.local_date, to_jsonb(c),
        encode(extensions.digest(convert_to(to_jsonb(c)::text, 'UTF8'), 'sha256'), 'hex'),
        d.input_key, c.latest_canonical_run_key, c.latest_shadow_run_key
      from public.onflows_activity_catalog c
      left join public.onflows_activity_derived_runs d
        on d.run_key = c.latest_shadow_run_key
       and d.athlete_alias = c.athlete_alias
       and d.activity_ref = c.activity_ref
      where c.athlete_alias = locked_generation.athlete_alias
        and c.local_date between p_period_start and p_period_end;
    end if;
  else
    activity_set_id := p_generation_id;
    insert into public.onflows_analysis_generation_activities (
      generation_id, athlete_alias, activity_ref, start_at_utc, local_date,
      catalog_payload, payload_hash, input_key, canonical_run_key, shadow_run_key
    )
    with normalized as (
      select
        coalesce(item ->> 'activity_ref', item #>> '{catalog_payload,activity_ref}')
          as activity_ref,
        coalesce(item ->> 'start_at_utc', item #>> '{catalog_payload,start_at_utc}')
          as start_at_utc,
        coalesce(item ->> 'local_date', item #>> '{catalog_payload,local_date}')
          as local_date,
        coalesce(
          item -> 'catalog_payload',
          item - array[
            'payload_hash', 'input_key', 'canonical_run_key', 'shadow_run_key'
          ]::text[]
        ) as catalog_payload,
        nullif(item ->> 'payload_hash', '') as supplied_payload_hash,
        nullif(item ->> 'input_key', '') as supplied_input_key,
        coalesce(
          nullif(item ->> 'canonical_run_key', ''),
          nullif(item ->> 'latest_canonical_run_key', ''),
          nullif(item #>> '{catalog_payload,latest_canonical_run_key}', '')
        ) as canonical_run_key,
        coalesce(
          nullif(item ->> 'shadow_run_key', ''),
          nullif(item ->> 'latest_shadow_run_key', ''),
          nullif(item #>> '{catalog_payload,latest_shadow_run_key}', '')
        ) as shadow_run_key
      from jsonb_array_elements(p_activities) item
    ), resolved as (
      select n.*, d.input_key as shadow_input_key
      from normalized n
      left join public.onflows_activity_derived_runs d
        on d.run_key = n.shadow_run_key
       and d.athlete_alias = locked_generation.athlete_alias
       and d.activity_ref = n.activity_ref
    )
    select p_generation_id, locked_generation.athlete_alias,
      r.activity_ref,
      nullif(r.start_at_utc, '')::timestamptz,
      nullif(r.local_date, '')::date,
      r.catalog_payload,
      coalesce(
        r.supplied_payload_hash,
        encode(
          extensions.digest(
            convert_to(r.catalog_payload::text, 'UTF8'), 'sha256'
          ),
          'hex'
        )
      ),
      coalesce(r.supplied_input_key, r.shadow_input_key),
      r.canonical_run_key,
      r.shadow_run_key
    from resolved r;
  end if;

  select count(*)::integer into staged_count
  from public.onflows_analysis_generation_activities a
  where a.generation_id = activity_set_id;

  if exists (
    select 1
    from jsonb_array_elements(
      coalesce(p_snapshot_payload #> '{load_history,activities}', '[]'::jsonb)
    ) snapshot_activity
    where not exists (
      select 1 from public.onflows_analysis_generation_activities staged
      where staged.generation_id = activity_set_id
        and staged.activity_ref = snapshot_activity ->> 'activity_ref'
    )
  ) then
    raise exception 'snapshot references an unstaged activity';
  end if;

  update public.onflows_analysis_generations g
  set status = 'READY',
      snapshot_schema_version = p_snapshot_payload ->> 'schema_version',
      snapshot_hash = p_snapshot_hash,
      snapshot_payload = p_snapshot_payload,
      period_start = p_period_start,
      period_end = p_period_end,
      as_of = p_as_of,
      provenance = p_provenance,
      activity_count = staged_count,
      activity_set_generation_id = activity_set_id,
      ready_at = now()
  where g.generation_id = p_generation_id;

  update public.onflows_sync_jobs j
  set progress_stage = 'READY', progress_percent = 95, updated_at = now()
  where j.job_id = p_job_id;

  return query select 'READY'::text, staged_count;
end;
$$;

create function public.activate_onflows_analysis_generation(
  p_job_id uuid,
  p_generation_id uuid,
  p_lease_token uuid
) returns table (
  outcome text,
  active_generation_id uuid,
  active_revision bigint
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  locked_job public.onflows_sync_jobs%rowtype;
  locked_generation public.onflows_analysis_generations%rowtype;
  state_row public.onflows_athlete_analysis_state%rowtype;
  next_revision bigint;
  job_found boolean;
begin
  -- All worker lifecycle RPCs use job -> generation -> state lock order.
  select * into locked_job from public.onflows_sync_jobs j
  where j.job_id = p_job_id for update;
  job_found := found;

  select * into locked_generation from public.onflows_analysis_generations g
  where g.generation_id = p_generation_id for update;
  if not found then
    return query select 'LEASE_LOST'::text, null::uuid, null::bigint;
    return;
  end if;

  select * into state_row from public.onflows_athlete_analysis_state s
  where s.athlete_alias = locked_generation.athlete_alias for update;

  -- A repeated activation after a lost HTTP response is safe and deterministic.
  if locked_generation.status = 'ACTIVE'
     and state_row.active_generation_id = p_generation_id then
    return query select 'ACTIVATED'::text, state_row.active_generation_id,
      state_row.active_revision;
    return;
  end if;

  if not job_found
     or locked_job.status <> 'RUNNING'
     or locked_job.current_generation_id <> p_generation_id
     or locked_job.lease_token <> p_lease_token
     or locked_job.lease_expires_at < now()
     or locked_generation.job_id <> p_job_id then
    return query select 'LEASE_LOST'::text, state_row.active_generation_id,
      state_row.active_revision;
    return;
  end if;

  if locked_generation.status <> 'READY'
     or state_row.active_revision <> locked_generation.base_revision
     or state_row.active_generation_id is distinct from locked_generation.base_generation_id then
    if locked_generation.status in ('BUILDING', 'READY') then
      update public.onflows_analysis_generations g
      set status = 'SUPERSEDED', superseded_at = now()
      where g.generation_id = p_generation_id;
    end if;
    update public.onflows_sync_jobs j
    set status = 'SUPERSEDED', lease_token = null, lease_owner = null,
        lease_expires_at = null, current_generation_id = null,
        completed_at = now(), updated_at = now(),
        progress_stage = 'SUPERSEDED'
    where j.job_id = p_job_id;
    return query select 'STALE'::text, state_row.active_generation_id,
      state_row.active_revision;
    return;
  end if;

  next_revision := state_row.active_revision + 1;

  update public.onflows_analysis_generations g
  set status = 'SUPERSEDED', superseded_at = now()
  where g.generation_id = state_row.active_generation_id
    and g.status = 'ACTIVE';

  update public.onflows_analysis_generations g
  set status = 'ACTIVE', activated_revision = next_revision,
      activated_at = now()
  where g.generation_id = p_generation_id;

  update public.onflows_athlete_analysis_state s
  set active_generation_id = p_generation_id,
      active_revision = next_revision,
      updated_at = now()
  where s.athlete_alias = locked_generation.athlete_alias;

  insert into public.onflows_training_snapshots (
    athlete_alias, payload, generation_id, revision, activated_at, updated_at
  ) values (
    locked_generation.athlete_alias, locked_generation.snapshot_payload,
    p_generation_id, next_revision, now(), now()
  )
  on conflict (athlete_alias) do update
  set payload = excluded.payload,
      generation_id = excluded.generation_id,
      revision = excluded.revision,
      activated_at = excluded.activated_at,
      updated_at = excluded.updated_at;

  update public.onflows_sync_jobs j
  set status = 'SUCCEEDED', lease_token = null, lease_owner = null,
      lease_expires_at = null, current_generation_id = null,
      completed_at = now(), updated_at = now(),
      progress_stage = 'ACTIVATED', progress_percent = 100
  where j.job_id = p_job_id;

  return query select 'ACTIVATED'::text, p_generation_id, next_revision;
end;
$$;

create function public.fail_onflows_sync_job(
  p_job_id uuid,
  p_generation_id uuid,
  p_lease_token uuid,
  p_error_code text,
  p_retryable boolean,
  p_retry_after_seconds integer default null
) returns table (status text, available_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  locked_job public.onflows_sync_jobs%rowtype;
  state_row public.onflows_athlete_analysis_state%rowtype;
  retry_at timestamptz;
  retry_delay integer;
  successor_exists boolean;
begin
  select * into locked_job from public.onflows_sync_jobs j
  where j.job_id = p_job_id for update;
  if not found
     or locked_job.status <> 'RUNNING'
     or locked_job.current_generation_id <> p_generation_id
     or locked_job.lease_token <> p_lease_token
     or locked_job.lease_expires_at < now() then
    return query select 'LEASE_LOST'::text, null::timestamptz;
    return;
  end if;
  if p_error_code !~ '^[A-Z0-9_]{1,64}$' then
    raise exception 'invalid sync error code';
  end if;
  if p_retry_after_seconds is not null
     and p_retry_after_seconds not between 1 and 86400 then
    raise exception 'invalid retry delay';
  end if;

  update public.onflows_analysis_generations g
  set status = 'FAILED', failed_at = now()
  where g.generation_id = p_generation_id
    and g.status in ('BUILDING', 'READY');

  select * into state_row
  from public.onflows_athlete_analysis_state s
  where s.athlete_alias = locked_job.athlete_alias
  for update;
  select exists (
    select 1 from public.onflows_sync_jobs successor
    where successor.athlete_alias = locked_job.athlete_alias
      and successor.job_kind = locked_job.job_kind
      and successor.job_id <> locked_job.job_id
      and successor.status in ('QUEUED', 'RETRY_WAIT')
  ) into successor_exists;

  if p_retryable and successor_exists then
    update public.onflows_sync_jobs j
    set status = 'SUPERSEDED', lease_token = null, lease_owner = null,
        lease_expires_at = null, current_generation_id = null,
        completed_at = now(), progress_stage = 'SUPERSEDED',
        progress_percent = null, error_code = p_error_code, updated_at = now()
    where j.job_id = p_job_id;
    return query select 'SUPERSEDED'::text, null::timestamptz;
  elsif p_retryable and locked_job.attempt_count < locked_job.max_attempts then
    retry_delay := coalesce(
      p_retry_after_seconds,
      least(300, 5 * (2 ^ greatest(0, locked_job.attempt_count - 1)))::integer
    );
    retry_at := now() + make_interval(secs => retry_delay);
    update public.onflows_sync_jobs j
    set status = 'RETRY_WAIT', available_at = retry_at,
        lease_token = null, lease_owner = null, lease_expires_at = null,
        current_generation_id = null, progress_stage = null,
        progress_percent = null, error_code = p_error_code, updated_at = now()
    where j.job_id = p_job_id;
    return query select 'RETRY_WAIT'::text, retry_at;
  else
    update public.onflows_sync_jobs j
    set status = 'FAILED', lease_token = null, lease_owner = null,
        lease_expires_at = null, current_generation_id = null,
        completed_at = now(),
        progress_stage = 'FAILED', error_code = p_error_code, updated_at = now()
    where j.job_id = p_job_id;
    return query select 'FAILED'::text, null::timestamptz;
  end if;
end;
$$;

-- Insert-only canonical result.  Unlike the pilot publication RPC, this does
-- not advance a mutable catalog pointer before generation activation.
create function public.store_onflows_canonical_activity_result(
  p_run_key text,
  p_athlete_alias text,
  p_activity_ref text,
  p_scientific_input_hash text,
  p_result_hash text,
  p_schema_version text,
  p_model_version text,
  p_result_payload jsonb
) returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.onflows_activity_canonical_runs (
    run_key, athlete_alias, activity_ref, scientific_input_hash,
    result_hash, schema_version, model_version, result_payload
  ) values (
    p_run_key, p_athlete_alias, p_activity_ref, p_scientific_input_hash,
    p_result_hash, p_schema_version, p_model_version, p_result_payload
  ) on conflict (run_key) do nothing;
end;
$$;

-- One coherent aggregate read.  The legacy revision-zero snapshot remains
-- readable until the first generation is activated.
create function public.active_onflows_analysis(p_athlete_alias text)
returns table (
  generation_id uuid,
  revision bigint,
  analysis_as_of date,
  activated_at timestamptz,
  snapshot_payload jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  with active as (
    select g.generation_id, s.active_revision as revision, g.as_of,
      g.activated_at, g.snapshot_payload
    from public.onflows_athlete_analysis_state s
    join public.onflows_analysis_generations g
      on g.generation_id = s.active_generation_id
     and g.athlete_alias = s.athlete_alias
     and g.status = 'ACTIVE'
    where s.athlete_alias = p_athlete_alias
  ), legacy as (
    select null::uuid as generation_id, 0::bigint as revision,
      null::date as as_of, t.activated_at, t.payload as snapshot_payload
    from public.onflows_training_snapshots t
    where t.athlete_alias = p_athlete_alias
      and not exists (
        select 1 from public.onflows_athlete_analysis_state state
        where state.athlete_alias = p_athlete_alias
          and state.active_generation_id is not null
      )
  )
  select * from active
  union all
  select * from legacy
  limit 1;
$$;

-- One status read selects the currently actionable job before queued follow-up
-- work, and only falls back to the latest terminal job.
create function public.onflows_sync_state(p_athlete_alias text)
returns table (
  athlete_alias text,
  active_generation_id uuid,
  active_revision bigint,
  request_sequence bigint,
  active_as_of date,
  activated_at timestamptz,
  job_id uuid,
  job_kind text,
  status text,
  progress_stage text,
  progress_percent smallint,
  error_code text,
  requested_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  available_at timestamptz,
  pending_job_count integer
)
language sql
stable
security definer
set search_path = ''
as $$
  select source.athlete_alias,
    state.active_generation_id,
    coalesce(state.active_revision, 0),
    coalesce(state.request_sequence, 0),
    active_generation.as_of,
    active_generation.activated_at,
    selected_job.job_id,
    selected_job.job_kind,
    selected_job.status,
    selected_job.progress_stage,
    selected_job.progress_percent,
    selected_job.error_code,
    selected_job.requested_at,
    selected_job.started_at,
    selected_job.completed_at,
    selected_job.available_at,
    coalesce(pending.count, 0)::integer
  from (select p_athlete_alias as athlete_alias) source
  left join public.onflows_athlete_analysis_state state
    on state.athlete_alias = source.athlete_alias
  left join public.onflows_analysis_generations active_generation
    on active_generation.generation_id = state.active_generation_id
   and active_generation.athlete_alias = source.athlete_alias
   and active_generation.status = 'ACTIVE'
  left join lateral (
    select j.*
    from public.onflows_sync_jobs j
    where j.athlete_alias = source.athlete_alias
    order by
      case
        when j.status = 'RUNNING' then 0
        when j.status in ('QUEUED', 'RETRY_WAIT') then 1
        else 2
      end,
      case when j.status in ('QUEUED', 'RETRY_WAIT') then j.request_sequence end,
      case when j.status not in ('RUNNING', 'QUEUED', 'RETRY_WAIT')
        then j.request_sequence end desc,
      j.requested_at desc,
      j.job_id desc
    limit 1
  ) selected_job on true
  left join lateral (
    select count(*)::integer as count
    from public.onflows_sync_jobs j
    where j.athlete_alias = source.athlete_alias
      and j.status in ('QUEUED', 'RETRY_WAIT')
  ) pending on true;
$$;

-- Calendar metadata and the snapshot used for wellness/recovery annotations
-- are pinned by one statement to the same active generation.
create function public.active_onflows_activity_calendar(
  p_athlete_alias text,
  p_period_start date,
  p_period_end date
) returns table (
  generation_id uuid,
  revision bigint,
  analysis_as_of date,
  activated_at timestamptz,
  snapshot_payload jsonb,
  activities jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  with active as (
    select a.generation_id, a.revision, a.analysis_as_of as as_of,
      a.activated_at, a.snapshot_payload as payload
    from public.active_onflows_analysis(p_athlete_alias) a
  ), pinned_rows as (
    select a.generation_id, ga.start_at_utc, ga.activity_ref,
      ga.catalog_payload || jsonb_build_object(
        'activity_ref', ga.activity_ref,
        'latest_canonical_run_key', ga.canonical_run_key,
        'latest_shadow_run_key', ga.shadow_run_key,
        'input_key', ga.input_key,
        'hrmod_zone_summary', coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
      ) as payload
    from active a
    join public.onflows_analysis_generations g
      on g.generation_id = a.generation_id
     and g.status = 'ACTIVE'
    join public.onflows_analysis_generation_activities ga
      on ga.generation_id = coalesce(
        g.activity_set_generation_id, g.generation_id
      )
     and ga.athlete_alias = p_athlete_alias
    left join public.onflows_activity_derived_runs d
      on d.run_key = ga.shadow_run_key
     and d.athlete_alias = ga.athlete_alias
     and d.activity_ref = ga.activity_ref
    where a.generation_id is not null
      and ga.local_date between p_period_start and p_period_end
    union all
    select null::uuid, c.start_at_utc, c.activity_ref,
      to_jsonb(c) || jsonb_build_object(
        'input_key', d.input_key,
        'hrmod_zone_summary', coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
      )
    from active a
    join public.onflows_activity_catalog c
      on c.athlete_alias = p_athlete_alias
    left join public.onflows_activity_derived_runs d
      on d.run_key = c.latest_shadow_run_key
     and d.athlete_alias = c.athlete_alias
     and d.activity_ref = c.activity_ref
    where a.generation_id is null
      and c.local_date between p_period_start and p_period_end
  )
  select a.generation_id, a.revision, a.as_of, a.activated_at, a.payload,
    coalesce(
      jsonb_agg(r.payload order by r.start_at_utc, r.activity_ref)
        filter (where r.activity_ref is not null),
      '[]'::jsonb
    )
  from active a
  left join pinned_rows r
    on r.generation_id is not distinct from a.generation_id
  group by a.generation_id, a.revision, a.as_of, a.activated_at, a.payload;
$$;

-- The diagnostic index is resolved from the same generation pointers.  A
-- selected run is immutable, so model metadata and zone summaries cannot drift.
create function public.active_onflows_activity_shadow_index(p_athlete_alias text)
returns table (
  activity_ref text,
  run_key text,
  vflat_model_version text,
  hrmod_model_version text,
  terrain_model_version text,
  zone_summary jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  with active as (
    select coalesce(g.activity_set_generation_id, g.generation_id)
      as generation_id
    from public.onflows_athlete_analysis_state s
    join public.onflows_analysis_generations g
      on g.generation_id = s.active_generation_id
     and g.athlete_alias = s.athlete_alias
     and g.status = 'ACTIVE'
    where s.athlete_alias = p_athlete_alias
  )
  select ga.activity_ref, d.run_key, d.vflat_model_version,
    d.hrmod_model_version, d.terrain_model_version,
    coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
  from active a
  join public.onflows_analysis_generation_activities ga
    on ga.generation_id = a.generation_id
   and ga.athlete_alias = p_athlete_alias
  join public.onflows_activity_derived_runs d
    on d.run_key = ga.shadow_run_key
   and d.input_key = ga.input_key
   and d.athlete_alias = ga.athlete_alias
   and d.activity_ref = ga.activity_ref
  union all
  select c.activity_ref, d.run_key, d.vflat_model_version,
    d.hrmod_model_version, d.terrain_model_version,
    coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
  from public.onflows_activity_catalog c
  join public.onflows_activity_derived_runs d
    on d.run_key = c.latest_shadow_run_key
   and d.athlete_alias = c.athlete_alias
   and d.activity_ref = c.activity_ref
  where c.athlete_alias = p_athlete_alias
    and not exists (
      select 1 from public.onflows_athlete_analysis_state state
      where state.athlete_alias = p_athlete_alias
        and state.active_generation_id is not null
    )
  order by activity_ref;
$$;

-- Resolve one exact activity pointer from the active generation.  Subsequent
-- reads address immutable input/run primary keys, never global created_at order.
create function public.active_onflows_activity(
  p_athlete_alias text,
  p_activity_ref text
) returns table (
  generation_id uuid,
  revision bigint,
  analysis_as_of date,
  activated_at timestamptz,
  catalog_payload jsonb,
  input_key text,
  canonical_run_key text,
  shadow_run_key text,
  previous_activity_ref text,
  next_activity_ref text
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  active_row record;
  activity_row record;
  previous_ref text;
  following_ref text;
  activity_set_id uuid;
begin
  select a.generation_id, a.revision, a.analysis_as_of, a.activated_at
  into active_row
  from public.active_onflows_analysis(p_athlete_alias) a;
  if not found then
    return;
  end if;

  if active_row.generation_id is not null then
    select coalesce(g.activity_set_generation_id, g.generation_id)
    into activity_set_id
    from public.onflows_analysis_generations g
    where g.generation_id = active_row.generation_id
      and g.athlete_alias = p_athlete_alias
      and g.status = 'ACTIVE';
    if activity_set_id is null then
      return;
    end if;
    select ga.* into activity_row
    from public.onflows_analysis_generation_activities ga
    where ga.generation_id = activity_set_id
      and ga.athlete_alias = p_athlete_alias
      and ga.activity_ref = p_activity_ref;
    if not found then
      return;
    end if;
    if activity_row.start_at_utc is not null then
      select ga.activity_ref into previous_ref
      from public.onflows_analysis_generation_activities ga
      where ga.generation_id = activity_set_id
        and ga.athlete_alias = p_athlete_alias
        and ga.start_at_utc is not null
        and (ga.start_at_utc, ga.activity_ref)
          < (activity_row.start_at_utc, activity_row.activity_ref)
      order by ga.start_at_utc desc, ga.activity_ref desc limit 1;
      select ga.activity_ref into following_ref
      from public.onflows_analysis_generation_activities ga
      where ga.generation_id = activity_set_id
        and ga.athlete_alias = p_athlete_alias
        and ga.start_at_utc is not null
        and (ga.start_at_utc, ga.activity_ref)
          > (activity_row.start_at_utc, activity_row.activity_ref)
      order by ga.start_at_utc, ga.activity_ref limit 1;
    end if;
    return query select active_row.generation_id, active_row.revision,
      active_row.analysis_as_of, active_row.activated_at,
      activity_row.catalog_payload || jsonb_build_object(
        'activity_ref', activity_row.activity_ref,
        'latest_canonical_run_key', activity_row.canonical_run_key,
        'latest_shadow_run_key', activity_row.shadow_run_key
      ),
      activity_row.input_key, activity_row.canonical_run_key,
      activity_row.shadow_run_key, previous_ref, following_ref;
    return;
  end if;

  select c.*,
    coalesce(d.input_key, latest_shadow.input_key, latest_input.input_key)
      as resolved_input_key,
    coalesce(d.run_key, latest_shadow.run_key) as resolved_shadow_run_key
  into activity_row
  from public.onflows_activity_catalog c
  left join public.onflows_activity_derived_runs d
    on d.run_key = c.latest_shadow_run_key
   and d.athlete_alias = c.athlete_alias
   and d.activity_ref = c.activity_ref
  left join lateral (
    select derived.run_key, derived.input_key
    from public.onflows_activity_derived_runs derived
    where derived.athlete_alias = c.athlete_alias
      and derived.activity_ref = c.activity_ref
    order by derived.created_at desc, derived.run_key desc
    limit 1
  ) latest_shadow on true
  left join lateral (
    select model_input.input_key
    from public.onflows_activity_model_inputs model_input
    where model_input.athlete_alias = c.athlete_alias
      and model_input.activity_ref = c.activity_ref
    order by model_input.created_at desc, model_input.input_key desc
    limit 1
  ) latest_input on true
  where c.athlete_alias = p_athlete_alias
    and c.activity_ref = p_activity_ref;
  if not found then
    return;
  end if;
  if activity_row.start_at_utc is not null then
    select c.activity_ref into previous_ref
    from public.onflows_activity_catalog c
    where c.athlete_alias = p_athlete_alias
      and c.start_at_utc is not null
      and (c.start_at_utc, c.activity_ref)
        < (activity_row.start_at_utc, activity_row.activity_ref)
    order by c.start_at_utc desc, c.activity_ref desc limit 1;
    select c.activity_ref into following_ref
    from public.onflows_activity_catalog c
    where c.athlete_alias = p_athlete_alias
      and c.start_at_utc is not null
      and (c.start_at_utc, c.activity_ref)
        > (activity_row.start_at_utc, activity_row.activity_ref)
    order by c.start_at_utc, c.activity_ref limit 1;
  end if;
  return query select null::uuid, active_row.revision, null::date,
    active_row.activated_at,
    to_jsonb(activity_row) - 'resolved_input_key' - 'resolved_shadow_run_key',
    activity_row.resolved_input_key, activity_row.latest_canonical_run_key,
    activity_row.resolved_shadow_run_key, previous_ref, following_ref;
end;
$$;

-- One DB statement for the activity page: catalog navigation, exact model
-- input and exact immutable shadow all resolve from the same generation pointer.
create function public.active_onflows_activity_view(
  p_athlete_alias text,
  p_activity_ref text
) returns table (
  generation_id uuid,
  revision bigint,
  analysis_as_of date,
  activated_at timestamptz,
  catalog_payload jsonb,
  input_key text,
  canonical_run_key text,
  shadow_run_key text,
  previous_activity_ref text,
  next_activity_ref text,
  series_payload jsonb,
  shadow_payload jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  select pointer.generation_id, pointer.revision, pointer.analysis_as_of,
    pointer.activated_at, pointer.catalog_payload, pointer.input_key,
    pointer.canonical_run_key, pointer.shadow_run_key,
    pointer.previous_activity_ref, pointer.next_activity_ref,
    model_input.input_payload, shadow.result_payload
  from public.active_onflows_activity(p_athlete_alias, p_activity_ref) pointer
  left join public.onflows_activity_model_inputs model_input
    on model_input.input_key = pointer.input_key
   and model_input.athlete_alias = p_athlete_alias
   and model_input.activity_ref = p_activity_ref
  left join public.onflows_activity_derived_runs shadow
    on shadow.run_key = pointer.shadow_run_key
   and shadow.input_key = pointer.input_key
   and shadow.athlete_alias = p_athlete_alias
   and shadow.activity_ref = p_activity_ref;
$$;

-- Operational rollback is intentionally server-only and is not exposed by an
-- API route.  It creates a new monotonic activation revision; it never rewrites
-- the immutable target generation content.
create function public.rollback_onflows_analysis_generation(
  p_athlete_alias text,
  p_target_generation_id uuid
) returns table (
  outcome text,
  active_generation_id uuid,
  active_revision bigint
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  state_row public.onflows_athlete_analysis_state%rowtype;
  target_row public.onflows_analysis_generations%rowtype;
  pinned_count integer;
  next_revision bigint;
begin
  -- Rollback follows generation -> state, never the inverse worker lock edge.
  select * into target_row
  from public.onflows_analysis_generations g
  where g.generation_id = p_target_generation_id
    and g.athlete_alias = p_athlete_alias
  for update;
  if not found
     or target_row.status <> 'SUPERSEDED'
     or target_row.snapshot_payload is null
     or target_row.snapshot_hash is null
     or target_row.ready_at is null
     or target_row.activity_count is null
     or target_row.activated_revision is null
     or target_row.activated_at is null then
    raise exception 'rollback generation is unavailable';
  end if;

  select * into state_row
  from public.onflows_athlete_analysis_state s
  where s.athlete_alias = p_athlete_alias
  for update;
  if not found or state_row.active_generation_id is null then
    raise exception 'active analysis is unavailable';
  end if;

  select count(*)::integer into pinned_count
  from public.onflows_analysis_generation_activities a
  where a.generation_id = coalesce(
    target_row.activity_set_generation_id, target_row.generation_id
  );
  if pinned_count <> target_row.activity_count then
    raise exception 'rollback generation is incomplete';
  end if;

  next_revision := state_row.active_revision + 1;
  update public.onflows_analysis_generations g
  set status = 'SUPERSEDED', superseded_at = now()
  where g.generation_id = state_row.active_generation_id
    and g.athlete_alias = p_athlete_alias
    and g.status = 'ACTIVE';
  update public.onflows_analysis_generations g
  set status = 'ACTIVE', activated_revision = next_revision,
      activated_at = now(), superseded_at = null
  where g.generation_id = p_target_generation_id;
  update public.onflows_athlete_analysis_state s
  set active_generation_id = p_target_generation_id,
      active_revision = next_revision, updated_at = now()
  where s.athlete_alias = p_athlete_alias;
  update public.onflows_training_snapshots s
  set payload = target_row.snapshot_payload,
      generation_id = p_target_generation_id,
      revision = next_revision,
      activated_at = now(), updated_at = now()
  where s.athlete_alias = p_athlete_alias;

  return query select 'ROLLED_BACK'::text, p_target_generation_id,
    next_revision;
end;
$$;

-- Explicit bounded maintenance; scheduling is deliberately left to operations.
-- Active/rollback-window generations are never removed.
create function public.prune_onflows_analysis_generations(
  p_athlete_alias text,
  p_keep_superseded integer default 5,
  p_terminal_older_than interval default interval '30 days',
  p_batch_limit integer default 100
) returns table (
  deleted_generations integer,
  deleted_activity_rows integer,
  deleted_jobs integer,
  deleted_shadow_runs integer,
  deleted_canonical_runs integer,
  deleted_model_inputs integer,
  deleted_catalog_rows integer
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  candidate_ids uuid[];
  activity_rows integer := 0;
  generation_rows integer := 0;
  job_rows integer := 0;
  shadow_rows integer := 0;
  canonical_rows integer := 0;
  input_rows integer := 0;
  catalog_rows integer := 0;
begin
  if p_keep_superseded not between 1 and 50
     or p_batch_limit not between 1 and 1000
     or p_terminal_older_than < interval '30 days'
     or p_terminal_older_than > interval '365 days' then
    raise exception 'invalid analysis retention policy';
  end if;

  with ranked_superseded as (
    select g.generation_id,
      row_number() over (
        order by g.activated_at desc nulls last, g.created_at desc,
          g.generation_id desc
      ) as recency
    from public.onflows_analysis_generations g
    where g.athlete_alias = p_athlete_alias
      and g.status = 'SUPERSEDED'
  ), candidates as (
    select g.generation_id
    from public.onflows_analysis_generations g
    left join ranked_superseded ranked
      on ranked.generation_id = g.generation_id
    where g.athlete_alias = p_athlete_alias
      and g.created_at < now() - p_terminal_older_than
      and (
        g.status in ('FAILED', 'BUILDING')
        or (g.status = 'SUPERSEDED' and ranked.recency > p_keep_superseded)
      )
      and not exists (
        select 1 from public.onflows_athlete_analysis_state state
        where state.athlete_alias = p_athlete_alias
          and state.active_generation_id = g.generation_id
      )
      and not exists (
        select 1 from public.onflows_sync_jobs running
        where running.current_generation_id = g.generation_id
          and running.status = 'RUNNING'
      )
      and not exists (
        select 1 from public.onflows_analysis_generations child
        where child.base_generation_id = g.generation_id
          and child.status in ('BUILDING', 'READY')
      )
      and not exists (
        select 1 from public.onflows_analysis_generations consumer
        where consumer.activity_set_generation_id = g.generation_id
          and consumer.generation_id <> g.generation_id
      )
    order by g.created_at, g.generation_id
    limit p_batch_limit
  )
  select coalesce(array_agg(c.generation_id), '{}'::uuid[])
  into candidate_ids
  from candidates c;

  select count(*)::integer into activity_rows
  from public.onflows_analysis_generation_activities a
  where a.generation_id = any(candidate_ids);

  update public.onflows_sync_jobs j
  set current_generation_id = null, updated_at = now()
  where j.current_generation_id = any(candidate_ids)
    and j.status in ('SUCCEEDED', 'FAILED', 'SUPERSEDED');

  delete from public.onflows_analysis_generations g
  where g.generation_id = any(candidate_ids);
  get diagnostics generation_rows = row_count;

  with candidates as (
    select j.job_id
    from public.onflows_sync_jobs j
    where j.athlete_alias = p_athlete_alias
      and j.status in ('SUCCEEDED', 'FAILED', 'SUPERSEDED')
      and j.completed_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generations g
        where g.job_id = j.job_id
      )
    order by j.completed_at, j.job_id
    limit p_batch_limit
  )
  delete from public.onflows_sync_jobs j
  using candidates c
  where j.job_id = c.job_id;
  get diagnostics job_rows = row_count;

  -- Once generation reads are active, old unpinned catalog pointers are only a
  -- legacy projection.  Release them first so run -> input -> catalog GC can
  -- converge without weakening the revision-zero fallback.
  with candidates as (
    select catalog.activity_ref
    from public.onflows_activity_catalog catalog
    where catalog.athlete_alias = p_athlete_alias
      and catalog.last_seen_at < now() - p_terminal_older_than
      and (
        catalog.latest_shadow_run_key is not null
        or catalog.latest_canonical_run_key is not null
      )
      and exists (
        select 1
        from public.onflows_athlete_analysis_state state
        join public.onflows_analysis_generations active_generation
          on active_generation.generation_id = state.active_generation_id
         and active_generation.athlete_alias = state.athlete_alias
         and active_generation.status = 'ACTIVE'
        where state.athlete_alias = p_athlete_alias
      )
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.athlete_alias = catalog.athlete_alias
          and retained.activity_ref = catalog.activity_ref
      )
    order by catalog.last_seen_at, catalog.activity_ref
    limit p_batch_limit
  )
  update public.onflows_activity_catalog catalog
  set latest_shadow_run_key = null,
      latest_canonical_run_key = null,
      metadata_synced_at = now()
  from candidates c
  where catalog.athlete_alias = p_athlete_alias
    and catalog.activity_ref = c.activity_ref;

  with candidates as (
    select d.run_key
    from public.onflows_activity_derived_runs d
    where d.athlete_alias = p_athlete_alias
      and d.created_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.shadow_run_key = d.run_key
      )
      and not exists (
        select 1 from public.onflows_activity_catalog catalog
        where catalog.latest_shadow_run_key = d.run_key
      )
    order by d.created_at, d.run_key
    limit p_batch_limit
  )
  delete from public.onflows_activity_derived_runs d
  using candidates c
  where d.run_key = c.run_key;
  get diagnostics shadow_rows = row_count;

  with candidates as (
    select canonical.run_key
    from public.onflows_activity_canonical_runs canonical
    where canonical.athlete_alias = p_athlete_alias
      and canonical.created_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.canonical_run_key = canonical.run_key
      )
      and not exists (
        select 1 from public.onflows_activity_catalog catalog
        where catalog.latest_canonical_run_key = canonical.run_key
      )
    order by canonical.created_at, canonical.run_key
    limit p_batch_limit
  )
  delete from public.onflows_activity_canonical_runs canonical
  using candidates c
  where canonical.run_key = c.run_key;
  get diagnostics canonical_rows = row_count;

  with candidates as (
    select model_input.input_key
    from public.onflows_activity_model_inputs model_input
    where model_input.athlete_alias = p_athlete_alias
      and model_input.created_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.input_key = model_input.input_key
      )
      and not exists (
        select 1 from public.onflows_activity_derived_runs derived
        where derived.input_key = model_input.input_key
      )
    order by model_input.created_at, model_input.input_key
    limit p_batch_limit
  )
  delete from public.onflows_activity_model_inputs model_input
  using candidates c
  where model_input.input_key = c.input_key;
  get diagnostics input_rows = row_count;

  with candidates as (
    select catalog.activity_ref
    from public.onflows_activity_catalog catalog
    where catalog.athlete_alias = p_athlete_alias
      and catalog.last_seen_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.athlete_alias = catalog.athlete_alias
          and retained.activity_ref = catalog.activity_ref
      )
      and not exists (
        select 1 from public.onflows_activity_model_inputs model_input
        where model_input.athlete_alias = catalog.athlete_alias
          and model_input.activity_ref = catalog.activity_ref
      )
      and not exists (
        select 1 from public.onflows_activity_derived_runs derived
        where derived.athlete_alias = catalog.athlete_alias
          and derived.activity_ref = catalog.activity_ref
      )
      and not exists (
        select 1 from public.onflows_activity_canonical_runs canonical
        where canonical.athlete_alias = catalog.athlete_alias
          and canonical.activity_ref = catalog.activity_ref
      )
    order by catalog.last_seen_at, catalog.activity_ref
    limit p_batch_limit
  )
  delete from public.onflows_activity_catalog catalog
  using candidates c
  where catalog.athlete_alias = p_athlete_alias
    and catalog.activity_ref = c.activity_ref;
  get diagnostics catalog_rows = row_count;

  return query select generation_rows, activity_rows, job_rows,
    shadow_rows, canonical_rows, input_rows, catalog_rows;
end;
$$;

revoke all on function public.enqueue_onflows_sync_job(text, text, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.claim_onflows_sync_job(text, integer)
  from public, anon, authenticated;
revoke all on function public.renew_onflows_sync_lease(uuid, uuid, uuid, integer)
  from public, anon, authenticated;
revoke all on function public.stage_onflows_analysis_generation(
  uuid, uuid, uuid, jsonb, text, date, date, date, jsonb, jsonb, boolean
) from public, anon, authenticated;
revoke all on function public.activate_onflows_analysis_generation(uuid, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.fail_onflows_sync_job(uuid, uuid, uuid, text, boolean, integer)
  from public, anon, authenticated;
revoke all on function public.store_onflows_canonical_activity_result(
  text, text, text, text, text, text, text, jsonb
) from public, anon, authenticated;
revoke all on function public.active_onflows_analysis(text)
  from public, anon, authenticated;
revoke all on function public.onflows_sync_state(text)
  from public, anon, authenticated;
revoke all on function public.active_onflows_activity_calendar(text, date, date)
  from public, anon, authenticated;
revoke all on function public.active_onflows_activity_shadow_index(text)
  from public, anon, authenticated;
revoke all on function public.active_onflows_activity(text, text)
  from public, anon, authenticated;
revoke all on function public.active_onflows_activity_view(text, text)
  from public, anon, authenticated;
revoke all on function public.rollback_onflows_analysis_generation(text, uuid)
  from public, anon, authenticated;
revoke all on function public.prune_onflows_analysis_generations(
  text, integer, interval, integer
)
  from public, anon, authenticated;

grant execute on function public.enqueue_onflows_sync_job(text, text, text, jsonb)
  to service_role;
grant execute on function public.claim_onflows_sync_job(text, integer)
  to service_role;
grant execute on function public.renew_onflows_sync_lease(uuid, uuid, uuid, integer)
  to service_role;
grant execute on function public.stage_onflows_analysis_generation(
  uuid, uuid, uuid, jsonb, text, date, date, date, jsonb, jsonb, boolean
) to service_role;
grant execute on function public.activate_onflows_analysis_generation(uuid, uuid, uuid)
  to service_role;
grant execute on function public.fail_onflows_sync_job(uuid, uuid, uuid, text, boolean, integer)
  to service_role;
grant execute on function public.store_onflows_canonical_activity_result(
  text, text, text, text, text, text, text, jsonb
) to service_role;
grant execute on function public.active_onflows_analysis(text)
  to service_role;
grant execute on function public.onflows_sync_state(text)
  to service_role;
grant execute on function public.active_onflows_activity_calendar(text, date, date)
  to service_role;
grant execute on function public.active_onflows_activity_shadow_index(text)
  to service_role;
grant execute on function public.active_onflows_activity(text, text)
  to service_role;
grant execute on function public.active_onflows_activity_view(text, text)
  to service_role;
grant execute on function public.rollback_onflows_analysis_generation(text, uuid)
  to service_role;
grant execute on function public.prune_onflows_analysis_generations(
  text, integer, interval, integer
)
  to service_role;

comment on table public.onflows_athlete_analysis_state is
  'One atomic active-analysis pointer and monotonic revision per athlete alias.';
comment on table public.onflows_sync_jobs is
  'Durable server-only queue; contains no provider token or raw provider payload.';
comment on table public.onflows_analysis_generations is
  'Immutable-after-READY aggregate analyses; only ACTIVE is visible to readers.';
comment on table public.onflows_analysis_generation_activities is
  'Pinned activity catalog and exact canonical/shadow run references per analysis generation.';
