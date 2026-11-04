-- Model/profile writers publish independent events, never lock the shared
-- dashboard queue row. Existing readers/workers consume exact visible IDs.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

create table onflows_private.dashboard_dirty_events (
 id bigint generated always as identity primary key,
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 transaction_id bigint not null default txid_current(),
 recorded_at timestamptz not null default clock_timestamp(),
 unique(athlete_alias,transaction_id)
);
create index dashboard_dirty_events_athlete on onflows_private.dashboard_dirty_events(athlete_alias,id);
alter table onflows_private.dashboard_dirty_events enable row level security;
revoke all on onflows_private.dashboard_dirty_events from public,anon,authenticated,service_role;
revoke all on sequence onflows_private.dashboard_dirty_events_id_seq from public,anon,authenticated,service_role;

create or replace function onflows_private.request_dashboard_preparation(p_alias text) returns void
language plpgsql security definer set search_path='' as $$
begin
 -- Settings may have just been deleted. Retain that event until a reader or
 -- worker removes the obsolete projection; connection deletion cascades both.
 if not exists(select 1 from public.onflows_intervals_connections where athlete_alias=p_alias) then return; end if;
 insert into onflows_private.dashboard_dirty_events(athlete_alias) values(p_alias)
 on conflict(athlete_alias,transaction_id) do nothing;
exception when foreign_key_violation then
 -- A concurrent connection deletion can finish between EXISTS and INSERT.
 -- Ignore only that vanished parent; retain every other integrity failure.
 if exists(select 1 from public.onflows_intervals_connections where athlete_alias=p_alias) then raise; end if;
end $$;

create or replace function onflows_private.invalidate_prepared_dashboard() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if tg_op='DELETE' then
  if tg_table_name='onflows_management_entries' then
   if old.kind<>'PROFILE' then return old; end if;
  end if;
  perform onflows_private.request_dashboard_preparation(old.athlete_alias);
  return old;
 end if;
 if tg_table_name='onflows_management_entries' then
  if new.kind<>'PROFILE' then return new; end if;
 end if;
 if tg_op='UPDATE' then
  if (to_jsonb(new)-'updated_at')=(to_jsonb(old)-'updated_at') then return new; end if;
 end if;
 perform onflows_private.request_dashboard_preparation(new.athlete_alias);
 return new;
end $$;

create function onflows_private.flush_dashboard_invalidations(p_alias text default null) returns void
language plpgsql security definer set search_path='' as $$
declare pending record;
begin
 -- Fixed alias ordering prevents queue-lock inversions between global flushes.
 -- Input writers only INSERT different transaction rows, so never wait here.
 for pending in
  select e.athlete_alias,array_agg(e.id order by e.id) as event_ids
   from onflows_private.dashboard_dirty_events e
   where p_alias is null or e.athlete_alias=p_alias
   group by e.athlete_alias order by e.athlete_alias limit 32
 loop
  if exists(select 1 from public.onflows_athlete_settings s where s.athlete_alias=pending.athlete_alias)
   and exists(select 1 from public.active_onflows_analysis(pending.athlete_alias)) then
   insert into onflows_private.prepared_dashboards(athlete_alias)
    values(pending.athlete_alias)
   on conflict(athlete_alias) do update
    set requested_revision=nextval('onflows_private.prepared_dashboard_revision_seq'),
     available_at=clock_timestamp(),attempts=0,error_code=null;
  else
   delete from onflows_private.prepared_dashboards where athlete_alias=pending.athlete_alias;
  end if;
  -- Sequence allocation order is not commit order. Never delete by MAX(id),
  -- or by alias alone: an older event may commit after this captured set.
  delete from onflows_private.dashboard_dirty_events e
   where e.athlete_alias=pending.athlete_alias and e.id=any(pending.event_ids);
 end loop;
end $$;
revoke all on function onflows_private.flush_dashboard_invalidations(text) from public,anon,authenticated,service_role;

-- Preserve the existing size/source/day/model/lease validation and synchronous
-- HTTP miss path. Only the invalidation boundary changes.
alter function onflows_private.dashboard_preparation(text,text,uuid,bigint,date,text,jsonb)
 rename to dashboard_preparation_core_v1;
revoke all on function onflows_private.dashboard_preparation_core_v1(text,text,uuid,bigint,date,text,jsonb)
 from public,anon,authenticated,service_role;

create function onflows_private.dashboard_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_envelope jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare result jsonb; read_state record;
begin
 if p_operation in ('READ','CLAIM','STORE') then
  perform onflows_private.flush_dashboard_invalidations(p_alias);
 end if;
 result:=onflows_private.dashboard_preparation_core_v1(
  p_operation,p_alias,p_token,p_revision,p_day,p_model_key,p_envelope);
 if p_operation<>'READ' then return result; end if;
 -- The core READ may have observed an older row before another worker flushed
 -- events and published. Re-read payload, freshness and dirty absence together.
 select d.envelope,d.requested_revision,
  (clock_timestamp() at time zone s.timezone)::date as local_day,
  exists(select 1 from public.active_onflows_analysis(p_alias)) as source_exists,
  (d.envelope is not null and d.prepared_revision=d.requested_revision
   and d.evaluated_on=(clock_timestamp() at time zone s.timezone)::date
   and d.model_key=p_model_key and d.expires_at>clock_timestamp()
   and not exists(select 1 from onflows_private.dashboard_dirty_events e where e.athlete_alias=p_alias)) as projection_ready
  into read_state from onflows_private.prepared_dashboards d
  join public.onflows_athlete_settings s using(athlete_alias)
  where d.athlete_alias=p_alias;
 if not found or not read_state.source_exists then return null; end if;
 if read_state.projection_ready then
  return jsonb_build_object('ready',true,'envelope',read_state.envelope);
 end if;
 return jsonb_build_object('ready',false,'revision',read_state.requested_revision,'day',read_state.local_day);
end $$;
revoke all on function onflows_private.dashboard_preparation(text,text,uuid,bigint,date,text,jsonb)
 from public,anon,authenticated;
grant execute on function onflows_private.dashboard_preparation(text,text,uuid,bigint,date,text,jsonb) to service_role;

-- Resolve the public invoker explicitly to the new wrapper after the rename.
create or replace function public.onflows_dashboard_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_envelope jsonb default null
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.dashboard_preparation(p_operation,p_alias,p_token,p_revision,p_day,p_model_key,p_envelope)
$$;

insert into onflows_private.dashboard_dirty_events(athlete_alias)
 select s.athlete_alias from public.onflows_athlete_settings s
 on conflict(athlete_alias,transaction_id) do nothing;
notify pgrst,'reload schema';
commit;
