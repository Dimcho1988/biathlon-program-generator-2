-- Working athlete, coach and head-coach account flows for onFlows.
-- Extends the v1 account foundation without changing scientific data tables.

alter table public.onflows_connection_invites
  add column organization_id uuid references public.onflows_organizations(id) on delete cascade,
  add column membership_role text;

alter table public.onflows_connection_invites
  add constraint onflows_connection_invites_membership_role_check
  check (membership_role is null or membership_role in ('HEAD_COACH', 'COACH', 'ATHLETE')),
  add constraint onflows_connection_invites_organization_role_check
  check ((organization_id is null) = (membership_role is null));

drop index public.onflows_connection_invites_inviter_user_id_lower_idx;
create unique index onflows_pending_organization_invite_unique
  on public.onflows_connection_invites (inviter_user_id, organization_id, lower(invitee_email))
  where status = 'PENDING' and organization_id is not null;
create unique index onflows_pending_direct_invite_unique
  on public.onflows_connection_invites (inviter_user_id, lower(invitee_email))
  where status = 'PENDING' and organization_id is null;
create index onflows_pending_invite_recipient_idx on public.onflows_connection_invites
  (lower(invitee_email), expires_at)
  where status = 'PENDING';
create index onflows_connection_invites_organization_id_idx on public.onflows_connection_invites (organization_id)
  where organization_id is not null;

