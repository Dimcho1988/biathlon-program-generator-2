-- Common load preferences remain private under the existing athlete settings RLS.
begin;
alter table public.onflows_athlete_settings
  add column if not exists model_preferences jsonb not null default '{}'::jsonb;
alter table public.onflows_athlete_settings add constraint onflows_model_preferences_object
  check (jsonb_typeof(model_preferences)='object'
    and (not model_preferences ? 'leading_indicator' or model_preferences->>'leading_indicator' in ('HEART_RATE','SPEED'))
    and (not model_preferences ? 'hrmax_source' or model_preferences->>'hrmax_source' in ('MANUAL','TANAKA','OBSERVED')));

-- Update the approved common accounting endpoints, retaining manual interior
-- thresholds. Automatic schemes use the newly approved six percentages.
update public.onflows_athlete_settings
set hr_zone_percentages=array[45.,72.,79.,89.,94.,100.],
    hr_zone_bounds=array[
      floor(hrmax_bpm*.45+.5)::smallint, floor(hrmax_bpm*.72+.5)::smallint,
      floor(hrmax_bpm*.79+.5)::smallint, floor(hrmax_bpm*.89+.5)::smallint,
      floor(hrmax_bpm*.94+.5)::smallint, hrmax_bpm]
where hr_zone_source='AUTOMATIC_HRMAX' and hrmax_bpm is not null;
update public.onflows_athlete_settings
set hr_zone_bounds=array[floor(hrmax_bpm*.45+.5)::smallint,
    hr_zone_bounds[2],hr_zone_bounds[3],hr_zone_bounds[4],hr_zone_bounds[5],hrmax_bpm]
where hr_zone_source='MANUAL' and hrmax_bpm is not null
  and floor(hrmax_bpm*.45+.5)<hr_zone_bounds[2] and hr_zone_bounds[5]<hrmax_bpm;

create function public.increase_onflows_hrmax(
 p_alias text, p_candidate integer, p_expected_hrmax integer, p_observation jsonb
) returns boolean language plpgsql security invoker set search_path='' as $$
declare s public.onflows_athlete_settings%rowtype; next_bounds smallint[];
begin
 if p_candidate is null or p_candidate<30 or p_candidate>240
    or jsonb_typeof(p_observation) is distinct from 'object'
    or p_observation->>'source' is distinct from 'RAW_MEASURED'
    or jsonb_typeof(p_observation->'window_seconds') is distinct from 'number'
    or jsonb_typeof(p_observation->'window_range_bpm') is distinct from 'number'
    or jsonb_typeof(p_observation->'raw_mean_bpm') is distinct from 'number'
    or jsonb_typeof(p_observation->'offset_bpm') is distinct from 'number'
    or jsonb_typeof(p_observation->'start_s') is distinct from 'number'
    or jsonb_typeof(p_observation->'end_s') is distinct from 'number' then
   raise exception 'Invalid sustained HRmax observation' using errcode='22023';
 end if;
 if (p_observation->>'window_seconds')::numeric<>15
    or (p_observation->>'window_range_bpm')::numeric not between 0 and 10
    or (p_observation->>'raw_mean_bpm')::numeric not between 30 and 240
    or (p_observation->>'offset_bpm')::numeric not in (0,7)
    or (p_observation->>'end_s')::numeric-(p_observation->>'start_s')::numeric<>15
    or p_candidate<>floor((p_observation->>'raw_mean_bpm')::numeric
                        +(p_observation->>'offset_bpm')::numeric+.5) then
   raise exception 'Invalid sustained HRmax observation' using errcode='22023';
 end if;
 select * into s from public.onflows_athlete_settings where athlete_alias=p_alias for update;
 if not found or s.hrmax_bpm is distinct from p_expected_hrmax
    or p_candidate<=coalesce(s.hrmax_bpm,s.hr_zone_bounds[6]) then return false; end if;
 if s.hr_zone_source='AUTOMATIC_HRMAX' then
   select array_agg(floor(p_candidate*p/100.+.5)::smallint order by ordinality)
     into next_bounds from unnest(s.hr_zone_percentages) with ordinality u(p,ordinality);
 else
   next_bounds=array[floor(p_candidate*.45+.5)::smallint,s.hr_zone_bounds[2],
       s.hr_zone_bounds[3],s.hr_zone_bounds[4],s.hr_zone_bounds[5],p_candidate::smallint];
 end if;
 update public.onflows_athlete_settings set
   hrmax_bpm=p_candidate,hr_zone_bounds=next_bounds,
   model_preferences=s.model_preferences || jsonb_build_object(
     'version','unified-load-preferences-v1','hrmax_source','OBSERVED',
     'hrmax_observation',p_observation || jsonb_build_object('recorded_at',clock_timestamp())),
   updated_at=clock_timestamp()
 where athlete_alias=p_alias;
 return true;
