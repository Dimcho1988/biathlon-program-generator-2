-- Keep saved programs usable while worker projections refresh. No access,
-- scientific freshness, lease ownership or publication fence is weakened.
alter table onflows_private.prepared_views add column last_requested_at timestamptz;

create or replace function onflows_private.prepared_view(
 p_operation text,p_alias text default null,p_kind text default null,p_parameters jsonb default '{}',
 p_token uuid default null,p_revision bigint default null,p_day date default null,
 p_model_key text default null,p_payload jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare j onflows_private.prepared_views%rowtype; src onflows_private.prepared_view_sources%rowtype;
 today_local date; zone text; params jsonb; k text; r bigint; generation uuid; read_state record;
begin
 if p_operation is null or p_operation not in ('READ','CLAIM','RENEW','STORE','FAIL') then
  raise exception 'Invalid prepared view operation' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') and (p_model_key is null or p_model_key!~'^[a-f0-9]{64}$') then
  raise exception 'Invalid prepared view model' using errcode='22023'; end if;
 if p_operation in ('READ','CLAIM','STORE') then
  perform onflows_private.flush_prepared_view_events(p_alias);
 end if;
 if p_operation='CLAIM' then
  if p_token is null then raise exception 'Worker token required' using errcode='22023'; end if;
  select v.* into j from onflows_private.prepared_views v
   join onflows_private.prepared_view_sources s using(athlete_alias)
   join public.onflows_athlete_settings a using(athlete_alias)
   join public.onflows_athlete_analysis_state active using(athlete_alias)
   where active.active_generation_id is not null and (p_alias is null or v.athlete_alias=p_alias)
    and (v.lease_until is null or v.lease_until<=clock_timestamp())
    and (v.payload is null or v.prepared_revision<>onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
     or v.evaluated_on is distinct from (clock_timestamp() at time zone a.timezone)::date or v.model_key is distinct from p_model_key)
    and (v.available_at<=clock_timestamp()
     or v.claimed_revision is distinct from onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
     or v.claimed_day is distinct from (clock_timestamp() at time zone a.timezone)::date or v.claimed_model_key is distinct from p_model_key)
   -- Failed projections retry with a bounded cooldown. An old transient failure
   -- must not leave READ reporting preparation forever after three attempts.
   order by case when v.last_requested_at > clock_timestamp()-interval '5 minutes' then 0 else 1 end,
    case v.kind when 'management-state' then 0 when 'management-active' then 0
    when 'management-view' then 1 when 'management-profile' then 1 when 'speed-load' then 4
    when 'speed-preview' then 3 else 2 end,v.is_default desc,v.requested_at
   limit 1 for update of v skip locked;
  if not found then return null; end if;
  select * into src from onflows_private.prepared_view_sources where athlete_alias=j.athlete_alias;
  select timezone into zone from public.onflows_athlete_settings where athlete_alias=j.athlete_alias;
  select active_generation_id into generation from public.onflows_athlete_analysis_state where athlete_alias=j.athlete_alias;
  today_local:=(clock_timestamp() at time zone zone)::date;
  r:=onflows_private.prepared_view_revision(j.kind,src.model_revision,src.context_revision);
  update onflows_private.prepared_views set lease_token=p_token,lease_until=clock_timestamp()+interval '90 seconds',
   attempts=case when claimed_revision=r and claimed_day=today_local and claimed_model_key=p_model_key then attempts+1 else 1 end,
   claimed_revision=r,claimed_day=today_local,claimed_model_key=p_model_key,claimed_generation_id=generation,error_code=null
   where athlete_alias=j.athlete_alias and kind=j.kind and parameter_key=j.parameter_key;
  return jsonb_build_object('athlete_alias',j.athlete_alias,'kind',j.kind,'parameters',j.parameters,'revision',r,'day',today_local);
 end if;
 if p_alias is null or p_kind is null or jsonb_typeof(p_parameters) is distinct from 'object'
  or octet_length(p_parameters::text)>16384 then raise exception 'Invalid prepared view scope' using errcode='22023'; end if;
 select timezone into zone from public.onflows_athlete_settings where athlete_alias=p_alias;
 if not found then return null; end if;
 today_local:=(clock_timestamp() at time zone zone)::date;
 params:=onflows_private.prepared_view_parameters(p_kind,p_parameters,today_local);
 k:=md5(params::text);
 if p_operation='READ' then
  if not exists(select 1 from public.onflows_athlete_analysis_state where athlete_alias=p_alias and active_generation_id is not null) then
   return jsonb_build_object('ready',false); end if;
  if not exists(select 1 from onflows_private.prepared_view_sources where athlete_alias=p_alias) then
   perform onflows_private.request_prepared_views(p_alias); end if;
  if not exists(select 1 from onflows_private.prepared_views where athlete_alias=p_alias and kind=p_kind and parameter_key=k) then
   -- Only reproducible derived variants are evicted, never athlete evidence.
   delete from onflows_private.prepared_views where (athlete_alias,kind,parameter_key) in (
    select athlete_alias,kind,parameter_key from onflows_private.prepared_views where athlete_alias=p_alias
     and not is_default and (lease_until is null or lease_until<=clock_timestamp())
     and prepared_at<clock_timestamp()-interval '7 days' order by prepared_at limit 16);
   if (select count(*) from onflows_private.prepared_views where athlete_alias=p_alias)>=128 then
    return jsonb_build_object('ready',false,'limited',true); end if;
   insert into onflows_private.prepared_views(athlete_alias,kind,parameter_key,parameters)
    values(p_alias,p_kind,k,params) on conflict do nothing;
  end if;
  -- Prioritize pages a person is currently reading without a write on every
  -- poll. This affects scheduling only, never revision/publication fences.
  update onflows_private.prepared_views set last_requested_at=clock_timestamp()
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k and parameters=params
    and (last_requested_at is null or last_requested_at<clock_timestamp()-interval '30 seconds');
  -- Observe payload, source epoch and absence of pending inputs in one snapshot.
  select v.payload,v.prepared_at,v.evaluated_on,v.model_key,v.prepared_revision,
   onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision) as current_revision,
   (v.payload is not null and v.prepared_revision=
   onflows_private.prepared_view_revision(v.kind,s.model_revision,s.context_revision)
   and v.evaluated_on=today_local and v.model_key=p_model_key
   and not exists(select 1 from onflows_private.prepared_view_events e where e.athlete_alias=p_alias
    and (e.models or onflows_private.prepared_view_revision(p_kind,0,1)=1))) as ready
   into read_state from onflows_private.prepared_views v join onflows_private.prepared_view_sources s using(athlete_alias)
   where v.athlete_alias=p_alias and v.kind=p_kind and v.parameter_key=k and v.parameters=params;
  if read_state.ready then
   return jsonb_build_object('ready',true,'athlete_alias',p_alias,'kind',p_kind,'payload',read_state.payload,'prepared_at',read_state.prepared_at);
  end if;
  -- Preserve access to the last saved program while its page is refreshed.
  -- Scientific-only predictions remain strict; this result is explicitly NOT
  -- ready/current and consumers must retain the preparation provenance.
  if p_kind in ('management-view','management-active') and read_state.payload is not null
   and not (read_state.payload ? 'prepared_error') and read_state.prepared_at is not null
   and read_state.evaluated_on is not null then
   return jsonb_build_object('ready',false,'saved',true,'athlete_alias',p_alias,'kind',p_kind,
    'payload',read_state.payload,'preparation',jsonb_build_object('status','UPDATING',
     'prepared_at',read_state.prepared_at,'evaluated_on',read_state.evaluated_on,
     'reason',case when read_state.prepared_revision is distinct from read_state.current_revision then 'INPUTS_CHANGED'
      when read_state.evaluated_on is distinct from today_local then 'DAY_CHANGED'
      when read_state.model_key is distinct from p_model_key then 'MODEL_CHANGED' else 'PREPARATION_PENDING' end));
  end if;
  return jsonb_build_object('ready',false,'day',today_local);
 end if;
 -- Lock the small source epoch before the queue row. Input writers append
 -- events independently; no plan/input locks are needed for publication.
 select * into src from onflows_private.prepared_view_sources where athlete_alias=p_alias for share;
 select * into j from onflows_private.prepared_views where athlete_alias=p_alias and kind=p_kind and parameter_key=k
  and parameters=params for update;
 if not found or p_token is null or j.lease_token is distinct from p_token or j.lease_until<=clock_timestamp() or j.lease_until is null then
  return jsonb_build_object('state','LOST'); end if;
 if p_operation='RENEW' then
  update onflows_private.prepared_views set lease_until=clock_timestamp()+interval '90 seconds'
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('state','RUNNING');
 elsif p_operation='FAIL' then
  update onflows_private.prepared_views set lease_token=null,lease_until=null,
   available_at=clock_timestamp()+make_interval(secs=>least(300,30*power(2,least(greatest(j.attempts-1,0),4)))::double precision),error_code='PREPARATION_DEFERRED'
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('state','DEFERRED');
 end if;
 r:=onflows_private.prepared_view_revision(p_kind,src.model_revision,src.context_revision);
 select active_generation_id into generation from public.onflows_athlete_analysis_state where athlete_alias=p_alias;
 if p_revision is distinct from r or p_revision is distinct from j.claimed_revision or p_day is distinct from today_local
  or p_day is distinct from j.claimed_day or p_model_key is distinct from j.claimed_model_key
  or generation is distinct from j.claimed_generation_id or jsonb_typeof(p_payload) is distinct from 'object'
  or octet_length(p_payload::text)>8388608 then
  update onflows_private.prepared_views set lease_token=null,lease_until=null,available_at=clock_timestamp()
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('stored',false);
 end if;
 update onflows_private.prepared_views set prepared_revision=r,evaluated_on=p_day,model_key=p_model_key,payload=p_payload,
  prepared_at=clock_timestamp(),available_at=((today_local+1)::timestamp at time zone zone),
  lease_token=null,lease_until=null,error_code=null
  where athlete_alias=p_alias and kind=p_kind and parameter_key=k
   and not exists(select 1 from onflows_private.prepared_view_events e where e.athlete_alias=p_alias
    and (e.models or onflows_private.prepared_view_revision(p_kind,0,1)=1));
 if not found then
  update onflows_private.prepared_views set lease_token=null,lease_until=null,available_at=clock_timestamp()
   where athlete_alias=p_alias and kind=p_kind and parameter_key=k;
  return jsonb_build_object('stored',false);
 end if;
 return jsonb_build_object('stored',true);
end $$;
-- The replaced private routine retains its existing service-only grant.
-- Public/anonymous callers cannot read projections or influence the queue.
revoke all on function onflows_private.prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb) from public,anon,authenticated;
grant execute on function onflows_private.prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb) to service_role;
