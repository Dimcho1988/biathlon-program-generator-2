-- Match Python _eligible exactly, including typed JSON booleans.
create or replace function onflows_private.management_plan_eligible(plan jsonb) returns boolean
language sql immutable security invoker set search_path='' as $$
 select coalesce(plan->'activation_eligible'='true'::jsonb and (
   plan#>'{source,readiness_known}'='true'::jsonb or (
   plan#>'{source,planning_history_supported}'='true'::jsonb
   and plan#>>'{source,readiness_basis}'='ESTIMATED_LOAD'
   and plan#>'{source,planning_history,supported}'='true'::jsonb
   and plan#>'{source,planning_history,estimated}'='true'::jsonb
   and plan#>>'{source,planning_history,policy}'='RECORDED_DURATION_EXPERT_Q_WITH_RECOVERY')), false)
$$;
revoke all on function onflows_private.management_plan_eligible(jsonb) from public,anon,authenticated;
grant execute on function onflows_private.management_plan_eligible(jsonb) to service_role;

create or replace function onflows_private.save_management_plan(
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
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
   values(p_alias,p_expected_revision+1,p_operation,p_actor,p_payload) returning recorded_at into saved_at;
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by,next_check_at)
   values(p_alias,p_expected_revision+1,p_payload->>'status',approved_actor,clock_timestamp()+interval '5 minutes')
   on conflict(athlete_alias) do update set revision=excluded.revision,status=excluded.status,
     approved_by=excluded.approved_by,next_check_at=excluded.next_check_at;
 return jsonb_build_object('saved',true,'revision',p_expected_revision+1,'payload',p_payload,'recorded_at',saved_at);
end $$;

create or replace function onflows_private.queue_management_import(p_alias text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare state public.onflows_management_plan_state%rowtype; profile jsonb; local_day text; slot text; result jsonb; race_end date; effective_end date;
begin
 select * into state from public.onflows_management_plan_state where athlete_alias=p_alias for share;
 if not found or state.status not in ('ACTIVE','REVIEW_REQUIRED')
   or not onflows_private.can_edit_management(p_alias,state.approved_by) then return jsonb_build_object('queued',false); end if;
 select payload into profile from public.onflows_management_entries where athlete_alias=p_alias and kind='PROFILE' order by revision desc limit 1;
 if coalesce((profile->>'auto_import_enabled')::boolean,true)=false then return jsonb_build_object('queued',false); end if;
 -- Every FULL sync refetches the entire 90-day provider history. Reserve
 -- provider quota for manual updates; automatic imports run at most 4/day.
 if exists(select 1 from public.onflows_sync_jobs j
   where j.athlete_alias=p_alias and j.job_kind='FULL_SYNC'
     and j.requested_at > clock_timestamp()-interval '6 hours') then
   return jsonb_build_object('queued',false,'reason','AUTOMATIC_IMPORT_COOLDOWN');
 end if;
 if not exists(select 1 from public.onflows_intervals_connections where athlete_alias=p_alias and status='CONNECTED') then
   return jsonb_build_object('queued',false,'reason','CONNECTION_REQUIRED'); end if;
 select (clock_timestamp() at time zone timezone)::date::text into local_day from public.onflows_athlete_settings where athlete_alias=p_alias;
 effective_end := (profile->>'program_end')::date;
 if coalesce(profile->>'horizon_mode','AUTO_CALENDAR')='AUTO_CALENDAR' then
   select max((event->>'end_date')::date) into race_end
   from public.onflows_athlete_settings s,
     lateral jsonb_array_elements(coalesce(s.planning_calendar->'events','[]'::jsonb)) event
   where s.athlete_alias=p_alias and event->>'event_type'='MAIN_RACE'
     and event->>'end_date'>=profile->>'program_start';
   if race_end is not null then effective_end := race_end+coalesce((profile->>'transition_days')::integer,0); end if;
 end if;
 effective_end := least(effective_end,(profile->>'program_start')::date+365);
 if local_day::date > effective_end then return jsonb_build_object('queued',false); end if;
 slot := p_alias||':'||to_char(clock_timestamp() at time zone 'UTC','YYYY-MM-DD-HH24');
 select to_jsonb(j) into result from public.enqueue_onflows_sync_job(p_alias,'FULL_SYNC',
   md5('management-import:'||slot)||md5('v2:'||slot),
   jsonb_build_object('schema_version','sync-request-v1','scope','FULL','as_of',local_day)) j;
 return jsonb_build_object('queued',true,'deduplicated',result->'deduplicated');
end $$;

-- One durable computation slot per athlete. Only the current lease owner may
-- renew or publish. A different input key waits for the current bounded lease.
create table onflows_private.planning_computations (
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 input_key text not null,
 lease_token uuid not null,
 lease_until timestamptz not null,
 status text not null check (status in ('RUNNING','READY','FAILED')),
 result jsonb,
 updated_at timestamptz not null default clock_timestamp()
);
alter table onflows_private.planning_computations enable row level security;
revoke all on onflows_private.planning_computations from public,anon,authenticated,service_role;

create function onflows_private.planning_computation(p_alias text,p_key text,p_token uuid,p_operation text,p_result jsonb default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare task onflows_private.planning_computations%rowtype;
begin
 if p_alias is null or p_key is null or p_key !~ '^[a-f0-9]{64}$' or p_token is null
    or p_operation is null or p_operation not in ('CLAIM','RENEW','COMPLETE','FAIL') then
   raise exception 'Invalid computation request' using errcode='22023';
 end if;
 if p_operation='CLAIM' then
   insert into onflows_private.planning_computations(athlete_alias,input_key,lease_token,lease_until,status)
   values(p_alias,p_key,p_token,clock_timestamp()+interval '90 seconds','RUNNING')
   on conflict (athlete_alias) do nothing;
 end if;
 select * into task from onflows_private.planning_computations where athlete_alias=p_alias for update;
 if not found then return jsonb_build_object('state','LOST'); end if;
 if p_operation='CLAIM' then
   if task.input_key=p_key and task.status='READY' then
     return jsonb_build_object('state','READY','result',task.result);
   end if;
   if task.status<>'RUNNING' or task.lease_until<=clock_timestamp() then
     update onflows_private.planning_computations set input_key=p_key,lease_token=p_token,
       lease_until=clock_timestamp()+interval '90 seconds',status='RUNNING',result=null,updated_at=clock_timestamp()
       where athlete_alias=p_alias;
     return jsonb_build_object('state','OWNED');
   end if;
   return jsonb_build_object('state',case when task.input_key=p_key and task.lease_token=p_token then 'OWNED' else 'BUSY' end);
 end if;
 if task.input_key<>p_key or task.lease_token<>p_token or task.status<>'RUNNING' or task.lease_until<=clock_timestamp() then
   return jsonb_build_object('state','LOST');
 end if;
 if p_operation='COMPLETE' then
   if p_result is null or jsonb_typeof(p_result)<>'object' then
     raise exception 'Invalid computation result' using errcode='22023';
   end if;
   update onflows_private.planning_computations set status='READY',result=p_result,updated_at=clock_timestamp() where athlete_alias=p_alias;
   return jsonb_build_object('state','READY');
 elsif p_operation='RENEW' then
   update onflows_private.planning_computations set lease_until=clock_timestamp()+interval '90 seconds',updated_at=clock_timestamp() where athlete_alias=p_alias;
   return jsonb_build_object('state','OWNED');
 else
   update onflows_private.planning_computations set status='FAILED',result=null,updated_at=clock_timestamp() where athlete_alias=p_alias;
   return jsonb_build_object('state','FAILED');
 end if;
end $$;
revoke all on function onflows_private.planning_computation(text,text,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.planning_computation(text,text,uuid,text,jsonb) to service_role;
create function public.onflows_planning_computation(p_alias text,p_key text,p_token uuid,p_operation text,p_result jsonb default null)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.planning_computation(p_alias,p_key,p_token,p_operation,p_result)
$$;
revoke all on function public.onflows_planning_computation(text,text,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_planning_computation(text,text,uuid,text,jsonb) to service_role;
