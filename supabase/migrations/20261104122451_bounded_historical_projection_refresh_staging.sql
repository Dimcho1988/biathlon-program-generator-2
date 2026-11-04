-- A bounded idle pass also prepares older years restored/backfilled later.
-- One lease per athlete prevents duplicate work across rolling worker deploys.
create table onflows_private.history_projection_queue (
 athlete_alias text primary key references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 requested_at timestamptz not null default clock_timestamp()
);
alter table onflows_private.history_projection_queue enable row level security;
revoke all on onflows_private.history_projection_queue from public,anon,authenticated,service_role;
create function onflows_private.queue_old_history_projection() returns trigger language plpgsql security definer set search_path='' as $$
begin
 if new.day<current_date-365 then
  update onflows_private.learning_timeline_days set needs_refresh=true where athlete_alias=new.athlete_alias and day between new.day and new.day+84 and day<current_date-365;
  insert into onflows_private.history_projection_queue(athlete_alias) values(new.athlete_alias) on conflict do nothing;
 end if;
 return new;
end $$;
revoke all on function onflows_private.queue_old_history_projection() from public,anon,authenticated,service_role;
create trigger queue_old_history_projection after insert or update of payload on onflows_private.history_day_inputs for each row execute function onflows_private.queue_old_history_projection();
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
   into candidate from onflows_private.history_projection_queue q
   join onflows_private.history_day_inputs h using(athlete_alias)
   join public.onflows_athlete_settings s using(athlete_alias)
   join public.onflows_intervals_connections c using(athlete_alias)
   where c.status='CONNECTED' and (p_alias is null or h.athlete_alias=p_alias)
    and h.day<(clock_timestamp() at time zone s.timezone)::date-365
    and not exists(select 1 from onflows_private.learning_timeline_days t where t.athlete_alias=h.athlete_alias and t.day=h.day and not t.needs_refresh)
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
     values(p_alias,(point->>'day')::date,p_model_key,'lifetime-timeline-v1',0,point)
    on conflict(athlete_alias,day) do update set model_key=excluded.model_key,model_version=excluded.model_version,
     source_revision=excluded.source_revision,payload=excluded.payload,needs_refresh=false,prepared_at=clock_timestamp()
    where learning_timeline_days.needs_refresh;
    n:=n+1;
   end if;
  end loop;
  delete from onflows_private.history_window_jobs where athlete_alias=p_alias;
  delete from onflows_private.history_projection_queue q where q.athlete_alias=p_alias and not exists(
   select 1 from onflows_private.history_day_inputs h where h.athlete_alias=p_alias and h.day<current_date-365
   and not exists(select 1 from onflows_private.learning_timeline_days t where t.athlete_alias=h.athlete_alias and t.day=h.day and not t.needs_refresh));
  return jsonb_build_object('stored',true,'days',n);
 end if;
 raise exception 'Unsupported history operation';
end $$;
revoke all on function public.onflows_history_window(text,text,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_history_window(text,text,uuid,text,jsonb) to service_role;

create function onflows_private.invalidate_old_response_history() returns trigger language plpgsql security definer set search_path='' as $$
declare d date;
begin
 d:=coalesce(new.observed_date,(new.payload->>'day')::date,(new.payload->>'start')::date);
 if d<current_date-365 then
  update onflows_private.learning_timeline_days set needs_refresh=true where athlete_alias=new.athlete_alias and day between d and d+84 and day<current_date-365;
  insert into onflows_private.history_projection_queue(athlete_alias) values(new.athlete_alias) on conflict do nothing;
 end if;
 return new;
end $$;
revoke all on function onflows_private.invalidate_old_response_history() from public,anon,authenticated,service_role;
create trigger invalidate_old_response_history after insert on public.onflows_response_entries for each row execute function onflows_private.invalidate_old_response_history();
