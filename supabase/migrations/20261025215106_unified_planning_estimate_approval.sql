-- Match the API's accepted estimate policies at the publication boundary.
-- Preserve typed coverage/readiness gates and service-only permissions.
create or replace function onflows_private.management_plan_eligible(plan jsonb) returns boolean
language sql immutable security invoker set search_path='' as $$
 select coalesce(plan->'activation_eligible'='true'::jsonb and (
   plan#>'{source,readiness_known}'='true'::jsonb or (
   plan#>'{source,planning_history_supported}'='true'::jsonb
   and plan#>>'{source,readiness_basis}'='ESTIMATED_LOAD'
   and plan#>'{source,planning_history,supported}'='true'::jsonb
   and plan#>'{source,planning_history,estimated}'='true'::jsonb
   and plan#>>'{source,planning_history,policy}' in (
     'RECORDED_DURATION_EXPERT_Q_WITH_RECOVERY',
     'UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES'))), false)
$$;
revoke all on function onflows_private.management_plan_eligible(jsonb) from public,anon,authenticated;
grant execute on function onflows_private.management_plan_eligible(jsonb) to service_role;
