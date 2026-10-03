-- Server-only persistence for the onFlows Intervals OAuth cloud pilot.
-- No raw activity streams or provider payloads are stored by these tables.

create table if not exists public.onflows_oauth_states (
  nonce_hash text primary key check (length(nonce_hash) = 64),
  athlete_alias text not null,
  redirect_uri text not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists public.onflows_intervals_connections (
  athlete_alias text primary key,
  provider_athlete_id text not null unique,
  encrypted_access_token text not null,
  scopes text[] not null default '{}',
  status text not null check (status in ('CONNECTED', 'REVOKED')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.onflows_training_snapshots (
  athlete_alias text primary key,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.onflows_oauth_states enable row level security;
alter table public.onflows_intervals_connections enable row level security;
alter table public.onflows_training_snapshots enable row level security;

revoke all on table public.onflows_oauth_states from anon, authenticated;
revoke all on table public.onflows_intervals_connections from anon, authenticated;
revoke all on table public.onflows_training_snapshots from anon, authenticated;
grant all on table public.onflows_oauth_states to service_role;
grant all on table public.onflows_intervals_connections to service_role;
grant all on table public.onflows_training_snapshots to service_role;

create or replace function public.consume_onflows_oauth_state(p_nonce_hash text)
returns table (athlete_alias text, redirect_uri text)
language sql
security definer
set search_path = public
as $$
  delete from public.onflows_oauth_states
  where nonce_hash = p_nonce_hash and expires_at >= now()
  returning onflows_oauth_states.athlete_alias, onflows_oauth_states.redirect_uri;
$$;

revoke all on function public.consume_onflows_oauth_state(text) from public, anon, authenticated;
grant execute on function public.consume_onflows_oauth_state(text) to service_role;
