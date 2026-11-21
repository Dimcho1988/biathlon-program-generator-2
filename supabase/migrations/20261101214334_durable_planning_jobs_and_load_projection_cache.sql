-- Server-only, bounded background work. No training rules or existing writers change.
-- One current job and one derived history per athlete; no full recordings retained.
create table onflows_private.planning_jobs (
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 job_id uuid not null unique default gen_random_uuid(),
 actor_id uuid not null references auth.users(id),
 request jsonb not null check (jsonb_typeof(request)='object' and octet_length(request::text)<=2048),
 status text not null check (status in ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
 stage text not null,
 attempts integer not null default 0 check (attempts between 0 and 3),
 lease_token uuid,
 lease_until timestamptz,
 available_at timestamptz not null default clock_timestamp(),
 requested_at timestamptz not null default clock_timestamp(),
 updated_at timestamptz not null default clock_timestamp(),
 receipt jsonb,
 error_code text
);
create index planning_jobs_ready on onflows_private.planning_jobs(available_at,requested_at)
 where status in ('QUEUED','RUNNING');
create index planning_jobs_actor on onflows_private.planning_jobs(actor_id);
alter table onflows_private.planning_jobs enable row level security;
revoke all on onflows_private.planning_jobs from public,anon,authenticated,service_role;

create table onflows_private.load_projection_cache (
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 input_key text not null check (input_key ~ '^[a-f0-9]{64}$'),
 payload jsonb not null check (jsonb_typeof(payload)='object' and octet_length(payload::text)<=8388608),
 expires_at timestamptz not null,
 updated_at timestamptz not null default clock_timestamp()
);
alter table onflows_private.load_projection_cache enable row level security;
revoke all on onflows_private.load_projection_cache from public,anon,authenticated,service_role;

create function onflows_private.can_prepare_plan(p_alias text,p_actor uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select exists (
  select 1 from public.onflows_user_athletes owner
  where owner.athlete_alias=p_alias and owner.is_owner and (
   owner.user_id=p_actor
   or exists(select 1 from public.onflows_sharing_grants g
      where g.owner_user_id=owner.user_id and g.viewer_user_id=p_actor and g.edit_plan)
   or exists(select 1 from public.onflows_organization_memberships a
      join public.onflows_organization_memberships c on c.organization_id=a.organization_id
      where a.user_id=owner.user_id and a.role='ATHLETE' and a.status='ACTIVE'
        and c.user_id=p_actor and c.status='ACTIVE' and (c.role in ('ADMIN','HEAD_COACH')
          or (c.role='COACH' and exists(select 1 from public.onflows_coach_athlete_assignments x
            where x.organization_id=a.organization_id and x.athlete_user_id=owner.user_id
              and x.coach_user_id=p_actor and x.can_edit_plan))))
  ))
$$;
revoke all on function onflows_private.can_prepare_plan(text,uuid) from public,anon,authenticated,service_role;

create function onflows_private.planning_job_view(j onflows_private.planning_jobs) returns jsonb
language sql stable set search_path='' as $$
 select case when j.job_id is null then null else jsonb_build_object(
  'job_id',j.job_id,'status',j.status,'stage',j.stage,'start_date',j.request->>'start_date',
  'requested_at',j.requested_at,'updated_at',j.updated_at,'attempts',j.attempts,
  'result_revision',j.receipt->'revision','error_code',j.error_code) end
$$;
revoke all on function onflows_private.planning_job_view(onflows_private.planning_jobs)
 from public,anon,authenticated,service_role;

create function onflows_private.planning_job(
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
   lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',updated_at=clock_timestamp(),error_code=null
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
  -- Lease fencing and the authoritative CAS/access/input checks commit together.
  begin
   saved := public.save_onflows_management_entry(j.athlete_alias,'DRAFT',j.request->>'start_date',p_payload,
    (j.request->>'expected_draft_revision')::integer,j.actor_id,
    (j.request->>'expected_profile_revision')::integer,(p_payload->'source'->>'generation_id')::uuid,true,
    p_payload->'input_snapshot'->'response_revisions');
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
revoke all on function onflows_private.planning_job(text,text,uuid,jsonb,uuid,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.planning_job(text,text,uuid,jsonb,uuid,uuid,text,jsonb) to service_role;
create function public.onflows_planning_job(
 p_operation text,p_alias text default null,p_actor uuid default null,p_request jsonb default null,
 p_job uuid default null,p_token uuid default null,p_stage text default null,p_payload jsonb default null
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.planning_job(p_operation,p_alias,p_actor,p_request,p_job,p_token,p_stage,p_payload)
$$;
revoke all on function public.onflows_planning_job(text,text,uuid,jsonb,uuid,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_planning_job(text,text,uuid,jsonb,uuid,uuid,text,jsonb) to service_role;

create function onflows_private.load_projection(p_alias text,p_key text,p_payload jsonb default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare value jsonb;
begin
 if p_alias is null or p_key is null or p_key !~ '^[a-f0-9]{64}$' then
  raise exception 'Invalid projection key' using errcode='22023';
 end if;
 if p_payload is null then
  select payload into value from onflows_private.load_projection_cache
   where athlete_alias=p_alias and input_key=p_key and expires_at>clock_timestamp();
  return value;
 end if;
 if jsonb_typeof(p_payload)<>'object' or octet_length(p_payload::text)>8388608
   or p_payload->'load_selection'->>'fingerprint' is distinct from p_key then
  return jsonb_build_object('stored',false);
 end if;
 insert into onflows_private.load_projection_cache(athlete_alias,input_key,payload,expires_at)
  values(p_alias,p_key,p_payload,clock_timestamp()+interval '2 days')
  on conflict(athlete_alias) do update set input_key=excluded.input_key,payload=excluded.payload,
   expires_at=excluded.expires_at,updated_at=clock_timestamp();
 return jsonb_build_object('stored',true);
end $$;
revoke all on function onflows_private.load_projection(text,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.load_projection(text,text,jsonb) to service_role;
create function public.onflows_load_projection(p_alias text,p_key text,p_payload jsonb default null)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.load_projection(p_alias,p_key,p_payload)
$$;
revoke all on function public.onflows_load_projection(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_load_projection(text,text,jsonb) to service_role;
notify pgrst,'reload schema';
