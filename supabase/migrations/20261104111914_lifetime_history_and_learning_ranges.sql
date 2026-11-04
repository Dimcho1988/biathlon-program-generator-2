-- Lifetime scientific facts are independent of the rolling operational cache.
-- Large original channels continue through the existing verified private archive.
create table onflows_private.history_activity_sources (
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 activity_ref text not null, start_at_utc timestamptz, local_date date,
 source_revision bigint not null, source_generation_id uuid not null,
 primary key(athlete_alias,activity_ref)
);
create index history_activity_sources_age on onflows_private.history_activity_sources(start_at_utc);
create table onflows_private.history_day_inputs (
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 day date not null, source_revision bigint not null, source_generation_id uuid not null,
 payload jsonb not null check(jsonb_typeof(payload)='object' and octet_length(payload::text)<=262144),
 updated_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,day)
);
create table onflows_private.learning_timeline_days (
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 day date not null, model_key text not null, model_version text not null,
 source_revision bigint not null, payload jsonb not null,
 prepared_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,day),
 check(jsonb_typeof(payload)='object' and payload->>'day'=day::text and octet_length(payload::text)<=16384)
);
alter table onflows_private.history_activity_sources enable row level security;
alter table onflows_private.history_day_inputs enable row level security;
alter table onflows_private.learning_timeline_days enable row level security;
revoke all on onflows_private.history_activity_sources,onflows_private.history_day_inputs,onflows_private.learning_timeline_days from public,anon,authenticated,service_role;

create function onflows_private.capture_lifetime_generation(p_generation uuid,p_first date default null,p_last date default null)
returns integer language plpgsql security definer set search_path='' as $$
declare g public.onflows_analysis_generations%rowtype; source jsonb; first_day date;last_day date;d date;
 activity_set uuid; doc jsonb; refs jsonb; updated integer:=0; n integer;
