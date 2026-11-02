-- Prepared read models and event-driven planning share the existing worker.
-- No browser table access, new service, model change or automatic approval.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

create sequence onflows_private.prepared_dashboard_revision_seq;
revoke all on sequence onflows_private.prepared_dashboard_revision_seq from public,anon,authenticated,service_role;
create table onflows_private.prepared_dashboards (
 athlete_alias text primary key references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 requested_revision bigint not null default nextval('onflows_private.prepared_dashboard_revision_seq'),
 prepared_revision bigint not null default 0,
 evaluated_on date,
 model_key text,
 envelope jsonb,
 prepared_at timestamptz,
 cleaned_on date,
 expires_at timestamptz,
 available_at timestamptz not null default clock_timestamp(),
 lease_token uuid,
 lease_until timestamptz,
 claimed_revision bigint,
 claimed_day date,
 claimed_model_key text,
 attempts integer not null default 0 check(attempts between 0 and 3),
 error_code text,
 check(envelope is null or (jsonb_typeof(envelope)='object' and octet_length(envelope::text)<=8388608))
);
create index prepared_dashboards_due on onflows_private.prepared_dashboards(available_at);
alter table onflows_private.prepared_dashboards enable row level security;
revoke all on onflows_private.prepared_dashboards from public,anon,authenticated,service_role;

create table onflows_private.training_plan_updates (
 athlete_alias text primary key references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 requested_revision bigint not null default 1,
 completed_revision bigint not null default 0,
 generation_id uuid,
 requested_at timestamptz not null default clock_timestamp(),
 available_at timestamptz not null default clock_timestamp(),
 completed_at timestamptz,
 lease_token uuid,
 lease_until timestamptz,
 claimed_revision bigint,
 attempts integer not null default 0 check(attempts between 0 and 3),
 error_code text
);
create index training_plan_updates_due on onflows_private.training_plan_updates(available_at)
 where requested_revision>completed_revision;
alter table onflows_private.training_plan_updates enable row level security;
revoke all on onflows_private.training_plan_updates from public,anon,authenticated,service_role;

create function onflows_private.request_dashboard_preparation(p_alias text) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.onflows_athlete_settings where athlete_alias=p_alias)
  or not exists(select 1 from public.active_onflows_analysis(p_alias)) then
  delete from onflows_private.prepared_dashboards where athlete_alias=p_alias;
  return;
 end if;
 insert into onflows_private.prepared_dashboards(athlete_alias)
 values(p_alias)
 on conflict(athlete_alias) do update set requested_revision=nextval('onflows_private.prepared_dashboard_revision_seq'),
  available_at=clock_timestamp(),attempts=0,error_code=null;
end;
$$;
revoke all on function onflows_private.request_dashboard_preparation(text) from public,anon,authenticated,service_role;

create function onflows_private.invalidate_prepared_dashboard() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if tg_op='DELETE' then
  if tg_table_name='onflows_athlete_settings' then
   delete from onflows_private.prepared_dashboards where athlete_alias=old.athlete_alias;
  elsif tg_table_name='onflows_management_entries' then
   if old.kind='PROFILE' then perform onflows_private.request_dashboard_preparation(old.athlete_alias); end if;
  else
   perform onflows_private.request_dashboard_preparation(old.athlete_alias);
  end if;
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
revoke all on function onflows_private.invalidate_prepared_dashboard() from public,anon,authenticated,service_role;
create trigger dashboard_settings_changed after insert or update or delete on public.onflows_athlete_settings
 for each row execute function onflows_private.invalidate_prepared_dashboard();
create trigger dashboard_models_changed after insert or update or delete on public.onflows_model_entries
 for each row execute function onflows_private.invalidate_prepared_dashboard();
create trigger dashboard_profile_changed after insert or update or delete on public.onflows_management_entries
 for each row execute function onflows_private.invalidate_prepared_dashboard();
create trigger dashboard_legacy_changed after insert or update or delete on public.onflows_training_snapshots
 for each row execute function onflows_private.invalidate_prepared_dashboard();

