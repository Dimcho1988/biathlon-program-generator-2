-- One coherent, read-only planning phase. Publication still captures a fresh
-- phase after calculation and uses the existing locked save/check RPCs.
-- Raw history bounds and per-field validation remain the caller's lazy checks.
begin;

create function public.read_onflows_planning_inputs(
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
     select hr_zone_bounds,timezone,hrmax_bpm,hr_zone_source,hr_zone_percentages,
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
commit;
