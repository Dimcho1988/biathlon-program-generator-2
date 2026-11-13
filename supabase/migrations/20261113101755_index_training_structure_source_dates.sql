-- Search frozen dates without unpacking every athlete plan when a requested
-- date has never been prescribed. The index contains dates only, not old doses.
create function onflows_private.training_structure_date_keys(p_payload jsonb)
returns text[] language sql immutable strict parallel safe cost 1000
set search_path='' as $$
 select coalesce(array_agg(distinct d.item->>'date'),array[]::text[])
 from jsonb_array_elements(case when jsonb_typeof(p_payload#>'{plan,days}')='array'
  then p_payload#>'{plan,days}' else '[]'::jsonb end) d(item)
 where jsonb_typeof(d.item->'date')='string'
$$;
revoke all on function onflows_private.training_structure_date_keys(jsonb) from public,anon,authenticated;
grant execute on function onflows_private.training_structure_date_keys(jsonb) to service_role;

create index onflows_management_plan_structure_dates
 on public.onflows_management_plan_revisions using gin(onflows_private.training_structure_date_keys(payload));
analyze public.onflows_management_plan_revisions;

create or replace function public.read_onflows_training_structure(
 p_alias text,p_start date,p_end date,p_revisions integer[] default null
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare result jsonb; selected_days jsonb := '[]'::jsonb;
 source_day date; candidate_revision integer; selected_revision integer; matches jsonb; selected_item jsonb;
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
 -- Materialize only matching revision identifiers before ordering. Missing
 -- dates never walk the descending history and repeatedly detoast large plans.
 -- Inspect full day payloads newest-first, stopping at the first prescription;
 -- old activity-only markers still fall back within the pinned revision set.
 for source_day in select p_start+i from generate_series(0,p_end-p_start) i loop
  selected_revision:=null; selected_item:=null;
  for candidate_revision in
   with candidates as materialized (
    select h.revision from public.onflows_management_plan_revisions h
    where h.athlete_alias=p_alias and (p_revisions is null or h.revision=any(p_revisions))
     and onflows_private.training_structure_date_keys(h.payload) @> array[source_day::text]
   ) select revision from candidates order by revision desc
  loop
   select jsonb_agg(d.item) into matches
   from public.onflows_management_plan_revisions h cross join lateral jsonb_array_elements(
    case when jsonb_typeof(h.payload#>'{plan,days}')='array' then h.payload#>'{plan,days}' else '[]'::jsonb end
   ) d(item)
   where h.athlete_alias=p_alias and h.revision=candidate_revision
    and d.item->>'date'=source_day::text
    and not coalesce(d.item->>'status'='EXISTING_ACTIVITY' and (
      (d.item ? 'sessions' and d.item->'sessions' in ('[]'::jsonb,'null'::jsonb))
      or (not (d.item ? 'sessions') and (not (d.item ? 'session') or d.item->'session'='null'::jsonb))
    ),false);
   if matches is not null then
    selected_revision:=candidate_revision;
    selected_item:=case when jsonb_array_length(matches)=1 then matches->0 else null end;
    exit;
   end if;
  end loop;
  selected_days:=selected_days||jsonb_build_array(jsonb_build_object(
   'day',source_day,'revision',selected_revision,'item',selected_item));
 end loop;
 with selected as (
  select * from jsonb_to_recordset(selected_days) as d(day date,revision integer,item jsonb)
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


notify pgrst,'reload schema';
