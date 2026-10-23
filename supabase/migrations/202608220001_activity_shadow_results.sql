-- Immutable, server-only activity inputs and versioned experimental outputs.
-- Only model-required channels are retained; GPS, names and full provider
-- payloads are deliberately excluded.

create table if not exists public.onflows_activity_model_inputs (
  input_key text primary key check (length(input_key) = 64),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  activity_ref text not null,
  input_hash text not null check (length(input_hash) = 64),
  schema_version text not null,
  input_payload jsonb not null,
  created_at timestamptz not null default now(),
  unique (athlete_alias, activity_ref, input_hash)
);

create table if not exists public.onflows_activity_derived_runs (
  run_key text primary key check (length(run_key) = 64),
  input_key text not null references public.onflows_activity_model_inputs(input_key)
    on update restrict on delete restrict,
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  activity_ref text not null,
  result_hash text not null check (length(result_hash) = 64),
  schema_version text not null,
  vflat_model_version text,
  vflat_config_version text,
  hrmod_model_version text,
  hrmod_config_version text,
  terrain_model_version text,
  result_payload jsonb not null,
  created_at timestamptz not null default now(),
  unique (
    input_key,
    vflat_model_version,
    vflat_config_version,
    hrmod_model_version,
    hrmod_config_version,
    terrain_model_version,
    result_hash
  )
);

create index if not exists onflows_activity_derived_runs_lookup
  on public.onflows_activity_derived_runs (athlete_alias, activity_ref, created_at desc);

alter table public.onflows_activity_model_inputs enable row level security;
alter table public.onflows_activity_derived_runs enable row level security;
revoke all on table public.onflows_activity_model_inputs from public, anon, authenticated;
revoke all on table public.onflows_activity_derived_runs from public, anon, authenticated;
revoke all on table public.onflows_activity_model_inputs from service_role;
revoke all on table public.onflows_activity_derived_runs from service_role;
grant select, insert on table public.onflows_activity_model_inputs to service_role;
grant select, insert on table public.onflows_activity_derived_runs to service_role;

create or replace function public.publish_onflows_activity_shadow(
  p_input_key text,
  p_athlete_alias text,
  p_activity_ref text,
  p_input_hash text,
  p_input_schema_version text,
  p_input_payload jsonb,
  p_run_key text,
  p_result_hash text,
  p_derived_schema_version text,
  p_vflat_model_version text,
  p_vflat_config_version text,
  p_hrmod_model_version text,
  p_hrmod_config_version text,
  p_terrain_model_version text,
  p_result_payload jsonb
) returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.onflows_activity_model_inputs (
    input_key, athlete_alias, activity_ref, input_hash, schema_version, input_payload
  ) values (
    p_input_key, p_athlete_alias, p_activity_ref, p_input_hash,
    p_input_schema_version, p_input_payload
  ) on conflict (input_key) do nothing;

  insert into public.onflows_activity_derived_runs (
    run_key, input_key, athlete_alias, activity_ref, result_hash,
    schema_version, vflat_model_version, vflat_config_version,
    hrmod_model_version, hrmod_config_version, terrain_model_version,
    result_payload
  ) values (
    p_run_key, p_input_key, p_athlete_alias, p_activity_ref, p_result_hash,
    p_derived_schema_version, p_vflat_model_version, p_vflat_config_version,
    p_hrmod_model_version, p_hrmod_config_version, p_terrain_model_version,
    p_result_payload
  ) on conflict (run_key) do nothing;
end;
$$;

revoke all on function public.publish_onflows_activity_shadow(
  text, text, text, text, text, jsonb, text, text, text,
  text, text, text, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.publish_onflows_activity_shadow(
  text, text, text, text, text, jsonb, text, text, text,
  text, text, text, text, text, jsonb
) to service_role;

comment on table public.onflows_activity_model_inputs is
  'Immutable minimal model input; no GPS, names or full provider payload.';
comment on table public.onflows_activity_derived_runs is
  'Append-only versioned Vflat/HRmod experimental results.';
