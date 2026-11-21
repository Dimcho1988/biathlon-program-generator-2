-- Derived page results live outside the public Data API. Only fenced worker
-- claims can publish; an HTTP read can request a missing variant, never fit it.
create sequence onflows_private.prepared_view_revision_seq;
revoke all on sequence onflows_private.prepared_view_revision_seq from public,anon,authenticated,service_role;

create table onflows_private.prepared_view_sources (
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 model_revision bigint not null,
 context_revision bigint not null
);
create table onflows_private.prepared_views (
 athlete_alias text not null references onflows_private.prepared_view_sources(athlete_alias) on delete cascade,
 kind text not null check(kind in ('speed','speed-history','speed-load','speed-preview','trainability','response',
  'management-methods','management-profile','management-view','management-outlook','management-active',
  'management-state','management-speed-comparison')),
 parameter_key text not null,
 parameters jsonb not null default '{}' check(jsonb_typeof(parameters)='object' and octet_length(parameters::text)<=16384),
 is_default boolean not null default false,
 prepared_revision bigint not null default 0,
 evaluated_on date,
 model_key text,
 payload jsonb,
 prepared_at timestamptz,
 requested_at timestamptz not null default clock_timestamp(),
 available_at timestamptz not null default clock_timestamp(),
 lease_token uuid,
 lease_until timestamptz,
 claimed_revision bigint,
 claimed_day date,
 claimed_model_key text,
 claimed_generation_id uuid,
 attempts integer not null default 0,
 error_code text,
 primary key(athlete_alias,kind,parameter_key),
 check(parameter_key=md5(parameters::text))
);
create index prepared_views_due on onflows_private.prepared_views(available_at,kind);
alter table onflows_private.prepared_view_sources enable row level security;
alter table onflows_private.prepared_views enable row level security;
revoke all on onflows_private.prepared_view_sources,onflows_private.prepared_views from public,anon,authenticated,service_role;

-- Input transactions append independent events. They never update/lock the
-- shared source epoch or queue rows used by other history publishers.
create table onflows_private.prepared_view_events (
 id bigint generated always as identity primary key,
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 models boolean not null,
 transaction_id bigint not null default txid_current(),
 unique(athlete_alias,transaction_id,models)
);
create index prepared_view_events_athlete on onflows_private.prepared_view_events(athlete_alias,id);
alter table onflows_private.prepared_view_events enable row level security;
revoke all on onflows_private.prepared_view_events from public,anon,authenticated,service_role;
revoke all on sequence onflows_private.prepared_view_events_id_seq from public,anon,authenticated,service_role;

create function onflows_private.prepared_view_revision(p_kind text,p_model bigint,p_context bigint)
returns bigint language sql immutable security invoker set search_path='' as $$
 select case when p_kind in ('speed','speed-history','speed-load','speed-preview','trainability') then p_model else p_context end
$$;
revoke all on function onflows_private.prepared_view_revision(text,bigint,bigint) from public,anon,authenticated,service_role;

create function onflows_private.seed_prepared_views(p_alias text)
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
 from (select distinct sport from public.onflows_activity_catalog
  where athlete_alias=p_alias and local_date>=current_date-90
   and sport in ('Run','TrailRun','VirtualRun','Ride','VirtualRide','MountainBikeRide','NordicSki','RollerSki')
  order by sport limit 8) s
 cross join (values('speed'),('speed-history'),('speed-load')) k(kind)
 cross join lateral (select jsonb_build_object('sport',s.sport) as parameters) p
 on conflict do nothing;
end $$;
revoke all on function onflows_private.seed_prepared_views(text) from public,anon,authenticated,service_role;

