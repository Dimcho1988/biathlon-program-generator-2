-- An imported generation requests planning without blocking the import lane.
create function onflows_private.request_management_check(p_alias text) returns void
language sql security definer set search_path='' as $$
 update public.onflows_management_plan_state set next_check_at=clock_timestamp()
 where athlete_alias=p_alias and status in ('ACTIVE','REVIEW_REQUIRED')
$$;
revoke all on function onflows_private.request_management_check(text) from public,anon,authenticated;
grant execute on function onflows_private.request_management_check(text) to service_role;
create function public.request_onflows_management_check(p_alias text) returns void
language sql security invoker set search_path='' as $$ select onflows_private.request_management_check(p_alias) $$;
revoke all on function public.request_onflows_management_check(text) from public,anon,authenticated;
grant execute on function public.request_onflows_management_check(text) to service_role;
