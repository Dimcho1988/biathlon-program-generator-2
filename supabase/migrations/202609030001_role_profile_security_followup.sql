-- Follow-up hardening for the working role profile migration.

drop policy invites_select on public.onflows_connection_invites;
create policy invites_select on public.onflows_connection_invites for select to authenticated
using (
  inviter_user_id = (select auth.uid())
  or accepted_by_user_id = (select auth.uid())
  or (
    status = 'PENDING'
    and expires_at > now()
    and lower(invitee_email) = lower(coalesce((select auth.jwt()) ->> 'email', ''))
  )
);

drop policy organizations_select on public.onflows_organizations;
create policy organizations_select on public.onflows_organizations for select to authenticated
using (
  (select private.onflows_org_role(id)) is not null
  or exists (
    select 1
    from public.onflows_connection_invites i
    where i.organization_id = onflows_organizations.id
      and i.status = 'PENDING'
      and i.expires_at > now()
      and lower(i.invitee_email) = lower(coalesce((select auth.jwt()) ->> 'email', ''))
  )
);

-- This event-trigger function is invoked by PostgreSQL itself. Client roles do
-- not need EXECUTE privileges on it.
do $$
begin
  if to_regprocedure('public.rls_auto_enable()') is not null then
    execute 'revoke all on function public.rls_auto_enable() from public, anon, authenticated';
  end if;
end;
$$;
