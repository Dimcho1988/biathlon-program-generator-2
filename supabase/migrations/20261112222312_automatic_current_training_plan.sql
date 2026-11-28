-- Generated valid prescriptions become current without a separate approval step.
-- Existing authorization, validation, immutable history and publication fences remain.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

create or replace function onflows_private.save_management_plan_core(
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
 approved_actor := case when p_operation in ('ACTIVATE','APPROVE','RESUME')
  or (not p_automatic and p_operation in ('ADAPT','DAY','REVIEW')
   and p_payload->>'approved_by'=p_actor::text) then p_actor else state.approved_by end;
 if approved_actor is null or p_payload->>'approved_by' is distinct from approved_actor::text then
   raise exception 'Invalid approval identity' using errcode='22023';
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

create or replace function onflows_private.save_plan_after_training(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean,
 p_training_token uuid,p_training_revision bigint
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.training_plan_updates%rowtype;
begin
 -- Match management's lock before source and queue locks. An activation takes
 -- the source row first; never take its event row before that source lock.
 perform pg_advisory_xact_lock(hashtextextended('management:'||p_alias,0));
 -- Same source-lock order as the core writer, before taking the event row.
 -- Input-change triggers enqueue on that row while holding their source row.
 perform 1 from public.onflows_athlete_settings where athlete_alias=p_alias for share;
 lock table public.onflows_model_entries in share mode;
 perform 1 from public.onflows_athlete_analysis_state where athlete_alias=p_alias for share;
 select * into j from onflows_private.training_plan_updates where athlete_alias=p_alias for update;
 if not found or p_training_token is null or j.lease_token is distinct from p_training_token
  or j.lease_until is null or j.lease_until<=clock_timestamp()
  or j.claimed_revision is distinct from p_training_revision or j.requested_revision<>p_training_revision then
  return jsonb_build_object('conflict',true);
 end if;
 if p_actor is distinct from j.claimed_actor or (p_automatic is distinct from true and not j.claimed_manual)
  or (j.claimed_manual and p_expected_revision is distinct from j.claimed_plan_revision) then
  return jsonb_build_object('conflict',true); end if;
 return public.save_onflows_management_plan_compact(p_alias,p_payload,p_expected_revision,p_actor,p_operation,
  p_expected_profile_revision,p_checkpoint,p_automatic);
end $$;

-- A generated artifact and its current prescription publish in one transaction.
-- The private implementation can lock read-only state; browser roles receive no
-- privileges. The actor, current profile, generation and complete input checkpoint
-- are validated by the existing writers, not trusted from an assistant response.
create function onflows_private.save_generated_plan(
 p_alias text,p_draft jsonb,p_expected_draft_revision integer,p_actor uuid,
 p_expected_profile_revision integer,p_publication jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare saved jsonb; published jsonb; state public.onflows_management_plan_state%rowtype;
 expected_plan integer; old_payload jsonb; result_message text; current_profile integer;
begin
 if p_actor is null or not onflows_private.can_edit_management(p_alias,p_actor) then
  raise exception 'Planning access required' using errcode='42501'; end if;
 if p_draft is null or jsonb_typeof(p_draft)<>'object' or p_expected_draft_revision is null
  or p_expected_draft_revision<0 or p_expected_profile_revision is null or p_expected_profile_revision<1 then
  raise exception 'Invalid generated plan request' using errcode='22023'; end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:'||p_alias,0));
 select * into state from public.onflows_management_plan_state where athlete_alias=p_alias for update;
 if p_publication='null'::jsonb then p_publication:=null; end if;
 if p_publication is null then
  if onflows_private.management_plan_eligible(p_draft) and coalesce(state.status,'')<>'PAUSED' then
   raise exception 'A complete generated plan requires current publication' using errcode='22023'; end if;
 else
  if jsonb_typeof(p_publication)<>'object'
    or not (p_publication ?& array['payload','expected_revision','checkpoint'])
    or (p_publication-array['payload','expected_revision','checkpoint'])<>'{}'::jsonb
    or jsonb_typeof(p_publication->'expected_revision') is distinct from 'number'
    or (p_publication->>'expected_revision') !~ '^[0-9]{1,9}$'
    or jsonb_typeof(p_publication->'checkpoint') is distinct from 'object'
    or p_publication#>>'{payload,status}' is distinct from 'ACTIVE'
    or p_publication#>>'{payload,origin,start_date}' is distinct from p_draft->>'start_date'
    or p_publication#>'{payload,origin,draft_revision}' is distinct from to_jsonb(p_expected_draft_revision+1)
    or p_publication#>'{payload,plan}' is distinct from p_draft then
   raise exception 'Invalid generated plan publication' using errcode='22023'; end if;
  if state.status='PAUSED' then
   return jsonb_build_object('conflict',true,'reason','PLAN_PAUSED'); end if;
  expected_plan:=(p_publication->>'expected_revision')::integer;
 end if;
 -- The exception block rolls back the draft append if publication loses its CAS.
 -- Returning a conflict from the second writer alone would leave half a save.
 begin
  saved:=public.save_onflows_management_entry(p_alias,'DRAFT',p_draft->>'start_date',p_draft,
   p_expected_draft_revision,p_actor,p_expected_profile_revision,
   (p_draft#>>'{source,generation_id}')::uuid,true,p_draft#>'{input_snapshot,response_revisions}');
  if saved->>'saved' is distinct from 'true' then return saved; end if;
  if p_publication is not null then
   if saved->'revision' is distinct from p_publication#>'{payload,origin,draft_revision}' then
    raise exception using errcode='PZ001',message='{"conflict":true,"reason":"DRAFT_REVISION_CHANGED"}'; end if;
   -- A byte-equivalent retry may acknowledge the immutable revision once, but
   -- cannot overwrite a later edit or reuse a result after the inputs changed.
   select payload into old_payload from public.onflows_management_plan_revisions
    where athlete_alias=p_alias and revision=state.revision;
   select revision into current_profile from public.onflows_management_entries
    where athlete_alias=p_alias and kind='PROFILE' and entry_key='management-profile-v1'
    order by revision desc limit 1;
   if saved->>'unchanged'='true' and state.revision=expected_plan+1
     and old_payload=p_publication->'payload' and current_profile=p_expected_profile_revision
     and public.onflows_management_checkpoint(p_alias)=p_publication->'checkpoint'
     and p_publication#>>'{payload,evaluated_on}'=(select (clock_timestamp() at time zone timezone)::date::text
      from public.onflows_athlete_settings where athlete_alias=p_alias) then
    published:=jsonb_build_object('saved',true,'revision',state.revision,'unchanged',true);
   else
    published:=onflows_private.save_management_plan_core(p_alias,p_publication->'payload',expected_plan,p_actor,
     'ACTIVATE',p_expected_profile_revision,
     p_publication->'checkpoint',false,null);
   end if;
   if published->>'saved' is distinct from 'true' then
    raise exception using errcode='PZ001',message=published::text; end if;
  end if;
 exception when sqlstate 'PZ001' then
  get stacked diagnostics result_message=message_text;
  return result_message::jsonb;
 end;
 return saved||jsonb_build_object('active_revision',published->'revision',
  'published',coalesce(published->>'saved'='true',false),'publication_checked',true);
end $$;
revoke all on function onflows_private.save_generated_plan(text,jsonb,integer,uuid,integer,jsonb)
 from public,anon,authenticated;
grant execute on function onflows_private.save_generated_plan(text,jsonb,integer,uuid,integer,jsonb) to service_role;
create function public.save_onflows_generated_plan(
 p_alias text,p_draft jsonb,p_expected_draft_revision integer,p_actor uuid,
 p_expected_profile_revision integer,p_publication jsonb default null
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.save_generated_plan(p_alias,p_draft,p_expected_draft_revision,p_actor,
  p_expected_profile_revision,p_publication)
$$;
revoke all on function public.save_onflows_generated_plan(text,jsonb,integer,uuid,integer,jsonb)
 from public,anon,authenticated;
grant execute on function public.save_onflows_generated_plan(text,jsonb,integer,uuid,integer,jsonb) to service_role;

create or replace function onflows_private.planning_job(
 p_operation text,p_alias text default null,p_actor uuid default null,p_request jsonb default null,
 p_job uuid default null,p_token uuid default null,p_stage text default null,p_payload jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.planning_jobs%rowtype; saved jsonb; profile_rev integer; draft_rev integer;
 day date; zone text; result_code text;
begin
 if p_operation is null or p_operation not in ('ENQUEUE','STATUS','CLAIM','RENEW','COMPLETE','FAIL') then
  raise exception 'Invalid planning job operation' using errcode='22023';
 end if;
 if p_operation in ('ENQUEUE','STATUS') then
  if p_actor is null or not onflows_private.can_prepare_plan(p_alias,p_actor) then
   raise exception 'Planning access required' using errcode='42501';
  end if;
 end if;
 if p_operation='STATUS' then
  select * into j from onflows_private.planning_jobs where athlete_alias=p_alias
    and (p_job is null or job_id=p_job);
  return jsonb_build_object('job',onflows_private.planning_job_view(j));
 elsif p_operation='ENQUEUE' then
  if p_request is null or jsonb_typeof(p_request)<>'object' or octet_length(p_request::text)>2048
    or not (p_request ?& array['start_date','expected_profile_revision','expected_draft_revision'])
    or (p_request-array['start_date','expected_profile_revision','expected_draft_revision'])<>'{}'::jsonb
    or jsonb_typeof(p_request->'start_date') is distinct from 'string'
    or jsonb_typeof(p_request->'expected_profile_revision') is distinct from 'number'
    or jsonb_typeof(p_request->'expected_draft_revision') is distinct from 'number'
    or (p_request->>'start_date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    or (p_request->>'expected_profile_revision') !~ '^[0-9]{1,9}$'
    or (p_request->>'expected_draft_revision') !~ '^[0-9]{1,9}$' then
   raise exception 'Invalid planning job request' using errcode='22023';
  end if;
  day := (p_request->>'start_date')::date;
  select timezone into zone from public.onflows_athlete_settings where athlete_alias=p_alias;
  if day < (clock_timestamp() at time zone zone)::date
    or day > (clock_timestamp() at time zone zone)::date+7 then
   return jsonb_build_object('conflict',true,'reason','INVALID_DATE');
  end if;
  -- Match the completion lock order, then the existing publication lock.
  perform pg_advisory_xact_lock(hashtextextended('planning-job:'||p_alias,0));
  select * into j from onflows_private.planning_jobs where athlete_alias=p_alias for update;
  if found and j.actor_id=p_actor and j.request=p_request and j.status in ('QUEUED','RUNNING','SUCCEEDED') then
   return onflows_private.planning_job_view(j);
  end if;
  if found and j.status in ('QUEUED','RUNNING') then
   return jsonb_build_object('conflict',true,'reason','JOB_IN_PROGRESS');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('management:'||p_alias,0));
  select coalesce(max(revision),0) into profile_rev from public.onflows_management_entries
   where athlete_alias=p_alias and kind='PROFILE' and entry_key='management-profile-v1';
  select coalesce(max(revision),0) into draft_rev from public.onflows_management_entries
   where athlete_alias=p_alias and kind='DRAFT' and entry_key=day::text;
  if profile_rev<1 or profile_rev<>(p_request->>'expected_profile_revision')::integer
    or draft_rev<>(p_request->>'expected_draft_revision')::integer then
   return jsonb_build_object('conflict',true,'reason','INPUT_CHANGED');
  end if;
  insert into onflows_private.planning_jobs(athlete_alias,actor_id,request,status,stage)
   values(p_alias,p_actor,p_request,'QUEUED','QUEUED')
   on conflict(athlete_alias) do update set job_id=gen_random_uuid(),actor_id=excluded.actor_id,
     request=excluded.request,status='QUEUED',stage='QUEUED',attempts=0,lease_token=null,lease_until=null,
     available_at=clock_timestamp(),requested_at=clock_timestamp(),updated_at=clock_timestamp(),receipt=null,error_code=null
   returning * into j;
  return onflows_private.planning_job_view(j);
 elsif p_operation='CLAIM' then
  if p_token is null then raise exception 'Worker token required' using errcode='22023'; end if;
  -- Bounded recovery of tasks that crashed on their last permitted attempt.
  update onflows_private.planning_jobs set status='FAILED',stage='FAILED',error_code='RETRIES_EXHAUSTED',updated_at=clock_timestamp()
   where athlete_alias in (select athlete_alias from onflows_private.planning_jobs
    where status='RUNNING' and lease_until<=clock_timestamp() and attempts>=3
      and (p_alias is null or athlete_alias=p_alias)
    order by requested_at limit 20 for update skip locked);
  select * into j from onflows_private.planning_jobs
   where (p_alias is null or athlete_alias=p_alias) and attempts<3 and ((status='QUEUED' and available_at<=clock_timestamp())
    or (status='RUNNING' and lease_until<=clock_timestamp()))
   order by requested_at limit 1 for update skip locked;
  if not found then return null; end if;
  if not onflows_private.can_prepare_plan(j.athlete_alias,j.actor_id) then
   update onflows_private.planning_jobs set status='FAILED',stage='FAILED',error_code='ACCESS_CHANGED',updated_at=clock_timestamp()
    where athlete_alias=j.athlete_alias;
   return null;
  end if;
  update onflows_private.planning_jobs set status='RUNNING',stage='PREPARING',attempts=attempts+1,
   lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',updated_at=clock_timestamp(),error_code=null,
   claim_checkpoint=onflows_private.planning_job_checkpoint(j.athlete_alias)
   where athlete_alias=j.athlete_alias returning * into j;
  return jsonb_build_object('job_id',j.job_id,'athlete_alias',j.athlete_alias,'actor_id',j.actor_id,'request',j.request);
 end if;
 if p_job is null or p_token is null then raise exception 'Job and lease required' using errcode='22023'; end if;
 if p_operation='COMPLETE' then
  -- Serialize enqueue/complete before taking the old draft writer's lock.
  select athlete_alias into p_alias from onflows_private.planning_jobs where job_id=p_job;
  if p_alias is null then return jsonb_build_object('state','LOST'); end if;
  perform pg_advisory_xact_lock(hashtextextended('planning-job:'||p_alias,0));
 end if;
 select * into j from onflows_private.planning_jobs where job_id=p_job for update;
 if not found then return jsonb_build_object('state','LOST'); end if;
 if j.status='SUCCEEDED' and j.lease_token=p_token then return onflows_private.planning_job_view(j); end if;
 if j.status<>'RUNNING' or j.lease_token is distinct from p_token or j.lease_until is null or j.lease_until<=clock_timestamp() then
  return jsonb_build_object('state','LOST');
 end if;
 if p_operation='RENEW' then
  if p_stage is not null and p_stage not in ('PREPARING','BASELINE','SEARCH','SAVING') then
   raise exception 'Invalid job stage' using errcode='22023';
  end if;
  update onflows_private.planning_jobs set lease_until=clock_timestamp()+interval '90 seconds',
   stage=coalesce(p_stage,stage),updated_at=clock_timestamp() where job_id=p_job returning * into j;
 elsif p_operation='COMPLETE' then
  perform pg_advisory_xact_lock(hashtextextended('management:'||j.athlete_alias,0));
  if j.claim_checkpoint is null or j.claim_checkpoint is distinct from onflows_private.planning_job_checkpoint(j.athlete_alias) then
   update onflows_private.planning_jobs set status='FAILED',stage='FAILED',error_code='INPUT_CHANGED',updated_at=clock_timestamp()
    where job_id=p_job returning * into j;
   return onflows_private.planning_job_view(j);
  end if;
  -- Lease fencing and the authoritative CAS/access/input checks commit together.
  begin
   if p_payload ? 'draft' then
    if jsonb_typeof(p_payload)<>'object' or not (p_payload ? 'publication')
      or p_payload#>>'{draft,start_date}' is distinct from j.request->>'start_date'
      or (p_payload-array['draft','publication'])<>'{}'::jsonb then
     raise exception 'Invalid generated job result' using errcode='22023'; end if;
    saved:=public.save_onflows_generated_plan(j.athlete_alias,p_payload->'draft',
     (j.request->>'expected_draft_revision')::integer,j.actor_id,
     (j.request->>'expected_profile_revision')::integer,p_payload->'publication');
   else
    -- Rolling deployment compatibility for a worker still returning a bare draft.
   saved := public.save_onflows_management_entry(j.athlete_alias,'DRAFT',j.request->>'start_date',p_payload,
    (j.request->>'expected_draft_revision')::integer,j.actor_id,
    (j.request->>'expected_profile_revision')::integer,(p_payload->'source'->>'generation_id')::uuid,true,
    p_payload->'input_snapshot'->'response_revisions');
   end if;
   if saved->>'saved'='true' then
    update onflows_private.planning_jobs set status='SUCCEEDED',stage='READY',receipt=saved-'payload',
     updated_at=clock_timestamp() where job_id=p_job returning * into j;
   else
    update onflows_private.planning_jobs set status='FAILED',stage='FAILED',error_code='INPUT_CHANGED',
     updated_at=clock_timestamp() where job_id=p_job returning * into j;
   end if;
  exception when insufficient_privilege then
   update onflows_private.planning_jobs set status='FAILED',stage='FAILED',error_code='ACCESS_CHANGED',
    updated_at=clock_timestamp() where job_id=p_job returning * into j;
  end;
 elsif p_operation='FAIL' then
  result_code := case when p_stage in ('INPUT_CHANGED','INVALID_INPUT','ACCESS_CHANGED','TEMPORARILY_UNAVAILABLE')
    then p_stage else 'COMPUTATION_FAILED' end;
  update onflows_private.planning_jobs set
   status=case when result_code='TEMPORARILY_UNAVAILABLE' and attempts<3 then 'QUEUED' else 'FAILED' end,
   stage=case when result_code='TEMPORARILY_UNAVAILABLE' and attempts<3 then 'QUEUED' else 'FAILED' end,
   available_at=clock_timestamp()+interval '30 seconds',error_code=result_code,updated_at=clock_timestamp()
   where job_id=p_job returning * into j;
 end if;
 return onflows_private.planning_job_view(j);
end $$;

create or replace function onflows_private.planning_job_view(j onflows_private.planning_jobs) returns jsonb
language sql stable set search_path='' as $$
 select case when j.job_id is null then null else jsonb_build_object(
  'job_id',j.job_id,'status',j.status,'stage',j.stage,'start_date',j.request->>'start_date',
  'requested_at',j.requested_at,'updated_at',j.updated_at,'attempts',j.attempts,
  'result_revision',j.receipt->'revision','result_active_revision',j.receipt->'active_revision',
  'published',coalesce(j.receipt->'published','false'::jsonb),'error_code',j.error_code) end
$$;


-- Adopt legacy draft-only profiles by requesting fresh canonical computation.
-- Never copy an old draft into the active pointer. The next worker revalidates
-- settings, observations, source generation and permissions before publication.
create function onflows_private.queue_unpublished_plans(p_limit integer default 10) returns jsonb
language plpgsql security definer set search_path='' as $$
declare candidate record; job onflows_private.planning_jobs%rowtype; request jsonb;
 result jsonb; queued integer:=0; today_local date; draft_revision integer; actor uuid;
begin
 if p_limit is null or p_limit not between 1 and 50 then
  raise exception 'Invalid adoption batch size' using errcode='22023'; end if;
 for candidate in
  select s.athlete_alias,s.timezone,p.revision,p.payload,rights.actor_id,dates.start_date
  from public.onflows_athlete_settings s
  join lateral (select e.revision,e.payload,e.actor_id from public.onflows_management_entries e
   where e.athlete_alias=s.athlete_alias and e.kind='PROFILE' and e.entry_key='management-profile-v1'
   order by e.revision desc limit 1) p on true
  cross join lateral (select case when onflows_private.can_prepare_plan(s.athlete_alias,p.actor_id) then p.actor_id
   else (select u.user_id from public.onflows_user_athletes u where u.athlete_alias=s.athlete_alias and u.is_owner limit 1) end as actor_id) rights
  cross join lateral (select (clock_timestamp() at time zone s.timezone)::date as today) local
  cross join lateral (select case when p.payload->>'program_start' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
   and p.payload->>'program_start'>local.today::text then p.payload->>'program_start' else local.today::text end as start_date) dates
  left join onflows_private.planning_jobs prior on prior.athlete_alias=s.athlete_alias
  where not exists(select 1 from public.onflows_management_plan_state a where a.athlete_alias=s.athlete_alias)
   and onflows_private.can_prepare_plan(s.athlete_alias,rights.actor_id)
   and dates.start_date<=(local.today+7)::text
   and (prior.athlete_alias is null or (prior.status not in ('QUEUED','RUNNING') and not (
    prior.request->>'start_date'=dates.start_date
    and prior.request->>'expected_profile_revision'=p.revision::text
    and coalesce(prior.claim_checkpoint=onflows_private.planning_job_checkpoint(s.athlete_alias),false)
    and (prior.status='FAILED' or coalesce(prior.receipt->>'publication_checked'='true',false))
   )))
   and exists(select 1 from public.onflows_management_entries d where d.athlete_alias=s.athlete_alias and d.kind='DRAFT')
  order by s.athlete_alias limit p_limit
 loop
  today_local:=candidate.start_date::date;
  actor:=candidate.actor_id;
  if not onflows_private.can_prepare_plan(candidate.athlete_alias,actor) then continue; end if;
  -- Same ordering as ENQUEUE/COMPLETE; a concurrent explicit request wins.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('planning-job:'||candidate.athlete_alias,0));
  select * into job from onflows_private.planning_jobs where athlete_alias=candidate.athlete_alias for update;
  if found and (job.status in ('QUEUED','RUNNING') or (
    job.request->>'start_date'=today_local::text
    and job.request->>'expected_profile_revision'=candidate.revision::text
    and job.claim_checkpoint=onflows_private.planning_job_checkpoint(candidate.athlete_alias)
    and (job.status='FAILED' or job.receipt->>'publication_checked'='true')
  )) then continue; end if;
  -- Recheck the absent pointer under the writer lock, including pause/completion.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:'||candidate.athlete_alias,0));
  if exists(select 1 from public.onflows_management_plan_state where athlete_alias=candidate.athlete_alias) then continue; end if;
  select coalesce(max(revision),0) into draft_revision from public.onflows_management_entries
   where athlete_alias=candidate.athlete_alias and kind='DRAFT' and entry_key=today_local::text;
  request:=jsonb_build_object('start_date',today_local::text,'expected_profile_revision',candidate.revision,
   'expected_draft_revision',draft_revision);
  result:=onflows_private.planning_job('ENQUEUE',candidate.athlete_alias,actor,request);
  if result->>'status'='QUEUED' then queued:=queued+1; end if;
 end loop;
 return jsonb_build_object('queued',queued);
end $$;
revoke all on function onflows_private.queue_unpublished_plans(integer) from public,anon,authenticated;
grant execute on function onflows_private.queue_unpublished_plans(integer) to service_role;
create function public.queue_onflows_unpublished_plans(p_limit integer default 10) returns jsonb
language sql security invoker set search_path='' as $$ select onflows_private.queue_unpublished_plans(p_limit) $$;
revoke all on function public.queue_onflows_unpublished_plans(integer) from public,anon,authenticated;
grant execute on function public.queue_onflows_unpublished_plans(integer) to service_role;

create or replace function onflows_private.training_plan_update(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare a text; d date; result jsonb;
begin
 if p_operation='CLAIM' then
  -- Both supported modes prepare against the new local day. The canonical
  -- planner retains a fixed horizon when appropriate; pauses/completion stay put.
  for a,d in select s.athlete_alias,(clock_timestamp() at time zone z.timezone)::date
   from public.onflows_management_plan_state s
   join public.onflows_management_plan_revisions r using(athlete_alias,revision)
   join public.onflows_athlete_settings z using(athlete_alias)
   join lateral (select payload from public.onflows_management_entries e where e.athlete_alias=s.athlete_alias
    and e.kind='PROFILE' and e.entry_key='management-profile-v1' order by revision desc limit 1) p on true
   left join onflows_private.training_plan_updates t using(athlete_alias)
   where s.status in ('ACTIVE','REVIEW_REQUIRED')
    and (p_alias is null or s.athlete_alias=p_alias)
    and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
    and coalesce(t.last_evaluated_on,(r.payload->>'evaluated_on')::date,'-infinity'::date)<(clock_timestamp() at time zone z.timezone)::date
    and (t.athlete_alias is null or t.requested_revision=t.completed_revision)
   limit 10 loop
   insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
    values(a,(select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=a))
   on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
    requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;
  end loop;
 end if;
 result:=onflows_private.training_plan_update_core_v1(p_operation,p_alias,p_token);
 if p_operation='CLAIM' and result is not null then
  select (clock_timestamp() at time zone timezone)::date into d from public.onflows_athlete_settings where athlete_alias=result->>'athlete_alias';
  update onflows_private.training_plan_updates set claimed_day=d,claimed_actor=(result->>'actor_id')::uuid,
   claimed_plan_revision=requested_plan_revision,claimed_manual=requested_manual,claimed_record_check=requested_record_check
   where athlete_alias=result->>'athlete_alias' and lease_token=p_token;
  select result||jsonb_build_object('manual',claimed_manual,'expected_plan_revision',claimed_plan_revision,
   'record_unchanged_check',claimed_record_check) into result from onflows_private.training_plan_updates
   where athlete_alias=result->>'athlete_alias';
 elsif p_operation in ('COMPLETE','REJECT') and result->>'state' in ('READY','REJECTED') then
  update onflows_private.training_plan_updates set last_evaluated_on=claimed_day,
   requested_manual=case when requested_revision=completed_revision then false else requested_manual end,
   requested_actor=case when requested_revision=completed_revision then null else requested_actor end,
   requested_plan_revision=case when requested_revision=completed_revision then null else requested_plan_revision end,
   requested_record_check=case when requested_revision=completed_revision then false else requested_record_check end
   where athlete_alias=p_alias;
 end if;
 if p_operation in ('COMPLETE','REJECT','FAIL') and p_alias is not null then
  perform onflows_private.request_prepared_views(p_alias,false);
 end if;
 return result;
end $$;


-- Changes made through existing forms or assistant commands request the same
-- canonical background refresh. Plan publications themselves are not inputs,
-- so this does not generate a self-sustaining refresh loop.
create function onflows_private.planning_inputs_changed() returns trigger
language plpgsql security definer set search_path='' as $$
declare a text; actor uuid;
begin
 if tg_table_name='onflows_management_entries' then
  if (case when tg_op='DELETE' then old.kind else new.kind end)<>'PROFILE' then
   if tg_op='DELETE' then return old; else return new; end if;
  end if;
 end if;
 if tg_op='UPDATE' and (to_jsonb(new)-'updated_at')=(to_jsonb(old)-'updated_at') then return new; end if;
 a:=case when tg_op='DELETE' then old.athlete_alias else new.athlete_alias end;
 select approved_by into actor from public.onflows_management_plan_state
  where athlete_alias=a and status in ('ACTIVE','REVIEW_REQUIRED');
 if actor is not null and onflows_private.can_prepare_plan(a,actor) then
  insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
   values(a,(select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=a))
   on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
    generation_id=excluded.generation_id,requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;
 end if;
 if tg_op='DELETE' then return old; else return new; end if;
end $$;
revoke all on function onflows_private.planning_inputs_changed() from public,anon,authenticated,service_role;
create trigger current_plan_profile_changed after insert or update or delete on public.onflows_management_entries
 for each row execute function onflows_private.planning_inputs_changed();
create trigger current_plan_settings_changed after insert or update or delete on public.onflows_athlete_settings
 for each row execute function onflows_private.planning_inputs_changed();
create trigger current_plan_models_changed after insert or update or delete on public.onflows_model_entries
 for each row execute function onflows_private.planning_inputs_changed();
create trigger current_plan_responses_changed after insert or update or delete on public.onflows_response_entries
 for each row execute function onflows_private.planning_inputs_changed();

-- Revalidate existing review-required programs using current inputs. This queues
-- computation only; no historical proposal is promoted by the migration.
insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
 select s.athlete_alias,a.active_generation_id from public.onflows_management_plan_state s
 left join public.onflows_athlete_analysis_state a using(athlete_alias)
 where s.status='REVIEW_REQUIRED' and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
 on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
  generation_id=excluded.generation_id,requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;


alter function public.save_onflows_generated_plan(text,jsonb,integer,uuid,integer,jsonb) set statement_timeout='30s';

notify pgrst,'reload schema';
commit;
