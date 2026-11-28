-- A lifetime allowance, not a recurring monthly budget. Funding is an explicit
-- operator action; neither the browser nor the application RPC can add credit.
create table onflows_private.assistant_budget_accounts (
 budget_id text primary key check (budget_id ~ '^[a-z0-9][a-z0-9_-]{0,99}$'),
 limit_nano_usd bigint not null default 0 check (limit_nano_usd between 0 and 1000000000000),
 charged_nano_usd bigint not null default 0 check (charged_nano_usd >= 0),
 reserved_nano_usd bigint not null default 0 check (reserved_nano_usd >= 0),
 enabled boolean not null default false,
 created_at timestamptz not null default clock_timestamp(),
 updated_at timestamptz not null default clock_timestamp(),
 check (charged_nano_usd + reserved_nano_usd <= limit_nano_usd)
);

create table onflows_private.assistant_budget_reservations (
 budget_id text not null references onflows_private.assistant_budget_accounts(budget_id),
 request_id uuid not null,
 reserved_nano_usd bigint not null check (reserved_nano_usd between 1 and 1000000000000),
 settled_nano_usd bigint check (settled_nano_usd between 0 and reserved_nano_usd),
 created_at timestamptz not null default clock_timestamp(),
 settled_at timestamptz,
 primary key (budget_id, request_id),
 check ((settled_nano_usd is null) = (settled_at is null))
);

alter table onflows_private.assistant_budget_accounts enable row level security;
alter table onflows_private.assistant_budget_reservations enable row level security;
revoke all on onflows_private.assistant_budget_accounts from public, anon, authenticated, service_role;
revoke all on onflows_private.assistant_budget_reservations from public, anon, authenticated, service_role;
grant select on onflows_private.assistant_budget_accounts to service_role;
grant update (charged_nano_usd, reserved_nano_usd, updated_at)
 on onflows_private.assistant_budget_accounts to service_role;
grant select, insert on onflows_private.assistant_budget_reservations to service_role;
grant update (settled_nano_usd, settled_at)
 on onflows_private.assistant_budget_reservations to service_role;

-- Zero credit remains the safe default in a clean installation. Deployment does
-- not silently fund or enable the allowance, and no reset job is installed.
insert into onflows_private.assistant_budget_accounts(budget_id)
 values ('onflows-staging-luna-pilot-v1');

create function public.onflows_assistant_budget(
 p_action text, p_budget_id text, p_request_id uuid, p_amount_nano_usd bigint
) returns jsonb
language plpgsql security invoker set search_path = ''
as $$
declare
 account onflows_private.assistant_budget_accounts%rowtype;
 reservation onflows_private.assistant_budget_reservations%rowtype;
begin
 if p_action is null or p_action not in ('RESERVE','SETTLE')
    or p_budget_id is null or p_budget_id !~ '^[a-z0-9][a-z0-9_-]{0,99}$'
    or p_request_id is null or p_amount_nano_usd is null
    or p_amount_nano_usd < 0 or p_amount_nano_usd > 1000000000000
    or (p_action='RESERVE' and p_amount_nano_usd=0) then
  return jsonb_build_object('error','invalid_budget');
 end if;

 -- Every mutation locks the account first. Concurrent API workers cannot admit
 -- the same remaining money, or race settlement against another reservation.
 select * into account from onflows_private.assistant_budget_accounts
  where budget_id=p_budget_id for update;
 if not found then
  return jsonb_build_object('error','budget_unavailable');
 end if;
 select * into reservation from onflows_private.assistant_budget_reservations
  where budget_id=p_budget_id and request_id=p_request_id;

 if p_action='RESERVE' then
  if found then
   -- A lost database response is not authorization to send a second paid call.
   return jsonb_build_object('error','invalid_budget');
  end if;
  if not account.enabled or p_amount_nano_usd >
      account.limit_nano_usd-account.charged_nano_usd-account.reserved_nano_usd then
   return jsonb_build_object('error','budget_exhausted');
  end if;
  insert into onflows_private.assistant_budget_reservations
   (budget_id,request_id,reserved_nano_usd)
   values (p_budget_id,p_request_id,p_amount_nano_usd);
  update onflows_private.assistant_budget_accounts
   set reserved_nano_usd=reserved_nano_usd+p_amount_nano_usd,updated_at=clock_timestamp()
   where budget_id=p_budget_id;
  return jsonb_build_object('reserved',true);
 end if;

 if not found or p_amount_nano_usd > reservation.reserved_nano_usd then
  return jsonb_build_object('error','invalid_budget');
 end if;
 if reservation.settled_nano_usd is not null then
  if reservation.settled_nano_usd=p_amount_nano_usd then
   return jsonb_build_object('settled',true);
  end if;
  return jsonb_build_object('error','invalid_budget');
 end if;
 update onflows_private.assistant_budget_reservations
  set settled_nano_usd=p_amount_nano_usd,settled_at=clock_timestamp()
  where budget_id=p_budget_id and request_id=p_request_id;
 update onflows_private.assistant_budget_accounts
  set reserved_nano_usd=reserved_nano_usd-reservation.reserved_nano_usd,
      charged_nano_usd=charged_nano_usd+p_amount_nano_usd,updated_at=clock_timestamp()
  where budget_id=p_budget_id;
 return jsonb_build_object('settled',true);
end;
$$;

revoke all on function public.onflows_assistant_budget(text,text,uuid,bigint)
 from public, anon, authenticated;
grant execute on function public.onflows_assistant_budget(text,text,uuid,bigint)
 to service_role;
