-- Explicit input changes and their durable receipts commit together.  These
-- service-only RPCs independently authorize the actor on every call/replay.
begin;

alter table public.onflows_athlete_settings
 add column calendar_revision integer not null default 0 check (calendar_revision >= 0),
 add column calendar_actor_id uuid references auth.users(id);

create function onflows_private.version_planning_calendar() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
 if new.planning_calendar is distinct from old.planning_calendar then
  new.calendar_revision := old.calendar_revision + 1;
 else
  new.calendar_revision := old.calendar_revision;
 end if;
 return new;
end $$;
revoke all on function onflows_private.version_planning_calendar() from public,anon,authenticated;
create trigger onflows_calendar_revision before update of planning_calendar,calendar_revision
 on public.onflows_athlete_settings for each row execute function onflows_private.version_planning_calendar();

create table onflows_private.assistant_operations (
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 actor_id uuid not null references auth.users(id) on delete cascade,
 operation_id uuid not null,
 request_hash text not null check(request_hash ~ '^[a-f0-9]{64}$'),
 kind text not null,
 entry_key text not null,
 origin text not null check(origin in ('CHAT','FORM')),
 receipt jsonb not null check(jsonb_typeof(receipt)='object'),
 recorded_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,actor_id,operation_id)
);
alter table onflows_private.assistant_operations enable row level security;
revoke all on onflows_private.assistant_operations from public,anon,authenticated;
grant select,insert on onflows_private.assistant_operations to service_role;

create function public.onflows_assistant_access(p_alias text,p_actor uuid) returns jsonb
language sql stable security invoker set search_path='' as $$
 with owner as (
  select user_id from public.onflows_user_athletes where athlete_alias=p_alias and is_owner
 ), org as (
  select 1 from owner o join public.onflows_organization_memberships a on a.user_id=o.user_id
  join public.onflows_organization_memberships c on c.organization_id=a.organization_id
  where a.role='ATHLETE' and a.status='ACTIVE' and c.user_id=p_actor and c.status='ACTIVE'
  and (c.role in ('ADMIN','HEAD_COACH') or (c.role='COACH' and exists(
   select 1 from public.onflows_coach_athlete_assignments x where x.organization_id=a.organization_id
    and x.athlete_user_id=o.user_id and x.coach_user_id=p_actor)))
 ), rights as (
  select exists(select 1 from owner where user_id=p_actor) as is_owner,
   exists(select 1 from org) as org_view,
   exists(select 1 from owner o join public.onflows_sharing_grants g on g.owner_user_id=o.user_id
    where g.viewer_user_id=p_actor and g.view_plan) as shared_plan,
   exists(select 1 from owner o join public.onflows_sharing_grants g on g.owner_user_id=o.user_id
    where g.viewer_user_id=p_actor and g.view_recovery) as shared_recovery
 ) select jsonb_build_object('is_owner',is_owner,'can_view_plan',is_owner or org_view or shared_plan,
   'can_view_recovery',is_owner or org_view or shared_recovery,
   'can_edit_plan',coalesce(onflows_private.can_edit_management(p_alias,p_actor),false)) from rights
$$;
revoke all on function public.onflows_assistant_access(text,uuid) from public,anon,authenticated;
grant execute on function public.onflows_assistant_access(text,uuid) to service_role;

create function public.onflows_assistant_operation_status(p_alias text,p_actor uuid,p_operation_id uuid)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare rights jsonb; saved onflows_private.assistant_operations%rowtype;
begin
 rights := public.onflows_assistant_access(p_alias,p_actor);
 if not coalesce((rights->>'can_view_plan')::boolean,false)
    and not coalesce((rights->>'can_view_recovery')::boolean,false) then
  return jsonb_build_object('forbidden',true);
 end if;
 select * into saved from onflows_private.assistant_operations
  where athlete_alias=p_alias and actor_id=p_actor and operation_id=p_operation_id;
 if not found then return jsonb_build_object('status','NOT_FOUND'); end if;
 if (saved.kind in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE')
       and not (rights->>'can_view_plan')::boolean)
    or (saved.kind not in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE')
       and not (rights->>'can_view_recovery')::boolean) then
  return jsonb_build_object('forbidden',true);
 end if;
 return saved.receipt || jsonb_build_object('request_hash',saved.request_hash);
end $$;
revoke all on function public.onflows_assistant_operation_status(text,uuid,uuid) from public,anon,authenticated;
grant execute on function public.onflows_assistant_operation_status(text,uuid,uuid) to service_role;

