-- Active schedules are append-only revisions; the pointer is changed only by
-- the service-only RPC. Canonical analysis tables keep their SELECT-only grants.
create schema if not exists onflows_private;
revoke all on schema onflows_private from public, anon, authenticated;
grant usage on schema onflows_private to service_role;

create table public.onflows_management_plan_revisions (
  athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
  revision integer not null check (revision > 0),
  operation text not null check (operation in ('ACTIVATE','ADAPT','PAUSE','RESUME','DAY','APPROVE','REVIEW','COMPLETE')),
  actor_id uuid not null references auth.users(id),
  payload jsonb not null check (jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 2097152),
  recorded_at timestamptz not null default clock_timestamp(),
  primary key (athlete_alias, revision)
);
create index onflows_management_plan_revisions_actor on public.onflows_management_plan_revisions(actor_id);
create table public.onflows_management_plan_state (
  athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on delete cascade,
  revision integer not null,
  status text not null check (status in ('ACTIVE','PAUSED','REVIEW_REQUIRED','COMPLETED')),
  approved_by uuid not null references auth.users(id),
  next_check_at timestamptz not null default now(),
  foreign key (athlete_alias, revision) references public.onflows_management_plan_revisions(athlete_alias, revision)
);
create index onflows_management_plan_state_due on public.onflows_management_plan_state(next_check_at)
  where status in ('ACTIVE','REVIEW_REQUIRED');
create index onflows_management_plan_state_actor on public.onflows_management_plan_state(approved_by);
alter table public.onflows_management_plan_revisions enable row level security;
alter table public.onflows_management_plan_state enable row level security;
revoke all on public.onflows_management_plan_revisions, public.onflows_management_plan_state from public, anon, authenticated, service_role;
grant select on public.onflows_management_plan_revisions, public.onflows_management_plan_state to service_role;

create function public.onflows_management_checkpoint(p_alias text) returns jsonb
language sql stable security invoker set search_path = '' as $$
 select jsonb_build_object(
   'settings', (select jsonb_build_object('hr_zone_bounds',s.hr_zone_bounds,'hrmax_bpm',s.hrmax_bpm,
     'timezone',s.timezone,'planning_profile',s.planning_profile,'planning_calendar',s.planning_calendar,
     'mesocycle_accent_preferences',s.mesocycle_accent_preferences)
     from public.onflows_athlete_settings s where s.athlete_alias=p_alias),
   'generation_id', (select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=p_alias),
   'models', coalesce((select jsonb_agg(to_jsonb(m) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_model_entries
      where athlete_alias=p_alias group by kind,entry_key) m),'[]'::jsonb))
$$;
revoke all on function public.onflows_management_checkpoint(text) from public, anon, authenticated;
grant execute on function public.onflows_management_checkpoint(text) to service_role;

create function onflows_private.can_edit_management(p_alias text,p_actor uuid) returns boolean
language sql stable security invoker set search_path='' as $$
 select exists(select 1 from public.onflows_user_athletes owner where owner.athlete_alias=p_alias and owner.is_owner and (
   owner.user_id=p_actor or exists(select 1 from public.onflows_sharing_grants g
      where g.owner_user_id=owner.user_id and g.viewer_user_id=p_actor and g.edit_plan)
   or exists(select 1 from public.onflows_organization_memberships a
      join public.onflows_organization_memberships c on c.organization_id=a.organization_id
      where a.user_id=owner.user_id and a.role='ATHLETE' and a.status='ACTIVE'
        and c.user_id=p_actor and c.status='ACTIVE' and (c.role in ('ADMIN','HEAD_COACH')
        or (c.role='COACH' and exists(select 1 from public.onflows_coach_athlete_assignments x
          where x.organization_id=a.organization_id and x.athlete_user_id=owner.user_id and x.coach_user_id=p_actor and x.can_edit_plan))))))
$$;
revoke all on function onflows_private.can_edit_management(text,uuid) from public,anon,authenticated;
grant execute on function onflows_private.can_edit_management(text,uuid) to service_role;

