-- Athlete-owned planning choices used by the canonical weekly-plan model.
-- Shared scientific coefficients and model versions remain service-wide.

alter table public.onflows_athlete_settings
  add column if not exists planning_profile jsonb;

alter table public.onflows_athlete_settings
  drop constraint if exists onflows_athlete_settings_planning_profile_version;

alter table public.onflows_athlete_settings
  add constraint onflows_athlete_settings_planning_profile_version check (
    planning_profile is null
    or (
      jsonb_typeof(planning_profile) = 'object'
      and planning_profile ->> 'schema_version' = 'planning-profile-v1'
    )
  );

comment on column public.onflows_athlete_settings.planning_profile is
  'Versioned athlete-owned planning inputs; excludes shared scientific parameters.';