create function onflows_private.training_arrived() returns trigger
language plpgsql security definer set search_path='' as $$
declare previous_set uuid; current_set uuid;
begin
 if tg_op='UPDATE' then
  if new.active_generation_id is not distinct from old.active_generation_id
   and new.active_revision=old.active_revision then return new; end if;
 end if;
 perform onflows_private.request_dashboard_preparation(new.athlete_alias);
 if new.active_generation_id is null then return new; end if;
 select coalesce(activity_set_generation_id,generation_id) into current_set
  from public.onflows_analysis_generations where generation_id=new.active_generation_id;
 if tg_op='UPDATE' then
  select coalesce(activity_set_generation_id,generation_id) into previous_set
   from public.onflows_analysis_generations where generation_id=old.active_generation_id;
 end if;
 -- Reimports, wellness-only generations, tests and a new day do not plan.
 -- A previously missing recording becoming available counts as new training.
 if exists(select 1 from public.onflows_analysis_generation_activities a
   where a.athlete_alias=new.athlete_alias and a.generation_id=current_set
    and coalesce(a.catalog_payload->>'quality_status','')<>'provider_missing'
    and greatest(coalesce((a.catalog_payload->>'elapsed_time_s')::numeric,0),
                 coalesce((a.catalog_payload->>'moving_time_s')::numeric,0),
                 coalesce((a.catalog_payload->>'recording_time_s')::numeric,0))>0
    and not exists(select 1 from public.onflows_analysis_generation_activities b
     where b.athlete_alias=a.athlete_alias and b.generation_id=previous_set and b.activity_ref=a.activity_ref
      and coalesce(b.catalog_payload->>'quality_status','')<>'provider_missing')) then
  insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
   values(new.athlete_alias,new.active_generation_id)
   on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
    generation_id=excluded.generation_id,requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;
 end if;
 return new;
end $$;
revoke all on function onflows_private.training_arrived() from public,anon,authenticated,service_role;
create trigger background_training_arrived after insert or update on public.onflows_athlete_analysis_state
 for each row execute function onflows_private.training_arrived();

