-- Reuse immutable athlete prescriptions as structural preferences. This read
-- deliberately omits doses, readiness, scientific evidence and provisional days.
create function public.read_onflows_training_structure(
 p_alias text,p_start date,p_end date,p_revisions integer[] default null
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare result jsonb;
begin
 if p_alias is null or btrim(p_alias)='' or p_start is null or p_end is null
  or not isfinite(p_start) or not isfinite(p_end) then
  raise exception 'Invalid training structure source' using errcode='22023';
 end if;
 if p_end-p_start not in (0,6,13) or (p_revisions is not null and (
   array_ndims(p_revisions) is distinct from 1 or cardinality(p_revisions) not between 1 and 14
   or exists(select 1 from unnest(p_revisions) r where r is null or r<1)
   or (select count(distinct r) from unnest(p_revisions) r)<>cardinality(p_revisions))) then
  raise exception 'Source must contain one day, one week or two weeks and valid revisions' using errcode='22023';
 end if;
 with requested as (
   select p_start+i as day from generate_series(0,p_end-p_start) i
 ), selected as materialized (
   select q.day,r.revision,
    case when jsonb_array_length(r.matches)=1 then r.matches->0 else null end as item
   from requested q left join lateral (
     select h.revision,source.matches
     from public.onflows_management_plan_revisions h cross join lateral (
       select jsonb_agg(d.item) as matches from jsonb_array_elements(
        case when jsonb_typeof(h.payload#>'{plan,days}')='array' then h.payload#>'{plan,days}' else '[]'::jsonb end
       ) d(item) where d.item->>'date'=q.day::text
        -- A later activity marker contains no prescription. Reuse the earlier
        -- immutable prescription, if it exists; never infer one from activity.
        -- Malformed records and explicit rest remain visible and take priority.
        and not coalesce(d.item->>'status'='EXISTING_ACTIVITY' and (
          (d.item ? 'sessions' and d.item->'sessions' in ('[]'::jsonb,'null'::jsonb))
          or (not (d.item ? 'sessions') and (not (d.item ? 'session') or d.item->'session'='null'::jsonb))
        ),false)
     ) source
     where h.athlete_alias=p_alias and (p_revisions is null or h.revision=any(p_revisions))
      and h.payload#>'{plan,days}' @> jsonb_build_array(jsonb_build_object('date',q.day::text))
      and source.matches is not null
     order by h.revision desc limit 1
   ) r on true
 ), projected as (
   select day,revision,jsonb_build_object(
    'date',day::text,'status',item->'status','revision',revision,'actual_sessions',item->'actual_sessions',
    'sessions',case when jsonb_typeof(source.sessions)='array' then (
      select coalesce(jsonb_agg(jsonb_build_object(
       'method_id',s.item->'method_id','sport',s.item->'sport',
       'slot',coalesce(s.item->'slot',to_jsonb(s.ord::integer)),
       'double_threshold_part',s.item->'double_threshold_part',
       'method_library',case when jsonb_typeof(s.item->'method_library')='object' then jsonb_build_object(
         'id',s.item#>'{method_library,id}',
         'base_method_id',coalesce(s.item#>'{method_library,personal_variant,base_method_id}',s.item#>'{method_library,base_method_id}'),
         'components',case when not (s.item->'method_library' ? 'components') then '[]'::jsonb
          when jsonb_typeof(s.item#>'{method_library,components}')='array' then (
            select coalesce(jsonb_agg(jsonb_build_object('id',c.item->'id',
              'base_method_id',coalesce(c.item#>'{personal_variant,base_method_id}',c.item->'base_method_id')) order by c.ord),'[]'::jsonb)
            from jsonb_array_elements(s.item#>'{method_library,components}') with ordinality c(item,ord)
          ) else 'null'::jsonb end) else 'null'::jsonb end,
       'execution_descriptor',case when jsonb_typeof(s.item->'execution_descriptor')='object' then jsonb_build_object(
         'id',s.item#>'{execution_descriptor,id}','structure',s.item#>'{execution_descriptor,structure}',
         'parts',case when not (s.item->'execution_descriptor' ? 'parts') then '[]'::jsonb
          when jsonb_typeof(s.item#>'{execution_descriptor,parts}')='array' then (
            select coalesce(jsonb_agg(jsonb_build_object('id',d.item->'id','structure',d.item->'structure') order by d.ord),'[]'::jsonb)
            from jsonb_array_elements(s.item#>'{execution_descriptor,parts}') with ordinality d(item,ord)
          ) else 'null'::jsonb end) else 'null'::jsonb end
      ) order by s.ord),'[]'::jsonb)
      from jsonb_array_elements(source.sessions) with ordinality s(item,ord)
    ) else 'null'::jsonb end
   ) as value
   from selected cross join lateral (select case
    when item ? 'sessions' then item->'sessions'
    when item->'session'='null'::jsonb then '[]'::jsonb
    when jsonb_typeof(item->'session')='object' and not (item->'session' ? '$session_ref') then jsonb_build_array(item->'session')
    else 'null'::jsonb end as sessions) source
 ) select jsonb_build_object(
  'days',coalesce(jsonb_agg(value order by day) filter(where revision is not null),'[]'::jsonb),
  'missing_dates',coalesce(jsonb_agg(day::text order by day) filter(where revision is null),'[]'::jsonb),
  'plan_revisions',coalesce(jsonb_agg(distinct revision order by revision) filter(where revision is not null),'[]'::jsonb)
 ) into result from projected;
 return result;
end $$;
revoke all on function public.read_onflows_training_structure(text,date,date,integer[]) from public,anon,authenticated;
grant execute on function public.read_onflows_training_structure(text,date,date,integer[]) to service_role;

-- A preview is based on a current plan as well as a profile. Fence both under
-- the canonical publication lock, preserving the existing atomic receipt/CAS.
create function public.save_onflows_training_proposal(
 p_alias text,p_actor uuid,p_operation_id uuid,p_request_hash text,p_kind text,p_key text,
 p_payload jsonb,p_day date,p_expected_revision integer,p_expected_plan_revision integer,p_origin text default 'CHAT'
) returns jsonb language plpgsql security invoker set search_path='' as $$
declare rights jsonb; prior jsonb; current_plan_revision integer;
begin
 if p_alias is null or btrim(p_alias)='' or p_actor is null or p_operation_id is null
  or p_request_hash is null or p_request_hash !~ '^[a-f0-9]{64}$'
  or p_kind is distinct from 'MANAGEMENT_PROFILE' or p_key is distinct from 'management-profile-v1'
  or p_payload is null or jsonb_typeof(p_payload)<>'object' or octet_length(p_payload::text)>262144
  or p_day is not null or p_expected_revision is null or p_expected_revision<0
  or p_expected_plan_revision<0
  or p_origin is null or p_origin not in ('CHAT','FORM') then
  raise exception 'Invalid training proposal operation' using errcode='22023';
 end if;
 rights:=public.onflows_assistant_access(p_alias,p_actor);
 if not (rights->>'can_edit_plan')::boolean or not (rights->>'can_view_plan')::boolean then
  return jsonb_build_object('forbidden',true);
 end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('management:'||p_alias,0));
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('assistant:'||p_alias||':'||p_actor::text||':'||p_operation_id::text,0));
 rights:=public.onflows_assistant_access(p_alias,p_actor);
 if not (rights->>'can_edit_plan')::boolean or not (rights->>'can_view_plan')::boolean then
  return jsonb_build_object('forbidden',true);
 end if;
 prior:=public.onflows_assistant_operation_status(p_alias,p_actor,p_operation_id);
 if coalesce((prior->>'forbidden')::boolean,false) then return prior; end if;
 -- Lost-response retries keep their original receipt even after later plans.
 if prior->>'status' is distinct from 'NOT_FOUND' then
  return public.save_onflows_assistant_operation(p_alias,p_actor,p_operation_id,p_request_hash,
   p_kind,p_key,p_payload,p_day,p_expected_revision,p_origin);
 end if;
 select revision into current_plan_revision from public.onflows_management_plan_state where athlete_alias=p_alias;
 current_plan_revision:=coalesce(current_plan_revision,0);
 if p_expected_plan_revision is not null and current_plan_revision<>p_expected_plan_revision then
  return jsonb_build_object('conflict',true,'status','CONFLICT','reason','PLAN_REVISION_CHANGED','revision',current_plan_revision);
 end if;
 return public.save_onflows_assistant_operation(p_alias,p_actor,p_operation_id,p_request_hash,
  p_kind,p_key,p_payload,p_day,p_expected_revision,p_origin);
end $$;
revoke all on function public.save_onflows_training_proposal(text,uuid,uuid,text,text,text,jsonb,date,integer,integer,text) from public,anon,authenticated;
grant execute on function public.save_onflows_training_proposal(text,uuid,uuid,text,text,text,jsonb,date,integer,integer,text) to service_role;

notify pgrst,'reload schema';
