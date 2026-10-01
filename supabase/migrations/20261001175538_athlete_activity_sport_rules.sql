-- Explicit, tenant-scoped source-label corrections. Applied during a full sync
-- so catalog, scientific models and snapshot activate in one generation.
create table public.onflows_activity_sport_rules (
  id uuid primary key default gen_random_uuid(),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  source_sport text not null check (length(trim(source_sport)) between 1 and 48),
  name_contains text not null check (length(trim(name_contains)) between 1 and 160),
  target_sport text not null check (target_sport in ('NordicSki', 'RollerSki', 'Run', 'TrailRun', 'VirtualRun', 'Ride')),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  unique (athlete_alias, source_sport, name_contains)
);
alter table public.onflows_activity_sport_rules enable row level security;
revoke all on public.onflows_activity_sport_rules from public, anon, authenticated;
grant select, insert, update, delete on public.onflows_activity_sport_rules to service_role;
comment on table public.onflows_activity_sport_rules is
  'Server-only athlete-approved source sport corrections; substring matching is case-insensitive. Athlete identifiers are provisioned separately, never in migrations.';
