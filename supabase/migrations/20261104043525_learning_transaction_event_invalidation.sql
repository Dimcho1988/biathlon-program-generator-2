-- Input writers append independent transaction events instead of contending
-- on one mutable athlete cache row. Capture exact visible IDs, never MAX(id):
-- an earlier sequence ID can commit after a later ID has been prepared.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

create table onflows_private.learning_dirty_events (
 id bigint generated always as identity primary key,
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 transaction_id bigint not null default txid_current(),
 recorded_at timestamptz not null default clock_timestamp(),
 unique(athlete_alias,transaction_id)
);
create index learning_dirty_events_athlete on onflows_private.learning_dirty_events(athlete_alias,id);
alter table onflows_private.learning_dirty_events enable row level security;
revoke all on onflows_private.learning_dirty_events from public,anon,authenticated,service_role;
revoke all on sequence onflows_private.learning_dirty_events_id_seq from public,anon,authenticated,service_role;
alter table onflows_private.prepared_learning_views
 add column claimed_invalidations bigint[] not null default '{}'::bigint[];

create or replace function onflows_private.request_learning_preparation(p_alias text) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.onflows_athlete_settings where athlete_alias=p_alias) then return; end if;
 begin
  insert into onflows_private.learning_dirty_events(athlete_alias) values(p_alias)
  on conflict(athlete_alias,transaction_id) do nothing;
 exception when foreign_key_violation then
  -- Settings may have been deleted after EXISTS. Deletion cascades already
  -- invalidate this athlete; preserve every unrelated constraint failure.
  if not exists(select 1 from public.onflows_athlete_settings where athlete_alias=p_alias) then return; end if;
  raise;
 end;
end $$;

create or replace function onflows_private.invalidate_prepared_learning() returns trigger
language plpgsql security definer set search_path='' as $$
declare affected text;
begin
 if tg_op='DELETE' then
  affected:=old.athlete_alias;
  -- Foreign-key cascades remove both cache and events after settings deletion.
  if tg_table_name='onflows_athlete_settings' then return old; end if;
  if tg_table_name='onflows_management_entries' then
   if old.kind not in ('PROFILE','DRAFT') then return old; end if;
  end if;
 else
  affected:=new.athlete_alias;
  if tg_table_name='onflows_management_entries' then
   if new.kind not in ('PROFILE','DRAFT') then return new; end if;
  end if;
  if tg_op='UPDATE' then
   if (to_jsonb(new)-'updated_at')=(to_jsonb(old)-'updated_at') then return new; end if;
  end if;
 end if;
 perform onflows_private.request_learning_preparation(affected);
 if tg_op='DELETE' then return old; else return new; end if;
end $$;

create or replace function onflows_private.learning_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_payload jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.prepared_learning_views%rowtype; today_local date; zone text; source record; read_state record;
 visible_ids bigint[]; changed_inputs boolean; next_revision bigint;
