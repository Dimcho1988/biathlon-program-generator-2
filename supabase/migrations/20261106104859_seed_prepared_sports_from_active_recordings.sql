-- Generation rows carry sport and local date. The catalog can contain identity-only records.
create or replace function onflows_private.seed_prepared_views(p_alias text)
returns void language plpgsql security definer set search_path='' as $$
begin
 insert into onflows_private.prepared_views(athlete_alias,kind,parameter_key,parameters,is_default)
 select p_alias,v.kind,md5(v.parameters::text),v.parameters,true from (values
  ('management-state','{}'::jsonb),('management-active','{}'::jsonb),
  ('management-view','{}'::jsonb),('management-view','{"view":"overview","projection":"overview-v1"}'::jsonb),
  ('management-profile','{}'::jsonb),('speed','{}'::jsonb),('trainability','{}'::jsonb),
  ('response','{}'::jsonb),('management-outlook','{}'::jsonb),('management-methods','{}'::jsonb),
  ('speed-load','{}'::jsonb),('speed-history','{}'::jsonb)) as v(kind,parameters)
 on conflict do nothing;
 -- The ordinary sport selector is prepared from actual recent sports rather
 -- than waiting for the first opening of each tab. User-entered scenarios
 -- remain bounded, separately queued variants of these durable results.
 insert into onflows_private.prepared_views(athlete_alias,kind,parameter_key,parameters,is_default)
 select p_alias,k.kind,md5(p.parameters::text),p.parameters,true
 from (
  select distinct recording.catalog_payload->>'sport' as sport
  from public.onflows_athlete_analysis_state active
  join public.onflows_analysis_generations generation
    on generation.generation_id=active.active_generation_id and generation.athlete_alias=active.athlete_alias
  join public.onflows_analysis_generation_activities recording
    on recording.generation_id=coalesce(generation.activity_set_generation_id,generation.generation_id)
   and recording.athlete_alias=active.athlete_alias
  join public.onflows_athlete_settings settings on settings.athlete_alias=active.athlete_alias
  where active.athlete_alias=p_alias and generation.status='ACTIVE'
    and recording.local_date between (now() at time zone settings.timezone)::date-89 and (now() at time zone settings.timezone)::date
    and recording.catalog_payload->>'sport' in ('Run','TrailRun','VirtualRun','Ride','VirtualRide','MountainBikeRide','NordicSki','RollerSki')
 ) s
 cross join (values('speed'),('speed-history'),('speed-load')) k(kind)
 cross join lateral (select jsonb_build_object('sport',s.sport) as parameters) p
 on conflict do nothing;
end $$;

-- Fill only absent variants. Preserve prepared values and all athlete evidence.
select onflows_private.seed_prepared_views(athlete_alias) from onflows_private.prepared_view_sources;
