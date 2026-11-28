-- Compact read projections of immutable activity results. The scientific
-- result_payload stays canonical; these fields are exact JSON projections.
-- A separate small table avoids rewriting the large TOAST payload table.
create table public.onflows_activity_run_summaries (
  run_key text primary key,
  athlete_alias text not null,
  activity_ref text not null,
  input_key text not null,
  zone_summary jsonb,
  trainability_index jsonb,
  configuration_fingerprint text,
  created_at timestamptz not null,
  foreign key (run_key, athlete_alias, activity_ref)
    references public.onflows_activity_derived_runs(run_key, athlete_alias, activity_ref)
    on update cascade on delete cascade
);
create index onflows_activity_run_summaries_latest
  on public.onflows_activity_run_summaries(athlete_alias, activity_ref, created_at desc);
alter table public.onflows_activity_run_summaries enable row level security;
revoke all on public.onflows_activity_run_summaries from public, anon, authenticated;
grant select, insert, update, delete on public.onflows_activity_run_summaries to service_role;

create function onflows_private.project_activity_run_summary()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
  insert into public.onflows_activity_run_summaries
    (run_key, athlete_alias, activity_ref, input_key, zone_summary, trainability_index,
     configuration_fingerprint, created_at)
  values (new.run_key, new.athlete_alias, new.activity_ref, new.input_key,
          new.result_payload->'zone_summary', new.result_payload->'trainability_index',
          new.result_payload->>'configuration_fingerprint', new.created_at)
  on conflict (run_key) do update set
    input_key=excluded.input_key,
    zone_summary=excluded.zone_summary,
    trainability_index=excluded.trainability_index,
    configuration_fingerprint=excluded.configuration_fingerprint,
    created_at=excluded.created_at;
  return new;
end;
$$;
revoke all on function onflows_private.project_activity_run_summary() from public, anon, authenticated;
grant execute on function onflows_private.project_activity_run_summary() to service_role;
create trigger onflows_activity_run_summary_projection
  after insert or update of result_payload, input_key, created_at on public.onflows_activity_derived_runs
  for each row execute function onflows_private.project_activity_run_summary();

insert into public.onflows_activity_run_summaries
  (run_key, athlete_alias, activity_ref, input_key, zone_summary, trainability_index,
   configuration_fingerprint, created_at)
select run_key, athlete_alias, activity_ref, input_key, result_payload->'zone_summary',
       result_payload->'trainability_index', result_payload->>'configuration_fingerprint', created_at
from public.onflows_activity_derived_runs
on conflict (run_key) do nothing;

-- Aggregate activity rows once, then attach the snapshot once. Grouping by
-- the 400 KB snapshot repeated large JSON comparisons for every activity.
-- Existing signature, tenant predicates, ordering and execute grants remain.
create or replace function public.active_onflows_activity_calendar(
  p_athlete_alias text,
  p_period_start date,
  p_period_end date
) returns table (
  generation_id uuid,
  revision bigint,
  analysis_as_of date,
  activated_at timestamptz,
  snapshot_payload jsonb,
  activities jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
with active as (
    select a.generation_id, a.revision, a.analysis_as_of as as_of,
      a.activated_at, a.snapshot_payload as payload
    from public.active_onflows_analysis(p_athlete_alias) a
  ), pinned_rows as (
    select a.generation_id, ga.start_at_utc, ga.activity_ref,
      ga.catalog_payload || jsonb_build_object(
        'activity_ref', ga.activity_ref,
        'latest_canonical_run_key', ga.canonical_run_key,
        'latest_shadow_run_key', ga.shadow_run_key,
        'input_key', ga.input_key,
        'hrmod_zone_summary', coalesce(d.zone_summary, '[]'::jsonb)
      ) as payload
    from active a
    join public.onflows_analysis_generations g
      on g.generation_id = a.generation_id
     and g.status = 'ACTIVE'
    join public.onflows_analysis_generation_activities ga
      on ga.generation_id = coalesce(
        g.activity_set_generation_id, g.generation_id
      )
     and ga.athlete_alias = p_athlete_alias
    left join public.onflows_activity_run_summaries d
      on d.run_key = ga.shadow_run_key
     and d.athlete_alias = ga.athlete_alias
     and d.activity_ref = ga.activity_ref
    where a.generation_id is not null
      and ga.local_date between p_period_start and p_period_end
    union all
    select null::uuid, c.start_at_utc, c.activity_ref,
      to_jsonb(c) || jsonb_build_object(
        'input_key', d.input_key,
        'hrmod_zone_summary', coalesce(d.zone_summary, '[]'::jsonb)
      )
    from active a
    join public.onflows_activity_catalog c
      on c.athlete_alias = p_athlete_alias
    left join public.onflows_activity_run_summaries d
      on d.run_key = c.latest_shadow_run_key
     and d.athlete_alias = c.athlete_alias
     and d.activity_ref = c.activity_ref
    where a.generation_id is null
      and c.local_date between p_period_start and p_period_end
  )
  select a.generation_id, a.revision, a.as_of, a.activated_at, a.payload,
    coalesce((select jsonb_agg(r.payload order by r.start_at_utc, r.activity_ref)
              from pinned_rows r
              where r.generation_id is not distinct from a.generation_id
                and r.activity_ref is not null), '[]'::jsonb) as activities
  from active a;
$$;
