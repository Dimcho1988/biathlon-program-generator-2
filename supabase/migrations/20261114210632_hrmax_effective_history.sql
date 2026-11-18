-- Date-effective HR calibration, durable rebuild and publication fences.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

create function onflows_private.hr_calibration(s public.onflows_athlete_settings, d date)
returns jsonb language sql immutable set search_path='' as $$
 select jsonb_build_object('from',d,'hrmax_bpm',s.hrmax_bpm,'zone_bounds_bpm',s.hr_zone_bounds,
   'hr_zone_source',s.hr_zone_source,'hr_zone_percentages',s.hr_zone_percentages)
$$;
revoke all on function onflows_private.hr_calibration(public.onflows_athlete_settings,date) from public,anon,authenticated;

create function onflows_private.version_hr_calibration() returns trigger
language plpgsql security definer set search_path='' as $$
declare timeline jsonb; effective date; local_today date; changed boolean;
begin
 changed:=new.hrmax_bpm is distinct from old.hrmax_bpm or new.hr_zone_bounds is distinct from old.hr_zone_bounds
   or new.hr_zone_source is distinct from old.hr_zone_source or new.hr_zone_percentages is distinct from old.hr_zone_percentages;
 -- Clients cannot replace the authoritative timeline through generic settings saves.
 timeline:=coalesce(old.model_preferences->'hr_history','[]'::jsonb);
 if changed then
  local_today:=(clock_timestamp() at time zone new.timezone)::date;
  effective:=coalesce((new.model_preferences->>'hrmax_apply_from')::date,local_today-89);
  if effective>local_today or effective<local_today-179 then
   raise exception 'HR calibration date must be within the last 180 days' using errcode='22023'; end if;
  if jsonb_array_length(timeline)=0 then
   timeline:=jsonb_build_array(onflows_private.hr_calibration(old,date '0001-01-01'));
  end if;
  select coalesce(jsonb_agg(e order by e->>'from'),'[]'::jsonb) into timeline
   from jsonb_array_elements(timeline) e where (e->>'from')::date<effective;
  timeline:=timeline||jsonb_build_array(onflows_private.hr_calibration(new,effective));
  new.model_preferences:=(new.model_preferences-'hrmax_apply_from')||jsonb_build_object(
    'hr_history',timeline,'hr_settings_revision',gen_random_uuid()::text,
    'hr_recompute_from',case when onflows_private.hr_analysis_current(old.athlete_alias) then effective
      else least(effective,coalesce((old.model_preferences->>'hr_recompute_from')::date,effective)) end);
 else
  new.model_preferences:=(new.model_preferences-array['hrmax_apply_from','hr_history','hr_settings_revision','hr_recompute_from'])
    ||(select coalesce(jsonb_object_agg(key,value),'{}'::jsonb) from jsonb_each(old.model_preferences)
       where key in ('hr_history','hr_settings_revision','hr_recompute_from'));
 end if;
 return new;
end $$;
revoke all on function onflows_private.version_hr_calibration() from public,anon,authenticated;
-- Run before the existing assistant revision trigger so it sees the final values.
create trigger onflows_00_hr_calibration before update of hrmax_bpm,hr_zone_bounds,hr_zone_source,hr_zone_percentages,model_preferences
 on public.onflows_athlete_settings for each row execute function onflows_private.version_hr_calibration();

create function onflows_private.queue_hr_recalculation() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if new.model_preferences->>'hr_settings_revision' is distinct from old.model_preferences->>'hr_settings_revision'
   and exists(select 1 from public.onflows_intervals_connections where athlete_alias=new.athlete_alias and status='CONNECTED') then
  perform public.enqueue_onflows_sync_job(new.athlete_alias,'FULL_SYNC',
   md5(new.athlete_alias||(new.model_preferences->>'hr_settings_revision'))||md5(new.model_preferences->>'hr_settings_revision'),
   jsonb_build_object('schema_version','sync-request-v1','scope','FULL','reason','HR_CALIBRATION_CHANGED'));
 end if;
 return new;
end $$;
revoke all on function onflows_private.queue_hr_recalculation() from public,anon,authenticated;
create trigger onflows_hr_recalculation after update on public.onflows_athlete_settings
 for each row execute function onflows_private.queue_hr_recalculation();

create function onflows_private.hr_analysis_current(p_alias text) returns boolean
language sql stable security definer set search_path='' as $$
 select coalesce((select s.model_preferences->>'hr_settings_revision' is not distinct from
     g.snapshot_payload#>>'{load_history,hr_settings_revision}'
   from public.onflows_athlete_settings s
   left join public.onflows_athlete_analysis_state a using(athlete_alias)
   left join public.onflows_analysis_generations g on g.generation_id=a.active_generation_id
   where s.athlete_alias=p_alias),false)
$$;
revoke all on function onflows_private.hr_analysis_current(text) from public,anon,authenticated;
grant execute on function onflows_private.hr_analysis_current(text) to service_role;

create function onflows_private.fence_hr_generation() returns trigger
language plpgsql security definer set search_path='' as $$
declare revision text;
begin
 if new.status='ACTIVE' and old.status is distinct from 'ACTIVE' then
  -- Activation already owns the analysis-state lock; the settings transaction
  -- must acquire that same lock to enqueue its rebuild before it can commit.
  -- This rejects obsolete worker results without reversing that lock order.
  select model_preferences->>'hr_settings_revision' into revision
   from public.onflows_athlete_settings where athlete_alias=new.athlete_alias;
  if revision is distinct from new.snapshot_payload#>>'{load_history,hr_settings_revision}' then
   raise exception 'HR calibration changed during analysis' using errcode='40001'; end if;
 end if;
 return new;
end $$;
revoke all on function onflows_private.fence_hr_generation() from public,anon,authenticated;
create trigger onflows_hr_generation_fence before update of status on public.onflows_analysis_generations
 for each row execute function onflows_private.fence_hr_generation();

-- Keep all existing lease/event fences. Guard their shared private entry points,
-- including callers such as the lifetime-learning reader and preparation worker.
do $migration$
declare name text; signature text; definition text; marker text;
begin
 for name,signature in values
  ('dashboard_preparation','text,text,uuid,bigint,date,text,jsonb'),
  ('learning_preparation','text,text,uuid,bigint,date,text,jsonb'),
  ('prepared_view','text,text,text,jsonb,uuid,bigint,date,text,jsonb')
 loop
  definition:=pg_get_functiondef(('onflows_private.'||name||'('||signature||')')::regprocedure);
  marker:=$guard$begin
 if p_alias is not null and p_operation in ('READ','STORE') and not onflows_private.hr_analysis_current(p_alias) then
  return jsonb_build_object('ready',false,'stored',false,'reason','HR_CALIBRATION_CHANGED');
 end if;$guard$;
  if position('begin' in lower(definition))=0 then raise exception 'Missing preparation guard insertion point'; end if;
  definition:=regexp_replace(definition,'\mbegin\M',marker,'i');
  execute definition;
 end loop;
end $migration$;
notify pgrst,'reload schema';
commit;
