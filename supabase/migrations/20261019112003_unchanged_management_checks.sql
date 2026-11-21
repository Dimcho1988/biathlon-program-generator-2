-- Manual REFRESH may prove the complete candidate identical in the trusted API.
-- The unchanged-check RPC sends only the pinned immutable revision and inputs.
-- It does not claim to independently recompute or compare the candidate in SQL.
create table onflows_private.management_plan_checks (
 sequence bigint generated always as identity primary key,
 athlete_alias text not null,
 check_id uuid not null,
 checked_revision integer not null check (checked_revision > 0),
 -- Keep the authorized actor's historical identity without adding a new auth
 -- user deletion restriction. Athlete/revision cleanup follows its parent.
 actor_id uuid not null,
 operation text not null default 'CHECK_NO_CHANGE' check (operation='CHECK_NO_CHANGE'),
 profile_revision integer not null check (profile_revision > 0),
 evaluated_on date not null,
 checkpoint_sha256 text not null check (checkpoint_sha256 ~ '^[a-f0-9]{64}$'),
 payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
 recorded_at timestamptz not null default clock_timestamp(),
 unique (athlete_alias,check_id),
 foreign key (athlete_alias,checked_revision)
   references public.onflows_management_plan_revisions(athlete_alias,revision) on delete cascade
);
create index management_plan_checks_athlete_sequence
 on onflows_private.management_plan_checks(athlete_alias,sequence desc);
-- Match the parent revision FK for existing athlete/history cleanup; the
-- history and nonce indexes cannot narrow the second revision key.
create index management_plan_checks_athlete_revision
 on onflows_private.management_plan_checks(athlete_alias,checked_revision);
alter table onflows_private.management_plan_checks enable row level security;
revoke all on onflows_private.management_plan_checks from public,anon,authenticated,service_role;
grant select on onflows_private.management_plan_checks to service_role;
revoke all on sequence onflows_private.management_plan_checks_sequence_seq
 from public,anon,authenticated,service_role;

-- One authority for old publications and the new audit-only branch. There is
-- deliberately no default nonce or overload: old callers cannot select CHECK.
create function onflows_private.save_management_plan_core(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean,p_check_id uuid
) returns jsonb language plpgsql security definer set search_path='' as $$
declare
 state public.onflows_management_plan_state%rowtype;
 current_profile integer;
 approved_actor uuid;
 saved_at timestamptz;
 old_payload jsonb;
 today_local text;
 previous_check onflows_private.management_plan_checks%rowtype;
 checkpoint_hash text;
 payload_hash text;
begin
 if p_actor is null or p_expected_revision is null or p_expected_revision<0 or p_checkpoint is null
    or p_operation is null or p_operation not in ('ACTIVATE','ADAPT','PAUSE','RESUME','DAY','APPROVE','REVIEW','COMPLETE','CHECK_NO_CHANGE')
    or p_automatic is null
    or (p_operation='CHECK_NO_CHANGE' and (p_payload is not null or p_check_id is null
        or p_expected_profile_revision is null or p_expected_profile_revision<1))
    or (p_operation<>'CHECK_NO_CHANGE' and (p_payload is null or jsonb_typeof(p_payload)<>'object'
        or p_payload->>'status' is null or p_check_id is not null)) then
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
 if p_operation='CHECK_NO_CHANGE' then
   if state.athlete_alias is null or state.status not in ('ACTIVE','REVIEW_REQUIRED')
      or old_payload is null or jsonb_typeof(old_payload)<>'object'
      or old_payload->>'status' is distinct from state.status then
     raise exception 'A current active or review-required plan is required for an unchanged check' using errcode='22023';
   end if;
   -- Auth + revision CAS pin the exact immutable payload previously compared
   -- by the API. Hydrate it only here, never from a supplied digest or payload.
   p_payload := old_payload;
 end if;
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
   not onflows_private.management_plan_eligible(p_payload->'plan')
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
 if p_operation='CHECK_NO_CHANGE' then
   checkpoint_hash := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p_checkpoint::text,'UTF8')),'hex');
   payload_hash := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(old_payload::text,'UTF8')),'hex');
   select * into previous_check from onflows_private.management_plan_checks
     where athlete_alias=p_alias and check_id=p_check_id;
   if found then
     if previous_check.checked_revision<>p_expected_revision or previous_check.actor_id<>p_actor
        or previous_check.profile_revision<>current_profile or previous_check.evaluated_on<>today_local::date
        or previous_check.checkpoint_sha256<>checkpoint_hash or previous_check.payload_sha256<>payload_hash then
       return jsonb_build_object('conflict',true,'reason','CHECK_ID_REUSED');
     end if;
     return jsonb_build_object('unchanged',true,'checked_revision',previous_check.checked_revision,
       'check_id',previous_check.check_id,'recorded_at',previous_check.recorded_at,'replayed',true);
   end if;
   insert into onflows_private.management_plan_checks
     (athlete_alias,check_id,checked_revision,actor_id,profile_revision,evaluated_on,checkpoint_sha256,payload_sha256)
     values(p_alias,p_check_id,p_expected_revision,p_actor,current_profile,today_local::date,checkpoint_hash,payload_hash)
     returning recorded_at into saved_at;
   update public.onflows_management_plan_state set next_check_at=clock_timestamp()+interval '5 minutes'
     where athlete_alias=p_alias and revision=p_expected_revision;
   return jsonb_build_object('unchanged',true,'checked_revision',p_expected_revision,
     'check_id',p_check_id,'recorded_at',saved_at,'replayed',false);
 end if;
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
   values(p_alias,p_expected_revision+1,p_operation,p_actor,p_payload) returning recorded_at into saved_at;
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by,next_check_at)
   values(p_alias,p_expected_revision+1,p_payload->>'status',approved_actor,clock_timestamp()+interval '5 minutes')
   on conflict(athlete_alias) do update set revision=excluded.revision,status=excluded.status,
     approved_by=excluded.approved_by,next_check_at=excluded.next_check_at;
 return jsonb_build_object('saved',true,'revision',p_expected_revision+1,'payload',p_payload,'recorded_at',saved_at);