create function onflows_private.dashboard_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_envelope jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.prepared_dashboards%rowtype; today_local date; zone text; source record;
begin
 if p_operation is null or p_operation not in ('READ','CLAIM','RENEW','STORE','FAIL') then
  raise exception 'Invalid dashboard operation' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') and (p_model_key is null or p_model_key !~ '^[a-f0-9]{64}$') then
  raise exception 'Invalid dashboard model key' using errcode='22023'; end if;
 if p_operation='CLAIM' then
  if p_token is null then raise exception 'Worker token required' using errcode='22023'; end if;
  select d.* into j from onflows_private.prepared_dashboards d
   join public.onflows_athlete_settings s using(athlete_alias)
   where (p_alias is null or d.athlete_alias=p_alias)
    and (d.available_at<=clock_timestamp() or
     (d.model_key is distinct from p_model_key and d.claimed_model_key is distinct from p_model_key))
    and (d.lease_until is null or d.lease_until<=clock_timestamp())
    and (d.envelope is null or d.prepared_revision<>d.requested_revision
     or d.evaluated_on is distinct from (clock_timestamp() at time zone s.timezone)::date
     or d.model_key is distinct from p_model_key or d.expires_at<=clock_timestamp())
    and (d.attempts<3 or d.claimed_revision is distinct from d.requested_revision
     or d.claimed_day is distinct from (clock_timestamp() at time zone s.timezone)::date
     or d.claimed_model_key is distinct from p_model_key)
   order by d.available_at limit 1 for update of d skip locked;
  if not found then return null; end if;
  select timezone into zone from public.onflows_athlete_settings where athlete_alias=j.athlete_alias;
  today_local:=(clock_timestamp() at time zone zone)::date;
  update onflows_private.prepared_dashboards set lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',
   attempts=case when claimed_revision=requested_revision and claimed_day=today_local and claimed_model_key=p_model_key then attempts+1 else 1 end,
   claimed_revision=requested_revision,claimed_day=today_local,claimed_model_key=p_model_key,error_code=null
   where athlete_alias=j.athlete_alias returning * into j;
  return jsonb_build_object('athlete_alias',j.athlete_alias,'revision',j.claimed_revision,'day',today_local);
 end if;
 if p_alias is null then raise exception 'Athlete required' using errcode='22023'; end if;
 select timezone into zone from public.onflows_athlete_settings where athlete_alias=p_alias;
 if not found then return null; end if;
 today_local:=(clock_timestamp() at time zone zone)::date;
 if p_operation='READ' then
  if not exists(select 1 from public.active_onflows_analysis(p_alias)) then return null; end if;
  insert into onflows_private.prepared_dashboards(athlete_alias) values(p_alias) on conflict do nothing;
  select * into j from onflows_private.prepared_dashboards where athlete_alias=p_alias;
  if j.envelope is not null and j.prepared_revision=j.requested_revision and j.evaluated_on=today_local
    and j.model_key=p_model_key and j.expires_at>clock_timestamp() then
   return jsonb_build_object('ready',true,'envelope',j.envelope);
  end if;
  -- A new build can need preparation before the next local-day timer.
  update onflows_private.prepared_dashboards set available_at=least(available_at,clock_timestamp())
   where athlete_alias=p_alias and model_key is distinct from p_model_key;
  return jsonb_build_object('ready',false,'revision',j.requested_revision,'day',today_local);
 end if;
 select * into j from onflows_private.prepared_dashboards where athlete_alias=p_alias for update;
 if not found then return jsonb_build_object('stored',false); end if;
 if p_operation in ('RENEW','FAIL') or p_token is not null then
  if p_token is null or j.lease_token is distinct from p_token or j.lease_until is null or j.lease_until<=clock_timestamp() then
   return jsonb_build_object('state','LOST'); end if;
 end if;
 if p_operation='STORE' and p_token is not null and (p_revision is distinct from j.claimed_revision
  or p_day is distinct from j.claimed_day or p_model_key is distinct from j.claimed_model_key) then
  return jsonb_build_object('stored',false);
 end if;
 if p_operation='RENEW' then
  update onflows_private.prepared_dashboards set lease_until=clock_timestamp()+interval '90 seconds' where athlete_alias=p_alias;
  return jsonb_build_object('state','RUNNING');
 elsif p_operation='FAIL' then
  update onflows_private.prepared_dashboards set lease_token=null,lease_until=null,
   available_at=clock_timestamp()+interval '30 seconds',error_code='PREPARATION_DEFERRED' where athlete_alias=p_alias;
  return jsonb_build_object('state','DEFERRED');
 end if;
 select generation_id,revision,analysis_as_of,activated_at into source from public.active_onflows_analysis(p_alias);
 if not found or p_revision is distinct from j.requested_revision or p_day is distinct from today_local
  or jsonb_typeof(p_envelope) is distinct from 'object' or octet_length(p_envelope::text)>8388608
  or p_envelope->'generation_id' is distinct from coalesce(to_jsonb(source.generation_id),'null'::jsonb)
  or p_envelope->'revision' is distinct from to_jsonb(source.revision)
  or p_envelope->'analysis_as_of' is distinct from coalesce(to_jsonb(source.analysis_as_of),'null'::jsonb)
  or (p_envelope->>'activated_at')::timestamptz is distinct from source.activated_at
  or p_envelope#>>'{snapshot_payload,schema_version}' is distinct from 'athlete-snapshot-v1'
  or p_envelope#>>'{snapshot_payload,training_status,athlete_id}' is distinct from p_alias
  or p_envelope#>>'{snapshot_payload,load_history,athlete_id}' is distinct from p_alias then
  if p_token is not null then
   update onflows_private.prepared_dashboards set lease_token=null,lease_until=null,available_at=clock_timestamp()
    where athlete_alias=p_alias;
  end if;
  return jsonb_build_object('stored',false);
 end if;
 update onflows_private.prepared_dashboards set prepared_revision=p_revision,evaluated_on=p_day,model_key=p_model_key,
  envelope=p_envelope,prepared_at=clock_timestamp(),expires_at=clock_timestamp()+interval '2 days',error_code=null,
  available_at=((today_local+1)::timestamp at time zone zone),
  lease_token=case when p_token is null then lease_token else null end,
  lease_until=case when p_token is null then lease_until else null end
  where athlete_alias=p_alias;
 return jsonb_build_object('stored',true);
