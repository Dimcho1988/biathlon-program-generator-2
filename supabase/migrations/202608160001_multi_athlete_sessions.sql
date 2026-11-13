-- Multi-athlete OAuth session handoff for the onFlows cloud pilot.
-- Provider tokens remain encrypted and no raw activity or wellness payloads
-- are stored. Existing pilot connections and snapshots are not modified.

-- A new OAuth flow does not know its onFlows alias until Intervals returns the
-- provider athlete id. Reconnect flows may still bind an existing alias.
alter table public.onflows_oauth_states
  alter column athlete_alias drop not null;

create table if not exists public.onflows_login_tickets (
  ticket_hash text primary key check (length(ticket_hash) = 64),
  athlete_alias text not null references public.onflows_intervals_connections(athlete_alias)
    on update cascade on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table public.onflows_login_tickets enable row level security;
revoke all on table public.onflows_login_tickets from anon, authenticated;
grant all on table public.onflows_login_tickets to service_role;

-- Consume once and delete expired matching rows instead of retaining them.
create or replace function public.consume_onflows_oauth_state(p_nonce_hash text)
returns table (athlete_alias text, redirect_uri text)
language sql
security definer
set search_path = public
as $$
  with consumed as (
    delete from public.onflows_oauth_states
    where nonce_hash = p_nonce_hash
    returning onflows_oauth_states.athlete_alias,
              onflows_oauth_states.redirect_uri,
              onflows_oauth_states.expires_at
  )
  select consumed.athlete_alias, consumed.redirect_uri
  from consumed
  where consumed.expires_at >= now();
$$;

revoke all on function public.consume_onflows_oauth_state(text) from public, anon, authenticated;
grant execute on function public.consume_onflows_oauth_state(text) to service_role;

create or replace function public.consume_onflows_login_ticket(p_ticket_hash text)
returns table (athlete_alias text)
language sql
security definer
set search_path = public
as $$
  with consumed as (
    delete from public.onflows_login_tickets
    where ticket_hash = p_ticket_hash
    returning onflows_login_tickets.athlete_alias,
              onflows_login_tickets.expires_at
  )
  select consumed.athlete_alias
  from consumed
  where consumed.expires_at >= now();
$$;

revoke all on function public.consume_onflows_login_ticket(text) from public, anon, authenticated;
grant execute on function public.consume_onflows_login_ticket(text) to service_role;