create function public.save_onflows_assistant_operation(
 p_alias text,p_actor uuid,p_operation_id uuid,p_request_hash text,p_kind text,p_key text,
 p_payload jsonb,p_day date,p_expected_revision integer,p_origin text default 'CHAT'
) returns jsonb language plpgsql security invoker set search_path='' as $$
declare rights jsonb; prior onflows_private.assistant_operations%rowtype; current_revision integer;
 result jsonb; receipt jsonb; stamp timestamptz; stored_kind text;
begin
 if p_actor is null or p_operation_id is null or p_request_hash is null or p_request_hash !~ '^[a-f0-9]{64}$'
  or p_kind is null or p_kind not in ('DAILY','WEIGHT','SESSION','TEST','LAB','BLOCK',
    'MANUAL_SPEED_TEST','MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE')
  or p_key is null or length(p_key) not between 1 and 80
  or p_payload is null or jsonb_typeof(p_payload)<>'object' or octet_length(p_payload::text)>262144
  or p_expected_revision is null or p_expected_revision<0
  or p_origin is null or p_origin not in ('CHAT','FORM') then
  raise exception 'Invalid assistant operation' using errcode='22023';
 end if;
 rights := public.onflows_assistant_access(p_alias,p_actor);
 if (p_kind in ('DAILY','WEIGHT','SESSION') and not (rights->>'is_owner')::boolean)
  or (p_kind not in ('DAILY','WEIGHT','SESSION') and not (rights->>'can_edit_plan')::boolean)
  or (p_kind in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE') and not (rights->>'can_view_plan')::boolean)
  or (p_kind not in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE') and not (rights->>'can_view_recovery')::boolean) then
  return jsonb_build_object('forbidden',true);
 end if;
 -- Lock order matches canonical input publication; distinct operations cannot
 -- race between the strict CAS check and the canonical save.
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:'||p_alias,0));
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('assistant:'||p_alias||':'||p_actor::text||':'||p_operation_id::text,0));
 -- A request may have waited for another editor. Re-read grants after the wait.
 rights := public.onflows_assistant_access(p_alias,p_actor);
 if (p_kind in ('DAILY','WEIGHT','SESSION') and not (rights->>'is_owner')::boolean)
  or (p_kind not in ('DAILY','WEIGHT','SESSION') and not (rights->>'can_edit_plan')::boolean)
  or (p_kind in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE') and not (rights->>'can_view_plan')::boolean)
  or (p_kind not in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE') and not (rights->>'can_view_recovery')::boolean) then
  return jsonb_build_object('forbidden',true);
 end if;
 select * into prior from onflows_private.assistant_operations
  where athlete_alias=p_alias and actor_id=p_actor and operation_id=p_operation_id;
 if found then
  if prior.request_hash<>p_request_hash then
   return jsonb_build_object('conflict',true,'status','CONFLICT','reason','OPERATION_REUSED');
  end if;
  return prior.receipt || jsonb_build_object('request_hash',prior.request_hash);
 end if;
 if p_kind in ('CALENDAR_EVENT','CALENDAR_REPLACE') then
  select calendar_revision into current_revision from public.onflows_athlete_settings where athlete_alias=p_alias for update;
 elsif p_kind='MANAGEMENT_PROFILE' then
  if p_key<>'management-profile-v1' then raise exception 'Invalid profile identity' using errcode='22023'; end if;
  select revision into current_revision from public.onflows_management_entries
   where athlete_alias=p_alias and kind='PROFILE' and entry_key=p_key order by revision desc limit 1;
 elsif p_kind='MANUAL_SPEED_TEST' then
  if p_key !~ '^manual_[a-f0-9]{32}$' then raise exception 'Invalid manual test identity' using errcode='22023'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_alias||':SPEED_TEST:'||p_key,0));
  select revision into current_revision from public.onflows_model_entries
   where athlete_alias=p_alias and kind='SPEED_TEST' and entry_key=p_key order by revision desc limit 1;
 else
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_alias||':'||p_kind||':'||case when p_kind='BLOCK' then '*' else p_key end,0));
  select revision into current_revision from public.onflows_response_entries
   where athlete_alias=p_alias and kind=p_kind and entry_key=p_key order by revision desc limit 1;
 end if;
 current_revision := coalesce(current_revision,0);
 if current_revision<>p_expected_revision then
  result := jsonb_build_object('conflict',true,'status','CONFLICT','reason','REVISION_CHANGED','revision',current_revision);
 elsif p_kind in ('CALENDAR_EVENT','CALENDAR_REPLACE') then
  update public.onflows_athlete_settings set planning_calendar=p_payload,calendar_actor_id=p_actor,updated_at=clock_timestamp()
   where athlete_alias=p_alias returning calendar_revision into current_revision;
  if not found then raise exception 'Athlete settings missing' using errcode='22023'; end if;
  result := jsonb_build_object('saved',true,'revision',current_revision);
 elsif p_kind='MANAGEMENT_PROFILE' then
  result := public.save_onflows_management_entry(p_alias,'PROFILE',p_key,p_payload,p_expected_revision,p_actor);
 elsif p_kind='MANUAL_SPEED_TEST' then
  result := public.save_onflows_model_entry(p_alias,'SPEED_TEST',p_key,p_payload,p_expected_revision,p_actor);
 else
  result := public.save_onflows_response_entry(p_alias,p_kind,p_key,p_day,p_payload,p_expected_revision,p_actor);
 end if;
 -- A receipt is an acknowledgement, not a second route to profile contents.
 result := result - 'payload';
 stamp := clock_timestamp();
 receipt := result || jsonb_build_object('operation_id',p_operation_id,'kind',p_kind,'entry_key',p_key,
   'status',case when coalesce((result->>'saved')::boolean,false) then 'SAVED' else 'CONFLICT' end,
   'recorded_at',stamp,'refresh_state',case when coalesce((result->>'saved')::boolean,false)
      then 'INPUT_SAVED' else 'UNCHANGED' end,'origin',p_origin);
 insert into onflows_private.assistant_operations(athlete_alias,actor_id,operation_id,request_hash,kind,entry_key,origin,receipt,recorded_at)
  values(p_alias,p_actor,p_operation_id,p_request_hash,p_kind,p_key,p_origin,receipt,stamp);
 return receipt || jsonb_build_object('request_hash',p_request_hash);
