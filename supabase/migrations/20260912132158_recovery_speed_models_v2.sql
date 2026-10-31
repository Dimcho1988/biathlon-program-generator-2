-- Scientific model settings and selected maximal tests. Append-only revisions.
create table public.onflows_model_entries (
  athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
  kind text not null check (kind in ('RECOVERY','SPEED_TEST')),
  entry_key text not null check (length(entry_key) between 1 and 80),
  revision integer not null check (revision > 0),
  payload jsonb not null check (jsonb_typeof(payload)='object' and octet_length(payload::text)<=16384),
  actor_id uuid not null references auth.users(id),
  recorded_at timestamptz not null default now(),
  primary key (athlete_alias,kind,entry_key,revision)
);
create index on public.onflows_model_entries(actor_id);
alter table public.onflows_model_entries enable row level security;
revoke all on public.onflows_model_entries from public,anon,authenticated;
grant select,insert on public.onflows_model_entries to service_role;

create function public.save_onflows_model_entry(
  p_alias text,p_kind text,p_key text,p_payload jsonb,p_expected_revision integer,p_actor uuid
) returns jsonb language plpgsql security invoker set search_path='' as $$
declare current_revision integer; current_payload jsonb; athlete_user uuid;
begin
  if p_kind not in ('RECOVERY','SPEED_TEST') or p_expected_revision is null or p_expected_revision<0 or p_actor is null then
    raise exception 'Invalid model entry';
  end if;
  if p_kind='RECOVERY' and p_key<>'recovery-v2' then raise exception 'Invalid configuration identity'; end if;
  select a.user_id into athlete_user from public.onflows_user_athletes a where a.athlete_alias=p_alias and a.is_owner;
  if athlete_user is null then raise exception 'Athlete owner is missing'; end if;
  if athlete_user<>p_actor and not (
    exists(select 1 from public.onflows_sharing_grants g where g.owner_user_id=athlete_user and g.viewer_user_id=p_actor and g.edit_plan)
    or exists(
      select 1 from public.onflows_organization_memberships a
      join public.onflows_organization_memberships c on c.organization_id=a.organization_id
      where a.user_id=athlete_user and a.role='ATHLETE' and a.status='ACTIVE'
        and c.user_id=p_actor and c.status='ACTIVE' and (
          c.role in ('ADMIN','HEAD_COACH') or exists(
            select 1 from public.onflows_coach_athlete_assignments assignment
            where assignment.organization_id=a.organization_id and assignment.athlete_user_id=athlete_user
              and assignment.coach_user_id=p_actor and assignment.can_edit_plan
          )
        )
    )
  ) then raise exception 'Model changes require planning access'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_alias||':'||p_kind||':'||p_key,0));
  select revision,payload into current_revision,current_payload from public.onflows_model_entries
    where athlete_alias=p_alias and kind=p_kind and entry_key=p_key order by revision desc limit 1;
  current_revision:=coalesce(current_revision,0);
  if current_revision<>p_expected_revision then
    if current_payload=p_payload then return jsonb_build_object('saved',true,'revision',current_revision); end if;
    return jsonb_build_object('conflict',true,'revision',current_revision);
  end if;
  insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
    values(p_alias,p_kind,p_key,current_revision+1,p_payload,p_actor);
  return jsonb_build_object('saved',true,'revision',current_revision+1);
end $$;
revoke all on function public.save_onflows_model_entry(text,text,text,jsonb,integer,uuid) from public,anon,authenticated;
grant execute on function public.save_onflows_model_entry(text,text,text,jsonb,integer,uuid) to service_role;
