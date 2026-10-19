-- Compare the analysis pointer's monotonic revision as well as its immutable
-- generation. A supported G1 -> G2 -> G1 rollback restores the same payload,
-- but does not restore the old active_revision or frozen input fingerprint.
-- Existing active publication/check writers already hold analysis_state SHARE
-- before comparing this complete checkpoint. No new locks or history writes.
begin;

create or replace function public.onflows_management_checkpoint(p_alias text) returns jsonb
language sql stable security invoker set search_path = '' as $$
 select jsonb_build_object(
   'settings', (select jsonb_build_object('hr_zone_bounds',s.hr_zone_bounds,'hrmax_bpm',s.hrmax_bpm,
     'timezone',s.timezone,'planning_profile',s.planning_profile,'planning_calendar',s.planning_calendar,
     'mesocycle_accent_preferences',s.mesocycle_accent_preferences)
     from public.onflows_athlete_settings s where s.athlete_alias=p_alias),
   'generation_id', (select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=p_alias),
   'analysis_revision', (select active_revision from public.onflows_athlete_analysis_state where athlete_alias=p_alias),
   'models', coalesce((select jsonb_agg(to_jsonb(m) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_model_entries
      where athlete_alias=p_alias group by kind,entry_key) m),'[]'::jsonb),
   'responses', coalesce((select jsonb_agg(to_jsonb(r) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_response_entries
      where athlete_alias=p_alias group by kind,entry_key) r),'[]'::jsonb))
$$;
revoke all on function public.onflows_management_checkpoint(text) from public, anon, authenticated;
grant execute on function public.onflows_management_checkpoint(text) to service_role;

commit;
