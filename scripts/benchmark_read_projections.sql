-- Read-only staging benchmark. No credentials or profile data in this file.
-- psql "$STAGING_DATABASE_URL" -v athlete_alias=... -v period_start=2026-06-28 -v period_end=2026-09-25 -f scripts/benchmark_read_projections.sql
\set ON_ERROR_STOP on
begin isolation level repeatable read read only;
-- Before: original generation calendar.
explain (analyze, buffers)
with active as (
    select a.generation_id, a.revision, a.analysis_as_of as as_of,
      a.activated_at, a.snapshot_payload as payload
    from public.active_onflows_analysis(:'athlete_alias') a
  ), pinned_rows as (
    select a.generation_id, ga.start_at_utc, ga.activity_ref,
      ga.catalog_payload || jsonb_build_object(
        'activity_ref', ga.activity_ref,
        'latest_canonical_run_key', ga.canonical_run_key,
        'latest_shadow_run_key', ga.shadow_run_key,
        'input_key', ga.input_key,
        'hrmod_zone_summary', coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
      ) as payload
    from active a
    join public.onflows_analysis_generations g
      on g.generation_id = a.generation_id
     and g.status = 'ACTIVE'
    join public.onflows_analysis_generation_activities ga
      on ga.generation_id = coalesce(
        g.activity_set_generation_id, g.generation_id
      )
     and ga.athlete_alias = :'athlete_alias'
    left join public.onflows_activity_derived_runs d
      on d.run_key = ga.shadow_run_key
     and d.athlete_alias = ga.athlete_alias
     and d.activity_ref = ga.activity_ref
    where a.generation_id is not null
      and ga.local_date between :'period_start'::date and :'period_end'::date
    union all
    select null::uuid, c.start_at_utc, c.activity_ref,
      to_jsonb(c) || jsonb_build_object(
        'input_key', d.input_key,
        'hrmod_zone_summary', coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
      )
    from active a
    join public.onflows_activity_catalog c
      on c.athlete_alias = :'athlete_alias'
    left join public.onflows_activity_derived_runs d
      on d.run_key = c.latest_shadow_run_key
     and d.athlete_alias = c.athlete_alias
     and d.activity_ref = c.activity_ref
    where a.generation_id is null
      and c.local_date between :'period_start'::date and :'period_end'::date
  )
  select a.generation_id, a.revision, a.as_of, a.activated_at, a.payload,
    coalesce(
      jsonb_agg(r.payload order by r.start_at_utc, r.activity_ref)
        filter (where r.activity_ref is not null),
      '[]'::jsonb
    )
  from active a
  left join pinned_rows r
    on r.generation_id is not distinct from a.generation_id
  group by a.generation_id, a.revision, a.as_of, a.activated_at, a.payload;
-- After: same read contract, compact projections.
explain (analyze, buffers)
select * from public.active_onflows_activity_calendar(:'athlete_alias', :'period_start'::date, :'period_end'::date);

with before(generation_id,revision,analysis_as_of,activated_at,snapshot_payload,activities) as (
with active as (
    select a.generation_id, a.revision, a.analysis_as_of as as_of,
      a.activated_at, a.snapshot_payload as payload
    from public.active_onflows_analysis(:'athlete_alias') a
  ), pinned_rows as (
    select a.generation_id, ga.start_at_utc, ga.activity_ref,
      ga.catalog_payload || jsonb_build_object(
        'activity_ref', ga.activity_ref,
        'latest_canonical_run_key', ga.canonical_run_key,
        'latest_shadow_run_key', ga.shadow_run_key,
        'input_key', ga.input_key,
        'hrmod_zone_summary', coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
      ) as payload
    from active a
    join public.onflows_analysis_generations g
      on g.generation_id = a.generation_id
     and g.status = 'ACTIVE'
    join public.onflows_analysis_generation_activities ga
      on ga.generation_id = coalesce(
        g.activity_set_generation_id, g.generation_id
      )
     and ga.athlete_alias = :'athlete_alias'
    left join public.onflows_activity_derived_runs d
      on d.run_key = ga.shadow_run_key
     and d.athlete_alias = ga.athlete_alias
     and d.activity_ref = ga.activity_ref
    where a.generation_id is not null
      and ga.local_date between :'period_start'::date and :'period_end'::date
    union all
    select null::uuid, c.start_at_utc, c.activity_ref,
      to_jsonb(c) || jsonb_build_object(
        'input_key', d.input_key,
        'hrmod_zone_summary', coalesce(d.result_payload -> 'zone_summary', '[]'::jsonb)
      )
    from active a
    join public.onflows_activity_catalog c
      on c.athlete_alias = :'athlete_alias'
    left join public.onflows_activity_derived_runs d
      on d.run_key = c.latest_shadow_run_key
     and d.athlete_alias = c.athlete_alias
     and d.activity_ref = c.activity_ref
    where a.generation_id is null
      and c.local_date between :'period_start'::date and :'period_end'::date
  )
  select a.generation_id, a.revision, a.as_of, a.activated_at, a.payload,
    coalesce(
      jsonb_agg(r.payload order by r.start_at_utc, r.activity_ref)
        filter (where r.activity_ref is not null),
      '[]'::jsonb
    )
  from active a
  left join pinned_rows r
    on r.generation_id is not distinct from a.generation_id
  group by a.generation_id, a.revision, a.as_of, a.activated_at, a.payload
), after as (select * from public.active_onflows_activity_calendar(:'athlete_alias', :'period_start'::date, :'period_end'::date))
select (select to_jsonb(b) from before b) is not distinct from
       (select to_jsonb(a) from after a) as all_fields_identical;

select count(*) as source_rows, count(s.run_key) as summary_rows,
  count(*) filter(where s.run_key is null or
    (d.input_key,d.result_payload->'zone_summary',d.result_payload->'trainability_index',
     d.result_payload->>'configuration_fingerprint',d.created_at) is distinct from
    (s.input_key,s.zone_summary,s.trainability_index,s.configuration_fingerprint,s.created_at)
  ) as mismatches
from public.onflows_activity_derived_runs d
left join public.onflows_activity_run_summaries s using(run_key,athlete_alias,activity_ref);
rollback;
