-- Stable, server-only catalog for completed athlete activities.
-- Display metadata is private athlete content and is kept outside model inputs.

create table if not exists public.onflows_activity_catalog (
  activity_ref text primary key
    default ('act_' || replace(gen_random_uuid()::text, '-', ''))
    check (activity_ref ~ '^act_[a-f0-9]{32}$'),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  provider text not null default 'intervals' check (provider = 'intervals'),
  provider_activity_key text not null check (length(provider_activity_key) = 64),
  provider_key_version text not null default 'hmac-sha256-v1',
  start_at_utc timestamptz,
  start_local timestamp,
  local_date date,
  timezone text check (timezone is null or length(timezone) <= 80),
  utc_offset_minutes smallint check (
    utc_offset_minutes is null or utc_offset_minutes between -840 and 840
  ),
  sport text not null default 'Activity' check (length(sport) between 1 and 48),
  activity_type text check (activity_type is null or length(activity_type) <= 48),
  activity_sub_type text check (activity_sub_type is null or length(activity_sub_type) <= 48),
  name text check (name is null or length(name) <= 160),
  description text check (description is null or length(description) <= 8000),
  moving_time_s integer check (moving_time_s is null or moving_time_s >= 0),
  elapsed_time_s integer check (elapsed_time_s is null or elapsed_time_s >= 0),
  recording_time_s integer check (recording_time_s is null or recording_time_s >= 0),
  distance_m double precision check (distance_m is null or distance_m >= 0),
  elevation_gain_m double precision check (elevation_gain_m is null or elevation_gain_m >= 0),
  average_hr_bpm double precision check (average_hr_bpm is null or average_hr_bpm >= 0),
  max_hr_bpm double precision check (max_hr_bpm is null or max_hr_bpm >= 0),
  average_speed_mps double precision check (average_speed_mps is null or average_speed_mps >= 0),
  max_speed_mps double precision check (max_speed_mps is null or max_speed_mps >= 0),
  provider_training_load double precision check (
    provider_training_load is null or provider_training_load >= 0
  ),
  canonical_training_load double precision check (
    canonical_training_load is null or canonical_training_load >= 0
  ),
  quality_status text not null default 'pending'
    check (quality_status in ('pending', 'valid', 'limited', 'excluded', 'provider_missing')),
  quality_reason text check (quality_reason is null or length(quality_reason) <= 500),
  hr_coverage_percent double precision check (
    hr_coverage_percent is null or hr_coverage_percent between 0 and 100
  ),
  canonical_summary jsonb not null default '{}'::jsonb,
  intervals jsonb not null default '[]'::jsonb,
  latest_canonical_run_key text check (
    latest_canonical_run_key is null or length(latest_canonical_run_key) = 64
  ),
  latest_shadow_run_key text check (
    latest_shadow_run_key is null or length(latest_shadow_run_key) = 64
  ),
  provider_created_at timestamptz,
  provider_sync_at timestamptz,
  provider_analyzed_at timestamptz,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  metadata_synced_at timestamptz,
  unique (athlete_alias, provider, provider_activity_key),
  unique (athlete_alias, activity_ref)
);

create index if not exists onflows_activity_catalog_athlete_start
  on public.onflows_activity_catalog (athlete_alias, start_at_utc desc, activity_ref);
create index if not exists onflows_activity_catalog_athlete_local_date
  on public.onflows_activity_catalog (athlete_alias, local_date desc, activity_ref);

create table if not exists public.onflows_activity_canonical_runs (
  run_key text primary key check (length(run_key) = 64),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  activity_ref text not null,
  scientific_input_hash text not null check (length(scientific_input_hash) = 64),
  result_hash text not null check (length(result_hash) = 64),
  schema_version text not null,
  model_version text not null,
  result_payload jsonb not null,
  created_at timestamptz not null default now(),
  unique (
    athlete_alias, activity_ref, scientific_input_hash,
    model_version, result_hash
  ),
  foreign key (athlete_alias, activity_ref)
    references public.onflows_activity_catalog(athlete_alias, activity_ref)
    on update restrict on delete restrict
);

create index if not exists onflows_activity_canonical_runs_lookup
  on public.onflows_activity_canonical_runs (
    athlete_alias, activity_ref, created_at desc
  );

alter table public.onflows_activity_catalog enable row level security;
alter table public.onflows_activity_canonical_runs enable row level security;
revoke all on table public.onflows_activity_catalog from public, anon, authenticated;
revoke all on table public.onflows_activity_canonical_runs from public, anon, authenticated;
revoke all on table public.onflows_activity_catalog from service_role;
revoke all on table public.onflows_activity_canonical_runs from service_role;
grant select, insert, update on table public.onflows_activity_catalog to service_role;
grant select, insert on table public.onflows_activity_canonical_runs to service_role;

create or replace function public.resolve_onflows_activity_ref(
  p_athlete_alias text,
  p_provider_activity_key text
) returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  resolved text;
begin
  insert into public.onflows_activity_catalog (
    athlete_alias, provider_activity_key, last_seen_at
  ) values (
    p_athlete_alias, p_provider_activity_key, now()
  )
  on conflict (athlete_alias, provider, provider_activity_key)
  do update set last_seen_at = excluded.last_seen_at
  returning activity_ref into resolved;
  return resolved;
end;
$$;

revoke all on function public.resolve_onflows_activity_ref(text, text)
  from public, anon, authenticated;
grant execute on function public.resolve_onflows_activity_ref(text, text)
  to service_role;

create or replace function public.publish_onflows_canonical_activity_result(
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
set search_path = public
as $$
begin
  insert into public.onflows_activity_canonical_runs (
    run_key, athlete_alias, activity_ref, scientific_input_hash,
    result_hash, schema_version, model_version, result_payload
  ) values (
    p_run_key, p_athlete_alias, p_activity_ref, p_scientific_input_hash,
    p_result_hash, p_schema_version, p_model_version, p_result_payload
  ) on conflict (run_key) do nothing;

  update public.onflows_activity_catalog
  set latest_canonical_run_key = p_run_key
  where athlete_alias = p_athlete_alias and activity_ref = p_activity_ref;
end;
$$;

revoke all on function public.publish_onflows_canonical_activity_result(
  text, text, text, text, text, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.publish_onflows_canonical_activity_result(
  text, text, text, text, text, text, text, jsonb
) to service_role;

comment on table public.onflows_activity_catalog is
  'Private athlete activity metadata and canonical summaries; no GPS or full provider payload.';
comment on column public.onflows_activity_catalog.description is
  'Private athlete-authored content. Never include in logs or technical exports.';
comment on table public.onflows_activity_canonical_runs is
  'Append-only canonical summaries keyed only by scientific input and model versions.';
