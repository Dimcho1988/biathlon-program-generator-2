-- Preserve immutable history provenance without rewriting its JSON payloads.
-- Pins are conservative and monotone; pruning remains disabled for the API.
-- The transaction is safely retryable after a busy lock or timeout. It never
-- changes a training rule, public RPC signature, or historical payload.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '60s';

-- Acquire every table needed by the new FKs/triggers before installing guards.
-- NOWAIT avoids mixed writer/state/generation DDL waits.
-- Row locks used below are also non-blocking. A busy migration aborts in full.
lock table public.onflows_analysis_generations,
  public.onflows_athlete_settings,
  public.onflows_management_entries,
  public.onflows_management_plan_revisions,
  public.onflows_response_entries
  in share row exclusive mode nowait;

create table onflows_private.history_generation_pins (
  athlete_alias text not null,
  generation_id uuid not null,
  -- One index covers alias cleanup and exact composite generation lookups.
  primary key (athlete_alias,generation_id),
  foreign key (athlete_alias)
    references public.onflows_athlete_settings(athlete_alias)
    on update cascade on delete cascade,
  -- Deferred NO ACTION preserves the populated connection/settings cleanup
  -- graph, while making an isolated generation deletion fail at commit.
  foreign key (generation_id,athlete_alias)
    references public.onflows_analysis_generations(generation_id,athlete_alias)
    on update cascade on delete no action deferrable initially deferred
);
alter table onflows_private.history_generation_pins enable row level security;
revoke all on onflows_private.history_generation_pins
  from public,anon,authenticated,service_role;

-- Recognize generation provenance at every object/array depth, including
-- learning episodes, saved TEST load windows, and future *_generation_id keys.
-- Only absent keys / JSON null are absent references. Malformed values fail
-- closed, rather than silently removing part of a historical dependency set.
create function onflows_private.history_generation_references(p_payload jsonb)
returns uuid[] language plpgsql immutable parallel safe set search_path='' as $$
declare
  reference_value jsonb;
  reference_id uuid;
  reference_ids uuid[] := '{}'::uuid[];
  reference_path pg_catalog.jsonpath;
