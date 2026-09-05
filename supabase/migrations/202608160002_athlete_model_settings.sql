-- Athlete-owned physiological inputs for the multi-profile cloud pilot.
-- Scientific model versions remain service-wide and are not duplicated here.

create table if not exists public.onflows_athlete_settings (
  athlete_alias text primary key references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  hr_zone_bounds smallint[] not null,
  timezone text not null check (length(timezone) between 1 and 64),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint onflows_athlete_settings_hr_bounds check (
    cardinality(hr_zone_bounds) = 6
    and hr_zone_bounds[1] between 30 and 240
    and hr_zone_bounds[1] < hr_zone_bounds[2]
    and hr_zone_bounds[2] < hr_zone_bounds[3]
    and hr_zone_bounds[3] < hr_zone_bounds[4]
    and hr_zone_bounds[4] < hr_zone_bounds[5]
    and hr_zone_bounds[5] < hr_zone_bounds[6]
    and hr_zone_bounds[6] between 30 and 240
  )
);

alter table public.onflows_athlete_settings enable row level security;
revoke all on table public.onflows_athlete_settings from anon, authenticated;
grant all on table public.onflows_athlete_settings to service_role;
