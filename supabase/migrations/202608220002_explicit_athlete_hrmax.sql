-- Explicit HRmax for HRmod. Existing profiles remain valid for canonical load;
-- HRmod fails closed until this field is deliberately supplied.

alter table public.onflows_athlete_settings
  add column if not exists hrmax_bpm smallint;

alter table public.onflows_athlete_settings
  drop constraint if exists onflows_athlete_settings_hrmax;

alter table public.onflows_athlete_settings
  add constraint onflows_athlete_settings_hrmax check (
    hrmax_bpm is null
    or (
      hrmax_bpm between 30 and 240
      and hr_zone_bounds[6] <= hrmax_bpm
    )
  );

comment on column public.onflows_athlete_settings.hrmax_bpm is
  'Explicit athlete HRmax. Never inferred from age, observed HR or Z5.';
