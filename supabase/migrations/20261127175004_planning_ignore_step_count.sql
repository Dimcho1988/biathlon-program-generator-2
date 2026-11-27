-- Queue and Python planning identity both ignore only the audited step count.
-- Derived views still receive the complete, immutable wellness snapshot.
create or replace function onflows_private.wellness_for_planning(p_calendar jsonb)
returns jsonb language plpgsql immutable security invoker set search_path='' as $$
declare item jsonb; metrics jsonb; result jsonb:='[]'::jsonb;
begin
 if jsonb_typeof(p_calendar) is distinct from 'array' then return p_calendar; end if;
 for item in select value from jsonb_array_elements(p_calendar) loop
  if jsonb_typeof(item)='object' and jsonb_typeof(item->'metrics')='object' then
   metrics:=(item->'metrics')-'steps';
   if metrics='{}'::jsonb and item ? 'date' and (item-'date'-'metrics')='{}'::jsonb then
    continue;
   end if;
   item:=jsonb_set(item,'{metrics}',metrics);
  end if;
  result:=result||jsonb_build_array(item);
 end loop;
 return result;
end $$;
revoke all on function onflows_private.wellness_for_planning(jsonb) from public,anon,authenticated,service_role;

-- Same-content source activations stay current without a new programme.
-- Changed executed load/wellness must prepare a current proposal in the worker.
create or replace function onflows_private.training_arrived() returns trigger
language plpgsql security definer set search_path='' as $$
declare previous_set uuid; current_set uuid; previous_snapshot jsonb; current_snapshot jsonb;
begin
 if tg_op='UPDATE' then
  if new.active_generation_id is not distinct from old.active_generation_id
   and new.active_revision=old.active_revision then return new; end if;
 end if;
 perform onflows_private.request_dashboard_preparation(new.athlete_alias);
 if new.active_generation_id is null then return new; end if;
 select coalesce(activity_set_generation_id,generation_id),snapshot_payload into current_set,current_snapshot
  from public.onflows_analysis_generations where generation_id=new.active_generation_id;
 if tg_op='UPDATE' then
  select coalesce(activity_set_generation_id,generation_id),snapshot_payload into previous_set,previous_snapshot
   from public.onflows_analysis_generations where generation_id=old.active_generation_id;
 end if;
 -- Identical imports never regenerate a programme. Material changes to
 -- executed load or wellness also need a prepared current proposal.
 -- A previously missing recording becoming available counts as new training.
 if exists(select 1 from public.onflows_analysis_generation_activities a
   where a.athlete_alias=new.athlete_alias and a.generation_id=current_set
    and coalesce(a.catalog_payload->>'quality_status','')<>'provider_missing'
    and greatest(coalesce((a.catalog_payload->>'elapsed_time_s')::numeric,0),
                 coalesce((a.catalog_payload->>'moving_time_s')::numeric,0),
                 coalesce((a.catalog_payload->>'recording_time_s')::numeric,0))>0
    and not exists(select 1 from public.onflows_analysis_generation_activities b
     where b.athlete_alias=a.athlete_alias and b.generation_id=previous_set and b.activity_ref=a.activity_ref
      and coalesce(b.catalog_payload->>'quality_status','')<>'provider_missing'))
  or (previous_snapshot is not null and
   ((previous_snapshot->'load_history') is distinct from (current_snapshot->'load_history')
    or onflows_private.wellness_for_planning(previous_snapshot->'wellness_calendar')
       is distinct from onflows_private.wellness_for_planning(current_snapshot->'wellness_calendar'))
   and exists(select 1 from public.onflows_management_plan_state s where s.athlete_alias=new.athlete_alias
    and s.status in ('ACTIVE','REVIEW_REQUIRED'))) then
  insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
   values(new.athlete_alias,new.active_generation_id)
   on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
    generation_id=excluded.generation_id,requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;
 end if;
 return new;
end $$;
revoke all on function onflows_private.training_arrived() from public,anon,authenticated,service_role;

