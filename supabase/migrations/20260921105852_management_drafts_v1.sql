-- Immutable management profiles and seven-day review drafts. No publication.
create table public.onflows_management_entries (
  athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
  kind text not null check (kind in ('PROFILE', 'DRAFT')),
  entry_key text not null check (length(entry_key) between 1 and 80),
  revision integer not null check (revision > 0),
  payload jsonb not null check (
    jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 1048576
    and (kind <> 'PROFILE' or octet_length(payload::text) <= 65536)
  ),
  actor_id uuid not null references auth.users(id),
  recorded_at timestamptz not null default clock_timestamp(),
  primary key (athlete_alias, kind, entry_key, revision),
  check ((kind = 'PROFILE' and entry_key = 'management-profile-v1')
    or (kind = 'DRAFT' and entry_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      and payload->>'start_date' is not null and payload->>'start_date' = entry_key))
);
create index onflows_management_entries_history
  on public.onflows_management_entries(athlete_alias, kind, recorded_at desc, entry_key desc, revision desc);
create index onflows_management_entries_actor on public.onflows_management_entries(actor_id);
alter table public.onflows_management_entries enable row level security;
revoke all on public.onflows_management_entries from public, anon, authenticated, service_role;
grant select, insert on public.onflows_management_entries to service_role;

create function public.save_onflows_management_entry(
  p_alias text, p_kind text, p_key text, p_payload jsonb, p_expected_revision integer, p_actor uuid,
  p_expected_profile_revision integer default null,
  p_expected_generation_id uuid default null,
  p_check_generation boolean default false
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  current_revision integer;
  current_profile_revision integer;
  current_profile jsonb;
  current_payload jsonb;
  current_recorded_at timestamptz;
  athlete_user uuid;
  active_generation uuid;
  frozen_payload jsonb := p_payload;
  saved_at timestamptz;
begin
  if p_alias is null or p_kind is null or p_kind not in ('PROFILE', 'DRAFT')
    or p_key is null or length(p_key) not between 1 and 80
    or p_expected_revision is null or p_expected_revision < 0 or p_actor is null
    or p_payload is null or jsonb_typeof(p_payload) <> 'object' or p_check_generation is null then
    raise exception 'Invalid management entry' using errcode = '22023';
  end if;
  if p_kind = 'PROFILE' and p_key <> 'management-profile-v1' then
    raise exception 'Invalid planning profile identity' using errcode = '22023';
  end if;
  if p_kind = 'DRAFT' and (
    p_expected_profile_revision is null or p_expected_profile_revision < 1
    or p_payload->>'start_date' is distinct from p_key
    or p_key !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  ) then
    raise exception 'Invalid planning draft identity or profile revision' using errcode = '22023';
  end if;
  if p_kind = 'DRAFT' then
    -- Also reject syntactically correct but impossible dates.
    perform p_key::date;
  end if;

  select a.user_id into athlete_user from public.onflows_user_athletes a
    where a.athlete_alias = p_alias and a.is_owner;
  if athlete_user is null then
    raise exception 'Athlete owner is missing' using errcode = '42501';
  end if;
  if athlete_user <> p_actor and not (
    exists(select 1 from public.onflows_sharing_grants g
      where g.owner_user_id = athlete_user and g.viewer_user_id = p_actor and g.edit_plan)
    or exists(
      select 1 from public.onflows_organization_memberships a
      join public.onflows_organization_memberships c on c.organization_id = a.organization_id
      where a.user_id = athlete_user and a.role = 'ATHLETE' and a.status = 'ACTIVE'
        and c.user_id = p_actor and c.status = 'ACTIVE' and (
          c.role in ('ADMIN', 'HEAD_COACH') or (c.role = 'COACH' and exists(
            select 1 from public.onflows_coach_athlete_assignments assignment
            where assignment.organization_id = a.organization_id
              and assignment.athlete_user_id = athlete_user
              and assignment.coach_user_id = p_actor and assignment.can_edit_plan
          ))
        )
    )
  ) then
    raise exception 'Management changes require planning access' using errcode = '42501';
  end if;

  -- One lock for all kinds: a profile edit and a draft append cannot race.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:' || p_alias, 0));
  if p_kind = 'DRAFT' then
    select revision, payload into current_profile_revision, current_profile
      from public.onflows_management_entries
      where athlete_alias = p_alias and kind = 'PROFILE' and entry_key = 'management-profile-v1'
      order by revision desc limit 1;
    current_profile_revision := coalesce(current_profile_revision, 0);
    if current_profile_revision <> p_expected_profile_revision then
      return jsonb_build_object('conflict', true, 'reason', 'PROFILE_CHANGED', 'revision', current_profile_revision);
    end if;
    if p_check_generation then
      -- This is a read checkpoint, not an activation lock. Analysis may advance
      -- afterwards; readers compare the frozen input fingerprint to live state.
      -- Preserve the existing SELECT-only grant on the analysis state table.
      select active_generation_id into active_generation from public.onflows_athlete_analysis_state
        where athlete_alias = p_alias;
      if active_generation is distinct from p_expected_generation_id then
        return jsonb_build_object('conflict', true, 'reason', 'ANALYSIS_CHANGED');
      end if;
    end if;
    frozen_payload := p_payload || jsonb_build_object('persistence', jsonb_build_object(
      'profile_revision', current_profile_revision, 'profile', current_profile,
      'analysis_generation_id', active_generation, 'analysis_checked', p_check_generation,
      'analysis_check_mode', case when p_check_generation then 'READ_COMPARISON' else 'NOT_CHECKED' end
    ));
  end if;

  select revision, payload, recorded_at into current_revision, current_payload, current_recorded_at
    from public.onflows_management_entries
    where athlete_alias = p_alias and kind = p_kind and entry_key = p_key
    order by revision desc limit 1;
  current_revision := coalesce(current_revision, 0);
  if current_revision <> p_expected_revision then
    -- A lost-response retry is harmless; a genuinely different edit conflicts.
    if current_payload = frozen_payload then
      return jsonb_build_object('saved', true, 'revision', current_revision, 'entry_key', p_key,
        'payload', current_payload, 'recorded_at', current_recorded_at, 'unchanged', true);
    end if;
    return jsonb_build_object('conflict', true, 'reason', 'REVISION_CHANGED', 'revision', current_revision);
  end if;
  insert into public.onflows_management_entries(athlete_alias, kind, entry_key, revision, payload, actor_id)
    values(p_alias, p_kind, p_key, current_revision + 1, frozen_payload, p_actor)
    returning recorded_at into saved_at;
  return jsonb_build_object('saved', true, 'revision', current_revision + 1, 'entry_key', p_key,
    'payload', frozen_payload, 'recorded_at', saved_at);
end $$;
revoke all on function public.save_onflows_management_entry(text, text, text, jsonb, integer, uuid, integer, uuid, boolean)
  from public, anon, authenticated;
grant execute on function public.save_onflows_management_entry(text, text, text, jsonb, integer, uuid, integer, uuid, boolean)
  to service_role;
