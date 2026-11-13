-- Callers that read the current plan after publication need only an acknowledgement.
-- The existing writer remains the sole authority for access, CAS, locks and data.
-- Keep its original full-response RPC unchanged for older API/worker versions.
create function public.save_onflows_management_plan_compact(
 p_alias text,p_payload jsonb,p_expected_revision integer,p_actor uuid,p_operation text,
 p_expected_profile_revision integer,p_checkpoint jsonb,p_automatic boolean default false
) returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.save_management_plan(
   p_alias,p_payload,p_expected_revision,p_actor,p_operation,
   p_expected_profile_revision,p_checkpoint,p_automatic) - 'payload'
$$;
revoke all on function public.save_onflows_management_plan_compact(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
  from public,anon,authenticated;
grant execute on function public.save_onflows_management_plan_compact(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
  to service_role;

notify pgrst, 'reload schema';
