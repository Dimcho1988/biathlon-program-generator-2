-- Accounts, consent-based sharing and organization hierarchy for onFlows.
-- Existing athlete_alias data remains server-only and backward compatible.

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create table public.onflows_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (char_length(trim(display_name)) between 1 and 100),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.onflows_user_athletes (
  user_id uuid not null references public.onflows_profiles(user_id) on delete cascade,
  athlete_alias text not null,
  is_owner boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (user_id, athlete_alias),
  unique (athlete_alias)
);

create table public.onflows_connection_invites (
  id uuid primary key default gen_random_uuid(),
  inviter_user_id uuid not null references public.onflows_profiles(user_id) on delete cascade,
  invitee_email text not null check (char_length(trim(invitee_email)) between 3 and 320),
  token_hash text not null unique check (length(token_hash) = 64),
  status text not null default 'PENDING' check (status in ('PENDING', 'ACCEPTED', 'DECLINED', 'REVOKED', 'EXPIRED')),
  expires_at timestamptz not null,
  accepted_by_user_id uuid references public.onflows_profiles(user_id) on delete set null,
  created_at timestamptz not null default now(),
  responded_at timestamptz,
  check (expires_at > created_at),
  check ((status = 'ACCEPTED') = (accepted_by_user_id is not null))
);

create unique index on public.onflows_connection_invites (inviter_user_id, lower(invitee_email))
where status = 'PENDING';
create index on public.onflows_connection_invites (accepted_by_user_id) where accepted_by_user_id is not null;

create table public.onflows_sharing_grants (
  owner_user_id uuid not null references public.onflows_profiles(user_id) on delete cascade,
  viewer_user_id uuid not null references public.onflows_profiles(user_id) on delete cascade,
  view_overview boolean not null default true,
  view_training boolean not null default false,
  view_recovery boolean not null default false,
  view_plan boolean not null default false,
  edit_plan boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, viewer_user_id),
  check (owner_user_id <> viewer_user_id),
  check (not edit_plan or view_plan)
);

