-- A separate, service-only read model for health/learning observations.
-- Opening /learning never fits a model or publishes a training decision.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

create sequence onflows_private.prepared_learning_revision_seq;
revoke all on sequence onflows_private.prepared_learning_revision_seq from public,anon,authenticated,service_role;
create table onflows_private.prepared_learning_views (
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 requested_revision bigint not null default nextval('onflows_private.prepared_learning_revision_seq'),
 prepared_revision bigint not null default 0,
 evaluated_on date,
 model_key text,
 payload jsonb,
 prepared_at timestamptz,
 expires_at timestamptz,
 available_at timestamptz not null default clock_timestamp(),
 lease_token uuid,
 lease_until timestamptz,
 claimed_revision bigint,
 claimed_day date,
 claimed_model_key text,
 source_generation_id uuid,
 source_revision bigint,
 source_activated_at timestamptz,
 attempts integer not null default 0 check(attempts between 0 and 3),
 error_code text,
 check(payload is null or (jsonb_typeof(payload)='object' and octet_length(payload::text)<=2097152))
);
create index prepared_learning_views_due on onflows_private.prepared_learning_views(available_at);
alter table onflows_private.prepared_learning_views enable row level security;
revoke all on onflows_private.prepared_learning_views from public,anon,authenticated,service_role;

create function onflows_private.request_learning_preparation(p_alias text) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.onflows_athlete_settings where athlete_alias=p_alias)
  or not exists(select 1 from public.active_onflows_analysis(p_alias)) then
  delete from onflows_private.prepared_learning_views where athlete_alias=p_alias;
  return;
 end if;
 insert into onflows_private.prepared_learning_views(athlete_alias) values(p_alias)
 on conflict(athlete_alias) do update set requested_revision=nextval('onflows_private.prepared_learning_revision_seq'),
  available_at=clock_timestamp(),attempts=0,error_code=null;
end $$;
revoke all on function onflows_private.request_learning_preparation(text) from public,anon,authenticated,service_role;

create function onflows_private.invalidate_prepared_learning() returns trigger
language plpgsql security definer set search_path='' as $$
declare affected text;
begin
 if tg_op='DELETE' then
  affected:=old.athlete_alias;
  if tg_table_name='onflows_athlete_settings' then
   delete from onflows_private.prepared_learning_views where athlete_alias=affected;
   return old;
  end if;
  if tg_table_name='onflows_management_entries' and old.kind not in ('PROFILE','DRAFT') then return old; end if;
 else
  affected:=new.athlete_alias;
  if tg_table_name='onflows_management_entries' and new.kind not in ('PROFILE','DRAFT') then return new; end if;
  if tg_op='UPDATE' and (to_jsonb(new)-'updated_at')=(to_jsonb(old)-'updated_at') then return new; end if;
 end if;
 perform onflows_private.request_learning_preparation(affected);
 if tg_op='DELETE' then return old; else return new; end if;
end $$;
revoke all on function onflows_private.invalidate_prepared_learning() from public,anon,authenticated,service_role;
create trigger learning_settings_changed after insert or update or delete on public.onflows_athlete_settings
 for each row execute function onflows_private.invalidate_prepared_learning();
create trigger learning_models_changed after insert or update or delete on public.onflows_model_entries
 for each row execute function onflows_private.invalidate_prepared_learning();
create trigger learning_responses_changed after insert or update or delete on public.onflows_response_entries
 for each row execute function onflows_private.invalidate_prepared_learning();
create trigger learning_profile_or_draft_changed after insert or update or delete on public.onflows_management_entries
 for each row execute function onflows_private.invalidate_prepared_learning();
create trigger learning_plan_memory_changed after insert or update or delete on public.onflows_management_plan_revisions
 for each row execute function onflows_private.invalidate_prepared_learning();
create trigger learning_analysis_changed after insert or update or delete on public.onflows_athlete_analysis_state
 for each row execute function onflows_private.invalidate_prepared_learning();