end $$;
revoke all on function onflows_private.dashboard_preparation(text,text,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.dashboard_preparation(text,text,uuid,bigint,date,text,jsonb) to service_role;
create function public.onflows_dashboard_preparation(
 p_operation text,p_alias text default null,p_token uuid default null,p_revision bigint default null,
 p_day date default null,p_model_key text default null,p_envelope jsonb default null
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.dashboard_preparation(p_operation,p_alias,p_token,p_revision,p_day,p_model_key,p_envelope)
$$;
revoke all on function public.onflows_dashboard_preparation(text,text,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function public.onflows_dashboard_preparation(text,text,uuid,bigint,date,text,jsonb) to service_role;

create function onflows_private.training_plan_update(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.training_plan_updates%rowtype; actor uuid;
begin
 if p_operation is null or p_operation not in ('CLAIM','RENEW','COMPLETE','FAIL') or p_token is null then
  raise exception 'Invalid training update operation' using errcode='22023'; end if;
 if p_operation='CLAIM' then
  select t.* into j from onflows_private.training_plan_updates t
   where (p_alias is null or athlete_alias=p_alias) and requested_revision>completed_revision
    and attempts<3 and available_at<=clock_timestamp() and (lease_until is null or lease_until<=clock_timestamp())
    and not exists(select 1 from onflows_private.planning_jobs p where p.athlete_alias=t.athlete_alias and p.status in ('QUEUED','RUNNING'))
   order by available_at limit 1 for update skip locked;
  if not found then return null; end if;
  select approved_by into actor from public.onflows_management_plan_state where athlete_alias=j.athlete_alias;
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
 if p_operation='RENEW' then
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
revoke all on function onflows_private.training_plan_update(text,text,uuid) from public,anon,authenticated;
grant execute on function onflows_private.training_plan_update(text,text,uuid) to service_role;
create function public.onflows_training_plan_update(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.training_plan_update(p_operation,p_alias,p_token)
$$;
revoke all on function public.onflows_training_plan_update(text,text,uuid) from public,anon,authenticated;
grant execute on function public.onflows_training_plan_update(text,text,uuid) to service_role;

create function onflows_private.fixed_plan_identity(p_plan jsonb) returns jsonb
language plpgsql immutable set search_path='' as $$
declare result jsonb:=p_plan; section text; days jsonb; day jsonb; first_session jsonb;
begin
 if p_plan->>'_storage'='primary-session-reference-v1' then result:=result-'_storage'; end if;
 foreach section in array array['days','lookahead_days'] loop
  if jsonb_typeof(p_plan->section) is distinct from 'array' then continue; end if;
  days:='[]'::jsonb;
  for day in select value from jsonb_array_elements(p_plan->section) loop
   first_session:=day#>'{sessions,0}';
   if p_plan->>'_storage'='primary-session-reference-v1' and day->'session'='{"$session_ref":0}'::jsonb
    and jsonb_typeof(first_session)='object' then day:=jsonb_set(day,'{session}',first_session); end if;
   days:=days||jsonb_build_array(day);
  end loop;
  result:=jsonb_set(result,array[section],days);
 end loop;
 return result;
end $$;
revoke all on function onflows_private.fixed_plan_identity(jsonb) from public,anon,authenticated,service_role;

create function onflows_private.save_plan_after_training(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean,
 p_training_token uuid,p_training_revision bigint
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.training_plan_updates%rowtype; old_payload jsonb;
begin
 if p_automatic is distinct from true then
  raise exception 'Automatic training publication required' using errcode='22023'; end if;
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
 select r.payload into old_payload from public.onflows_management_plan_state s
  join public.onflows_management_plan_revisions r using(athlete_alias,revision) where s.athlete_alias=p_alias;
 if old_payload->>'mode'='REVIEW' and onflows_private.fixed_plan_identity(p_payload->'plan')
  is distinct from onflows_private.fixed_plan_identity(old_payload->'plan') then
  raise exception 'Fixed program requires approval' using errcode='42501'; end if;
 return public.save_onflows_management_plan_compact(p_alias,p_payload,p_expected_revision,p_actor,p_operation,
  p_expected_profile_revision,p_checkpoint,p_automatic);
end $$;
revoke all on function onflows_private.save_plan_after_training(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid,bigint)
 from public,anon,authenticated;
grant execute on function onflows_private.save_plan_after_training(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid,bigint)
 to service_role;
create function public.save_onflows_management_plan_after_training(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean,
 p_training_token uuid,p_training_revision bigint
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.save_plan_after_training(p_alias,p_payload,p_expected_revision,p_actor,p_operation,
  p_expected_profile_revision,p_checkpoint,p_automatic,p_training_token,p_training_revision)
$$;
revoke all on function public.save_onflows_management_plan_after_training(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid,bigint)
 from public,anon,authenticated;
grant execute on function public.save_onflows_management_plan_after_training(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid,bigint)
 to service_role;

-- Evaluate the captured selected plan once per projection statement.
-- Read-only, opt-in presentation projection. Immutable evidence stays intact.
create or replace function public.read_onflows_management_overview(p_alias text)
returns jsonb language sql stable security invoker set search_path='' as $$
 with pinned as materialized (
   select s.revision, r.revision as record_revision, r.payload,
     coalesce(nullif(r.payload->'proposal','null'::jsonb),r.payload->'plan') as chosen
   from public.onflows_management_plan_state s
   left join public.onflows_management_plan_revisions r
     on r.athlete_alias=s.athlete_alias and r.revision=s.revision
   where s.athlete_alias=p_alias
 )
 select coalesce((select jsonb_build_object(
   'revision',revision,'record_revision',record_revision,
   'stored_fingerprint',payload->'processed_input_fingerprint',
   'stored_evaluated_on',payload->'evaluated_on',
   'active_schema_version',payload->'schema_version',
   'plan_type',jsonb_typeof(payload->'plan'),'proposal_type',jsonb_typeof(payload->'proposal'),
   'selected_schema_version',chosen->'schema_version','selected_status',chosen->'status',
   'start_date',chosen->'start_date','end_date',chosen->'end_date',
   'status',payload->'status','has_proposal',nullif(payload->'proposal','null'::jsonb) is not null,
   'engine_version',chosen->'engine_version',
   'days',case when chosen ? 'days' then (select coalesce(jsonb_agg(jsonb_build_object(
     'date',d.day->'date','status',d.day->'status',
     'sessions',(select coalesce(jsonb_agg(jsonb_build_object(
       'total_minutes',s.session->'total_minutes',
       'blocks',case when s.session ? 'blocks' then (select coalesce(jsonb_agg(jsonb_build_object(
         'zone',b.block->'zone','duration_min',b.block->'duration_min'
       ) order by b.ord),'[]'::jsonb)
       from jsonb_array_elements(s.session->'blocks') with ordinality b(block,ord)) else 'null'::jsonb end
     ) order by s.ord),'[]'::jsonb)
     from jsonb_array_elements(case
       when d.day ? 'sessions' then d.day->'sessions'
       when d.day->'session'='null'::jsonb then '[]'::jsonb
       when jsonb_typeof(d.day->'session')='object' then jsonb_build_array(d.day->'session')
       else 'null'::jsonb end) with ordinality s(session,ord))
   ) order by d.ord),'[]'::jsonb)
   from jsonb_array_elements(chosen->'days') with ordinality d(day,ord)) else 'null'::jsonb end,
   'history_comparison',(select coalesce(jsonb_agg(jsonb_build_object(
     'start_date',h.week->'start_date','end_date',h.week->'end_date',
     'actual_minutes',h.week->'actual_minutes','covered_days',h.week->'covered_days'
   ) order by h.ord),'[]'::jsonb)
   from jsonb_array_elements(case when jsonb_typeof(chosen->'history_comparison')='array'
     then chosen->'history_comparison' else '[]'::jsonb end) with ordinality h(week,ord)),
   'outcomes',case when payload ? 'outcomes' then (select coalesce(jsonb_agg(jsonb_build_object(
     'date',o.outcome->'date','planned_minutes',o.outcome->'planned_minutes',
     'actual_minutes',o.outcome->'actual_minutes'
   ) order by o.ord),'[]'::jsonb)
   from jsonb_array_elements(payload->'outcomes') with ordinality o(outcome,ord)) else 'null'::jsonb end
 ) from pinned),'null'::jsonb)
$$;
revoke all on function public.read_onflows_management_overview(text) from public,anon,authenticated;
grant execute on function public.read_onflows_management_overview(text) to service_role;


create function onflows_private.cleanup_prepared_data(p_alias text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare drafts integer; projections integer; computations integer; cleanup_day date;
begin
 perform pg_advisory_xact_lock(hashtextextended('management:'||p_alias,0));
 select evaluated_on into cleanup_day from onflows_private.prepared_dashboards
  where athlete_alias=p_alias and cleaned_on is distinct from evaluated_on for update;
 if not found or cleanup_day is null then
  return jsonb_build_object('drafts',0,'projections',0,'computations',0);
 end if;
 -- Latest complete learning memory and progression anchors survive even when
 -- they belong to an older, unapproved draft. Approved plan revisions stay.
 with protected as (
  (select entry_key,revision from public.onflows_management_entries where athlete_alias=p_alias and kind='DRAFT'
   order by recorded_at desc,revision desc limit 1)
  union
  (select entry_key,revision from public.onflows_management_entries where athlete_alias=p_alias and kind='DRAFT'
   and jsonb_typeof(payload#>'{parameters,individual_learning,memory}')='object'
   order by coalesce(payload->>'generated_at',recorded_at::text) desc,recorded_at desc limit 1)
  union
  (select entry_key,revision from public.onflows_management_entries where athlete_alias=p_alias and kind='DRAFT'
   and payload#>'{parameters,load_progression,anchor}' is not null
   order by recorded_at desc,revision desc limit 1)
 ), obsolete as (
  select d.athlete_alias,d.kind,d.entry_key,d.revision from public.onflows_management_entries d
   where d.athlete_alias=p_alias and d.kind='DRAFT' and d.recorded_at<clock_timestamp()-interval '30 days'
    and not exists(select 1 from protected p where p.entry_key=d.entry_key and p.revision=d.revision)
    and not exists(select 1 from public.onflows_management_plan_revisions r where r.athlete_alias=d.athlete_alias
     and r.payload#>>'{origin,start_date}'=d.entry_key and r.payload#>>'{origin,draft_revision}'=d.revision::text)
    and not exists(select 1 from onflows_private.planning_jobs j where j.athlete_alias=d.athlete_alias
     and (j.status in ('QUEUED','RUNNING') or
      (j.request->>'start_date'=d.entry_key and j.receipt->>'revision'=d.revision::text)))
   order by d.recorded_at limit 100 for update of d skip locked
 ) delete from public.onflows_management_entries d using obsolete o
   where (d.athlete_alias,d.kind,d.entry_key,d.revision)=(o.athlete_alias,o.kind,o.entry_key,o.revision);
 get diagnostics drafts=row_count;
 -- Historical generation pins remain conservative; no approved history or
 -- provider recording is unpinned/deleted by this technical cache cleanup.
 delete from onflows_private.load_projection_cache where athlete_alias=p_alias and expires_at<=clock_timestamp();
 get diagnostics projections=row_count;
 delete from onflows_private.planning_computations where athlete_alias=p_alias and status='READY'
  and updated_at<clock_timestamp()-interval '2 days';
 get diagnostics computations=row_count;
 update onflows_private.prepared_dashboards set cleaned_on=cleanup_day where athlete_alias=p_alias;
 return jsonb_build_object('drafts',drafts,'projections',projections,'computations',computations);
end $$;
revoke all on function onflows_private.cleanup_prepared_data(text) from public,anon,authenticated;
grant execute on function onflows_private.cleanup_prepared_data(text) to service_role;
create function public.cleanup_onflows_prepared_data(p_alias text) returns jsonb
language sql security invoker set search_path='' as $$ select onflows_private.cleanup_prepared_data(p_alias) $$;
revoke all on function public.cleanup_onflows_prepared_data(text) from public,anon,authenticated;
grant execute on function public.cleanup_onflows_prepared_data(text) to service_role;

-- Existing athletes get a read model, never a synthetic new-training event.
insert into onflows_private.prepared_dashboards(athlete_alias)
 select s.athlete_alias from public.onflows_athlete_settings s where exists(select 1 from public.active_onflows_analysis(s.athlete_alias))
 on conflict do nothing;
notify pgrst,'reload schema';
commit;