create or replace function private.onflows_can_view_profile(p_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select (select auth.uid()) is not null and (
    p_user_id = (select auth.uid())
    or exists (
      select 1
      from public.onflows_sharing_grants g
      where g.owner_user_id = p_user_id
        and g.viewer_user_id = (select auth.uid())
    )
    or exists (
      select 1
      from public.onflows_organization_memberships viewer
      join public.onflows_organization_memberships subject
        on subject.organization_id = viewer.organization_id
      where viewer.user_id = (select auth.uid())
        and viewer.status = 'ACTIVE'
        and subject.user_id = p_user_id
        and subject.status = 'ACTIVE'
    )
  );
$$;

create or replace function private.onflows_can_access_athlete(p_athlete_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select (select auth.uid()) is not null and (
    p_athlete_user_id = (select auth.uid())
    or exists (
      select 1
      from public.onflows_sharing_grants g
      where g.owner_user_id = p_athlete_user_id
        and g.viewer_user_id = (select auth.uid())
        and (g.view_overview or g.view_training or g.view_recovery or g.view_plan)
    )
    or exists (
      select 1
      from public.onflows_organization_memberships viewer
      join public.onflows_organization_memberships athlete
        on athlete.organization_id = viewer.organization_id
      where viewer.user_id = (select auth.uid())
        and viewer.status = 'ACTIVE'
        and athlete.user_id = p_athlete_user_id
        and athlete.role = 'ATHLETE'
        and athlete.status = 'ACTIVE'
        and (
          viewer.role in ('ADMIN', 'HEAD_COACH')
          or (
            viewer.role = 'COACH'
            and exists (
              select 1
              from public.onflows_coach_athlete_assignments a
              where a.organization_id = viewer.organization_id
                and a.coach_user_id = viewer.user_id
                and a.athlete_user_id = p_athlete_user_id
            )
          )
        )
    )
  );
$$;

revoke all on function private.onflows_can_view_profile(uuid) from public, anon;
revoke all on function private.onflows_can_access_athlete(uuid) from public, anon;
grant execute on function private.onflows_can_view_profile(uuid) to authenticated;
grant execute on function private.onflows_can_access_athlete(uuid) to authenticated;

drop policy profiles_select on public.onflows_profiles;
create policy profiles_select on public.onflows_profiles for select to authenticated
using ((select private.onflows_can_view_profile(user_id)));

drop policy user_athletes_select on public.onflows_user_athletes;
create policy user_athletes_select on public.onflows_user_athletes for select to authenticated
using ((select private.onflows_can_access_athlete(user_id)));
create policy user_athletes_insert on public.onflows_user_athletes for insert to authenticated
with check (user_id = (select auth.uid()) and is_owner);
create policy user_athletes_update on public.onflows_user_athletes for update to authenticated
using (user_id = (select auth.uid()) and is_owner)
with check (user_id = (select auth.uid()) and is_owner);

grant insert, update on public.onflows_user_athletes to authenticated;

drop policy invites_select on public.onflows_connection_invites;
create policy invites_select on public.onflows_connection_invites for select to authenticated
using (
  inviter_user_id = (select auth.uid())
  or accepted_by_user_id = (select auth.uid())
  or (
    status = 'PENDING'
    and expires_at > now()
    and lower(invitee_email) = lower(coalesce((select auth.jwt() ->> 'email'), ''))
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
      and lower(i.invitee_email) = lower(coalesce((select auth.jwt() ->> 'email'), ''))
  )
);

drop policy invites_insert on public.onflows_connection_invites;
create policy invites_insert on public.onflows_connection_invites for insert to authenticated
with check (
  inviter_user_id = (select auth.uid())
  and accepted_by_user_id is null
  and status = 'PENDING'
  and expires_at > now()
  and (
    (organization_id is null and membership_role is null)
    or (
      organization_id is not null
      and membership_role is not null
      and (
        (select private.onflows_org_role(organization_id)) = 'ADMIN'
        or (
          (select private.onflows_org_role(organization_id)) = 'HEAD_COACH'
          and membership_role in ('COACH', 'ATHLETE')
        )
      )
    )
  )
);

-- Membership acceptance and organization creation must remain atomic. Direct
-- Data API writes could otherwise create orphan organizations or self-promote.
drop policy invites_update on public.onflows_connection_invites;
revoke update on public.onflows_connection_invites from authenticated;
drop policy organizations_insert on public.onflows_organizations;
drop policy organizations_update on public.onflows_organizations;
revoke insert, update on public.onflows_organizations from authenticated;
drop policy memberships_insert on public.onflows_organization_memberships;
drop policy memberships_update on public.onflows_organization_memberships;
drop policy memberships_delete on public.onflows_organization_memberships;
revoke insert, update, delete on public.onflows_organization_memberships from authenticated;

create or replace function public.create_onflows_organization(
  p_name text,
  p_slug text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := (select auth.uid());
  organization_id uuid;
begin
  if caller_id is null then
    raise exception 'authentication_required' using errcode = '42501';
  end if;
  if char_length(trim(p_name)) not between 1 and 160
    or trim(p_slug) !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' then
    raise exception 'invalid_organization';
  end if;
  if not exists (
    select 1 from public.onflows_profiles p where p.user_id = caller_id
  ) then
    raise exception 'profile_required';
  end if;

  insert into public.onflows_organizations (name, slug, created_by_user_id)
  values (trim(p_name), trim(p_slug), caller_id)
  returning id into organization_id;

  insert into public.onflows_organization_memberships (
    organization_id, user_id, role, status, invited_by_user_id
  ) values (
    organization_id, caller_id, 'HEAD_COACH', 'ACTIVE', caller_id
  );

  insert into public.onflows_organization_settings (
    organization_id, updated_by_user_id
  ) values (
    organization_id, caller_id
  );

  insert into public.onflows_access_audit_log (
    actor_user_id, organization_id, action, target_type, target_id
  ) values (
    caller_id, organization_id, 'ORGANIZATION_CREATED', 'organization', organization_id::text
  );

  return organization_id;
end;
$$;

create or replace function public.accept_onflows_organization_invite(
  p_invite_id uuid
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := (select auth.uid());
  caller_email text;
  invite_row public.onflows_connection_invites%rowtype;
  existing_role text;
  existing_status text;
begin
  if caller_id is null then
    raise exception 'authentication_required' using errcode = '42501';
  end if;
  if not exists (
    select 1 from public.onflows_profiles p where p.user_id = caller_id
  ) then
    raise exception 'profile_required';
  end if;

  select lower(u.email)
  into caller_email
  from auth.users u
  where u.id = caller_id;

  select *
  into invite_row
  from public.onflows_connection_invites i
  where i.id = p_invite_id
  for update;

  if invite_row.id is null
    or invite_row.organization_id is null
    or invite_row.membership_role is null
    or invite_row.status <> 'PENDING'
    or invite_row.expires_at <= now()
    or caller_email is null
    or lower(invite_row.invitee_email) <> caller_email then
    raise exception 'invite_not_available' using errcode = '42501';
  end if;

  select m.role, m.status
  into existing_role, existing_status
  from public.onflows_organization_memberships m
  where m.organization_id = invite_row.organization_id
    and m.user_id = caller_id;

  if existing_status = 'ACTIVE' and existing_role <> invite_row.membership_role then
    raise exception 'membership_already_active';
  end if;

  insert into public.onflows_organization_memberships (
    organization_id, user_id, role, status, invited_by_user_id
  ) values (
    invite_row.organization_id,
    caller_id,
    invite_row.membership_role,
    'ACTIVE',
    invite_row.inviter_user_id
  )
  on conflict (organization_id, user_id) do update
  set role = excluded.role,
      status = 'ACTIVE',
      invited_by_user_id = excluded.invited_by_user_id,
      updated_at = now();

  update public.onflows_connection_invites
  set status = 'ACCEPTED',
      accepted_by_user_id = caller_id,
      responded_at = now()
  where id = invite_row.id;

  insert into public.onflows_access_audit_log (
    actor_user_id, organization_id, action, target_type, target_id,
    metadata
  ) values (
    caller_id,
    invite_row.organization_id,
    'ORGANIZATION_INVITE_ACCEPTED',
    'membership',
    caller_id::text,
    jsonb_build_object('role', invite_row.membership_role)
  );

  return invite_row.organization_id;
end;
$$;

revoke all on function public.create_onflows_organization(text, text) from public, anon;
revoke all on function public.accept_onflows_organization_invite(uuid) from public, anon;
grant execute on function public.create_onflows_organization(text, text) to authenticated;
grant execute on function public.accept_onflows_organization_invite(uuid) to authenticated;

create or replace function private.validate_onflows_assignment_memberships()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not exists (
    select 1
    from public.onflows_organization_memberships m
    where m.organization_id = new.organization_id
      and m.user_id = new.coach_user_id
      and m.role in ('HEAD_COACH', 'COACH')
      and m.status = 'ACTIVE'
  ) then
    raise exception 'active_coach_membership_required';
  end if;
  if not exists (
    select 1
    from public.onflows_organization_memberships m
    where m.organization_id = new.organization_id
      and m.user_id = new.athlete_user_id
      and m.role = 'ATHLETE'
      and m.status = 'ACTIVE'
  ) then
    raise exception 'active_athlete_membership_required';
  end if;
  if new.assigned_by_user_id <> (select auth.uid())
    or (select private.onflows_org_role(new.organization_id)) not in ('ADMIN', 'HEAD_COACH') then
    raise exception 'assignment_not_authorized' using errcode = '42501';
  end if;
  return new;
end;
$$;

revoke all on function private.validate_onflows_assignment_memberships() from public, anon, authenticated;
create trigger validate_onflows_assignment_memberships
before insert or update on public.onflows_coach_athlete_assignments
for each row execute function private.validate_onflows_assignment_memberships();