create table public.onflows_organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(trim(name)) between 1 and 160),
  slug text not null unique check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
  created_by_user_id uuid not null references public.onflows_profiles(user_id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.onflows_organization_memberships (
  organization_id uuid not null references public.onflows_organizations(id) on delete cascade,
  user_id uuid not null references public.onflows_profiles(user_id) on delete cascade,
  role text not null check (role in ('ADMIN', 'HEAD_COACH', 'COACH', 'ATHLETE')),
  status text not null default 'ACTIVE' check (status in ('INVITED', 'ACTIVE', 'SUSPENDED', 'LEFT')),
  invited_by_user_id uuid references public.onflows_profiles(user_id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create table public.onflows_coach_athlete_assignments (
  organization_id uuid not null,
  coach_user_id uuid not null,
  athlete_user_id uuid not null,
  assigned_by_user_id uuid not null references public.onflows_profiles(user_id),
  can_edit_plan boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (organization_id, coach_user_id, athlete_user_id),
  foreign key (organization_id, coach_user_id)
    references public.onflows_organization_memberships(organization_id, user_id) on delete cascade,
  foreign key (organization_id, athlete_user_id)
    references public.onflows_organization_memberships(organization_id, user_id) on delete cascade,
  check (coach_user_id <> athlete_user_id)
);

create table public.onflows_organization_settings (
  organization_id uuid primary key references public.onflows_organizations(id) on delete cascade,
  workflow_key text not null default 'ONFLOWS_STANDARD' check (workflow_key ~ '^[A-Z0-9_]+$'),
  methodology_version text not null default 'canonical-v1',
  feature_flags jsonb not null default '{}'::jsonb check (jsonb_typeof(feature_flags) = 'object'),
  updated_by_user_id uuid not null references public.onflows_profiles(user_id),
  updated_at timestamptz not null default now()
);

create table public.onflows_plan_authorities (
  athlete_user_id uuid primary key references public.onflows_profiles(user_id) on delete cascade,
  organization_id uuid references public.onflows_organizations(id) on delete cascade,
  coach_user_id uuid references public.onflows_profiles(user_id) on delete set null,
  granted_by_user_id uuid not null references public.onflows_profiles(user_id),
  created_at timestamptz not null default now(),
  check (organization_id is not null or coach_user_id is not null)
);

create table public.onflows_access_audit_log (
  id bigint generated always as identity primary key,
  actor_user_id uuid references public.onflows_profiles(user_id) on delete set null,
  organization_id uuid references public.onflows_organizations(id) on delete set null,
  action text not null check (char_length(action) between 1 and 100),
  target_type text not null check (char_length(target_type) between 1 and 100),
  target_id text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now()
);

-- PostgreSQL does not create indexes for the referencing side of foreign keys.
create index on public.onflows_sharing_grants (viewer_user_id);
create index on public.onflows_organizations (created_by_user_id);
create index on public.onflows_organization_memberships (user_id);
create index on public.onflows_organization_memberships (invited_by_user_id) where invited_by_user_id is not null;
create index on public.onflows_coach_athlete_assignments (organization_id, athlete_user_id);
create index on public.onflows_coach_athlete_assignments (assigned_by_user_id);
create index on public.onflows_organization_settings (updated_by_user_id);
create index on public.onflows_plan_authorities (organization_id) where organization_id is not null;
create index on public.onflows_plan_authorities (coach_user_id) where coach_user_id is not null;
create index on public.onflows_plan_authorities (granted_by_user_id);
create index on public.onflows_access_audit_log (actor_user_id) where actor_user_id is not null;
create index on public.onflows_access_audit_log (organization_id) where organization_id is not null;

create or replace function private.onflows_org_role(p_organization_id uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select m.role
  from public.onflows_organization_memberships m
  where auth.uid() is not null
    and m.organization_id = p_organization_id
    and m.user_id = auth.uid()
    and m.status = 'ACTIVE';
$$;

revoke all on function private.onflows_org_role(uuid) from public, anon;
grant usage on schema private to authenticated;
grant execute on function private.onflows_org_role(uuid) to authenticated;

alter table public.onflows_profiles enable row level security;
alter table public.onflows_user_athletes enable row level security;
alter table public.onflows_connection_invites enable row level security;
alter table public.onflows_sharing_grants enable row level security;
alter table public.onflows_organizations enable row level security;
alter table public.onflows_organization_memberships enable row level security;
alter table public.onflows_coach_athlete_assignments enable row level security;
alter table public.onflows_organization_settings enable row level security;
alter table public.onflows_plan_authorities enable row level security;
alter table public.onflows_access_audit_log enable row level security;

revoke all on all tables in schema public from anon;
grant select, insert, update on public.onflows_profiles to authenticated;
grant select on public.onflows_user_athletes to authenticated;
grant select, insert, update on public.onflows_connection_invites to authenticated;
grant select, insert, update, delete on public.onflows_sharing_grants to authenticated;
grant select, insert, update on public.onflows_organizations to authenticated;
grant select, insert, update, delete on public.onflows_organization_memberships to authenticated;
grant select, insert, update, delete on public.onflows_coach_athlete_assignments to authenticated;
grant select, insert, update on public.onflows_organization_settings to authenticated;
grant select, insert, update, delete on public.onflows_plan_authorities to authenticated;
grant select on public.onflows_access_audit_log to authenticated;
grant all on all tables in schema public to service_role;
grant usage, select on sequence public.onflows_access_audit_log_id_seq to service_role;

create policy profiles_select on public.onflows_profiles for select to authenticated
using (user_id = (select auth.uid()) or exists (
  select 1 from public.onflows_sharing_grants g
  where g.owner_user_id = onflows_profiles.user_id and g.viewer_user_id = (select auth.uid())
));
create policy profiles_insert on public.onflows_profiles for insert to authenticated
with check (user_id = (select auth.uid()));
create policy profiles_update on public.onflows_profiles for update to authenticated
using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy user_athletes_select on public.onflows_user_athletes for select to authenticated
using (user_id = (select auth.uid()) or exists (
  select 1 from public.onflows_sharing_grants g
  where g.owner_user_id = onflows_user_athletes.user_id and g.viewer_user_id = (select auth.uid())
));

create policy invites_select on public.onflows_connection_invites for select to authenticated
using (inviter_user_id = (select auth.uid()) or accepted_by_user_id = (select auth.uid()));
create policy invites_insert on public.onflows_connection_invites for insert to authenticated
with check (inviter_user_id = (select auth.uid()) and accepted_by_user_id is null and status = 'PENDING');
create policy invites_update on public.onflows_connection_invites for update to authenticated
using (inviter_user_id = (select auth.uid()) or accepted_by_user_id = (select auth.uid()))
with check (inviter_user_id = (select auth.uid()) or accepted_by_user_id = (select auth.uid()));

create policy sharing_select on public.onflows_sharing_grants for select to authenticated
using (owner_user_id = (select auth.uid()) or viewer_user_id = (select auth.uid()));
create policy sharing_insert on public.onflows_sharing_grants for insert to authenticated
with check (owner_user_id = (select auth.uid()));
create policy sharing_update on public.onflows_sharing_grants for update to authenticated
using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy sharing_delete on public.onflows_sharing_grants for delete to authenticated
using (owner_user_id = (select auth.uid()));

create policy organizations_select on public.onflows_organizations for select to authenticated
using ((select private.onflows_org_role(id)) is not null);
create policy organizations_insert on public.onflows_organizations for insert to authenticated
with check (created_by_user_id = (select auth.uid()));
create policy organizations_update on public.onflows_organizations for update to authenticated
using ((select private.onflows_org_role(id)) = 'ADMIN') with check ((select private.onflows_org_role(id)) = 'ADMIN');

create policy memberships_select on public.onflows_organization_memberships for select to authenticated
using ((select private.onflows_org_role(organization_id)) is not null);
create policy memberships_insert on public.onflows_organization_memberships for insert to authenticated
with check (
  (select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH')
  or (
    user_id = (select auth.uid())
    and role = 'ADMIN'
    and status = 'ACTIVE'
    and exists (
      select 1 from public.onflows_organizations o
      where o.id = organization_id and o.created_by_user_id = (select auth.uid())
    )
  )
);
create policy memberships_update on public.onflows_organization_memberships for update to authenticated
using ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'))
with check ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'));
create policy memberships_delete on public.onflows_organization_memberships for delete to authenticated
using ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'));

create policy assignments_select on public.onflows_coach_athlete_assignments for select to authenticated
using ((select private.onflows_org_role(organization_id)) is not null);
create policy assignments_write on public.onflows_coach_athlete_assignments for all to authenticated
using ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'))
with check ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'));

create policy organization_settings_select on public.onflows_organization_settings for select to authenticated
using ((select private.onflows_org_role(organization_id)) is not null);
create policy organization_settings_write on public.onflows_organization_settings for all to authenticated
using ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'))
with check ((select private.onflows_org_role(organization_id)) in ('ADMIN', 'HEAD_COACH'));

create policy plan_authorities_select on public.onflows_plan_authorities for select to authenticated
using (athlete_user_id = (select auth.uid()) or (select private.onflows_org_role(organization_id)) is not null);
create policy plan_authorities_owner_write on public.onflows_plan_authorities for all to authenticated
using (athlete_user_id = (select auth.uid()))
with check (
  athlete_user_id = (select auth.uid())
  and (
    (
      organization_id is not null
      and (select private.onflows_org_role(organization_id)) = 'ATHLETE'
      and coach_user_id is not null
      and exists (
        select 1 from public.onflows_coach_athlete_assignments a
        where a.organization_id = onflows_plan_authorities.organization_id
          and a.coach_user_id = onflows_plan_authorities.coach_user_id
          and a.athlete_user_id = (select auth.uid())
          and a.can_edit_plan
      )
    )
    or (
      organization_id is null
      and coach_user_id is not null
      and exists (
        select 1 from public.onflows_sharing_grants g
        where g.owner_user_id = (select auth.uid())
          and g.viewer_user_id = onflows_plan_authorities.coach_user_id
          and g.edit_plan
      )
    )
  )
);

create policy access_audit_select on public.onflows_access_audit_log for select to authenticated
using (actor_user_id = (select auth.uid()) or (select private.onflows_org_role(organization_id)) = 'ADMIN');
