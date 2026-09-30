-- Existing athlete boundaries remain manual. Automatic schemes record their
-- exact percentages so configuration changes never silently rewrite a profile.
alter table public.onflows_athlete_settings
  add column if not exists hr_zone_source text not null default 'MANUAL',
  add column if not exists hr_zone_percentages numeric[];

alter table public.onflows_athlete_settings
  add constraint onflows_athlete_settings_zone_provenance check (
    (hr_zone_source = 'MANUAL' and hr_zone_percentages is null)
    or (
      hr_zone_source = 'AUTOMATIC_HRMAX'
      and hrmax_bpm is not null
      and hr_zone_percentages is not null
      and array_ndims(hr_zone_percentages) = 1
      and array_lower(hr_zone_percentages, 1) = 1
      and cardinality(hr_zone_percentages) = 6
      and array_position(hr_zone_percentages, null) is null
      and hr_zone_percentages[1] > 0
      and hr_zone_percentages[1] < hr_zone_percentages[2]
      and hr_zone_percentages[2] < hr_zone_percentages[3]
      and hr_zone_percentages[3] < hr_zone_percentages[4]
      and hr_zone_percentages[4] < hr_zone_percentages[5]
      and hr_zone_percentages[5] < hr_zone_percentages[6]
      and hr_zone_percentages[6] = 100
      and hr_zone_bounds = array[
        round(hrmax_bpm * hr_zone_percentages[1] / 100)::smallint,
        round(hrmax_bpm * hr_zone_percentages[2] / 100)::smallint,
        round(hrmax_bpm * hr_zone_percentages[3] / 100)::smallint,
        round(hrmax_bpm * hr_zone_percentages[4] / 100)::smallint,
        round(hrmax_bpm * hr_zone_percentages[5] / 100)::smallint,
        hrmax_bpm
      ]
    )
  );

comment on column public.onflows_athlete_settings.hr_zone_source is
  'MANUAL boundaries or an explicitly configured expert HRmax estimate. Not a measured physiological-domain classification.';
comment on column public.onflows_athlete_settings.hr_zone_percentages is
  'The six expert percentages used for this automatic profile, ending at 100. No universal preset; null for manual boundaries.';
