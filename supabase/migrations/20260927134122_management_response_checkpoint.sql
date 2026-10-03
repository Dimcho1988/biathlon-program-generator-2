-- Include append-only stress/response evidence in the publication checkpoint.
-- No new observation payloads or actor identities are exposed by this projection.
-- Existing authorization, RLS, idempotent retries and server-only RPC grants stay
-- in place. Expensive plan generation remains outside the transaction lock.
begin;

create or replace function public.onflows_management_checkpoint(p_alias text) returns jsonb
language sql stable security invoker set search_path = '' as $$
 select jsonb_build_object(
   'settings', (select jsonb_build_object('hr_zone_bounds',s.hr_zone_bounds,'hrmax_bpm',s.hrmax_bpm,
     'timezone',s.timezone,'planning_profile',s.planning_profile,'planning_calendar',s.planning_calendar,
     'mesocycle_accent_preferences',s.mesocycle_accent_preferences)
     from public.onflows_athlete_settings s where s.athlete_alias=p_alias),
   'generation_id', (select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=p_alias),
   'models', coalesce((select jsonb_agg(to_jsonb(m) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_model_entries
      where athlete_alias=p_alias group by kind,entry_key) m),'[]'::jsonb),
   'responses', coalesce((select jsonb_agg(to_jsonb(r) order by kind,entry_key) from (
      select kind,entry_key,max(revision) as revision from public.onflows_response_entries
      where athlete_alias=p_alias group by kind,entry_key) r),'[]'::jsonb))
$$;
revoke all on function public.onflows_management_checkpoint(text) from public, anon, authenticated;
grant execute on function public.onflows_management_checkpoint(text) to service_role;

create or replace function public.save_onflows_response_entry(
  p_alias text, p_kind text, p_key text, p_day date, p_payload jsonb,
  p_expected_revision integer, p_actor uuid
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare current_revision integer; current_payload jsonb; athlete_user uuid;
begin
  if p_kind is null or p_kind not in ('DAILY','SESSION','BLOCK','TEST','WEIGHT','LAB') then
    raise exception 'Unknown observation kind';
  end if;
  if p_expected_revision is null or p_expected_revision < 0 or p_actor is null then
    raise exception 'Invalid observation revision or actor';
  end if;
  -- Actor identities are supplied only by the authenticated web server.
  select a.user_id into athlete_user from public.onflows_user_athletes a where a.athlete_alias=p_alias and a.is_owner;
  if athlete_user is null then
    raise exception 'Athlete owner is missing';
  end if;
  if p_kind in ('DAILY','SESSION','WEIGHT') and not exists (
    select 1 from public.onflows_user_athletes a where a.athlete_alias=p_alias and a.user_id=p_actor and a.is_owner
  ) then raise exception 'Only the athlete can provide self reports'; end if;
  if p_kind in ('BLOCK','TEST','LAB') and athlete_user <> p_actor and not (
    exists (select 1 from public.onflows_sharing_grants g
      where g.owner_user_id=athlete_user and g.viewer_user_id=p_actor and g.edit_plan)
    or exists (
      select 1 from public.onflows_organization_memberships athlete_membership
      join public.onflows_organization_memberships coach_membership
        on coach_membership.organization_id=athlete_membership.organization_id
      where athlete_membership.user_id=athlete_user and athlete_membership.role='ATHLETE'
        and athlete_membership.status='ACTIVE' and coach_membership.user_id=p_actor
        and coach_membership.status='ACTIVE' and (
          coach_membership.role in ('ADMIN','HEAD_COACH') or exists (
            select 1 from public.onflows_coach_athlete_assignments assignment
            where assignment.organization_id=athlete_membership.organization_id
              and assignment.athlete_user_id=athlete_user and assignment.coach_user_id=p_actor
              and assignment.can_edit_plan
          )
        )
    )
  ) then raise exception 'Observation context requires planning access'; end if;
  -- Match active-plan and draft publication. An observation committed while a
  -- plan is calculated invalidates its checkpoint; once publication takes this
  -- lock no observation can slip between validation and the revision insert.
  -- Always acquire management before the finer entry/block lock.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:' || p_alias, 0));
  -- All block dates share one lock so simultaneous different start dates cannot overlap.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_alias || ':' || p_kind || ':' || case when p_kind='BLOCK' then '*' else p_key end, 0));
  select revision, payload into current_revision,current_payload from public.onflows_response_entries
    where athlete_alias=p_alias and kind=p_kind and entry_key=p_key order by revision desc limit 1;
  current_revision := coalesce(current_revision,0);
  if current_revision <> p_expected_revision then
    if current_payload=p_payload then return jsonb_build_object('revision',current_revision,'saved',true); end if;
    return jsonb_build_object('conflict',true,'revision',current_revision);
  end if;
  if p_kind='BLOCK' and exists (
    select 1 from (
      select distinct on (entry_key) entry_key,payload from public.onflows_response_entries
      where athlete_alias=p_alias and kind='BLOCK' order by entry_key,revision desc
    ) b where b.entry_key<>p_key
      and (p_payload->>'start')::date <= (b.payload->>'recovery_end')::date
      and (p_payload->>'recovery_end')::date >= (b.payload->>'start')::date
  ) then return jsonb_build_object('conflict',true,'revision',current_revision); end if;
  insert into public.onflows_response_entries(athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id)
    values(p_alias,p_kind,p_key,p_day,current_revision+1,p_payload,p_actor);
  return jsonb_build_object('saved',true,'revision',current_revision+1);
end $$;
revoke all on function public.save_onflows_response_entry(text,text,text,date,jsonb,integer,uuid) from public,anon,authenticated;
grant execute on function public.save_onflows_response_entry(text,text,text,date,jsonb,integer,uuid) to service_role;


-- Append a defaulted parameter without leaving an ambiguous PostgREST overload.
-- Legacy calls can still omit the response comparison; new planners supply it.
drop function public.save_onflows_management_entry(text,text,text,jsonb,integer,uuid,integer,uuid,boolean);

create function public.save_onflows_management_entry(
  p_alias text, p_kind text, p_key text, p_payload jsonb, p_expected_revision integer, p_actor uuid,
  p_expected_profile_revision integer default null,
  p_expected_generation_id uuid default null,
  p_check_generation boolean default false,
  p_expected_responses jsonb default null
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
    or p_payload is null or jsonb_typeof(p_payload) <> 'object' or p_check_generation is null
    or (p_expected_responses is not null and jsonb_typeof(p_expected_responses) <> 'array') then
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
    if p_expected_responses is not null and
      (public.onflows_management_checkpoint(p_alias)->'responses') is distinct from p_expected_responses then
      return jsonb_build_object('conflict', true, 'reason', 'INPUTS_CHANGED');
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
    if p_expected_responses is not null then
      frozen_payload := jsonb_set(frozen_payload, '{persistence}',
        frozen_payload->'persistence' || jsonb_build_object('response_revisions', p_expected_responses));
    end if;
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
revoke all on function public.save_onflows_management_entry(text, text, text, jsonb, integer, uuid, integer, uuid, boolean, jsonb)
  from public, anon, authenticated;
grant execute on function public.save_onflows_management_entry(text, text, text, jsonb, integer, uuid, integer, uuid, boolean, jsonb)
  to service_role;

-- Refresh the RPC signature in PostgREST after removing the old overload.
notify pgrst, 'reload schema';
commit;
