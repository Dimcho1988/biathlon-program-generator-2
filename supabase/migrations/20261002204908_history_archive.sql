-- Private objects contain only exact model channels and derived series.
insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('onflows-history', 'onflows-history', false, 33554432, array['application/octet-stream'])
on conflict (id) do nothing;

create table public.onflows_history_archive_policy (
  id boolean primary key default true check (id),
  enabled boolean not null default false,
  hot_days integer not null default 365 check (hot_days between 180 and 3650),
  batch_rows integer not null default 5 check (batch_rows between 1 and 20),
  minimum_bytes integer not null default 65536 check (minimum_bytes >= 65536)
);
insert into public.onflows_history_archive_policy(id) values(true);
alter table public.onflows_history_archive_policy enable row level security;
revoke all on public.onflows_history_archive_policy from public, anon, authenticated;
grant select on public.onflows_history_archive_policy to service_role;

create table public.onflows_history_archive_events (
  event_id bigint generated always as identity primary key,
  entity_kind text not null check (entity_kind in ('shadow','input')),
  entity_key text not null,
  operation text not null check (operation in ('ARCHIVED','RESTORED')),
  fields text[] not null,
  created_at timestamptz not null default now()
);
alter table public.onflows_history_archive_events enable row level security;
revoke all on public.onflows_history_archive_events from public, anon, authenticated;
grant select on public.onflows_history_archive_events to service_role;

create index onflows_generation_activities_history_age
  on public.onflows_analysis_generation_activities(athlete_alias,activity_ref,start_at_utc);

create function onflows_private.history_activity_is_cold(p_alias text, p_ref text, p_days integer)
returns boolean language sql stable security invoker set search_path='' as $$
  select max(x.start_at_utc) < now() - make_interval(days => p_days)
  from (
    select c.start_at_utc from public.onflows_activity_catalog c
      where c.athlete_alias=p_alias and c.activity_ref=p_ref
    union all
    select ga.start_at_utc from public.onflows_analysis_generation_activities ga
      where ga.athlete_alias=p_alias and ga.activity_ref=p_ref
  ) x;
$$;
revoke all on function onflows_private.history_activity_is_cold(text,text,integer) from public,anon,authenticated;

create function public.read_onflows_history_archive_batch(
  p_kind text, p_after text default '', p_limit integer default 1
) returns table(entity_key text, athlete_alias text, activity_ref text, storage_hash text, payload jsonb, archive_fields text[])
language plpgsql security definer set search_path='' set statement_timeout='60s' as $$
declare policy public.onflows_history_archive_policy;
begin
  if p_kind is null or p_kind not in ('shadow','input') or p_after is null
     or p_after !~ '^([a-f0-9]{64})?$' or p_limit is null or p_limit not between 1 and 5 then
    raise exception 'invalid archive cursor';
  end if;
  select * into strict policy from public.onflows_history_archive_policy where id;
  if p_kind='shadow' then
    return query select d.run_key,d.athlete_alias,d.activity_ref,md5(d.result_payload::text),d.result_payload,
      array(select f from unnest(array['timeseries','speed_test_series','segments_15s','hrmod_waves']) f
        where coalesce(d.result_payload->f->>'codec','') <> 'onflows-history-object-xz-v1'
          and octet_length((d.result_payload->f)::text)>=policy.minimum_bytes)
    from public.onflows_activity_derived_runs d
    where d.run_key>p_after and d.created_at < now()-interval '7 days'
      and onflows_private.history_activity_is_cold(d.athlete_alias,d.activity_ref,policy.hot_days)
      and exists (select 1 from unnest(array['timeseries','speed_test_series','segments_15s','hrmod_waves']) f
        where coalesce(d.result_payload->f->>'codec','') <> 'onflows-history-object-xz-v1'
          and octet_length((d.result_payload->f)::text)>=policy.minimum_bytes)
    order by d.run_key limit p_limit;
  else
    return query select i.input_key,i.athlete_alias,i.activity_ref,md5(i.input_payload::text),i.input_payload,array['samples']::text[]
    from public.onflows_activity_model_inputs i
    where i.input_key>p_after and i.created_at < now()-interval '7 days'
      and onflows_private.history_activity_is_cold(i.athlete_alias,i.activity_ref,policy.hot_days)
      and jsonb_typeof(i.input_payload->'samples')='array'
      and octet_length((i.input_payload->'samples')::text)>=policy.minimum_bytes
    order by i.input_key limit p_limit;
  end if;
end;
$$;

create function public.read_onflows_history_storage_hash(p_kind text,p_key text)
returns table(storage_hash text) language plpgsql security definer set search_path='' as $$
begin
  if p_kind is null or p_kind not in ('shadow','input') or p_key is null or p_key !~ '^[a-f0-9]{64}$' then
    raise exception 'invalid archive identity';
  end if;
  if p_kind='shadow' then
    return query select md5(d.result_payload::text) from public.onflows_activity_derived_runs d where d.run_key=p_key;
  else
    return query select md5(i.input_payload::text) from public.onflows_activity_model_inputs i where i.input_key=p_key;
  end if;
end;
$$;

