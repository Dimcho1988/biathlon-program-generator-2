-- Nullable provider self-report. Existing permissions and canonical loads stay intact.
alter table public.onflows_activity_catalog
  add column if not exists provider_feel smallint
  check (provider_feel between 1 and 5);

comment on column public.onflows_activity_catalog.provider_feel is
  'Intervals activity feel: 1=strong, 5=weak. Athlete self-report, not a computed stress score.';

notify pgrst, 'reload schema';