begin
 if p_operation is null or p_operation not in ('READ','CLAIM','RENEW','STORE','FAIL') then
  raise exception 'Invalid learning operation' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') and (p_model_key is null or p_model_key !~ '^[a-f0-9]{64}$') then
  raise exception 'Invalid learning model key' using errcode='22023'; end if;
 if p_operation='CLAIM' then
  if p_token is null then raise exception 'Worker token required' using errcode='22023'; end if;
  -- Only workers clean/seed queue rows. Late input commits remain separate
  -- event rows and cannot be erased by cleanup of currently visible rows.
  delete from onflows_private.prepared_learning_views c
   where (p_alias is null or c.athlete_alias=p_alias)
    and not exists(select 1 from public.active_onflows_analysis(c.athlete_alias));
  delete from onflows_private.learning_dirty_events e
   where (p_alias is null or e.athlete_alias=p_alias)
    and not exists(select 1 from public.active_onflows_analysis(e.athlete_alias));
  insert into onflows_private.prepared_learning_views(athlete_alias)
   select distinct e.athlete_alias from onflows_private.learning_dirty_events e
    where (p_alias is null or e.athlete_alias=p_alias)
     and exists(select 1 from public.active_onflows_analysis(e.athlete_alias))
   on conflict do nothing;
  select d.* into j from onflows_private.prepared_learning_views d
   join public.onflows_athlete_settings s using(athlete_alias)
   where (p_alias is null or d.athlete_alias=p_alias)
    and (d.available_at<=clock_timestamp() or
     exists(select 1 from onflows_private.learning_dirty_events e where e.athlete_alias=d.athlete_alias
      and not(e.id=any(d.claimed_invalidations))) or
     (d.model_key is distinct from p_model_key and d.claimed_model_key is distinct from p_model_key))
    and (d.lease_until is null or d.lease_until<=clock_timestamp())
    and (d.payload is null or d.prepared_revision<>d.requested_revision
     or d.evaluated_on is distinct from (clock_timestamp() at time zone s.timezone)::date
     or d.model_key is distinct from p_model_key or d.expires_at<=clock_timestamp()
     or exists(select 1 from onflows_private.learning_dirty_events e where e.athlete_alias=d.athlete_alias))
    and (d.attempts<3 or d.claimed_revision is distinct from d.requested_revision
     or d.claimed_day is distinct from (clock_timestamp() at time zone s.timezone)::date
     or d.claimed_model_key is distinct from p_model_key
     or exists(select 1 from onflows_private.learning_dirty_events e where e.athlete_alias=d.athlete_alias
      and not(e.id=any(d.claimed_invalidations))))
   order by d.available_at limit 1 for update of d skip locked;
  if not found then return null; end if;
  select timezone into zone from public.onflows_athlete_settings where athlete_alias=j.athlete_alias;
  today_local:=(clock_timestamp() at time zone zone)::date;
  select coalesce(array_agg(e.id order by e.id),'{}'::bigint[]) into visible_ids
   from onflows_private.learning_dirty_events e where e.athlete_alias=j.athlete_alias;
  select generation_id,revision,activated_at into source from public.active_onflows_analysis(j.athlete_alias);
  if not found then
   delete from onflows_private.prepared_learning_views where athlete_alias=j.athlete_alias;
   delete from onflows_private.learning_dirty_events e
    where e.athlete_alias=j.athlete_alias and e.id=any(visible_ids);
   return null;
  end if;
  changed_inputs:=visible_ids is distinct from j.claimed_invalidations;
  next_revision:=case when changed_inputs or j.claimed_revision is null
    or j.claimed_day is distinct from today_local or j.claimed_model_key is distinct from p_model_key
   then nextval('onflows_private.prepared_learning_revision_seq') else j.requested_revision end;
  update onflows_private.prepared_learning_views set lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',
   available_at=clock_timestamp(),
   attempts=case when not changed_inputs and claimed_day=today_local and claimed_model_key=p_model_key then attempts+1 else 1 end,
   requested_revision=next_revision,claimed_revision=next_revision,claimed_day=today_local,
   claimed_model_key=p_model_key,claimed_invalidations=visible_ids,error_code=null,
   source_generation_id=source.generation_id,source_revision=source.revision,source_activated_at=source.activated_at
   where athlete_alias=j.athlete_alias returning * into j;
  return jsonb_build_object('athlete_alias',j.athlete_alias,'revision',j.claimed_revision,'day',today_local);
 end if;
 if p_alias is null then raise exception 'Athlete required' using errcode='22023'; end if;
 select timezone into zone from public.onflows_athlete_settings where athlete_alias=p_alias;
 if not found then return null; end if;
 today_local:=(clock_timestamp() at time zone zone)::date;
 if p_operation='READ' then
  if not exists(select 1 from public.active_onflows_analysis(p_alias)) then return null; end if;
  insert into onflows_private.prepared_learning_views(athlete_alias) values(p_alias) on conflict do nothing;
  -- Payload and dirty-event absence must be observed in the SAME snapshot.
  -- Otherwise a concurrent STORE could clear events after an old payload read.
  select d.*,(d.payload is not null and d.prepared_revision=d.requested_revision and d.evaluated_on=today_local
    and d.model_key=p_model_key and d.expires_at>clock_timestamp()
    and not exists(select 1 from onflows_private.learning_dirty_events e where e.athlete_alias=p_alias)) as projection_ready
   into read_state from onflows_private.prepared_learning_views d where d.athlete_alias=p_alias;
  if not found then return null; end if;
  if read_state.projection_ready then
   return jsonb_build_object('ready',true,'day',today_local,'prepared_at',read_state.prepared_at,'payload',read_state.payload);
  end if;
  update onflows_private.prepared_learning_views set available_at=least(available_at,clock_timestamp())
   where athlete_alias=p_alias and model_key is distinct from p_model_key;
  return jsonb_build_object('ready',false,'revision',read_state.requested_revision,'day',today_local);
 end if;
 select * into j from onflows_private.prepared_learning_views where athlete_alias=p_alias for update;
 if not found then return jsonb_build_object('stored',false); end if;
 if p_token is null or j.lease_token is distinct from p_token or j.lease_until is null or j.lease_until<=clock_timestamp() then
  return jsonb_build_object('state','LOST'); end if;
 if p_operation='STORE' and (p_revision is distinct from j.claimed_revision
  or p_day is distinct from j.claimed_day or p_model_key is distinct from j.claimed_model_key) then
  return jsonb_build_object('stored',false);
 end if;
 if p_operation='RENEW' then
  update onflows_private.prepared_learning_views set lease_until=clock_timestamp()+interval '90 seconds' where athlete_alias=p_alias;
  return jsonb_build_object('state','RUNNING');
 elsif p_operation='FAIL' then
  update onflows_private.prepared_learning_views set lease_token=null,lease_until=null,
   available_at=clock_timestamp()+interval '30 seconds',error_code='PREPARATION_DEFERRED' where athlete_alias=p_alias;
  return jsonb_build_object('state','DEFERRED');
 end if;
 select generation_id,revision,activated_at into source from public.active_onflows_analysis(p_alias);
 if not found or p_revision is distinct from j.requested_revision or p_day is distinct from today_local
  or source.generation_id is distinct from j.source_generation_id or source.revision is distinct from j.source_revision
  or source.activated_at is distinct from j.source_activated_at
  or exists(select 1 from onflows_private.learning_dirty_events e where e.athlete_alias=p_alias
   and not(e.id=any(j.claimed_invalidations)))
  or jsonb_typeof(p_payload) is distinct from 'object' or octet_length(p_payload::text)>2097152
  or p_payload->>'athlete_alias' is distinct from p_alias
  or p_payload#>>'{report,schema_version}' is distinct from 'recovery-learning-view-v1'
  or p_payload#>>'{report,as_of}' is distinct from p_day::text then
  update onflows_private.prepared_learning_views set lease_token=null,lease_until=null,available_at=clock_timestamp()
   where athlete_alias=p_alias;
  return jsonb_build_object('stored',false);
 end if;
 update onflows_private.prepared_learning_views set prepared_revision=p_revision,evaluated_on=p_day,model_key=p_model_key,
  payload=p_payload,prepared_at=clock_timestamp(),expires_at=clock_timestamp()+interval '2 days',error_code=null,
  available_at=((today_local+1)::timestamp at time zone zone),lease_token=null,lease_until=null
  where athlete_alias=p_alias;
 delete from onflows_private.learning_dirty_events e
  where e.athlete_alias=p_alias and e.id=any(j.claimed_invalidations);
 return jsonb_build_object('stored',true);
end $$;

-- Existing source users receive events; no input writer touches a cache row.
insert into onflows_private.learning_dirty_events(athlete_alias)
 select s.athlete_alias from public.onflows_athlete_settings s
 on conflict(athlete_alias,transaction_id) do nothing;
notify pgrst,'reload schema';
commit;
