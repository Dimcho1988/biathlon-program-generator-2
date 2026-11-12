-- Typed legacy form settings and canonical Intervals refresh with atomic receipts.
begin;
create or replace function onflows_private.version_assistant_settings() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
 if row(new.hr_zone_bounds,new.timezone,new.hrmax_bpm,new.hr_zone_source,new.hr_zone_percentages,new.model_preferences,new.planning_profile,new.mesocycle_accent_preferences)
  is distinct from row(old.hr_zone_bounds,old.timezone,old.hrmax_bpm,old.hr_zone_source,old.hr_zone_percentages,old.model_preferences,old.planning_profile,old.mesocycle_accent_preferences) then
  new.settings_revision := old.settings_revision+1;
 else new.settings_revision := old.settings_revision; end if;
 return new;
end $$;
revoke all on function onflows_private.version_assistant_settings() from public,anon,authenticated;
drop trigger onflows_assistant_settings_revision on public.onflows_athlete_settings;
create trigger onflows_assistant_settings_revision before update of hr_zone_bounds,timezone,hrmax_bpm,hr_zone_source,hr_zone_percentages,model_preferences,planning_profile,mesocycle_accent_preferences,settings_revision
on public.onflows_athlete_settings for each row execute function onflows_private.version_assistant_settings();

create or replace function public.onflows_assistant_operation_status(p_alias text,p_actor uuid,p_operation_id uuid)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare rights jsonb; saved onflows_private.assistant_operations%rowtype; plan_kind boolean;
begin
 rights := public.onflows_assistant_access(p_alias,p_actor);
 if not coalesce((rights->>'can_view_plan')::boolean,false)
    and not coalesce((rights->>'can_view_recovery')::boolean,false)
    and not coalesce((rights->>'can_edit_plan')::boolean,false) then
  return jsonb_build_object('forbidden',true);
 end if;
 select * into saved from onflows_private.assistant_operations
  where athlete_alias=p_alias and actor_id=p_actor and operation_id=p_operation_id;
 if not found then return jsonb_build_object('status','NOT_FOUND'); end if;
 plan_kind := saved.kind in ('MANAGEMENT_PROFILE','CALENDAR_EVENT','CALENDAR_REPLACE','PLAN_ACTION','PLAN_DAY','PLAN_ACTIVATE','PLAN_GENERATE');
 if (plan_kind and not (rights->>'can_view_plan')::boolean)
    or (saved.kind in ('ATHLETE_SETTINGS','PLANNING_PROFILE','MESOCYCLE_PREFERENCES') and not (rights->>'can_edit_plan')::boolean)
    or (saved.kind='SYNC_DATA' and not ((rights->>'can_view_recovery')::boolean or ((rights->>'can_view_plan')::boolean and (rights->>'can_edit_plan')::boolean)))
    or (not plan_kind and saved.kind not in ('ATHLETE_SETTINGS','PLANNING_PROFILE','MESOCYCLE_PREFERENCES','SYNC_DATA') and not (rights->>'can_view_recovery')::boolean) then
  return jsonb_build_object('forbidden',true);
 end if;
 return saved.receipt || jsonb_build_object('request_hash',saved.request_hash);
end $$;

create or replace function public.save_onflows_assistant_app_operation(
 p_alias text,p_actor uuid,p_operation_id uuid,p_request_hash text,p_kind text,p_key text,
 p_payload jsonb,p_expected_revision integer
) returns jsonb language plpgsql security invoker set search_path='' as $$
declare rights jsonb; prior onflows_private.assistant_operations%rowtype; current_revision integer;
 result jsonb; receipt jsonb; stamp timestamptz; stored_kind text; plan_kind boolean; args jsonb; rpc text;
