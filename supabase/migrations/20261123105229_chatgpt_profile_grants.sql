-- Credentials issued to ChatGPT are opaque and valid only for the onFlows MCP resource.
create table public.onflows_chatgpt_grants (
  id uuid primary key default gen_random_uuid(),
  actor uuid not null references auth.users(id) on delete cascade,
  athlete_alias text not null references public.onflows_user_athletes(athlete_alias) on delete cascade,
  scopes text[] not null check (scopes <@ array['plan.read','calendar.write'] and 'plan.read'=any(scopes)),
  code_hash text unique,
  challenge text,
  access_hash text unique,
  access_until timestamptz,
  expires_at timestamptz not null default (now() + interval '5 minutes')
);
create index on public.onflows_chatgpt_grants(actor);
create index on public.onflows_chatgpt_grants(expires_at);
create table public.onflows_chatgpt_refresh (
  digest text primary key,
  grant_id uuid not null references public.onflows_chatgpt_grants(id) on delete cascade,
  used boolean not null default false
);
create index on public.onflows_chatgpt_refresh(grant_id);
alter table public.onflows_chatgpt_grants enable row level security;
alter table public.onflows_chatgpt_refresh enable row level security;
revoke all on public.onflows_chatgpt_grants, public.onflows_chatgpt_refresh from public, anon, authenticated;
grant select,insert,update,delete on public.onflows_chatgpt_grants, public.onflows_chatgpt_refresh to service_role;

create function public.exchange_onflows_chatgpt_token(
  p_kind text, p_digest text, p_challenge text, p_access text, p_refresh text
) returns text[] language plpgsql security invoker set search_path='' as $$
declare
  g public.onflows_chatgpt_grants;
  gid uuid;
begin
  delete from public.onflows_chatgpt_grants where expires_at <= now();
  if p_kind='authorization_code' then
    select * into g from public.onflows_chatgpt_grants
      where code_hash=p_digest and challenge=p_challenge and expires_at>now() for update;
  elsif p_kind='refresh_token' then
    select grant_id into gid from public.onflows_chatgpt_refresh where digest=p_digest;
    select * into g from public.onflows_chatgpt_grants where id=gid and expires_at>now() for update;
    if g.id is not null then
      update public.onflows_chatgpt_refresh set used=true where digest=p_digest and not used;
      if not found then
        delete from public.onflows_chatgpt_grants where id=g.id;
        return null;
      end if;
    end if;
  end if;
  if g.id is null then return null; end if;
  update public.onflows_chatgpt_grants set code_hash=null, challenge=null, access_hash=p_access,
    access_until=least(now()+interval '1 hour', case when p_kind='authorization_code' then now()+interval '30 days' else g.expires_at end),
    expires_at=case when p_kind='authorization_code' then now()+interval '30 days' else g.expires_at end where id=g.id;
  insert into public.onflows_chatgpt_refresh(digest,grant_id) values(p_refresh,g.id);
  return g.scopes;
end $$;
revoke all on function public.exchange_onflows_chatgpt_token(text,text,text,text,text) from public,anon,authenticated;
grant execute on function public.exchange_onflows_chatgpt_token(text,text,text,text,text) to service_role;