-- SECURITY DEFINER is confined to the non-exposed schema so the RPC can take
-- read locks on canonical state without granting the API arbitrary UPDATE.
-- Only the trusted service role can invoke it; p_actor is checked independently.
create function onflows_private.save_management_plan(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean default false
) returns jsonb language plpgsql security definer set search_path='' as $$
declare
 state public.onflows_management_plan_state%rowtype;
 current_profile integer;
 approved_actor uuid;
 saved_at timestamptz;
 old_payload jsonb;
 today_local text;
begin
 if p_actor is null or p_payload is null or jsonb_typeof(p_payload)<>'object'
    or p_expected_revision is null or p_expected_revision<0 or p_checkpoint is null
    or p_operation is null or p_operation not in ('ACTIVATE','ADAPT','PAUSE','RESUME','DAY','APPROVE','REVIEW','COMPLETE')
    or p_automatic is null or p_payload->>'status' is null then
   raise exception 'Invalid active plan request' using errcode='22023';
 end if;
 if not onflows_private.can_edit_management(p_alias,p_actor) then
   raise exception 'Planning access required' using errcode='42501';
 end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:'||p_alias,0));
 select * into state from public.onflows_management_plan_state where athlete_alias=p_alias for update;
 if coalesce(state.revision,0)<>p_expected_revision then
   return jsonb_build_object('conflict',true,'reason','REVISION_CHANGED');
 end if;
 select payload into old_payload from public.onflows_management_plan_revisions
    where athlete_alias=p_alias and revision=state.revision;
 if p_automatic and (state.athlete_alias is null or state.status in ('PAUSED','COMPLETED') or state.approved_by<>p_actor
      or p_operation not in ('ADAPT','REVIEW','COMPLETE')) then
   raise exception 'Automatic planning is not authorized' using errcode='42501';
 end if;
 if p_automatic and p_operation='ADAPT' and old_payload->>'mode'<>'AUTO' then
   raise exception 'Review mode cannot automatically replace a plan' using errcode='42501';
 end if;
 select revision into current_profile from public.onflows_management_entries
   where athlete_alias=p_alias and kind='PROFILE' order by revision desc limit 1;
 if current_profile is distinct from p_expected_profile_revision then
   return jsonb_build_object('conflict',true,'reason','PROFILE_CHANGED');
 end if;
 -- Brief publication checkpoint. No generation work happens inside locks.
 perform 1 from public.onflows_athlete_settings where athlete_alias=p_alias for share;
 lock table public.onflows_model_entries in share mode;
 perform 1 from public.onflows_athlete_analysis_state where athlete_alias=p_alias for share;
 if public.onflows_management_checkpoint(p_alias) is distinct from p_checkpoint then
   return jsonb_build_object('conflict',true,'reason','INPUTS_CHANGED');
 end if;
 select (clock_timestamp() at time zone timezone)::date::text into today_local
   from public.onflows_athlete_settings where athlete_alias=p_alias;
 if p_payload->>'evaluated_on' is distinct from today_local then
   return jsonb_build_object('conflict',true,'reason','LOCAL_DATE_CHANGED');
 end if;
 if p_payload->>'status'='ACTIVE' and (
   p_payload#>>'{plan,activation_eligible}' is distinct from 'true'
   or p_payload#>>'{plan,source,readiness_known}' is distinct from 'true'
   or p_payload#>>'{plan,source,generation_id}' is distinct from p_checkpoint->>'generation_id'
   or p_payload->>'approved_profile_revision' is distinct from current_profile::text
 ) then
   raise exception 'A current, complete prescription is required for activation' using errcode='22023';
 end if;
 approved_actor := case when p_operation in ('ACTIVATE','APPROVE','RESUME') then p_actor else state.approved_by end;
 if approved_actor is null or p_payload->>'approved_by' is distinct from approved_actor::text then
   raise exception 'Invalid approval identity' using errcode='22023';
 end if;
 if p_automatic and (p_payload->>'approved_settings_fingerprint' is distinct from old_payload->>'approved_settings_fingerprint'
     or p_payload->>'approved_profile_revision' is distinct from old_payload->>'approved_profile_revision'
     or p_payload->>'mode' is distinct from old_payload->>'mode') then
   raise exception 'Automatic work cannot change approved rules' using errcode='42501';
 end if;
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
   values(p_alias,p_expected_revision+1,p_operation,p_actor,p_payload) returning recorded_at into saved_at;
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by,next_check_at)
   values(p_alias,p_expected_revision+1,p_payload->>'status',approved_actor,clock_timestamp()+interval '5 minutes')
   on conflict(athlete_alias) do update set revision=excluded.revision,status=excluded.status,
     approved_by=excluded.approved_by,next_check_at=excluded.next_check_at;
 return jsonb_build_object('saved',true,'revision',p_expected_revision+1,'payload',p_payload,'recorded_at',saved_at);