begin
 select * into g from public.onflows_analysis_generations where generation_id=p_generation for key share;
 if not found or g.activated_revision is null or g.snapshot_payload is null then return 0;end if;
 source:=g.snapshot_payload->'load_history';
 if jsonb_typeof(source) is distinct from 'object' or g.period_start is null or g.period_end is null then return 0;end if;
 first_day:=greatest(g.period_start,coalesce(p_first,g.period_start));
 last_day:=least(g.period_end,coalesce(p_last,g.period_end));
 if last_day<first_day then return 0;end if;
 if last_day-first_day>366 then raise exception 'Lifetime capture is bounded';end if;
 activity_set:=coalesce(g.activity_set_generation_id,g.generation_id);
 -- Reuse the existing native pin closure. Unused technical generations remain
 -- eligible for cleanup; actual published inputs/results remain reproducible.
 select jsonb_build_object(
  'run_keys',coalesce((select jsonb_agg(distinct k) from public.onflows_analysis_generation_activities a
    cross join lateral unnest(array[a.shadow_run_key,a.canonical_run_key]) k
    where a.generation_id=activity_set and a.athlete_alias=g.athlete_alias and a.local_date between first_day and last_day and k is not null),'[]'::jsonb),
  'input_keys',coalesce((select jsonb_agg(distinct a.input_key) from public.onflows_analysis_generation_activities a
    where a.generation_id=activity_set and a.athlete_alias=g.athlete_alias and a.local_date between first_day and last_day and a.input_key is not null),'[]'::jsonb),
  'activity_refs',coalesce((select jsonb_agg(distinct a.activity_ref) from public.onflows_analysis_generation_activities a
    where a.generation_id=activity_set and a.athlete_alias=g.athlete_alias and a.local_date between first_day and last_day),'[]'::jsonb)) into refs;
 perform onflows_private.pin_history_activity_references(g.athlete_alias,refs,false);
 insert into onflows_private.history_activity_sources(athlete_alias,activity_ref,start_at_utc,local_date,source_revision,source_generation_id)
 select a.athlete_alias,a.activity_ref,a.start_at_utc,a.local_date,g.activated_revision,g.generation_id
 from public.onflows_analysis_generation_activities a
 where a.generation_id=activity_set and a.athlete_alias=g.athlete_alias and a.local_date between first_day and last_day
 on conflict(athlete_alias,activity_ref) do update set start_at_utc=excluded.start_at_utc,local_date=excluded.local_date,
 source_revision=excluded.source_revision,source_generation_id=excluded.source_generation_id
 where excluded.source_revision>history_activity_sources.source_revision and (excluded.start_at_utc is distinct from history_activity_sources.start_at_utc or excluded.local_date is distinct from history_activity_sources.local_date);
 for d in select generate_series(first_day,last_day,interval '1 day')::date loop
  doc:=jsonb_build_object('schema_version','history-day-input-v1',
   'equivalence_version',source->'equivalence_version','load_selection',source->'load_selection',
   'daily',coalesce((select jsonb_agg(x) from jsonb_array_elements(coalesce(source->'daily','[]')) x where x->>'date'=d::text),'[]'::jsonb),
   'strength',(select x from jsonb_array_elements(coalesce(source#>'{strength,daily}','[]')) x where x->>'date'=d::text limit 1),
   'activities',coalesce((select jsonb_agg(x) from jsonb_array_elements(coalesce(source->'activities','[]')) x where coalesce(x->>'date',x->>'local_date')=d::text),'[]'::jsonb),
   'catalog',coalesce((select jsonb_agg(a.catalog_payload||jsonb_build_object('activity_ref',a.activity_ref,'local_date',a.local_date,'start_at_utc',a.start_at_utc))
     from public.onflows_analysis_generation_activities a where a.generation_id=activity_set and a.athlete_alias=g.athlete_alias and a.local_date=d),'[]'::jsonb),
   'wellness',(select x from jsonb_array_elements(coalesce(g.snapshot_payload->'wellness_calendar','[]')) x where x->>'date'=d::text limit 1),
   'exclusions',coalesce((select jsonb_agg(x) from jsonb_array_elements(coalesce(source#>'{quality,exclusions}','[]')) x
     where coalesce(x->>'date',x->>'local_date',d::text)=d::text),'[]'::jsonb));
  insert into onflows_private.history_day_inputs(athlete_alias,day,source_revision,source_generation_id,payload)
  values(g.athlete_alias,d,g.activated_revision,g.generation_id,doc)
  on conflict(athlete_alias,day) do update set source_revision=excluded.source_revision,source_generation_id=excluded.source_generation_id,
   payload=excluded.payload,updated_at=clock_timestamp()
  where excluded.source_revision>history_day_inputs.source_revision and excluded.payload is distinct from history_day_inputs.payload;
  get diagnostics n=row_count;updated:=updated+n;
 end loop;
 return updated;
end $$;
revoke all on function onflows_private.capture_lifetime_generation(uuid,date,date) from public,anon,authenticated,service_role;

create function onflows_private.capture_lifetime_activation() returns trigger language plpgsql security definer set search_path='' as $$
begin
 if new.status='ACTIVE' and new.activated_revision is not null and (old.status is distinct from new.status or old.activated_revision is distinct from new.activated_revision) then
  perform onflows_private.capture_lifetime_generation(new.generation_id);
 end if;
 return new;
end $$;
revoke all on function onflows_private.capture_lifetime_activation() from public,anon,authenticated,service_role;
create trigger capture_lifetime_activation after update of status,activated_revision on public.onflows_analysis_generations
 for each row execute function onflows_private.capture_lifetime_activation();

create function public.backfill_onflows_lifetime_history(p_alias text,p_limit integer default 31)
returns jsonb language plpgsql security definer set search_path='' set statement_timeout='45s' as $$
declare candidate record; n integer:=0; first_day date; last_day date;
begin
 if p_alias is null or p_alias='' or p_limit not between 1 and 31 then raise exception 'Invalid history backfill';end if;
 select min(g.period_start),max(g.period_end) into first_day,last_day from public.onflows_analysis_generations g
  where g.athlete_alias=p_alias and g.activated_revision is not null and g.snapshot_payload is not null;
 if first_day is null then return jsonb_build_object('captured',0,'remaining',false);end if;
 for candidate in select d::date as day,latest.generation_id
  from generate_series(first_day,last_day,interval '1 day') d
  cross join lateral(select g.generation_id from public.onflows_analysis_generations g
   where g.athlete_alias=p_alias and g.activated_revision is not null and g.period_start<=d::date and g.period_end>=d::date
    and g.snapshot_payload is not null order by g.activated_revision desc limit 1) latest
  where not exists(select 1 from onflows_private.history_day_inputs h where h.athlete_alias=p_alias and h.day=d::date)
  order by d desc limit p_limit loop
  n:=n+onflows_private.capture_lifetime_generation(candidate.generation_id,candidate.day,candidate.day);
 end loop;
 return jsonb_build_object('captured',n,'remaining',exists(select 1 from generate_series(first_day,last_day,interval '1 day') d
   where not exists(select 1 from onflows_private.history_day_inputs h where h.athlete_alias=p_alias and h.day=d::date)
   and exists(select 1 from public.onflows_analysis_generations g where g.athlete_alias=p_alias and g.activated_revision is not null and g.period_start<=d::date and g.period_end>=d::date)));
end $$;
revoke all on function public.backfill_onflows_lifetime_history(text,integer) from public,anon,authenticated;
grant execute on function public.backfill_onflows_lifetime_history(text,integer) to service_role;

-- Keep activity dates after redundant technical generations have been pruned.
create or replace function onflows_private.history_activity_is_cold(p_alias text,p_ref text,p_days integer)
returns boolean language sql stable security invoker set search_path='' as $$
 select case when exists(select 1 from onflows_private.history_activity_sources h where h.athlete_alias=p_alias and h.activity_ref=p_ref
  and h.start_at_utc>=now()-make_interval(days=>p_days)) then false else (select max(x.start_at_utc)<now()-make_interval(days=>p_days) from (
 select c.start_at_utc from public.onflows_activity_catalog c where c.athlete_alias=p_alias and c.activity_ref=p_ref
 union all select a.start_at_utc from public.onflows_analysis_generation_activities a where a.athlete_alias=p_alias and a.activity_ref=p_ref
 union all select h.start_at_utc from onflows_private.history_activity_sources h where h.athlete_alias=p_alias and h.activity_ref=p_ref) x) end;
$$;

create function public.read_onflows_history_day_inputs(p_alias text,p_first date,p_last date)
returns jsonb language plpgsql stable security definer set search_path='' as $$
begin
 if p_alias is null or p_alias='' or p_first is null or p_last is null or p_last<p_first or p_last-p_first>449 then raise exception 'Invalid history range';end if;
 return coalesce((select jsonb_agg(jsonb_build_object('day',h.day,'source_revision',h.source_revision,'source_generation_id',h.source_generation_id,'payload',h.payload) order by h.day)
   from onflows_private.history_day_inputs h where h.athlete_alias=p_alias and h.day between p_first and p_last),'[]'::jsonb);
end $$;
revoke all on function public.read_onflows_history_day_inputs(text,date,date) from public,anon,authenticated;
grant execute on function public.read_onflows_history_day_inputs(text,date,date) to service_role;

create function onflows_private.capture_learning_timeline() returns trigger language plpgsql security definer set search_path='' as $$
declare point jsonb;
begin
 if new.payload#>>'{report,history_version}' is distinct from 'lifetime-timeline-v1' or new.prepared_revision<>new.requested_revision then return new;end if;
 for point in select value from jsonb_array_elements(new.payload#>'{report,series}') loop
  insert into onflows_private.learning_timeline_days(athlete_alias,day,model_key,model_version,source_revision,payload)
   values(new.athlete_alias,(point->>'day')::date,new.model_key,new.payload#>>'{report,history_version}',new.prepared_revision,point)
  on conflict(athlete_alias,day) do update set model_key=excluded.model_key,model_version=excluded.model_version,
   source_revision=excluded.source_revision,payload=excluded.payload,prepared_at=clock_timestamp()
  where excluded.source_revision>=learning_timeline_days.source_revision and (excluded.payload is distinct from learning_timeline_days.payload or excluded.model_key is distinct from learning_timeline_days.model_key);
 end loop;
 return new;
end $$;
revoke all on function onflows_private.capture_learning_timeline() from public,anon,authenticated,service_role;
create trigger capture_learning_timeline after insert or update of payload on onflows_private.prepared_learning_views
 for each row execute function onflows_private.capture_learning_timeline();

create function public.read_onflows_learning_history(p_alias text,p_days integer,p_end date,p_model_key text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare state jsonb; report jsonb; last_day date;first_day date;available_first date;available_last date;points jsonb;
begin
 if p_alias is null or p_alias='' or p_days not in(14,30,60,90,366) or p_model_key is null or p_model_key='' then raise exception 'Invalid learning period';end if;
 state:=onflows_private.learning_preparation('READ',p_alias,null,null,null,p_model_key,null);
 if coalesce((state->>'ready')::boolean,false) is not true then return state;end if;
 report:=state#>'{payload,report}';last_day:=coalesce(p_end,(report->>'as_of')::date);
 if last_day>(report->>'as_of')::date then return jsonb_build_object('invalid_period',true);end if;
 first_day:=last_day-(p_days-1);
 select min(day),max(day) into available_first,available_last from onflows_private.learning_timeline_days where athlete_alias=p_alias;
 select coalesce(jsonb_agg(h.payload order by h.day),'[]'::jsonb) into points
  from onflows_private.learning_timeline_days h where h.athlete_alias=p_alias and h.day between first_day and last_day;
 report:=jsonb_set(report,'{series}',points);
 report:=report||jsonb_build_object('history',jsonb_build_object('available_start',available_first,'available_end',available_last,
  'display_start',first_day,'display_end',last_day,'requested_days',p_days,'limited',available_first is null or available_first>first_day or available_last<last_day));
 return jsonb_set(state,'{payload,report}',report);
end $$;
revoke all on function public.read_onflows_learning_history(text,integer,date,text) from public,anon,authenticated;
grant execute on function public.read_onflows_learning_history(text,integer,date,text) to service_role;

-- A bounded idle pass also prepares older years restored/backfilled later.
-- One lease per athlete prevents duplicate work across rolling worker deploys.
create table onflows_private.history_window_jobs (
 athlete_alias text primary key references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 token uuid not null, first_day date not null,last_day date not null,
 source_fingerprint text not null,lease_until timestamptz not null
);
alter table onflows_private.history_window_jobs enable row level security;
revoke all on onflows_private.history_window_jobs from public,anon,authenticated,service_role;
create function onflows_private.history_window_fingerprint(p_alias text,p_first date,p_last date)
returns text language sql stable security invoker set search_path='' as $$
 select md5(coalesce((select jsonb_agg(jsonb_build_array(day,source_revision) order by day)::text
 from onflows_private.history_day_inputs where athlete_alias=p_alias and day between p_first-84 and p_last),'[]')||
 (select jsonb_build_array(count(*),max(recorded_at))::text from public.onflows_response_entries where athlete_alias=p_alias));
$$;
revoke all on function onflows_private.history_window_fingerprint(text,date,date) from public,anon,authenticated,service_role;
create function public.onflows_history_window(p_operation text,p_alias text default null,p_token uuid default null,
 p_model_key text default null,p_payload jsonb default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare candidate record;job onflows_private.history_window_jobs%rowtype;finish date;point jsonb; n integer:=0;
begin
 if p_operation='CLAIM' then
  if p_token is null or p_model_key is null then raise exception 'Invalid history claim';end if;
  select h.athlete_alias,min(h.day) as first_day,(clock_timestamp() at time zone s.timezone)::date as today
   into candidate from onflows_private.history_day_inputs h
   join public.onflows_athlete_settings s using(athlete_alias)
   join public.onflows_intervals_connections c using(athlete_alias)
   where c.status='CONNECTED' and (p_alias is null or h.athlete_alias=p_alias)
    and h.day<(clock_timestamp() at time zone s.timezone)::date-365
    and not exists(select 1 from onflows_private.learning_timeline_days t where t.athlete_alias=h.athlete_alias and t.day=h.day)
    and not exists(select 1 from onflows_private.history_window_jobs j where j.athlete_alias=h.athlete_alias and j.lease_until>clock_timestamp())
   group by h.athlete_alias,s.timezone order by min(h.day),h.athlete_alias limit 1;
  if not found then return null;end if;
  finish:=least(candidate.first_day+364,candidate.today-366);
  insert into onflows_private.history_window_jobs(athlete_alias,token,first_day,last_day,source_fingerprint,lease_until)
   values(candidate.athlete_alias,p_token,candidate.first_day,finish,
    onflows_private.history_window_fingerprint(candidate.athlete_alias,candidate.first_day,finish),clock_timestamp()+interval '5 minutes')
   on conflict(athlete_alias) do update set token=excluded.token,first_day=excluded.first_day,last_day=excluded.last_day,
    source_fingerprint=excluded.source_fingerprint,lease_until=excluded.lease_until
   where history_window_jobs.lease_until<=clock_timestamp();
  if not found then return null;end if;
  return jsonb_build_object('athlete_alias',candidate.athlete_alias,'first_day',candidate.first_day,'last_day',finish);
 elsif p_operation in('STORE','FAIL') then
  select * into job from onflows_private.history_window_jobs where athlete_alias=p_alias and token=p_token and lease_until>clock_timestamp() for update;
  if not found then return jsonb_build_object('stored',false);end if;
  if p_operation='FAIL' then delete from onflows_private.history_window_jobs where athlete_alias=p_alias;return jsonb_build_object('released',true);end if;
  if job.source_fingerprint is distinct from onflows_private.history_window_fingerprint(p_alias,job.first_day,job.last_day) then
   delete from onflows_private.history_window_jobs where athlete_alias=p_alias;return jsonb_build_object('stored',false);end if;
  if p_payload->>'history_version' is distinct from 'lifetime-timeline-v1' or jsonb_typeof(p_payload->'series') is distinct from 'array'
    or jsonb_array_length(p_payload->'series')>367 or octet_length(p_payload::text)>2097152 or p_model_key is null then raise exception 'Invalid historical projection';end if;
  for point in select value from jsonb_array_elements(p_payload->'series') loop
   if (point->>'day')::date between job.first_day and job.last_day then
    insert into onflows_private.learning_timeline_days(athlete_alias,day,model_key,model_version,source_revision,payload)
     values(p_alias,(point->>'day')::date,p_model_key,'lifetime-timeline-v1',0,point) on conflict(athlete_alias,day) do nothing;
    n:=n+1;
   end if;
  end loop;
  delete from onflows_private.history_window_jobs where athlete_alias=p_alias;
  return jsonb_build_object('stored',true,'days',n);
 end if;
 raise exception 'Unsupported history operation';
end $$;
revoke all on function public.onflows_history_window(text,text,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_history_window(text,text,uuid,text,jsonb) to service_role;
