-- Athlete-owned planning events. The readiness context is derived by the API;
-- no plan is generated and no virtual race is persisted by this migration.

alter table public.onflows_athlete_settings
  add column if not exists planning_calendar jsonb;

alter table public.onflows_athlete_settings
  drop constraint if exists onflows_athlete_settings_planning_calendar_version;

alter table public.onflows_athlete_settings
  add constraint onflows_athlete_settings_planning_calendar_version check (
    planning_calendar is null
    or (
      jsonb_typeof(planning_calendar) = 'object'
      and planning_calendar ->> 'schema_version' = 'planning-calendar-v1'
      and jsonb_typeof(planning_calendar -> 'events') = 'array'
      and jsonb_array_length(planning_calendar -> 'events') <= 100
    )
  );

comment on column public.onflows_athlete_settings.planning_calendar is
  'Versioned athlete-owned MAIN_RACE, CONTROL_RACE, CAMP, TEST and UNAVAILABLE events.';