create function onflows_private.request_prepared_views(p_alias text,p_models boolean default true)
returns void language plpgsql security definer set search_path='' as $$
declare r bigint;
begin
 if p_alias is null or not exists(select 1 from public.onflows_athlete_settings where athlete_alias=p_alias) then return; end if;
 if not exists(select 1 from onflows_private.prepared_view_sources where athlete_alias=p_alias) then
  r:=nextval('onflows_private.prepared_view_revision_seq');
  insert into onflows_private.prepared_view_sources values(p_alias,r,r) on conflict do nothing;
  perform onflows_private.seed_prepared_views(p_alias);
 end if;
 insert into onflows_private.prepared_view_events(athlete_alias,models) values(p_alias,p_models)
 on conflict(athlete_alias,transaction_id,models) do nothing;
exception when foreign_key_violation then
 if exists(select 1 from public.onflows_athlete_settings where athlete_alias=p_alias) then raise; end if;
end $$;
revoke all on function onflows_private.request_prepared_views(text,boolean) from public,anon,authenticated,service_role;

create function onflows_private.flush_prepared_view_events(p_alias text default null)
returns void language plpgsql security definer set search_path='' as $$
declare pending record; r bigint;
begin
 for pending in select athlete_alias,array_agg(id order by id) as ids,bool_or(models) as models
  from onflows_private.prepared_view_events where p_alias is null or athlete_alias=p_alias
  group by athlete_alias order by athlete_alias limit 32 loop
  r:=nextval('onflows_private.prepared_view_revision_seq');
  insert into onflows_private.prepared_view_sources values(pending.athlete_alias,r,r)
  on conflict(athlete_alias) do update set context_revision=r,
   model_revision=case when pending.models then r else onflows_private.prepared_view_sources.model_revision end;
  perform onflows_private.seed_prepared_views(pending.athlete_alias);
  -- Identity allocation is not commit order. Consume only captured IDs.
  delete from onflows_private.prepared_view_events where athlete_alias=pending.athlete_alias and id=any(pending.ids);
 end loop;
end $$;
revoke all on function onflows_private.flush_prepared_view_events(text) from public,anon,authenticated,service_role;

create function onflows_private.invalidate_prepared_views() returns trigger
language plpgsql security definer set search_path='' as $$
declare a text; models boolean:=true;
begin
 a:=case when tg_op='DELETE' then old.athlete_alias else new.athlete_alias end;
 if tg_table_name in ('onflows_response_entries','onflows_management_plan_revisions') then models:=false;
 elsif tg_table_name='onflows_management_entries' then
  models:=case when tg_op='DELETE' then old.kind='PROFILE' else new.kind='PROFILE' end;
 end if;
 perform onflows_private.request_prepared_views(a,models);
 if tg_op='DELETE' then return old; else return new; end if;
end $$;
revoke all on function onflows_private.invalidate_prepared_views() from public,anon,authenticated,service_role;

create trigger page_views_settings after insert or update or delete on public.onflows_athlete_settings
 for each row execute function onflows_private.invalidate_prepared_views();
create trigger page_views_models after insert or update or delete on public.onflows_model_entries
 for each row execute function onflows_private.invalidate_prepared_views();
create trigger page_views_responses after insert or update or delete on public.onflows_response_entries
 for each row execute function onflows_private.invalidate_prepared_views();
create trigger page_views_profiles after insert or update or delete on public.onflows_management_entries
 for each row execute function onflows_private.invalidate_prepared_views();
create trigger page_views_plan after insert or update or delete on public.onflows_management_plan_revisions
 for each row execute function onflows_private.invalidate_prepared_views();
create trigger page_views_analysis after insert or update or delete on public.onflows_athlete_analysis_state
 for each row execute function onflows_private.invalidate_prepared_views();
create trigger page_views_legacy after insert or update or delete on public.onflows_training_snapshots
 for each row execute function onflows_private.invalidate_prepared_views();

create function onflows_private.prepared_view_parameters(p_kind text,p_parameters jsonb,p_day date)
returns jsonb language plpgsql immutable security invoker set search_path='' as $$
declare v jsonb:=p_parameters;
begin
 if p_kind='trainability' and v->>'period_end'=p_day::text and v->>'period_start'=(p_day-89)::text then
  v:=v-'period_start'-'period_end';
 elsif p_kind='response' and v->>'period_end'=p_day::text and v->>'period_start'=(p_day-29)::text then
  v:=v-'period_start'-'period_end';
 elsif p_kind='speed-load' then
  if v->>'as_of'=p_day::text then v:=v-'as_of'; end if;
  if v->>'period_end'=p_day::text and v->>'period_start'=(p_day-39)::text then v:=v-'period_start'-'period_end'; end if;
 end if;
 return v;