end $$;
revoke all on function public.save_onflows_assistant_operation(text,uuid,uuid,text,text,text,jsonb,date,integer,text) from public,anon,authenticated;
grant execute on function public.save_onflows_assistant_operation(text,uuid,uuid,text,text,text,jsonb,date,integer,text) to service_role;

-- Reads collapse revisions inside PostgreSQL instead of stopping at the old
-- 10,000 revision boundary. Historical observations remain append-only.
create function public.read_onflows_latest_response_entries(p_alias text) returns jsonb
language sql stable security invoker set search_path='' as $$
 select coalesce(jsonb_agg(to_jsonb(r) order by r.kind,r.entry_key),'[]'::jsonb) from (
  select distinct on (kind,entry_key) kind,entry_key,observed_date,revision,payload,recorded_at
   from public.onflows_response_entries where athlete_alias=p_alias order by kind,entry_key,revision desc
 ) r
$$;
revoke all on function public.read_onflows_latest_response_entries(text) from public,anon,authenticated;
grant execute on function public.read_onflows_latest_response_entries(text) to service_role;
create function public.read_onflows_latest_model_entries(p_alias text) returns jsonb
language sql stable security invoker set search_path='' as $$
 select coalesce(jsonb_agg(to_jsonb(r) order by r.kind,r.entry_key),'[]'::jsonb) from (
  select distinct on (kind,entry_key) kind,entry_key,revision,payload,recorded_at
   from public.onflows_model_entries where athlete_alias=p_alias order by kind,entry_key,revision desc
 ) r
$$;
revoke all on function public.read_onflows_latest_model_entries(text) from public,anon,authenticated;
grant execute on function public.read_onflows_latest_model_entries(text) to service_role;

-- Captured planning phases use the same latest-identity policy. Retain the
-- envelope, ordering and lazy per-section size checks; never delete history.
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
     select hr_zone_bounds,timezone,hrmax_bpm,hr_zone_source,hr_zone_percentages,
       planning_profile,mesocycle_accent_preferences,planning_calendar
     from public.onflows_athlete_settings where athlete_alias=p_alias limit 1
   ), profile_rows as materialized (
     select kind,entry_key,revision,payload,actor_id,recorded_at
     from public.onflows_management_entries
     where athlete_alias=p_alias and kind='PROFILE' and entry_key='management-profile-v1'
     order by revision desc limit 1
   ), model_latest as materialized (
     -- Bound distinct current identities, not append-only revision history.
     select distinct on (kind,entry_key) kind,entry_key,revision,payload,recorded_at
     from public.onflows_model_entries where athlete_alias=p_alias
     order by kind,entry_key,revision desc limit 10000
   ), model_bound as (
     select count(*)>=10000 as exhausted from model_latest
   ), response_raw as (
     select kind,entry_key,observed_date,revision,payload,recorded_at
     from public.onflows_response_entries where athlete_alias=p_alias
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
     order by first_seen_at desc,kind,entry_key limit 10000
   ), response_bound as (
     select count(*)>=10000 as exhausted from response_latest
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
