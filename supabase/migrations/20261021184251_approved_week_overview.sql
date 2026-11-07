-- The primary calendar always presents approved prescriptions.
-- Monitoring history is fresh; a proposal never silently replaces approved volume.
create or replace function public.read_onflows_management_overview(p_alias text)
returns jsonb language sql stable security invoker set search_path='' as $$
 with pinned as materialized (
   select s.revision, r.revision as record_revision, r.payload,
     r.payload->'plan' as chosen,
     coalesce(r.payload#>'{monitoring,history_comparison}',r.payload#>'{plan,history_comparison}') as history
   from public.onflows_management_plan_state s
   left join public.onflows_management_plan_revisions r
     on r.athlete_alias=s.athlete_alias and r.revision=s.revision
   where s.athlete_alias=p_alias
 )
 select coalesce((select jsonb_build_object(
   'revision',revision,'record_revision',record_revision,
   'stored_fingerprint',payload->'processed_input_fingerprint',
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
   from jsonb_array_elements(case when jsonb_typeof(history)='array'
     then history else '[]'::jsonb end) with ordinality h(week,ord)),
   'outcomes',case when payload ? 'outcomes' then (select coalesce(jsonb_agg(jsonb_build_object(
     'date',o.outcome->'date','planned_minutes',o.outcome->'planned_minutes',
     'actual_minutes',o.outcome->'actual_minutes'
   ) order by o.ord),'[]'::jsonb)
   from jsonb_array_elements(payload->'outcomes') with ordinality o(outcome,ord)) else 'null'::jsonb end
 ) from pinned),'null'::jsonb)
$$;
revoke all on function public.read_onflows_management_overview(text) from public,anon,authenticated;
grant execute on function public.read_onflows_management_overview(text) to service_role;

notify pgrst, 'reload schema';