create trigger learning_legacy_changed after insert or update or delete on public.onflows_training_snapshots
 for each row execute function onflows_private.invalidate_prepared_learning();

create function onflows_private.learning_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_payload jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.prepared_learning_views%rowtype; today_local date; zone text; source record;
begin
 if p_operation is null or p_operation not in ('READ','CLAIM','RENEW','STORE','FAIL') then
  raise exception 'Invalid learning operation' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') and (p_model_key is null or p_model_key !~ '^[a-f0-9]{64}$') then
  raise exception 'Invalid learning model key' using errcode='22023'; end if;
 if p_operation='CLAIM' then
  if p_token is null then raise exception 'Worker token required' using errcode='22023'; end if;
  select d.* into j from onflows_private.prepared_learning_views d
   join public.onflows_athlete_settings s using(athlete_alias)
   where (p_alias is null or d.athlete_alias=p_alias)
    and (d.available_at<=clock_timestamp() or
     (d.model_key is distinct from p_model_key and d.claimed_model_key is distinct from p_model_key))
    and (d.lease_until is null or d.lease_until<=clock_timestamp())
    and (d.payload is null or d.prepared_revision<>d.requested_revision
     or d.evaluated_on is distinct from (clock_timestamp() at time zone s.timezone)::date
     or d.model_key is distinct from p_model_key or d.expires_at<=clock_timestamp())
    and (d.attempts<3 or d.claimed_revision is distinct from d.requested_revision
     or d.claimed_day is distinct from (clock_timestamp() at time zone s.timezone)::date
     or d.claimed_model_key is distinct from p_model_key)
   order by d.available_at limit 1 for update of d skip locked;
  if not found then return null; end if;
  select timezone into zone from public.onflows_athlete_settings where athlete_alias=j.athlete_alias;
  today_local:=(clock_timestamp() at time zone zone)::date;
  select generation_id,revision,activated_at into source from public.active_onflows_analysis(j.athlete_alias);
  if not found then
   delete from onflows_private.prepared_learning_views where athlete_alias=j.athlete_alias;
   return null;
  end if;
  update onflows_private.prepared_learning_views set lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',
   attempts=case when claimed_revision=requested_revision and claimed_day=today_local and claimed_model_key=p_model_key then attempts+1 else 1 end,
   claimed_revision=requested_revision,claimed_day=today_local,claimed_model_key=p_model_key,error_code=null,
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
  select * into j from onflows_private.prepared_learning_views where athlete_alias=p_alias;
  if j.payload is not null and j.prepared_revision=j.requested_revision and j.evaluated_on=today_local
    and j.model_key=p_model_key and j.expires_at>clock_timestamp() then
   return jsonb_build_object('ready',true,'day',today_local,'prepared_at',j.prepared_at,'payload',j.payload);
  end if;
  update onflows_private.prepared_learning_views set available_at=least(available_at,clock_timestamp())
   where athlete_alias=p_alias and model_key is distinct from p_model_key;
  return jsonb_build_object('ready',false,'revision',j.requested_revision,'day',today_local);
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
 return jsonb_build_object('stored',true);
end $$;
revoke all on function onflows_private.learning_preparation(text,text,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.learning_preparation(text,text,uuid,bigint,date,text,jsonb) to service_role;
create function public.onflows_learning_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_payload jsonb default null
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.learning_preparation(p_operation,p_alias,p_token,p_revision,p_day,p_model_key,p_payload)
$$;
revoke all on function public.onflows_learning_preparation(text,text,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_learning_preparation(text,text,uuid,bigint,date,text,jsonb) to service_role;

insert into onflows_private.prepared_learning_views(athlete_alias)
 select s.athlete_alias from public.onflows_athlete_settings s where exists(select 1 from public.active_onflows_analysis(s.athlete_alias))
 on conflict do nothing;
notify pgrst,'reload schema';
commit;