create function public.replace_onflows_history_payload(
  p_kind text,p_key text,p_expected_storage_hash text,p_payload jsonb,p_restore boolean default false
) returns table(outcome text)
language plpgsql security definer set search_path='' set lock_timeout='2s' set statement_timeout='60s' as $$
declare
  original jsonb; alias_value text; ref_value text; stamp timestamptz;
  eligible text[]; changed text[] := array[]::text[]; f text; replacement jsonb;
  policy public.onflows_history_archive_policy; status_value text;
begin
  if p_kind is null or p_kind not in ('shadow','input') or p_key is null or p_key !~ '^[a-f0-9]{64}$'
     or p_expected_storage_hash is null or p_expected_storage_hash !~ '^[a-f0-9]{32}$'
     or p_restore is null or jsonb_typeof(p_payload) is distinct from 'object' then
    raise exception 'invalid archive replacement';
  end if;
  if p_kind='shadow' then
    eligible := array['timeseries','speed_test_series','segments_15s','hrmod_waves'];
    select d.result_payload,d.athlete_alias,d.activity_ref,d.created_at into original,alias_value,ref_value,stamp
      from public.onflows_activity_derived_runs d where d.run_key=p_key for update;
  else
    eligible := array['samples'];
    select i.input_payload,i.athlete_alias,i.activity_ref,i.created_at into original,alias_value,ref_value,stamp
      from public.onflows_activity_model_inputs i where i.input_key=p_key for update;
  end if;
  if original is null then return query select 'MISSING'::text; return; end if;
  if md5(original::text) <> p_expected_storage_hash then return query select 'CONFLICT'::text; return; end if;
  if original=p_payload then return query select 'UNCHANGED'::text; return; end if;
  if (original-eligible) is distinct from (p_payload-eligible) then
    raise exception 'archive cannot alter scientific metadata';
  end if;
  if not p_restore then
    select * into strict policy from public.onflows_history_archive_policy where id;
    if not policy.enabled or stamp >= now()-interval '7 days'
       or onflows_private.history_activity_is_cold(alias_value,ref_value,policy.hot_days) is not true then
      raise exception 'history record is not eligible for archiving';
    end if;
    if not exists(select 1 from storage.buckets b where b.id='onflows-history' and not b.public) then
      raise exception 'history bucket must be private';
    end if;
  end if;
  foreach f in array eligible loop
    if (original ? f) is distinct from (p_payload ? f) then raise exception 'archive cannot remove or add series'; end if;
    if original->f is not distinct from p_payload->f then continue; end if;
    changed := array_append(changed,f);
    replacement := p_payload->f;
    if p_restore then
      if original->f->>'codec' is distinct from 'onflows-history-object-xz-v1'
         or (jsonb_typeof(replacement) is distinct from 'array' and
             (p_kind <> 'shadow' or coalesce(replacement->>'codec','') not in
              ('onflows-shadow-series-columnar-lzma-v2','onflows-shadow-timeseries-gzip-json-v1'))) then
        raise exception 'invalid history restoration';
      end if;
    else
      if original->f->>'codec' = 'onflows-history-object-xz-v1'
         or jsonb_typeof(replacement) is distinct from 'object'
         or replacement->>'codec' is distinct from 'onflows-history-object-xz-v1'
         or replacement->>'bucket' is distinct from 'onflows-history'
         or coalesce(replacement->>'sha256','') !~ '^[a-f0-9]{64}$'
         or coalesce(replacement->>'json_sha256','') !~ '^[a-f0-9]{64}$'
         or replacement->>'path' is distinct from format('v1/%s/%s/%s/%s.xz',p_kind,p_key,f,replacement->>'sha256')
         or coalesce(replacement->>'bytes','') !~ '^[1-9][0-9]{0,8}$'
         or jsonb_typeof(replacement->'bytes') is distinct from 'number'
         or (replacement->>'bytes')::bigint > 33554432
         or coalesce(replacement->>'json_bytes','') !~ '^[1-9][0-9]{0,8}$'
         or jsonb_typeof(replacement->'json_bytes') is distinct from 'number'
         or (replacement->>'json_bytes')::bigint > 67108864 then
        raise exception 'invalid history archive reference';
      end if;
      if not exists(select 1 from storage.objects o where o.bucket_id='onflows-history' and o.name=replacement->>'path') then
        raise exception 'history archive object is missing';
      end if;
    end if;
  end loop;
  if p_kind='shadow' then
    update public.onflows_activity_derived_runs set result_payload=p_payload where run_key=p_key;
  else
    update public.onflows_activity_model_inputs set input_payload=p_payload where input_key=p_key;
  end if;
  status_value := case when p_restore then 'RESTORED' else 'ARCHIVED' end;
  insert into public.onflows_history_archive_events(entity_kind,entity_key,operation,fields)
    values(p_kind,p_key,status_value,changed);
  return query select status_value;
end;
$$;

revoke all on function public.read_onflows_history_archive_batch(text,text,integer) from public,anon,authenticated;
revoke all on function public.read_onflows_history_storage_hash(text,text) from public,anon,authenticated;
revoke all on function public.replace_onflows_history_payload(text,text,text,jsonb,boolean) from public,anon,authenticated;
grant execute on function public.read_onflows_history_archive_batch(text,text,integer) to service_role;
grant execute on function public.read_onflows_history_storage_hash(text,text) to service_role;
grant execute on function public.replace_onflows_history_payload(text,text,text,jsonb,boolean) to service_role;
