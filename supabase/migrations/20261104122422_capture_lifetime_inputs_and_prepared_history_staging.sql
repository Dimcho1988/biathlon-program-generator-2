create function onflows_private.capture_lifetime_generation(p_generation uuid,p_first date default null,p_last date default null)
returns integer language plpgsql security definer set search_path='' as $$
declare g public.onflows_analysis_generations%rowtype; source jsonb; first_day date;last_day date;d date;
 activity_set uuid; doc jsonb; refs jsonb; updated integer:=0; n integer; unidentified integer; quality_counts jsonb;
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
 quality_counts:=jsonb_build_object('limited_activities',coalesce(source#>'{quality,limited_activities}','0'::jsonb),'excluded_activities',coalesce(source#>'{quality,excluded_activities}','0'::jsonb));
 select greatest(coalesce((source#>>'{quality,excluded_activities}')::integer,0)+coalesce((source#>>'{quality,limited_activities}')::integer,0)-count(*),0)::integer into unidentified
 from public.onflows_analysis_generation_activities a where a.generation_id=activity_set and a.athlete_alias=g.athlete_alias and
 (coalesce(a.catalog_payload->>'quality_status','valid')<>'valid' or not exists(select 1 from jsonb_array_elements(coalesce(source->'activities','[]')) x where x->>'activity_ref'=a.activity_ref));
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
   'source_quality_counts',quality_counts,'unidentified_activities',unidentified,
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
   source_revision=excluded.source_revision,payload=excluded.payload,needs_refresh=false,prepared_at=clock_timestamp()
  where excluded.source_revision>=learning_timeline_days.source_revision and (learning_timeline_days.needs_refresh or excluded.payload is distinct from learning_timeline_days.payload or excluded.model_key is distinct from learning_timeline_days.model_key);
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
  from onflows_private.learning_timeline_days h where h.athlete_alias=p_alias and h.day between first_day and last_day and not h.needs_refresh;
 report:=jsonb_set(report,'{series}',points);
 report:=report||jsonb_build_object('history',jsonb_build_object('available_start',available_first,'available_end',available_last,
  'display_start',first_day,'display_end',last_day,'requested_days',p_days,'preparing',exists(select 1 from onflows_private.learning_timeline_days h where h.athlete_alias=p_alias and h.day between first_day and last_day and h.needs_refresh),'limited',available_first is null or available_first>first_day or available_last<last_day));
 return jsonb_set(state,'{payload,report}',report);
end $$;
revoke all on function public.read_onflows_learning_history(text,integer,date,text) from public,anon,authenticated;
grant execute on function public.read_onflows_learning_history(text,integer,date,text) to service_role;

