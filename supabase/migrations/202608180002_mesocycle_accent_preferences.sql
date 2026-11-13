-- Athlete-owned mesocycle accent choices. Dynamic automatic resolution and
-- scientific factors remain in the versioned planning methodology.

alter table public.onflows_athlete_settings
  add column if not exists mesocycle_accent_preferences jsonb;

alter table public.onflows_athlete_settings
  drop constraint if exists onflows_athlete_settings_mesocycle_accent_preferences_version;

alter table public.onflows_athlete_settings
  add constraint onflows_athlete_settings_mesocycle_accent_preferences_version check (
    mesocycle_accent_preferences is null
    or (
      jsonb_typeof(mesocycle_accent_preferences) = 'object'
      and mesocycle_accent_preferences ->> 'schema_version' = 'mesocycle-accent-preferences-v1'
      and mesocycle_accent_preferences ->> 'accent_mode' in ('AUTO', 'MANUAL', 'HYBRID')
      and jsonb_typeof(mesocycle_accent_preferences -> 'accent_limit') = 'number'
      and (mesocycle_accent_preferences ->> 'accent_limit')::integer between 1 and 6
      and jsonb_typeof(mesocycle_accent_preferences -> 'manual_components') = 'array'
    )
  );

comment on column public.onflows_athlete_settings.mesocycle_accent_preferences is
  'Versioned athlete-owned mode, limit and selected mesocycle accent components.';
