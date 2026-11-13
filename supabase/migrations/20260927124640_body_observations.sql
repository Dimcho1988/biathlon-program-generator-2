-- Expand the existing append-only observation store; preserve RLS and privileges.
begin;
alter table public.onflows_response_entries drop constraint onflows_response_entries_kind_check;
alter table public.onflows_response_entries add constraint onflows_response_entries_kind_check
  check (kind in ('DAILY','SESSION','BLOCK','TEST','WEIGHT','LAB'));

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

commit;