begin
 if p_actor is null or p_operation_id is null or p_request_hash is null or p_request_hash !~ '^[a-f0-9]{64}$'
  or p_kind is null or p_kind not in ('PLANNING_PROFILE','MESOCYCLE_PREFERENCES','SYNC_DATA','ATHLETE_SETTINGS','RECOVERY_CONFIG','SPEED_TEST','PLAN_ACTION','PLAN_DAY','PLAN_ACTIVATE','PLAN_GENERATE')
  or p_key is null or length(p_key) not between 1 and 80
  or p_payload is null or jsonb_typeof(p_payload)<>'object' or octet_length(p_payload::text)>16777216
  or p_expected_revision is null or p_expected_revision<0 then
  raise exception 'Invalid assistant app operation' using errcode='22023';
 end if;
 plan_kind := p_kind in ('PLAN_ACTION','PLAN_DAY','PLAN_ACTIVATE','PLAN_GENERATE');
 rights := public.onflows_assistant_access(p_alias,p_actor);
 if (p_kind='SYNC_DATA' and not (coalesce((rights->>'can_view_recovery')::boolean,false)
       or (coalesce((rights->>'can_view_plan')::boolean,false) and coalesce((rights->>'can_edit_plan')::boolean,false))))
  or (p_kind<>'SYNC_DATA' and (not coalesce((rights->>'can_edit_plan')::boolean,false)
   or (p_kind not in ('ATHLETE_SETTINGS','PLANNING_PROFILE','MESOCYCLE_PREFERENCES')
    and not coalesce((rights->>case when plan_kind then 'can_view_plan' else 'can_view_recovery' end)::boolean,false)))) then
  return jsonb_build_object('forbidden',true);
 end if;
 -- Generation workers take the job lock before the management lock.
 if p_kind='PLAN_GENERATE' then
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('planning-job:'||p_alias,0));
 end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:'||p_alias,0));
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('assistant:'||p_alias||':'||p_actor::text||':'||p_operation_id::text,0));
 rights := public.onflows_assistant_access(p_alias,p_actor);
 if (p_kind='SYNC_DATA' and not (coalesce((rights->>'can_view_recovery')::boolean,false)
       or (coalesce((rights->>'can_view_plan')::boolean,false) and coalesce((rights->>'can_edit_plan')::boolean,false))))
  or (p_kind<>'SYNC_DATA' and (not coalesce((rights->>'can_edit_plan')::boolean,false)
   or (p_kind not in ('ATHLETE_SETTINGS','PLANNING_PROFILE','MESOCYCLE_PREFERENCES')
    and not coalesce((rights->>case when plan_kind then 'can_view_plan' else 'can_view_recovery' end)::boolean,false)))) then
  return jsonb_build_object('forbidden',true);
 end if;
 select * into prior from onflows_private.assistant_operations
  where athlete_alias=p_alias and actor_id=p_actor and operation_id=p_operation_id;
 if found then
  if prior.request_hash<>p_request_hash then
   return jsonb_build_object('conflict',true,'status','CONFLICT','reason','OPERATION_REUSED');
  end if;
  return prior.receipt || jsonb_build_object('request_hash',prior.request_hash);
 end if;
 if p_kind='SYNC_DATA' then
  if p_key<>'intervals-sync' or p_expected_revision<>0 then
   raise exception 'Invalid sync intent' using errcode='22023'; end if;
  current_revision := 0;
 elsif p_kind in ('PLANNING_PROFILE','MESOCYCLE_PREFERENCES') then
  if p_key is distinct from (case when p_kind='PLANNING_PROFILE' then 'planning-profile-v1' else 'mesocycle-accent-preferences-v1' end)
   or p_payload->>'schema_version' is distinct from p_key then
   raise exception 'Invalid planning settings identity' using errcode='22023'; end if;
  select settings_revision into current_revision from public.onflows_athlete_settings where athlete_alias=p_alias for update;
 elsif p_kind='ATHLETE_SETTINGS' then
  if p_key<>'athlete-settings' or p_payload - array['hr_zone_bounds','timezone','hrmax_bpm','hr_zone_source','hr_zone_percentages','model_preferences']::text[] <> '{}'::jsonb
   or not p_payload ?& array['hr_zone_bounds','timezone','hrmax_bpm','hr_zone_source','hr_zone_percentages','model_preferences'] then
   raise exception 'Invalid settings payload' using errcode='22023'; end if;
  select settings_revision into current_revision from public.onflows_athlete_settings where athlete_alias=p_alias for update;
 elsif p_kind='PLAN_GENERATE' then
  if p_key !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then raise exception 'Invalid draft date' using errcode='22023'; end if;
  select revision into current_revision from public.onflows_management_entries
   where athlete_alias=p_alias and kind='PROFILE' and entry_key='management-profile-v1' order by revision desc limit 1;
 elsif plan_kind then
  if p_key<>'active-plan' then raise exception 'Invalid plan key' using errcode='22023'; end if;
  select revision into current_revision from public.onflows_management_plan_state where athlete_alias=p_alias;
 else
  stored_kind := case when p_kind='RECOVERY_CONFIG' then 'RECOVERY' else 'SPEED_TEST' end;
  if (p_kind='RECOVERY_CONFIG' and p_key<>'recovery-v2') or (p_kind='SPEED_TEST' and p_key !~ '^[a-f0-9]{32}$') then
   raise exception 'Invalid model key' using errcode='22023'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_alias||':'||stored_kind||':'||p_key,0));
  select revision into current_revision from public.onflows_model_entries
   where athlete_alias=p_alias and kind=stored_kind and entry_key=p_key order by revision desc limit 1;
 end if;
 current_revision := coalesce(current_revision,0);
 if current_revision<>p_expected_revision then
  result := jsonb_build_object('conflict',true,'reason','REVISION_CHANGED','revision',current_revision);
 elsif p_kind in ('PLANNING_PROFILE','MESOCYCLE_PREFERENCES') then
  if p_kind='PLANNING_PROFILE' then
   if p_payload - array['schema_version','season_start','season_end','annual_target_hours','sessions_per_week','rest_days','double_session_days','long_session_day','intensity_days','strength_days','max_key_sessions_per_week','mesocycle_anchor_date','mesocycle_length_weeks','camp_default_accent_limit','double_threshold_enabled','double_threshold_day','double_threshold_components']::text[] <> '{}'::jsonb then
    raise exception 'Unknown planning profile fields' using errcode='22023'; end if;
   update public.onflows_athlete_settings set planning_profile=p_payload,updated_at=clock_timestamp()
    where athlete_alias=p_alias returning settings_revision into current_revision;
  else
   if p_payload - array['schema_version','accent_mode','accent_limit','manual_components']::text[] <> '{}'::jsonb then
    raise exception 'Unknown mesocycle preferences fields' using errcode='22023'; end if;
   update public.onflows_athlete_settings set mesocycle_accent_preferences=p_payload,updated_at=clock_timestamp()
    where athlete_alias=p_alias and planning_profile is not null returning settings_revision into current_revision;
  end if;
  if not found then raise exception 'Planning settings prerequisites missing' using errcode='22023'; end if;
  result := jsonb_build_object('saved',true,'revision',current_revision);
 elsif p_kind='SYNC_DATA' then
  args := p_payload->'request_payload';
  if p_payload - array['job_kind','request_payload']::text[] <> '{}'::jsonb
   or jsonb_typeof(args) is distinct from 'object'
   or args - array['schema_version','scope','as_of']::text[] <> '{}'::jsonb
   or args->>'schema_version' is distinct from 'sync-request-v1'
   or args->>'scope' is null or args->>'scope' not in ('FULL','WELLNESS','RECOVERY')
   or p_payload->>'job_kind' is distinct from (case args->>'scope' when 'FULL' then 'FULL_SYNC' when 'WELLNESS' then 'WELLNESS_SYNC' when 'RECOVERY' then 'RECOVERY_RESTORE' end)
   or args->>'as_of' is null or args->>'as_of' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
   raise exception 'Invalid canonical sync request' using errcode='22023'; end if;
  perform (args->>'as_of')::date;
  -- The stable operation key and receipt commit together. A new intent after
  -- completion gets a new operation UUID, as the form's refresh button does.
  select to_jsonb(queued) into result from public.enqueue_onflows_sync_job(p_alias,p_payload->>'job_kind',
   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p_alias||':'||p_actor::text||':'||p_operation_id::text,'UTF8')),'hex'),args) queued;
  if result->>'status' is null or result->>'status' not in ('QUEUED','RUNNING','RETRY_WAIT') then
   raise exception 'Sync queue returned invalid accepted state' using errcode='22023'; end if;
  result := jsonb_build_object('saved',true,'revision',0,'queued',true,'sync_job',result,
   'job',result||jsonb_build_object('scope',args->>'scope','coalesced',(result->>'deduplicated')::boolean),
   'sync',jsonb_build_object('schema_version','sync-enqueue-v1','job_id',result->>'job_id',
    'scope',args->>'scope','state',case when result->>'status'='RETRY_WAIT' then 'QUEUED' else result->>'status' end,
    'coalesced',(result->>'deduplicated')::boolean));
 elsif p_kind='ATHLETE_SETTINGS' then
  update public.onflows_athlete_settings set
   hr_zone_bounds=array(select jsonb_array_elements_text(p_payload->'hr_zone_bounds')::integer),
   timezone=p_payload->>'timezone',hrmax_bpm=(p_payload->>'hrmax_bpm')::integer,
   hr_zone_source=p_payload->>'hr_zone_source',
   hr_zone_percentages=case when p_payload->'hr_zone_percentages'='null'::jsonb then null else array(select jsonb_array_elements_text(p_payload->'hr_zone_percentages')::double precision) end,
   model_preferences=p_payload->'model_preferences',updated_at=clock_timestamp()
   where athlete_alias=p_alias returning settings_revision into current_revision;
  if not found then raise exception 'Athlete settings missing' using errcode='22023'; end if;
  result := jsonb_build_object('saved',true,'revision',current_revision);
 elsif p_kind='PLAN_GENERATE' then
  args := p_payload->'request';
  if p_payload - 'request' <> '{}'::jsonb or jsonb_typeof(args) is distinct from 'object'
   or args->>'start_date' is distinct from p_key
   or (args->>'expected_profile_revision')::integer is distinct from p_expected_revision then
   raise exception 'Invalid draft request identity' using errcode='22023'; end if;
  result := public.onflows_planning_job('ENQUEUE',p_alias,p_actor,args);
  if not coalesce((result->>'conflict')::boolean,false) then
   result := jsonb_build_object('saved',true,'revision',p_expected_revision,'queued',result->>'status' in ('QUEUED','RUNNING'),'job',result);
  end if;
 elsif not plan_kind then
  result := public.save_onflows_model_entry(p_alias,stored_kind,p_key,p_payload,p_expected_revision,p_actor);
 else
  rpc := p_payload->>'rpc'; args := p_payload->'arguments';
  if p_payload - array['rpc','arguments']::text[] <> '{}'::jsonb or jsonb_typeof(args)<>'object'
   or args->>'p_alias' is distinct from p_alias or args->>'p_actor' is distinct from p_actor::text
   or (args->>'p_expected_revision')::integer is distinct from p_expected_revision then
   raise exception 'Plan operation identity mismatch' using errcode='22023'; end if;
  if rpc='queue_onflows_plan_refresh' and p_kind='PLAN_ACTION' then
   if args - array['p_alias','p_actor','p_expected_revision','p_record_unchanged_check']::text[] <> '{}'::jsonb then
    raise exception 'Unknown refresh arguments' using errcode='22023'; end if;
   result := public.queue_onflows_plan_refresh(p_alias,p_actor,p_expected_revision,coalesce((args->>'p_record_unchanged_check')::boolean,false));
   if coalesce((result->>'queued')::boolean,false) then result := result||jsonb_build_object('saved',true,'revision',p_expected_revision); end if;
  elsif rpc='save_onflows_management_plan_compact' then
   if args - array['p_alias','p_actor','p_expected_revision','p_payload','p_operation','p_expected_profile_revision','p_checkpoint','p_automatic']::text[] <> '{}'::jsonb
    or coalesce((args->>'p_automatic')::boolean,false)
    or (p_kind='PLAN_ACTIVATE' and args->>'p_operation' is distinct from 'ACTIVATE')
    or (p_kind='PLAN_ACTION' and args->>'p_operation' not in ('PAUSE','RESUME','APPROVE','REVIEW','COMPLETE'))
    or (p_kind='PLAN_DAY' and args->>'p_operation' not in ('DAY','REVIEW','COMPLETE')) then
    raise exception 'Unsupported plan publication' using errcode='22023'; end if;
   result := public.save_onflows_management_plan_compact(p_alias,args->'p_payload',p_expected_revision,p_actor,
      args->>'p_operation',(args->>'p_expected_profile_revision')::integer,args->'p_checkpoint',false);
   result := result || jsonb_build_object('plan_status',args#>>'{p_payload,status}','plan_operation',args->>'p_operation');
  else raise exception 'Unsupported canonical action' using errcode='22023'; end if;
 end if;
 stamp := clock_timestamp();
 receipt := (result-'payload') || jsonb_build_object('operation_id',p_operation_id,'kind',p_kind,'entry_key',p_key,
  'status',case when coalesce((result->>'saved')::boolean,false) then 'SAVED' else 'CONFLICT' end,
  'recorded_at',coalesce(result->>'recorded_at',stamp::text),'origin','CHAT',
  'refresh_state',case when coalesce((result->>'queued')::boolean,false) then 'QUEUED'
    when coalesce((result->>'saved')::boolean,false) then case when plan_kind then 'PLAN_SAVED' else 'INPUT_SAVED' end else 'UNCHANGED' end);
 insert into onflows_private.assistant_operations(athlete_alias,actor_id,operation_id,request_hash,kind,entry_key,origin,receipt,recorded_at)
 values(p_alias,p_actor,p_operation_id,p_request_hash,p_kind,p_key,'CHAT',receipt,stamp);
 return receipt||jsonb_build_object('request_hash',p_request_hash);
end $$;
revoke all on function public.save_onflows_assistant_app_operation(text,uuid,uuid,text,text,text,jsonb,integer) from public,anon,authenticated;
grant execute on function public.save_onflows_assistant_app_operation(text,uuid,uuid,text,text,text,jsonb,integer) to service_role;
alter function public.save_onflows_assistant_app_operation(text,uuid,uuid,text,text,text,jsonb,integer) set statement_timeout='35s';
notify pgrst,'reload schema';
commit;
