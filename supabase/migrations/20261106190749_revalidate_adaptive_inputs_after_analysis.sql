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
    or (previous_snapshot->'wellness_calendar') is distinct from (current_snapshot->'wellness_calendar'))
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
   'analysis_hints',jsonb_build_array(jsonb_build_object(
    'generation_id',chosen#>'{input_snapshot,generation_id}',
    'analysis_revision',chosen#>'{input_snapshot,analysis_revision}',
    'analysis_as_of',chosen#>'{input_snapshot,analysis_as_of}',
    'snapshot_fingerprint',chosen#>'{input_snapshot,snapshot_fingerprint}')),

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

-- Refresh already stale active records once after upgrading this event policy.
insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
 select s.athlete_alias,a.active_generation_id from public.onflows_management_plan_state s
 join public.onflows_athlete_analysis_state a using(athlete_alias)
 where s.status in ('ACTIVE','REVIEW_REQUIRED') and a.active_generation_id is not null
  and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
 on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
  generation_id=excluded.generation_id,requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null;

notify pgrst,'reload schema';