end $$;
revoke all on function onflows_private.prepared_view_parameters(text,jsonb,date) from public,anon,authenticated,service_role;

create function onflows_private.prepared_view(
 p_operation text,p_alias text default null,p_kind text default null,p_parameters jsonb default '{}',
 p_token uuid default null,p_revision bigint default null,p_day date default null,
 p_model_key text default null,p_payload jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.prepared_views%rowtype; src onflows_private.prepared_view_sources%rowtype;
 today_local date; zone text; params jsonb; k text; r bigint; generation uuid; read_state record;
begin
 if p_operation is null or p_operation not in ('READ','CLAIM','RENEW','STORE','FAIL') then
  raise exception 'Invalid prepared view operation' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') and (p_model_key is null or p_model_key!~'^[a-f0-9]{64}$') then
  raise exception 'Invalid prepared view model' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') then
  perform onflows_private.flush_prepared_view_events(p_alias);
 end if;
 if p_operation='CLAIM' then
  if p_token is null then raise exception 'Worker token required' using errcode='22023'; end if;
  select v.* into j from onflows_private.prepared_views v
   join onflows_private.prepared_view_sources s using(athlete_alias)
   join public.onflows_athlete_settings a using(athlete_alias)
   join public.onflows_athlete_analysis_state active using(athlete_alias)
   where active.active_generation_id is not null and (p_alias is null or v.athlete_alias=p_alias)
    and (v.lease_until is null or v.lease_until<=clock_timestamp())
    and (v.payload is null or v.prepared_revision<>onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
     or v.evaluated_on is distinct from (clock_timestamp() at time zone a.timezone)::date or v.model_key is distinct from p_model_key)
    and (v.available_at<=clock_timestamp()
     or v.claimed_revision is distinct from onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
     or v.claimed_day is distinct from (clock_timestamp() at time zone a.timezone)::date or v.claimed_model_key is distinct from p_model_key)
    and (v.attempts<3 or v.claimed_revision is distinct from onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
     or v.claimed_day is distinct from (clock_timestamp() at time zone a.timezone)::date or v.claimed_model_key is distinct from p_model_key)
   order by case v.kind when 'management-state' then 0 when 'management-active' then 0
    when 'management-view' then 1 when 'management-profile' then 1 when 'speed-load' then 4
    when 'speed-preview' then 3 else 2 end,v.is_default desc,v.requested_at
   limit 1 for update of v skip locked;
  if not found then return null; end if;
  select * into src from onflows_private.prepared_view_sources where athlete_alias=j.athlete_alias;
  select timezone into zone from public.onflows_athlete_settings where athlete_alias=j.athlete_alias;
  select active_generation_id into generation from public.onflows_athlete_analysis_state where athlete_alias=j.athlete_alias;
  today_local:=(clock_timestamp() at time zone zone)::date;
  r:=onflows_private.prepared_view_revision(j.kind,src.model_revision,src.context_revision);
  update onflows_private.prepared_views set lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',
   attempts=case when claimed_revision=r and claimed_day=today_local and claimed_model_key=p_model_key then attempts+1 else 1 end,
   claimed_revision=r,claimed_day=today_local,claimed_model_key=p_model_key,claimed_generation_id=generation,error_code=null
   where athlete_alias=j.athlete_alias and kind=j.kind and parameter_key=j.parameter_key;
  return jsonb_build_object('athlete_alias',j.athlete_alias,'kind',j.kind,'parameters',j.parameters,'revision',r,'day',today_local);
 end if;
 if p_alias is null or p_kind is null or jsonb_typeof(p_parameters) is distinct from 'object'
  or octet_length(p_parameters::text)>16384 then raise exception 'Invalid prepared view scope' using errcode='22023'; end if;
 select timezone into zone from public.onflows_athlete_settings where athlete_alias=p_alias;
 if not found then return null; end if;
 today_local:=(clock_timestamp() at time zone zone)::date;
 params:=onflows_private.prepared_view_parameters(p_kind,p_parameters,today_local);
 k:=md5(params::text);
 if p_operation='READ' then
  if not exists(select 1 from public.onflows_athlete_analysis_state where athlete_alias=p_alias and active_generation_id is not null) then
   return jsonb_build_object('ready',false); end if;
  if not exists(select 1 from onflows_private.prepared_view_sources where athlete_alias=p_alias) then
   perform onflows_private.request_prepared_views(p_alias); end if;
  if not exists(select 1 from onflows_private.prepared_views where athlete_alias=p_alias and kind=p_kind and parameter_key=k) then
   -- Only reproducible derived variants are evicted, never athlete evidence.
   delete from onflows_private.prepared_views where (athlete_alias,kind,parameter_key) in (
    select athlete_alias,kind,parameter_key from onflows_private.prepared_views where athlete_alias=p_alias
     and not is_default and (lease_until is null or lease_until<=clock_timestamp())
     and prepared_at<clock_timestamp()-interval '7 days' order by prepared_at limit 16);
   if (select count(*) from onflows_private.prepared_views where athlete_alias=p_alias)>=128 then
    return jsonb_build_object('ready',false,'limited',true); end if;
   insert into onflows_private.prepared_views(athlete_alias,kind,parameter_key,parameters)
    values(p_alias,p_kind,k,params) on conflict do nothing;
  end if;
  -- Observe payload, source epoch and absence of pending inputs in one snapshot.
  select v.payload,v.prepared_at,(v.payload is not null and v.prepared_revision=
   onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
   and v.evaluated_on=today_local and v.model_key=p_model_key
   and not exists(select 1 from onflows_private.prepared_view_events e where e.athlete_alias=p_alias
    and (e.models or onflows_private.prepared_view_revision(p_kind,0,1)=1))) as ready
   into read_state from onflows_private.prepared_views v join onflows_private.prepared_view_sources s using(athlete_alias)
   where v.athlete_alias=p_alias and v.kind=p_kind and v.parameter_key=k and v.parameters=params;
  if read_state.ready then
   return jsonb_build_object('ready',true,'athlete_alias',p_alias,'kind',p_kind,'payload',read_state.payload,'prepared_at',read_state.prepared_at);
  end if;
  return jsonb_build_object('ready',false,'day',today_local);
 end if;
 -- Lock the small source epoch before the queue row. Input writers append
 -- events independently; no plan/input locks are needed for publication.
 select * into src from onflows_private.prepared_view_sources where athlete_alias=p_alias for share;
 select * into j from onflows_private.prepared_views where athlete_alias=p_alias and kind=p_kind and parameter_key=k
  and parameters=params for update;
 if not found or p_token is null or j.lease_token is distinct from p_token or j.lease_until<=clock_timestamp() or j.lease_until is null then
  return jsonb_build_object('state','LOST'); end if;
 if p_operation='RENEW' then
  update onflows_private.prepared_views set lease_until=clock_timestamp()+interval '90 seconds'
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('state','RUNNING');
 elsif p_operation='FAIL' then
  update onflows_private.prepared_views set lease_token=null,lease_until=null,
   available_at=clock_timestamp()+interval '30 seconds',error_code='PREPARATION_DEFERRED'
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('state','DEFERRED');
 end if;
 r:=onflows_private.prepared_view_revision(p_kind,src.model_revision,src.context_revision);
 select active_generation_id into generation from public.onflows_athlete_analysis_state where athlete_alias=p_alias;
 if p_revision is distinct from r or p_revision is distinct from j.claimed_revision or p_day is distinct from today_local
  or p_day is distinct from j.claimed_day or p_model_key is distinct from j.claimed_model_key
  or generation is distinct from j.claimed_generation_id or jsonb_typeof(p_payload) is distinct from 'object'
  or octet_length(p_payload::text)>8388608 then
  update onflows_private.prepared_views set lease_token=null,lease_until=null,available_at=clock_timestamp()
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('stored',false);
 end if;
 update onflows_private.prepared_views set prepared_revision=r,evaluated_on=p_day,model_key=p_model_key,payload=p_payload,
  prepared_at=clock_timestamp(),available_at=((today_local+1)::timestamp at time zone zone),
  lease_token=null,lease_until=null,error_code=null
  where athlete_alias=p_alias and kind=p_kind and parameter_key=k
   and not exists(select 1 from onflows_private.prepared_view_events e where e.athlete_alias=p_alias
    and (e.models or onflows_private.prepared_view_revision(p_kind,0,1)=1));
 if not found then
  update onflows_private.prepared_views set lease_token=null,lease_until=null,available_at=clock_timestamp()
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('stored',false);
 end if;
 return jsonb_build_object('stored',true);
end $$;
revoke all on function onflows_private.prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb) to service_role;
create function public.onflows_prepared_view(
 p_operation text,p_alias text default null,p_kind text default null,p_parameters jsonb default '{}',
 p_token uuid default null,p_revision bigint default null,p_day date default null,
 p_model_key text default null,p_payload jsonb default null
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.prepared_view(p_operation,p_alias,p_kind,p_parameters,p_token,p_revision,p_day,p_model_key,p_payload)
$$;
revoke all on function public.onflows_prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb) to service_role;