end $$;
revoke all on function onflows_private.save_management_plan_core(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid)
 from public,anon,authenticated;
grant execute on function onflows_private.save_management_plan_core(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid)
 to service_role;

-- Keep the existing eight-argument private/public and compact contracts intact.
create or replace function onflows_private.save_management_plan(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean default false
) returns jsonb language sql security definer set search_path='' as $$
 select onflows_private.save_management_plan_core(p_alias,p_payload,p_expected_revision,p_actor,p_operation,
   p_expected_profile_revision,p_checkpoint,p_automatic,null)
$$;
revoke all on function onflows_private.save_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
 from public,anon,authenticated;
grant execute on function onflows_private.save_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
 to service_role;

create function public.record_onflows_management_check(
 p_alias text,p_expected_revision integer,p_actor uuid,p_expected_profile_revision integer,p_checkpoint jsonb,p_check_id uuid
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.save_management_plan_core(p_alias,null,p_expected_revision,p_actor,'CHECK_NO_CHANGE',
   p_expected_profile_revision,p_checkpoint,false,p_check_id)
$$;
revoke all on function public.record_onflows_management_check(text,integer,uuid,integer,jsonb,uuid)
 from public,anon,authenticated;
grant execute on function public.record_onflows_management_check(text,integer,uuid,integer,jsonb,uuid)
 to service_role;

create function public.read_onflows_management_checks(p_alias text,p_before bigint default null,p_limit integer default 10)
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare result jsonb;
begin
 if p_alias is null or p_alias='' or p_limit is null or p_limit not between 1 and 50
    or (p_before is not null and p_before<1) then
   raise exception 'Invalid management check page' using errcode='22023';
 end if;
 with windowed as materialized (
   select check_id,sequence,checked_revision,recorded_at
   from onflows_private.management_plan_checks
   where athlete_alias=p_alias and (p_before is null or sequence<p_before)
   order by sequence desc limit p_limit+1
 ), page as materialized (
   select * from windowed order by sequence desc limit p_limit
 )
 select jsonb_build_object('checks',coalesce((select jsonb_agg(jsonb_build_object(
   'id',check_id,'sequence',sequence,'checked_revision',checked_revision,'operation','CHECK_NO_CHANGE',
   'recorded_at',recorded_at,'reason','Проверката приключи. Програмата остава без промяна.'
 ) order by sequence desc) from page),'[]'::jsonb),
   'next_before',case when (select count(*) from windowed)>p_limit then (select min(sequence) from page) else null end)
 into result;
 return result;
end $$;
revoke all on function public.read_onflows_management_checks(text,bigint,integer) from public,anon,authenticated;
grant execute on function public.read_onflows_management_checks(text,bigint,integer) to service_role;

notify pgrst, 'reload schema';