end $$;
revoke all on function public.increase_onflows_hrmax(text,integer,integer,jsonb) from public,anon,authenticated;
grant execute on function public.increase_onflows_hrmax(text,integer,integer,jsonb) to service_role;

create or replace function public.read_onflows_planning_inputs(
 p_alias text,
 p_analysis_mode text default 'FULL',
 p_analysis_hints jsonb default '[]'::jsonb
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
begin
 if p_analysis_mode is null or p_analysis_mode not in ('FULL','HINTED') then
   raise exception 'Invalid planning analysis mode' using errcode='22023';
 end if;
 return (
   with settings_rows as materialized (
     select hr_zone_bounds,timezone,hrmax_bpm,hr_zone_source,hr_zone_percentages,model_preferences,
       planning_profile,mesocycle_accent_preferences,planning_calendar
     from public.onflows_athlete_settings where athlete_alias=p_alias limit 1
   ), profile_rows as materialized (
     select kind,entry_key,revision,payload,actor_id,recorded_at
     from public.onflows_management_entries
     where athlete_alias=p_alias and kind='PROFILE' and entry_key='management-profile-v1'
     order by revision desc limit 1
   ), model_raw as materialized (
     -- The existing client rejects exactly 10,000 raw revisions as well as
     -- larger histories. A latest-identity count would change that contract.
     select kind,entry_key,revision,payload,recorded_at
     from public.onflows_model_entries where athlete_alias=p_alias limit 10000
   ), model_bound as (
     select count(*)>=10000 as exhausted from model_raw
   ), model_latest as (
     select distinct on (kind,entry_key) kind,entry_key,revision,payload,recorded_at
     from model_raw order by kind,entry_key,revision desc
   ), response_raw as materialized (
     select kind,entry_key,observed_date,revision,payload,recorded_at
     from public.onflows_response_entries where athlete_alias=p_alias limit 10000
   ), response_bound as (
     select count(*)>=10000 as exhausted from response_raw
   ), response_ranked as (
     select r.*,
       row_number() over (partition by kind,entry_key order by revision desc) as latest_rank,
       max(recorded_at) over (partition by kind,entry_key) as first_seen_at
     from response_raw r
   ), response_latest as (
     -- Python replaces the value when a higher revision appears, but retains
     -- its dictionary position from the identity's first history occurrence.
     -- That position uses max(recorded_at), even when the winner is older.
     select kind,entry_key,observed_date,revision,payload,recorded_at,first_seen_at
     from response_ranked where latest_rank=1
   ), analysis as materialized (
     -- Reuse the exact active/legacy/missing semantics of the original reader.
     select generation_id,revision,analysis_as_of,activated_at,snapshot_payload
     from public.active_onflows_analysis(p_alias)
   ), analysis_choice as (
     select a.*,
       p_analysis_mode='HINTED' and a.generation_id is not null and a.revision>0 and exists (
         select 1 from jsonb_array_elements(case
           when jsonb_typeof(p_analysis_hints)='array' then p_analysis_hints else '[]'::jsonb end) h
         where jsonb_typeof(h)='object'
           and jsonb_typeof(h->'generation_id')='string' and h->>'generation_id'<>''
           and jsonb_typeof(h->'analysis_revision')='number'
           and h->>'analysis_revision' ~ '^[1-9][0-9]*$'
           and jsonb_typeof(h->'snapshot_fingerprint')='string'
           and h->>'snapshot_fingerprint' ~ '^[0-9a-f]{64}$'
           -- Compare JSON identities without normalizing or casting hint UUIDs,
           -- dates, or numbers. Malformed hints simply require the full row.
           and h->'generation_id'=to_jsonb(a.generation_id)
           and h->'analysis_revision'=to_jsonb(a.revision)
           and coalesce(h->'analysis_as_of','null'::jsonb)=coalesce(to_jsonb(a.analysis_as_of),'null'::jsonb)
       ) as use_metadata
     from analysis a
   )
   select jsonb_build_object(
     'schema_version','planning-inputs-v1','athlete_alias',p_alias,
     'settings_rows',coalesce((select jsonb_agg(to_jsonb(s)) from settings_rows s),'[]'::jsonb),
     'profile_rows',coalesce((select jsonb_agg(to_jsonb(p)) from profile_rows p),'[]'::jsonb),
     'model_entries',case when (select exhausted from model_bound) then '[]'::jsonb else
       coalesce((select jsonb_agg(to_jsonb(m) order by kind,entry_key) from model_latest m),'[]'::jsonb) end,
     'model_history_exhausted',(select exhausted from model_bound),
     'response_entries',case when (select exhausted from response_bound) then '[]'::jsonb else
       coalesce((select jsonb_agg(jsonb_build_object('kind',kind,'entry_key',entry_key,
         'observed_date',observed_date,'revision',revision,'payload',payload,'recorded_at',recorded_at)
         order by first_seen_at desc,kind,entry_key) from response_latest),'[]'::jsonb) end,
     'response_history_exhausted',(select exhausted from response_bound),
     'analysis',(select jsonb_build_object('mode',case when use_metadata then 'METADATA' else 'FULL' end,
       'row',jsonb_build_object('generation_id',generation_id,'revision',revision,
         'analysis_as_of',analysis_as_of,'activated_at',activated_at)
         || case when use_metadata then '{}'::jsonb else jsonb_build_object('snapshot_payload',snapshot_payload) end)
       from analysis_choice)
   )
 );
end $$;

revoke all on function public.read_onflows_planning_inputs(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.read_onflows_planning_inputs(text,text,jsonb) to service_role;

notify pgrst,'reload schema';

create or replace function public.onflows_management_checkpoint(p_alias text) returns jsonb
language sql stable security invoker set search_path='' as $$
 select jsonb_build_object(
   'settings',(select jsonb_build_object('hr_zone_bounds',s.hr_zone_bounds,'hrmax_bpm',s.hrmax_bpm,
     'timezone',s.timezone,'planning_profile',s.planning_profile,'planning_calendar',s.planning_calendar,
     'mesocycle_accent_preferences',s.mesocycle_accent_preferences,'model_preferences',s.model_preferences)
     from public.onflows_athlete_settings s where s.athlete_alias=p_alias),
   'generation_id',(select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=p_alias),
   'analysis_revision',(select active_revision from public.onflows_athlete_analysis_state where athlete_alias=p_alias),
   'models',coalesce((select jsonb_agg(to_jsonb(m) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_model_entries
      where athlete_alias=p_alias group by kind,entry_key) m),'[]'::jsonb),
   'responses',coalesce((select jsonb_agg(to_jsonb(r) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_response_entries
      where athlete_alias=p_alias group by kind,entry_key) r),'[]'::jsonb))
$$;
revoke all on function public.onflows_management_checkpoint(text) from public,anon,authenticated;
grant execute on function public.onflows_management_checkpoint(text) to service_role;
notify pgrst,'reload schema';
commit;