do $$ declare a text; begin
 for a in select athlete_alias from public.onflows_athlete_settings loop
  perform onflows_private.request_prepared_views(a);
 end loop;
end $$;

-- Calendar extension is independent of imports. New training still increments
-- the existing coalesced revision; a local day adds at most one pending update.
alter table onflows_private.training_plan_updates add column last_evaluated_on date,
 add column claimed_day date,
add column requested_actor uuid,
add column requested_plan_revision integer,
add column requested_manual boolean not null default false,
add column requested_record_check boolean not null default false,
add column claimed_actor uuid,
add column claimed_plan_revision integer,
add column claimed_manual boolean not null default false,
add column claimed_record_check boolean not null default false;
update onflows_private.training_plan_updates t set last_evaluated_on=
 (coalesce(completed_at,requested_at) at time zone s.timezone)::date
 from public.onflows_athlete_settings s where s.athlete_alias=t.athlete_alias;

alter function onflows_private.training_plan_update(text,text,uuid) rename to training_plan_update_core_v1;
revoke all on function onflows_private.training_plan_update_core_v1(text,text,uuid) from public,anon,authenticated,service_role;
create or replace function onflows_private.training_plan_update_core_v1(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.training_plan_updates%rowtype; actor uuid;
begin
 if p_operation is null or p_operation not in ('CLAIM','RENEW','COMPLETE','FAIL','REJECT') or p_token is null then
  raise exception 'Invalid training update operation' using errcode='22023'; end if;
 if p_operation='CLAIM' then
  select t.* into j from onflows_private.training_plan_updates t
   where (p_alias is null or athlete_alias=p_alias) and requested_revision>completed_revision
    and attempts<3 and available_at<=clock_timestamp() and (lease_until is null or lease_until<=clock_timestamp())
    and not exists(select 1 from onflows_private.planning_jobs p where p.athlete_alias=t.athlete_alias and p.status in ('QUEUED','RUNNING'))
   order by available_at limit 1 for update skip locked;
  if not found then return null; end if;
  select coalesce(j.requested_actor,approved_by) into actor from public.onflows_management_plan_state where athlete_alias=j.athlete_alias;
  if actor is null then
   select actor_id into actor from public.onflows_management_entries where athlete_alias=j.athlete_alias
    and kind='PROFILE' and entry_key='management-profile-v1' order by revision desc limit 1;
  end if;
  if actor is null or not onflows_private.can_prepare_plan(j.athlete_alias,actor) then
   update onflows_private.training_plan_updates set completed_revision=requested_revision,completed_at=clock_timestamp(),
    error_code='ACCESS_OR_PROFILE_REQUIRED',lease_token=null,lease_until=null where athlete_alias=j.athlete_alias;
   return null;
  end if;
  update onflows_private.training_plan_updates set claimed_revision=requested_revision,attempts=attempts+1,
   lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',error_code=null
   where athlete_alias=j.athlete_alias returning * into j;
  return jsonb_build_object('athlete_alias',j.athlete_alias,'actor_id',actor,'revision',j.claimed_revision);
 end if;
 select * into j from onflows_private.training_plan_updates where athlete_alias=p_alias for update;
 if not found or j.lease_token is distinct from p_token or j.lease_until is null or j.lease_until<=clock_timestamp() then
  return jsonb_build_object('state','LOST'); end if;
 if p_operation='REJECT' then
  update onflows_private.training_plan_updates set completed_revision=j.claimed_revision,completed_at=clock_timestamp(),
   lease_token=null,lease_until=null,error_code='INPUT_CHANGED',
   attempts=case when requested_revision>j.claimed_revision then 0 else attempts end where athlete_alias=p_alias;
  return jsonb_build_object('state','REJECTED');
 elsif p_operation='RENEW' then
  update onflows_private.training_plan_updates set lease_until=clock_timestamp()+interval '90 seconds' where athlete_alias=p_alias;
  return jsonb_build_object('state','RUNNING');
 elsif p_operation='COMPLETE' then
  update onflows_private.training_plan_updates set completed_revision=j.claimed_revision,completed_at=clock_timestamp(),
   lease_token=null,lease_until=null,attempts=case when requested_revision>j.claimed_revision then 0 else attempts end
   where athlete_alias=p_alias;
  return jsonb_build_object('state','READY');
 else
  update onflows_private.training_plan_updates set lease_token=null,lease_until=null,
   available_at=clock_timestamp()+interval '30 seconds',error_code='PREPARATION_DEFERRED' where athlete_alias=p_alias;
  return jsonb_build_object('state','DEFERRED');
 end if;
end $$;

create function onflows_private.training_plan_update(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare a text; d date; result jsonb;
begin
 if p_operation='CLAIM' then
  -- Only the approved adaptive mode extends at midnight. Fixed, paused and
  -- completed plans keep their previous lifecycle and explicit approval.
  for a,d in select s.athlete_alias,(clock_timestamp() at time zone z.timezone)::date
   from public.onflows_management_plan_state s
   join public.onflows_management_plan_revisions r using(athlete_alias,revision)
   join public.onflows_athlete_settings z using(athlete_alias)
   join lateral (select payload from public.onflows_management_entries e where e.athlete_alias=s.athlete_alias
    and e.kind='PROFILE' and e.entry_key='management-profile-v1' order by revision desc limit 1) p on true
   left join onflows_private.training_plan_updates t using(athlete_alias)
   where s.status in ('ACTIVE','REVIEW_REQUIRED') and r.payload->>'mode'='AUTO' and p.payload->>'adaptation_mode'='AUTO'
    and (p_alias is null or s.athlete_alias=p_alias)
    and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
    and coalesce(t.last_evaluated_on,(r.payload->>'evaluated_on')::date,'-infinity'::date)<(clock_timestamp() at time zone z.timezone)::date
    and (t.athlete_alias is null or t.requested_revision=t.completed_revision)
   limit 10 loop
   insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
    select a,active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=a
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
revoke all on function onflows_private.training_plan_update(text,text,uuid) from public,anon,authenticated;
grant execute on function onflows_private.training_plan_update(text,text,uuid) to service_role;

-- Prepare the newly extended horizon once for existing adaptive programs,
-- including athletes already evaluated earlier today. This does not approve
-- proposals: the unchanged lifecycle still enforces every approval boundary.
insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
 select s.athlete_alias,a.active_generation_id from public.onflows_management_plan_state s
 join public.onflows_management_plan_revisions r using(athlete_alias,revision)
 join public.onflows_athlete_analysis_state a using(athlete_alias)
 join lateral (select payload from public.onflows_management_entries e
  where e.athlete_alias=s.athlete_alias and e.kind='PROFILE' and e.entry_key='management-profile-v1'
  order by revision desc limit 1) p on true
 where s.status in ('ACTIVE','REVIEW_REQUIRED') and r.payload->>'mode'='AUTO'
  and p.payload->>'adaptation_mode'='AUTO' and a.active_generation_id is not null
  and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
 on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
  requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;

-- SQL function bodies bind function OIDs. Rebind the public wrapper to the new
-- calendar-aware implementation after renaming the original implementation.
create or replace function public.onflows_training_plan_update(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.training_plan_update(p_operation,p_alias,p_token)
$$;

create or replace function onflows_private.save_plan_after_training(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean,
 p_training_token uuid,p_training_revision bigint
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.training_plan_updates%rowtype; old_payload jsonb;
begin
 -- Match management's lock before source and queue locks. An activation takes
 -- the source row first; never take its event row before that source lock.
 perform pg_advisory_xact_lock(hashtextextended('management:'||p_alias,0));
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
 select r.payload into old_payload from public.onflows_management_plan_state s
  join public.onflows_management_plan_revisions r using(athlete_alias,revision) where s.athlete_alias=p_alias;
 if old_payload->>'mode'='REVIEW' and onflows_private.fixed_plan_identity(p_payload->'plan')
  is distinct from onflows_private.fixed_plan_identity(old_payload->'plan') then
  raise exception 'Fixed program requires approval' using errcode='42501'; end if;
 return public.save_onflows_management_plan_compact(p_alias,p_payload,p_expected_revision,p_actor,p_operation,
  p_expected_profile_revision,p_checkpoint,p_automatic);
end $$;

create function onflows_private.queue_plan_refresh(p_alias text,p_actor uuid,p_expected_revision integer,p_record_unchanged_check boolean default false)
returns jsonb language plpgsql security definer set search_path='' as $$
declare current_revision integer;
begin
 if p_actor is null or not onflows_private.can_prepare_plan(p_alias,p_actor) then
  raise exception 'Planning access required' using errcode='42501'; end if;
 perform pg_advisory_xact_lock(hashtextextended('management:'||p_alias,0));
 select revision into current_revision from public.onflows_management_plan_state where athlete_alias=p_alias;
 if current_revision is null or p_expected_revision is distinct from current_revision then
  return jsonb_build_object('conflict',true); end if;
 insert into onflows_private.training_plan_updates(athlete_alias,generation_id,requested_actor,
  requested_plan_revision,requested_manual,requested_record_check)
  select p_alias,active_generation_id,p_actor,p_expected_revision,true,p_record_unchanged_check
   from public.onflows_athlete_analysis_state where athlete_alias=p_alias
 on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
  generation_id=excluded.generation_id,requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null,
  requested_actor=p_actor,requested_plan_revision=p_expected_revision,requested_manual=true,requested_record_check=p_record_unchanged_check;
 if not found then return jsonb_build_object('conflict',true); end if;
 perform onflows_private.request_prepared_views(p_alias,false);
 return jsonb_build_object('queued',true);
end $$;
revoke all on function onflows_private.queue_plan_refresh(text,uuid,integer,boolean) from public,anon,authenticated;
grant execute on function onflows_private.queue_plan_refresh(text,uuid,integer,boolean) to service_role;
create function public.queue_onflows_plan_refresh(p_alias text,p_actor uuid,p_expected_revision integer,p_record_unchanged_check boolean default false)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.queue_plan_refresh(p_alias,p_actor,p_expected_revision,p_record_unchanged_check)
$$;
revoke all on function public.queue_onflows_plan_refresh(text,uuid,integer,boolean) from public,anon,authenticated;
grant execute on function public.queue_onflows_plan_refresh(text,uuid,integer,boolean) to service_role;

create function onflows_private.plan_preparation(p_alias text) returns jsonb
language sql security definer set search_path='' as $$
 select jsonb_build_object('status',case when requested_revision<=completed_revision then
   case when error_code in ('INPUT_CHANGED','ACCESS_OR_PROFILE_REQUIRED') then 'FAILED' else 'READY' end
  when attempts>=3 and lease_token is null then 'FAILED'
  when lease_until>clock_timestamp() then 'RUNNING' else 'QUEUED' end,'error_code',error_code)
 from onflows_private.training_plan_updates where athlete_alias=p_alias
$$;
revoke all on function onflows_private.plan_preparation(text) from public,anon,authenticated;
grant execute on function onflows_private.plan_preparation(text) to service_role;
create function public.read_onflows_plan_preparation(p_alias text) returns jsonb
language sql security invoker set search_path='' as $$ select onflows_private.plan_preparation(p_alias) $$;
revoke all on function public.read_onflows_plan_preparation(text) from public,anon,authenticated;
grant execute on function public.read_onflows_plan_preparation(text) to service_role;

create table onflows_private.activity_derivations (
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
 kind text not null check(kind in ('SPEED_Q','UNIFIED_Q')),
 input_key text not null check(input_key~'^[a-f0-9]{64}$'),
 payload jsonb not null check(jsonb_typeof(payload)='object' and octet_length(payload::text)<=131072),
 expires_at timestamptz not null,
 primary key(athlete_alias,kind,input_key)
);
create index activity_derivations_expiry on onflows_private.activity_derivations(expires_at);
alter table onflows_private.activity_derivations enable row level security;
revoke all on onflows_private.activity_derivations from public,anon,authenticated,service_role;

create function onflows_private.activity_derivations(p_alias text,p_kind text,p_keys text[] default null,p_values jsonb default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare result jsonb;
begin
 if p_alias is null or p_kind is null or p_kind not in ('SPEED_Q','UNIFIED_Q')
  or coalesce(cardinality(p_keys),0)>2000 or octet_length(coalesce(p_values,'{}')::text)>8388608 then
  raise exception 'Invalid activity derivation scope' using errcode='22023'; end if;
 if p_values is null then
  select coalesce(jsonb_object_agg(input_key,payload),'{}'::jsonb) into result
   from onflows_private.activity_derivations where athlete_alias=p_alias and kind=p_kind
    and input_key=any(p_keys) and expires_at>clock_timestamp();
  return result;
 end if;
 if jsonb_typeof(p_values)<>'object' or (select count(*) from jsonb_object_keys(p_values))>2000 then
  raise exception 'Invalid activity derivation results' using errcode='22023'; end if;
 insert into onflows_private.activity_derivations(athlete_alias,kind,input_key,payload,expires_at)
  select p_alias,p_kind,key,value,clock_timestamp()+interval '90 days' from jsonb_each(p_values)
  where value->>'reason' is distinct from 'SPEED_RECOMPUTATION_REQUIRED'
 on conflict(athlete_alias,kind,input_key) do update set expires_at=excluded.expires_at;
 delete from onflows_private.activity_derivations where (athlete_alias,kind,input_key) in
  (select athlete_alias,kind,input_key from onflows_private.activity_derivations
   where expires_at<=clock_timestamp() order by expires_at limit 100);
 return jsonb_build_object('stored',true);
end $$;
revoke all on function onflows_private.activity_derivations(text,text,text[],jsonb) from public,anon,authenticated;
grant execute on function onflows_private.activity_derivations(text,text,text[],jsonb) to service_role;
create function public.onflows_activity_derivations(p_alias text,p_kind text,p_keys text[] default null,p_values jsonb default null)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.activity_derivations(p_alias,p_kind,p_keys,p_values)
$$;
revoke all on function public.onflows_activity_derivations(text,text,text[],jsonb) from public,anon,authenticated;
grant execute on function public.onflows_activity_derivations(text,text,text[],jsonb) to service_role;

notify pgrst,'reload schema';