end $$;
revoke all on function onflows_private.save_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean) from public,anon,authenticated;
grant execute on function onflows_private.save_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean) to service_role;
create function public.save_onflows_management_plan(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean default false
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.save_management_plan(p_alias,p_payload,p_expected_revision,p_actor,p_operation,p_expected_profile_revision,p_checkpoint,p_automatic)
$$;
revoke all on function public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean) from public,anon,authenticated;
grant execute on function public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean) to service_role;

-- Operational scheduling metadata is separate from the immutable plan history.
create function onflows_private.defer_management_check(p_alias text,p_revision integer) returns void
language sql security definer set search_path='' as $$
 update public.onflows_management_plan_state set next_check_at=clock_timestamp()+interval '5 minutes'
 where athlete_alias=p_alias and revision=p_revision
$$;
revoke all on function onflows_private.defer_management_check(text,integer) from public,anon,authenticated;
grant execute on function onflows_private.defer_management_check(text,integer) to service_role;
create function public.defer_onflows_management_check(p_alias text,p_revision integer) returns void
language sql security invoker set search_path='' as $$ select onflows_private.defer_management_check(p_alias,p_revision) $$;
revoke all on function public.defer_onflows_management_check(text,integer) from public,anon,authenticated;
grant execute on function public.defer_onflows_management_check(text,integer) to service_role;

-- An approved active program may refresh its connected activity source at
-- most once per UTC hour. Existing queue idempotency and retry limits apply.
create function onflows_private.queue_management_import(p_alias text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare state public.onflows_management_plan_state%rowtype; profile jsonb; local_day text; slot text; result jsonb;
begin
 select * into state from public.onflows_management_plan_state where athlete_alias=p_alias for share;
 if not found or state.status not in ('ACTIVE','REVIEW_REQUIRED')
   or not onflows_private.can_edit_management(p_alias,state.approved_by) then return jsonb_build_object('queued',false); end if;
 select payload into profile from public.onflows_management_entries where athlete_alias=p_alias and kind='PROFILE' order by revision desc limit 1;
 if coalesce((profile->>'auto_import_enabled')::boolean,true)=false then return jsonb_build_object('queued',false); end if;
 if not exists(select 1 from public.onflows_intervals_connections where athlete_alias=p_alias and status='CONNECTED') then
   return jsonb_build_object('queued',false,'reason','CONNECTION_REQUIRED'); end if;
 select (clock_timestamp() at time zone timezone)::date::text into local_day from public.onflows_athlete_settings where athlete_alias=p_alias;
 if local_day > profile->>'program_end' then return jsonb_build_object('queued',false); end if;
 slot := p_alias||':'||to_char(clock_timestamp() at time zone 'UTC','YYYY-MM-DD-HH24');
 select to_jsonb(j) into result from public.enqueue_onflows_sync_job(p_alias,'FULL_SYNC',
   md5('management-import:'||slot)||md5('v2:'||slot),
   jsonb_build_object('schema_version','sync-request-v1','scope','FULL','as_of',local_day)) j;
 return jsonb_build_object('queued',true,'deduplicated',result->'deduplicated');
end $$;
revoke all on function onflows_private.queue_management_import(text) from public,anon,authenticated;
grant execute on function onflows_private.queue_management_import(text) to service_role;
create function public.queue_onflows_management_import(p_alias text) returns jsonb
language sql security invoker set search_path='' as $$ select onflows_private.queue_management_import(p_alias) $$;
revoke all on function public.queue_onflows_management_import(text) from public,anon,authenticated;
grant execute on function public.queue_onflows_management_import(text) to service_role;
