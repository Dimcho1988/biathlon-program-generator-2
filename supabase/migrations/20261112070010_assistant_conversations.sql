begin;

-- Private conversation data are deliberately separate from scientific records.
create table public.onflows_assistant_conversations (
  id uuid primary key default gen_random_uuid(),
  athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on delete cascade,
  actor_id uuid not null references auth.users(id) on delete cascade,
  permission_scope jsonb not null,
  state jsonb not null default '{}'::jsonb check (octet_length(state::text) <= 16000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create index on public.onflows_assistant_conversations(actor_id, athlete_alias, updated_at desc);
create index on public.onflows_assistant_conversations(expires_at);
create table public.onflows_assistant_turns (
  operation_id uuid primary key,
  conversation_id uuid not null references public.onflows_assistant_conversations(id) on delete cascade,
  actor_id uuid not null,
  athlete_alias text not null,
  user_message text not null check (octet_length(user_message) <= 8000),
  response jsonb check (octet_length(response::text) <= 60000),
  status text not null default 'PENDING' check (status in ('PENDING','COMPLETED')),
  created_at timestamptz not null default clock_timestamp(),
  finished_at timestamptz
);
create index on public.onflows_assistant_turns(conversation_id, created_at desc);
create index on public.onflows_assistant_turns(actor_id, created_at desc);
alter table public.onflows_assistant_conversations enable row level security;
alter table public.onflows_assistant_turns enable row level security;
revoke all on public.onflows_assistant_conversations, public.onflows_assistant_turns from public, anon, authenticated;
grant select, insert, update, delete on public.onflows_assistant_conversations, public.onflows_assistant_turns to service_role;

-- Only the authenticated web boundary supplies p_actor. Access is re-read from
-- the database, even for an already completed/cached response.
create function public.onflows_assistant_conversation(
  p_action text, p_alias text, p_actor uuid, p_conversation uuid default null,
  p_operation uuid default null, p_payload jsonb default '{}'::jsonb,
  p_retention_days integer default 30
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  permission jsonb;
  conversation public.onflows_assistant_conversations%rowtype;
  turn public.onflows_assistant_turns%rowtype;
  selected_conversation uuid := p_conversation;
  messages jsonb;
  pending_operation jsonb;
begin
  permission := public.onflows_assistant_access(p_alias,p_actor);
  if not (coalesce((permission->>'can_view_plan')::boolean,false)
          or coalesce((permission->>'can_view_recovery')::boolean,false)) then
    return jsonb_build_object('error','forbidden');
  end if;
  if p_retention_days not between 1 and 365 then return jsonb_build_object('error','invalid'); end if;
  if p_action='LATEST' then
    select c.id into selected_conversation from public.onflows_assistant_conversations c
      where c.actor_id=p_actor and c.athlete_alias=p_alias and c.expires_at>now() and c.permission_scope=permission
      order by c.updated_at desc limit 1;
    if selected_conversation is null then
      return jsonb_build_object('conversation_id',null,'messages','[]'::jsonb);
    end if;
  end if;
  if p_action = 'BEGIN' and (p_operation is null or length(coalesce(p_payload->>'message','')) not between 1 and 4000) then
    return jsonb_build_object('error','invalid');
  end if;
  if p_operation is not null then
    perform pg_advisory_xact_lock(hashtextextended('assistant-turn:'||p_actor::text||':'||p_operation::text,0));
  end if;
  -- The operation identity survives a lost initial response where the client
  -- still has no conversation UUID. Never execute that turn a second time.
  if p_action in ('BEGIN','STATUS') and p_operation is not null then
    select * into turn from public.onflows_assistant_turns where operation_id=p_operation;
    if found then
      if turn.actor_id<>p_actor or turn.athlete_alias<>p_alias or
        (selected_conversation is not null and selected_conversation<>turn.conversation_id) then
        return jsonb_build_object('error','not_found');
      end if;
      if p_action='BEGIN' and turn.user_message<>p_payload->>'message' then
        return jsonb_build_object('error','operation_conflict');
      end if;
      selected_conversation := turn.conversation_id;
    elsif p_action='STATUS' then return jsonb_build_object('status','unknown');
    end if;
  end if;
  if selected_conversation is null and p_action='BEGIN' then
    insert into public.onflows_assistant_conversations(athlete_alias,actor_id,permission_scope,expires_at)
      values(p_alias,p_actor,permission,now()+make_interval(days=>p_retention_days)) returning id into selected_conversation;
  end if;
  select * into conversation from public.onflows_assistant_conversations where id=selected_conversation
    and actor_id=p_actor and athlete_alias=p_alias for update;
  if not found or conversation.expires_at<=now() then return jsonb_build_object('error','not_found'); end if;
  if conversation.permission_scope<>permission then return jsonb_build_object('error','scope_changed'); end if;
  if p_action='CLEAR' then
    delete from public.onflows_assistant_conversations where id=selected_conversation;
    return jsonb_build_object('cleared',true);
  elsif p_action='BEGIN' then
    if turn.operation_id is not null then
      return jsonb_build_object('conversation_id',selected_conversation,'replayed',true,'status',turn.status,'response',turn.response);
    end if;
    if exists(select 1 from public.onflows_assistant_turns where conversation_id=conversation.id and status='PENDING') then
      return jsonb_build_object('error','busy');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('assistant-rate:'||p_actor::text,0));
    if (select count(*) from public.onflows_assistant_turns where actor_id=p_actor and created_at>now()-interval '1 minute')>=8
      or (select count(*) from public.onflows_assistant_turns where actor_id=p_actor and created_at>now()-interval '1 hour')>=80 then
      return jsonb_build_object('error','rate_limit');
    end if;
    insert into public.onflows_assistant_turns(operation_id,conversation_id,actor_id,athlete_alias,user_message)
      values(p_operation,selected_conversation,p_actor,p_alias,p_payload->>'message');
  elsif p_action='FINISH' then
    if jsonb_typeof(p_payload->'response')<>'object' or octet_length((p_payload->'response')::text)>60000
      or jsonb_typeof(p_payload->'state')<>'object' then return jsonb_build_object('error','invalid'); end if;
    update public.onflows_assistant_turns set response=p_payload->'response', status='COMPLETED',finished_at=clock_timestamp()
      where operation_id=p_operation and conversation_id=conversation.id and status='PENDING';
    if not found then return jsonb_build_object('error','operation_conflict'); end if;
    update public.onflows_assistant_conversations set state=p_payload->'state',updated_at=now(),
      expires_at=now()+make_interval(days=>p_retention_days) where id=conversation.id;
    return p_payload->'response';
  elsif p_action='STATUS' then
    return jsonb_build_object('conversation_id',selected_conversation,'status',turn.status,'response',turn.response,'created_at',turn.created_at);
  elsif p_action not in ('READ','LATEST') then return jsonb_build_object('error','invalid');
  end if;
  select coalesce(jsonb_agg(row_to_json(m) order by m.created_at),'[]'::jsonb) into messages from (
    select user_message, response->>'message' as assistant_message, created_at from public.onflows_assistant_turns
      where conversation_id=conversation.id and status='COMPLETED' order by created_at desc limit 12
  ) m;
  select jsonb_build_object('operation_id',t.operation_id,'conversation_id',t.conversation_id,'message',t.user_message)
    into pending_operation from public.onflows_assistant_turns t where t.conversation_id=conversation.id and t.status='PENDING'
    order by t.created_at desc limit 1;
  return jsonb_build_object('conversation_id',selected_conversation,'messages',messages,'state',conversation.state,
    'status','PENDING','retention_days',p_retention_days,'expires_at',conversation.expires_at,'pending_operation',pending_operation);
end $$;
revoke all on function public.onflows_assistant_conversation(text,text,uuid,uuid,uuid,jsonb,integer) from public,anon,authenticated;
grant execute on function public.onflows_assistant_conversation(text,text,uuid,uuid,uuid,jsonb,integer) to service_role;

-- Bounded maintenance, invoked explicitly by the existing worker maintenance.
create function public.cleanup_onflows_assistant_conversations(p_limit integer default 200)
returns integer language plpgsql security invoker set search_path = '' as $$
declare removed integer;
begin
  with expired as (select id from public.onflows_assistant_conversations where expires_at<now()
    order by expires_at limit greatest(1,least(p_limit,1000)) for update skip locked)
  delete from public.onflows_assistant_conversations c using expired e where c.id=e.id;
  get diagnostics removed = row_count;
  return removed;
end $$;
revoke all on function public.cleanup_onflows_assistant_conversations(integer) from public,anon,authenticated;
grant execute on function public.cleanup_onflows_assistant_conversations(integer) to service_role;
commit;