begin
  -- Calling keyvalue() on every large ancestor copies unrelated JSON values.
  -- Most payloads use these three known names: prefilter their small objects.
  -- Unknown suffix keys must still be retained. Canonical JSON text is ONLY a
  -- conservative fallback selector; actual values always come from JSONPath.
  -- The lookbehind avoids removing a known-name suffix following an escaped
  -- quote inside an unknown key. An overmatch merely chooses the slower path.
  if pg_catalog.regexp_replace(p_payload::text,
      $known$(?<!\\)"(?:generation_id|source_generation_id|analysis_generation_id)"[[:space:]]*:$known$,
      '', 'g') ~ $unknown$_generation_id"[[:space:]]*:$unknown$ then
    reference_path := 'strict $.** ? (@.type()=="object").keyvalue() ? (@.key like_regex "(^|_)generation_id$").value'::pg_catalog.jsonpath;
  else
    reference_path := 'strict $.** ? (@.type()=="object" && (exists(@.generation_id)||exists(@.source_generation_id)||exists(@.analysis_generation_id))).keyvalue() ? (@.key like_regex "(^|_)generation_id$").value'::pg_catalog.jsonpath;
  end if;
  for reference_value in
    select matched.value
    from pg_catalog.jsonb_path_query(p_payload,reference_path)
      as matched(value)
  loop
    if reference_value='null'::jsonb then
      continue;
    end if;
    if pg_catalog.jsonb_typeof(reference_value)<>'string' then
      raise exception 'Historical analysis reference is unavailable; reload and try again'
        using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
    end if;
    begin
      reference_id := (reference_value #>> '{}')::uuid;
    exception when invalid_text_representation then
      raise exception 'Historical analysis reference is unavailable; reload and try again'
        using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
    end;
    reference_ids := pg_catalog.array_append(reference_ids,reference_id);
  end loop;
  return array(select distinct value from pg_catalog.unnest(reference_ids) as refs(value) order by value);
end $$;
revoke all on function onflows_private.history_generation_references(jsonb)
  from public,anon,authenticated,service_role;

create function onflows_private.pin_history_generations(p_alias text,p_generation_ids uuid[])
returns void language plpgsql security definer set search_path='' as $$
declare
  missing_ids uuid[];
  locked_ids uuid[] := '{}'::uuid[];
  selected_id uuid;
begin
  if p_alias is null or p_alias='' then
    raise exception 'Historical analysis reference is unavailable; reload and try again'
      using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
  end if;
  -- Existing references remain available even while another transaction is
  -- adding unrelated pins for this athlete. They need no row/advisory lock.
  if not exists(select 1 from pg_catalog.unnest(p_generation_ids) as roots(value)
      where value is not null
        and not exists(select 1 from onflows_private.history_generation_pins pin
          where pin.athlete_alias=p_alias and pin.generation_id=roots.value)) then
    return;
  end if;
  -- Per-call sorted INSERTs alone cannot prevent opposite pin insertion order
  -- across multiple history rows/helper calls in the same transaction. A
  -- non-blocking per-athlete transaction lock serializes only NEW pin writers.
  -- It is held across all such calls until commit, with no waiting lock edge.
  if not pg_catalog.pg_try_advisory_xact_lock(
      pg_catalog.hashtextextended('history-pins:'||p_alias,0)) then
    raise exception 'Historical analysis reference is unavailable; reload and try again'
      using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
  end if;
  loop
    -- UNION is deliberately distinct: legacy/self/cyclic lineage terminates.
    -- Existing pins already own their closure and require no generation lock.
    -- Repeat discovery after new row locks so a parent changed just before its
    -- lock was taken is included too. The link-update trigger below prevents
    -- a further parent change racing an uncommitted new pin.
    with recursive required(generation_id) as (
      select value from pg_catalog.unnest(p_generation_ids) as roots(value)
      where value is not null
      union
      select parent.generation_id
      from required current_reference
      join public.onflows_analysis_generations generation
        on generation.generation_id=current_reference.generation_id
        and generation.athlete_alias=p_alias
      cross join lateral (values(generation.base_generation_id),
        (generation.activity_set_generation_id)) as parent(generation_id)
      where parent.generation_id is not null
        and not exists(select 1 from onflows_private.history_generation_pins pin
          where pin.athlete_alias=p_alias and pin.generation_id=current_reference.generation_id)
    )
    select coalesce(pg_catalog.array_agg(required.generation_id order by required.generation_id),'{}'::uuid[])
      into missing_ids
    from required
    where not(required.generation_id=any(locked_ids))
      and not exists(select 1 from onflows_private.history_generation_pins pin
        where pin.athlete_alias=p_alias and pin.generation_id=required.generation_id);
    exit when pg_catalog.cardinality(missing_ids)=0;
    foreach selected_id in array missing_ids loop
      begin
        perform 1 from public.onflows_analysis_generations generation
          where generation.athlete_alias=p_alias and generation.generation_id=selected_id
          for key share nowait;
      exception when lock_not_available then
        raise exception 'Historical analysis reference is unavailable; reload and try again'
          using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
      end;
      if not found then
        raise exception 'Historical analysis reference is unavailable; reload and try again'
          using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
      end if;
      locked_ids := pg_catalog.array_append(locked_ids,selected_id);
    end loop;
  end loop;
  -- Insert this closure in a stable order. The per-athlete guard above also
  -- covers ordering across multiple helper calls/rows in one transaction.
  insert into onflows_private.history_generation_pins(athlete_alias,generation_id)
    select p_alias,value from pg_catalog.unnest(locked_ids) as refs(value) order by value
    on conflict(athlete_alias,generation_id) do nothing;
end $$;
revoke all on function onflows_private.pin_history_generations(text,uuid[])
  from public,anon,authenticated,service_role;

create function onflows_private.pin_history_payload()
returns trigger language plpgsql security definer set search_path='' as $$
begin
  perform onflows_private.pin_history_generations(new.athlete_alias,
    onflows_private.history_generation_references(new.payload));
  return new;
end $$;
revoke all on function onflows_private.pin_history_payload()
  from public,anon,authenticated,service_role;

create trigger history_generation_pins_management_entry
after insert on public.onflows_management_entries for each row
when (new.kind='DRAFT') execute function onflows_private.pin_history_payload();
create trigger history_generation_pins_plan_revision
after insert on public.onflows_management_plan_revisions for each row
execute function onflows_private.pin_history_payload();
create trigger history_generation_pins_response_entry
after insert on public.onflows_response_entries for each row
execute function onflows_private.pin_history_payload();

create function onflows_private.extend_history_generation_pins()
returns trigger language plpgsql security definer set search_path='' as $$
begin
  -- An ordinary link UPDATE otherwise holds only NO KEY UPDATE, compatible
  -- with a new pin's KEY SHARE. Upgrade non-blockingly before checking pins,
  -- including an unpinned row whose first pin may still be uncommitted.
  begin
    perform 1 from public.onflows_analysis_generations generation
      where generation.generation_id=new.generation_id
        and generation.athlete_alias=new.athlete_alias for update nowait;
  exception when lock_not_available then
    raise exception 'Historical analysis reference is unavailable; reload and try again'
      using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
  end;
  if exists(select 1 from onflows_private.history_generation_pins pin
      where pin.athlete_alias=new.athlete_alias and pin.generation_id=new.generation_id) then
    perform onflows_private.pin_history_generations(new.athlete_alias,
      array[new.base_generation_id,new.activity_set_generation_id]);
  end if;
  return new;
end $$;
revoke all on function onflows_private.extend_history_generation_pins()
  from public,anon,authenticated,service_role;
create trigger history_generation_pins_generation_links
after update of base_generation_id,activity_set_generation_id
on public.onflows_analysis_generations for each row
when (old.base_generation_id is distinct from new.base_generation_id
   or old.activity_set_generation_id is distinct from new.activity_set_generation_id)
execute function onflows_private.extend_history_generation_pins();

-- Two-phase bootstrap: no full historical JSON scan is held under these DDL
-- locks. Before considering any later cleanup, an owner must run bounded,
-- immutable-primary-key batches from management_entries(kind='DRAFT'),
-- management_plan_revisions and response_entries, calling only:
--   onflows_private.pin_history_generations(athlete_alias,
--     onflows_private.history_generation_references(payload))
-- Missing/foreign/malformed references fail that batch; report them rather
-- than rewriting payloads or substituting IDs. Then verify full coverage of
-- recognized JSON references AND base/activity-set ancestor closure. New
-- INSERTs are guarded meanwhile. No public backfill RPC/grant is introduced.
-- Only identifiers are added; historical payloads and hashes remain intact.

-- Preserve every prior retention rule and return contract. The sole candidate
-- change is the indexed history-pin exclusion before ordering/LIMIT. The FK
-- independently rejects a stale candidate if a new pin commits concurrently.
create or replace function public.prune_onflows_analysis_generations(
  p_athlete_alias text,
  p_keep_superseded integer default 5,
  p_terminal_older_than interval default interval '30 days',
  p_batch_limit integer default 100
) returns table (
  deleted_generations integer,
  deleted_activity_rows integer,
  deleted_jobs integer,
  deleted_shadow_runs integer,
  deleted_canonical_runs integer,
  deleted_model_inputs integer,
  deleted_catalog_rows integer
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  candidate_ids uuid[];
  activity_rows integer := 0;
  generation_rows integer := 0;
  job_rows integer := 0;
  shadow_rows integer := 0;
  canonical_rows integer := 0;
  input_rows integer := 0;
  catalog_rows integer := 0;
begin
  if p_keep_superseded not between 1 and 50
     or p_batch_limit not between 1 and 1000
     or p_terminal_older_than < interval '30 days'
     or p_terminal_older_than > interval '365 days' then
    raise exception 'invalid analysis retention policy';
  end if;

  with ranked_superseded as (
    select g.generation_id,
      row_number() over (
        order by g.activated_at desc nulls last, g.created_at desc,
          g.generation_id desc
      ) as recency
    from public.onflows_analysis_generations g
    where g.athlete_alias = p_athlete_alias
      and g.status = 'SUPERSEDED'
  ), candidates as (
    select g.generation_id
    from public.onflows_analysis_generations g
    left join ranked_superseded ranked
      on ranked.generation_id = g.generation_id
    where g.athlete_alias = p_athlete_alias
      and g.created_at < now() - p_terminal_older_than
      and (
        g.status in ('FAILED', 'BUILDING')
        or (g.status = 'SUPERSEDED' and ranked.recency > p_keep_superseded)
      )
      and not exists (
        select 1 from public.onflows_athlete_analysis_state state
        where state.athlete_alias = p_athlete_alias
          and state.active_generation_id = g.generation_id
      )
      and not exists (
        select 1 from public.onflows_sync_jobs running
        where running.current_generation_id = g.generation_id
          and running.status = 'RUNNING'
      )
      and not exists (
        select 1 from public.onflows_analysis_generations child
        where child.base_generation_id = g.generation_id
          and child.status in ('BUILDING', 'READY')
      )
      and not exists (
        select 1 from public.onflows_analysis_generations consumer
        where consumer.activity_set_generation_id = g.generation_id
          and consumer.generation_id <> g.generation_id
      )
      and not exists (
        select 1 from onflows_private.history_generation_pins pin
        where pin.athlete_alias = g.athlete_alias
          and pin.generation_id = g.generation_id
      )
    order by g.created_at, g.generation_id
    limit p_batch_limit
  )
  select coalesce(array_agg(c.generation_id), '{}'::uuid[])
  into candidate_ids
  from candidates c;

  select count(*)::integer into activity_rows
  from public.onflows_analysis_generation_activities a
  where a.generation_id = any(candidate_ids);

  update public.onflows_sync_jobs j
  set current_generation_id = null, updated_at = now()
  where j.current_generation_id = any(candidate_ids)
    and j.status in ('SUCCEEDED', 'FAILED', 'SUPERSEDED');

  delete from public.onflows_analysis_generations g
  where g.generation_id = any(candidate_ids);
  get diagnostics generation_rows = row_count;

  with candidates as (
    select j.job_id
    from public.onflows_sync_jobs j
    where j.athlete_alias = p_athlete_alias
      and j.status in ('SUCCEEDED', 'FAILED', 'SUPERSEDED')
      and j.completed_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generations g
        where g.job_id = j.job_id
      )
    order by j.completed_at, j.job_id
    limit p_batch_limit
  )
  delete from public.onflows_sync_jobs j
  using candidates c
  where j.job_id = c.job_id;
  get diagnostics job_rows = row_count;

  -- Once generation reads are active, old unpinned catalog pointers are only a
  -- legacy projection.  Release them first so run -> input -> catalog GC can
  -- converge without weakening the revision-zero fallback.
  with candidates as (
    select catalog.activity_ref
    from public.onflows_activity_catalog catalog
    where catalog.athlete_alias = p_athlete_alias
      and catalog.last_seen_at < now() - p_terminal_older_than
      and (
        catalog.latest_shadow_run_key is not null
        or catalog.latest_canonical_run_key is not null
      )
      and exists (
        select 1
        from public.onflows_athlete_analysis_state state
        join public.onflows_analysis_generations active_generation
          on active_generation.generation_id = state.active_generation_id
         and active_generation.athlete_alias = state.athlete_alias
         and active_generation.status = 'ACTIVE'
        where state.athlete_alias = p_athlete_alias
      )
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.athlete_alias = catalog.athlete_alias
          and retained.activity_ref = catalog.activity_ref
      )
    order by catalog.last_seen_at, catalog.activity_ref
    limit p_batch_limit
  )
  update public.onflows_activity_catalog catalog
  set latest_shadow_run_key = null,
      latest_canonical_run_key = null,
      metadata_synced_at = now()
  from candidates c
  where catalog.athlete_alias = p_athlete_alias
    and catalog.activity_ref = c.activity_ref;

  with candidates as (
    select d.run_key
    from public.onflows_activity_derived_runs d
    where d.athlete_alias = p_athlete_alias
      and d.created_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.shadow_run_key = d.run_key
      )
      and not exists (
        select 1 from public.onflows_activity_catalog catalog
        where catalog.latest_shadow_run_key = d.run_key
      )
    order by d.created_at, d.run_key
    limit p_batch_limit
  )
  delete from public.onflows_activity_derived_runs d
  using candidates c
  where d.run_key = c.run_key;
  get diagnostics shadow_rows = row_count;

  with candidates as (
    select canonical.run_key
    from public.onflows_activity_canonical_runs canonical
    where canonical.athlete_alias = p_athlete_alias
      and canonical.created_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.canonical_run_key = canonical.run_key
      )
      and not exists (
        select 1 from public.onflows_activity_catalog catalog
        where catalog.latest_canonical_run_key = canonical.run_key
      )
    order by canonical.created_at, canonical.run_key
    limit p_batch_limit
  )
  delete from public.onflows_activity_canonical_runs canonical
  using candidates c
  where canonical.run_key = c.run_key;
  get diagnostics canonical_rows = row_count;

  with candidates as (
    select model_input.input_key
    from public.onflows_activity_model_inputs model_input
    where model_input.athlete_alias = p_athlete_alias
      and model_input.created_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.input_key = model_input.input_key
      )
      and not exists (
        select 1 from public.onflows_activity_derived_runs derived
        where derived.input_key = model_input.input_key
      )
    order by model_input.created_at, model_input.input_key
    limit p_batch_limit
  )
  delete from public.onflows_activity_model_inputs model_input
  using candidates c
  where model_input.input_key = c.input_key;
  get diagnostics input_rows = row_count;

  with candidates as (
    select catalog.activity_ref
    from public.onflows_activity_catalog catalog
    where catalog.athlete_alias = p_athlete_alias
      and catalog.last_seen_at < now() - p_terminal_older_than
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.athlete_alias = catalog.athlete_alias
          and retained.activity_ref = catalog.activity_ref
      )
      and not exists (
        select 1 from public.onflows_activity_model_inputs model_input
        where model_input.athlete_alias = catalog.athlete_alias
          and model_input.activity_ref = catalog.activity_ref
      )
      and not exists (
        select 1 from public.onflows_activity_derived_runs derived
        where derived.athlete_alias = catalog.athlete_alias
          and derived.activity_ref = catalog.activity_ref
      )
      and not exists (
        select 1 from public.onflows_activity_canonical_runs canonical
        where canonical.athlete_alias = catalog.athlete_alias
          and canonical.activity_ref = catalog.activity_ref
      )
    order by catalog.last_seen_at, catalog.activity_ref
    limit p_batch_limit
  )
  delete from public.onflows_activity_catalog catalog
  using candidates c
  where catalog.athlete_alias = p_athlete_alias
    and catalog.activity_ref = c.activity_ref;
  get diagnostics catalog_rows = row_count;

  return query select generation_rows, activity_rows, job_rows,
    shadow_rows, canonical_rows, input_rows, catalog_rows;
end;
$$;

-- Bootstrap/backfill is not authorization to delete. Keep the live guard.
revoke execute on function public.prune_onflows_analysis_generations(text,integer,interval,integer) from service_role;

commit;
