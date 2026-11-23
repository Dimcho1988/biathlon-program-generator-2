-- A NULL primary athlete identifies an explicitly selected coaching roster.
alter table public.onflows_chatgpt_grants alter column athlete_alias drop not null;

create table public.onflows_chatgpt_athletes (
  grant_id uuid not null references public.onflows_chatgpt_grants(id) on delete cascade,
  athlete_alias text not null references public.onflows_user_athletes(athlete_alias) on delete cascade,
  allow_write boolean not null,
  primary key (grant_id, athlete_alias)
);
create index on public.onflows_chatgpt_athletes(athlete_alias);
alter table public.onflows_chatgpt_athletes enable row level security;
revoke all on public.onflows_chatgpt_athletes from public, anon, authenticated;
grant select,insert on public.onflows_chatgpt_athletes to service_role;

create function public.create_onflows_chatgpt_roster(
  p_actor uuid, p_aliases text[], p_write boolean, p_code_hash text, p_challenge text
) returns boolean language plpgsql security invoker set search_path='' as $$
declare
  grant_key uuid;
  editable text[];
begin
  if coalesce(cardinality(p_aliases),0)<2 then return false; end if;
  select array_agg(alias) filter (where (rights->>'can_edit_plan')::boolean)
    into editable
    from (select alias, public.onflows_assistant_access(alias,p_actor) as rights
          from unnest(p_aliases) as a(alias)) as selected
    having bool_and((rights->>'can_view_plan')::boolean);
  if not found then return false; end if;
  insert into public.onflows_chatgpt_grants(actor,scopes,code_hash,challenge)
    values(p_actor,case when p_write and cardinality(editable)>0 then array['plan.read','calendar.write']
                        else array['plan.read'] end,p_code_hash,p_challenge)
    returning id into grant_key;
  insert into public.onflows_chatgpt_athletes(grant_id,athlete_alias,allow_write)
    select grant_key,alias,p_write and coalesce(alias=any(editable),false) from unnest(p_aliases) as a(alias);
  return true;
end $$;
revoke all on function public.create_onflows_chatgpt_roster(uuid,text[],boolean,text,text) from public, anon, authenticated;
grant execute on function public.create_onflows_chatgpt_roster(uuid,text[],boolean,text,text) to service_role;
