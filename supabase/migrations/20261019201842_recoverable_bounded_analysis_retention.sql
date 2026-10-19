-- Restore optional cleanup only after owner verification of historical roots.
-- No service grant is restored by this migration.
begin;
set local lock_timeout='2s';
set local statement_timeout='60s';

create table onflows_private.analysis_retention_coverage(
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 verified_at timestamptz not null default clock_timestamp(),
 evidence jsonb not null check(jsonb_typeof(evidence)='object')
);
-- New settings rows cannot already have immutable model/response/plan history
-- under the existing immediate settings FKs. Establish coverage from the first
-- write; pre-existing aliases still require the explicit bounded owner census.
create function onflows_private.initialize_analysis_retention_coverage()
returns trigger language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.onflows_model_entries e where e.athlete_alias=new.athlete_alias)
  and not exists(select 1 from public.onflows_response_entries e where e.athlete_alias=new.athlete_alias)
  and not exists(select 1 from public.onflows_management_entries e where e.athlete_alias=new.athlete_alias)
  and not exists(select 1 from public.onflows_management_plan_revisions e where e.athlete_alias=new.athlete_alias) then
  insert into onflows_private.analysis_retention_coverage(athlete_alias,evidence)
   values(new.athlete_alias,'{"source":"NEW_ALIAS_EMPTY_HISTORY","protected_from_first_write":true}'::jsonb)
   on conflict(athlete_alias) do nothing;
 end if;
 return new;
end $$;
revoke all on function onflows_private.initialize_analysis_retention_coverage() from public,anon,authenticated,service_role;
create trigger analysis_retention_new_alias after insert on public.onflows_athlete_settings
 for each row execute function onflows_private.initialize_analysis_retention_coverage();
create table onflows_private.analysis_cleanup_batches(
 batch_id uuid primary key default gen_random_uuid(),
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 transaction_id bigint not null,
 created_at timestamptz not null default clock_timestamp(),
 recoverable_until timestamptz not null default(clock_timestamp()+interval '14 days'),
 restored_at timestamptz,payload_expired_at timestamptz,
 policy jsonb not null,counts jsonb not null default '{}'::jsonb,
 backup_rows integer not null default 0 check(backup_rows between 0 and 1000),
 backup_bytes bigint not null default 0 check(backup_bytes between 0 and 16777216)
);
create index analysis_cleanup_batches_expiry on onflows_private.analysis_cleanup_batches(recoverable_until,batch_id)
 where payload_expired_at is null;
create table onflows_private.analysis_cleanup_rows(
 batch_id uuid not null references onflows_private.analysis_cleanup_batches(batch_id) on delete cascade,
 table_name text not null check(table_name in('onflows_activity_catalog','onflows_activity_model_inputs',
  'onflows_activity_derived_runs','onflows_activity_canonical_runs','onflows_sync_jobs',
  'onflows_analysis_generations','onflows_analysis_generation_activities','onflows_activity_run_summaries')),
 row_key jsonb not null check(jsonb_typeof(row_key)='object'),
 before_row jsonb,before_sha256 text not null,after_sha256 text,
 final_operation text not null check(final_operation in('UPDATE','DELETE')),
 schema_sha256 text not null,logical_bytes integer not null check(logical_bytes>0),
 primary key(batch_id,table_name,row_key)
);
do $$ declare name text;begin
 foreach name in array array['analysis_retention_coverage','analysis_cleanup_batches','analysis_cleanup_rows'] loop
  execute format('alter table onflows_private.%I enable row level security',name);
  execute format('revoke all on onflows_private.%I from public,anon,authenticated,service_role',name);
 end loop;
end $$;

create function onflows_private.analysis_cleanup_row_key(p_table text,p_row jsonb)
returns jsonb language plpgsql immutable set search_path='' as $$
begin
 case p_table
 when 'onflows_activity_catalog' then return pg_catalog.jsonb_build_object('activity_ref',p_row->'activity_ref');
 when 'onflows_activity_model_inputs' then return pg_catalog.jsonb_build_object('input_key',p_row->'input_key');
 when 'onflows_activity_derived_runs','onflows_activity_canonical_runs','onflows_activity_run_summaries' then
  return pg_catalog.jsonb_build_object('run_key',p_row->'run_key');
 when 'onflows_sync_jobs' then return pg_catalog.jsonb_build_object('job_id',p_row->'job_id');
 when 'onflows_analysis_generations' then return pg_catalog.jsonb_build_object('generation_id',p_row->'generation_id');
 when 'onflows_analysis_generation_activities' then
  return pg_catalog.jsonb_build_object('generation_id',p_row->'generation_id','activity_ref',p_row->'activity_ref');
 else raise exception 'Unsupported cleanup table';end case;
end $$;
create function onflows_private.analysis_cleanup_schema_hash(p_table text)
returns text language sql stable set search_path='' as $$
 select pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.string_agg(
  a.attname||':'||a.atttypid::text||':'||a.atttypmod::text||':'||a.attnotnull::text||':'||a.attgenerated::text||':'||a.attidentity::text,
  ',' order by a.attnum),'UTF8')),'hex')
 from pg_catalog.pg_attribute a where a.attrelid=pg_catalog.to_regclass('public.'||p_table)
 and a.attnum>0 and not a.attisdropped
$$;
create function onflows_private.capture_analysis_cleanup_row()
returns trigger language plpgsql security definer set search_path='' as $$
declare batch uuid;batch_alias text;old_document jsonb;new_document jsonb;key_document jsonb;
 row_bytes integer;previously_saved boolean;fingerprint text;
begin
 if coalesce(pg_catalog.current_setting('onflows.cleanup_batch_id',true),'')='' then
  if tg_op='DELETE' then return old;else return new;end if;
 end if;
 batch:=pg_catalog.current_setting('onflows.cleanup_batch_id',true)::uuid;
 select b.athlete_alias into batch_alias from onflows_private.analysis_cleanup_batches b
  where b.batch_id=batch and b.transaction_id=pg_catalog.txid_current() for update;
 if not found then raise exception 'Invalid cleanup journal transaction';end if;
 old_document:=pg_catalog.to_jsonb(old);
 if old_document->>'athlete_alias' is distinct from batch_alias then raise exception 'Cleanup crossed athlete boundary';end if;
 if tg_op='UPDATE' then new_document:=pg_catalog.to_jsonb(new);if new_document=old_document then return new;end if;end if;
 key_document:=onflows_private.analysis_cleanup_row_key(tg_table_name,old_document);
 select exists(select 1 from onflows_private.analysis_cleanup_rows r
  where r.batch_id=batch and r.table_name=tg_table_name and r.row_key=key_document) into previously_saved;
 if not previously_saved then
  -- The budget includes a small metadata envelope per first preimage. Exceeding
  -- it aborts the ENTIRE transaction; no unjournaled partial cleanup commits.
  row_bytes:=pg_catalog.octet_length(old_document::text)+pg_catalog.octet_length(key_document::text)+512;
  update onflows_private.analysis_cleanup_batches b set backup_rows=b.backup_rows+1,backup_bytes=b.backup_bytes+row_bytes
   where b.batch_id=batch and b.backup_rows<1000 and b.backup_bytes+row_bytes<=16777216;
  if not found then raise exception 'Cleanup recovery budget exceeded';end if;
 end if;
 fingerprint:=onflows_private.analysis_cleanup_schema_hash(tg_table_name);
 insert into onflows_private.analysis_cleanup_rows(batch_id,table_name,row_key,before_row,before_sha256,after_sha256,final_operation,schema_sha256,logical_bytes)
 values(batch,tg_table_name,key_document,old_document,
  pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(old_document::text,'UTF8')),'hex'),
  case when tg_op='UPDATE' then pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(new_document::text,'UTF8')),'hex') end,
  tg_op,fingerprint,coalesce(row_bytes,1))
 on conflict(batch_id,table_name,row_key) do update set final_operation=excluded.final_operation,after_sha256=excluded.after_sha256;
 if tg_op='DELETE' then return old;else return new;end if;
end $$;
do $$ declare name text;begin
 foreach name in array array['onflows_activity_catalog','onflows_activity_model_inputs','onflows_activity_derived_runs',
  'onflows_activity_canonical_runs','onflows_sync_jobs','onflows_analysis_generations','onflows_analysis_generation_activities','onflows_activity_run_summaries'] loop
  execute format('create trigger analysis_cleanup_journal before update or delete on public.%I for each row execute function onflows_private.capture_analysis_cleanup_row()',name);
 end loop;
end $$;

-- Private restore refuses schema drift and concurrent replacement. It never
-- overwrites a newer row, an occupied alternate unique key, or a changed pointer.
create function onflows_private.restore_analysis_cleanup_batch(p_batch uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
declare batch onflows_private.analysis_cleanup_batches%rowtype;item record;current_document jsonb;
 predicate text;column_list text;assignments text;current_hash text;restored integer:=0;
begin
 select * into batch from onflows_private.analysis_cleanup_batches b where b.batch_id=p_batch for update nowait;
 if not found then raise exception 'Cleanup recovery batch is unavailable';end if;
 if batch.restored_at is not null then return pg_catalog.jsonb_build_object('restored',true,'replayed',true,'batch_id',p_batch);end if;
 if batch.payload_expired_at is not null or batch.recoverable_until<=clock_timestamp() then raise exception 'Cleanup recovery window has expired';end if;
 if coalesce(pg_catalog.current_setting('onflows.cleanup_batch_id',true),'')<>'' then raise exception 'Cleanup restore cannot nest';end if;
 if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended('history-activity-pins:'||batch.athlete_alias,0)) then raise lock_not_available;end if;
 set constraints all deferred;
 for item in select r.* from onflows_private.analysis_cleanup_rows r where r.batch_id=p_batch
  order by case r.table_name when 'onflows_activity_catalog' then 1 when 'onflows_activity_model_inputs' then 2
   when 'onflows_activity_derived_runs' then 3 when 'onflows_activity_canonical_runs' then 4
   when 'onflows_sync_jobs' then 5 when 'onflows_analysis_generations' then 6
   when 'onflows_analysis_generation_activities' then 7 else 8 end,r.row_key loop
  if item.before_row is null or item.schema_sha256 is distinct from onflows_private.analysis_cleanup_schema_hash(item.table_name)
   or item.before_sha256 is distinct from pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(item.before_row::text,'UTF8')),'hex') then
   raise exception 'Cleanup recovery evidence does not match current schema';end if;
  select pg_catalog.string_agg(format('r.%1$I = ($1->>%2$L)::%3$s',a.attname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod)),' and ' order by a.attnum)
   into predicate from pg_catalog.pg_attribute a where a.attrelid=pg_catalog.to_regclass('public.'||item.table_name)
    and a.attname in(select pg_catalog.jsonb_object_keys(item.row_key));
  execute format('select pg_catalog.to_jsonb(r) from public.%I r where %s for update nowait',item.table_name,predicate)
   into current_document using item.row_key;
  if item.final_operation='DELETE' then
   if current_document is not null then
    if current_document<>item.before_row then raise exception 'Cleanup restore conflicts with a newer row';end if;
   else
    execute format('insert into public.%1$I select (pg_catalog.jsonb_populate_record(null::public.%1$I,$1)).*',item.table_name) using item.before_row;
   end if;
  else
   current_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(current_document::text,'UTF8')),'hex');
   if current_document is null then raise exception 'Cleanup restore target no longer exists';end if;
   if current_document<>item.before_row then
    if current_hash is distinct from item.after_sha256 then raise exception 'Cleanup restore conflicts with a changed pointer';end if;
    select pg_catalog.string_agg(format('%1$I = s.%1$I',a.attname),',' order by a.attnum) into assignments
     from pg_catalog.pg_attribute a where a.attrelid=pg_catalog.to_regclass('public.'||item.table_name)
      and a.attnum>0 and not a.attisdropped and a.attgenerated='';
    execute format('update public.%1$I r set %2$s from pg_catalog.jsonb_populate_record(null::public.%1$I,$2) s where %3$s',item.table_name,assignments,predicate)
     using item.row_key,item.before_row;
   end if;
  end if;
  restored:=restored+1;
 end loop;
 -- Check deferred graph constraints before marking an apparently successful
 -- restore. New references/pointer conflicts abort the whole operation.
 set constraints all immediate;
 update onflows_private.analysis_cleanup_batches b set restored_at=clock_timestamp() where b.batch_id=p_batch;
 return pg_catalog.jsonb_build_object('restored',true,'replayed',false,'batch_id',p_batch,'rows',restored);
end $$;

-- Expiry removes only recovery PREIMAGES after 14 days. Keys, hashes, counts,
-- schema fingerprints and original expiry remain as a permanent small manifest.
create function public.expire_onflows_analysis_cleanup_recovery(p_limit integer default 10)
returns integer language plpgsql security definer set search_path='' as $$
declare selected uuid[];expired integer;
begin
 if p_limit is null or p_limit not between 1 and 50 then raise exception 'Invalid cleanup recovery expiry batch';end if;
 select coalesce(array_agg(b.batch_id),'{}'::uuid[]) into selected from(
  select candidate.batch_id from onflows_private.analysis_cleanup_batches candidate
   where candidate.payload_expired_at is null and candidate.recoverable_until<=clock_timestamp()
   order by candidate.recoverable_until,candidate.batch_id for update skip locked limit p_limit) b;
 update onflows_private.analysis_cleanup_rows r set before_row=null where r.batch_id=any(selected);
 update onflows_private.analysis_cleanup_batches b set payload_expired_at=clock_timestamp() where b.batch_id=any(selected);
 get diagnostics expired=row_count;return expired;
end $$;
revoke all on function onflows_private.analysis_cleanup_row_key(text,jsonb),
 onflows_private.analysis_cleanup_schema_hash(text),onflows_private.capture_analysis_cleanup_row(),
 onflows_private.restore_analysis_cleanup_batch(uuid) from public,anon,authenticated,service_role;
revoke all on function public.expire_onflows_analysis_cleanup_recovery(integer) from public,anon,authenticated,service_role;
grant execute on function public.expire_onflows_analysis_cleanup_recovery(integer) to service_role;

-- Cost every first preimage, including implicit FK effects, before choosing a
-- phase's keys. A large generation/run is retained and cannot starve independent
-- orphan phases. The capture trigger is the final atomic budget enforcement.
create function onflows_private.analysis_cleanup_candidate_cost(p_batch uuid,p_table text,p_row jsonb)
returns table(required_rows integer,required_bytes bigint)
language plpgsql stable set search_path='' as $$
declare dependency_count integer;
begin
 if p_table='onflows_analysis_generations' then
  select 1+
   (select count(*) from(select 1 from public.onflows_analysis_generation_activities a where a.generation_id=(p_row->>'generation_id')::uuid limit 1001) x)+
   (select count(*) from(select 1 from public.onflows_sync_jobs j where j.current_generation_id=(p_row->>'generation_id')::uuid and j.status in('SUCCEEDED','FAILED','SUPERSEDED') limit 1001) x)+
   (select count(*) from(select 1 from public.onflows_analysis_generations g where g.base_generation_id=(p_row->>'generation_id')::uuid limit 1001) x)
   into dependency_count;
  if dependency_count>1000 then return query select dependency_count,16777217::bigint;return;end if;
 end if;
 return query with preimages(table_name,row_document) as(
  select p_table,p_row
  union all select 'onflows_analysis_generation_activities',pg_catalog.to_jsonb(a)
   from public.onflows_analysis_generation_activities a where p_table='onflows_analysis_generations' and a.generation_id=(p_row->>'generation_id')::uuid
  union all select 'onflows_sync_jobs',pg_catalog.to_jsonb(j) from public.onflows_sync_jobs j
   where p_table='onflows_analysis_generations' and j.current_generation_id=(p_row->>'generation_id')::uuid and j.status in('SUCCEEDED','FAILED','SUPERSEDED')
  union all select 'onflows_analysis_generations',pg_catalog.to_jsonb(g) from public.onflows_analysis_generations g
   where p_table='onflows_analysis_generations' and g.base_generation_id=(p_row->>'generation_id')::uuid
  union all select 'onflows_activity_run_summaries',pg_catalog.to_jsonb(s) from public.onflows_activity_run_summaries s
   where p_table='onflows_activity_derived_runs' and s.run_key=p_row->>'run_key'
 ),distinct_preimages as(
  select distinct table_name,onflows_private.analysis_cleanup_row_key(table_name,row_document) as row_key,row_document from preimages
 ),unsaved as(
  select d.* from distinct_preimages d where not exists(select 1 from onflows_private.analysis_cleanup_rows r
   where r.batch_id=p_batch and r.table_name=d.table_name and r.row_key=d.row_key)
 ) select count(*)::integer,coalesce(sum(pg_catalog.octet_length(row_document::text)+pg_catalog.octet_length(row_key::text)+512),0)::bigint from unsaved;
end $$;
create function onflows_private.budget_analysis_cleanup_keys(p_batch uuid,p_table text,p_keys text[],p_limit integer)
returns text[] language plpgsql set search_path='' as $$
declare key_column text;key_type text;selected_key text;document jsonb;cost record;
 rows_left integer;bytes_left bigint;accepted text[]:='{}';batch_alias text;
begin
 -- This also validates the whitelist before interpolating an identifier.
 perform onflows_private.analysis_cleanup_row_key(p_table,'{}'::jsonb);
 key_column:=case p_table when 'onflows_activity_catalog' then 'activity_ref' when 'onflows_activity_model_inputs' then 'input_key'
  when 'onflows_sync_jobs' then 'job_id' when 'onflows_analysis_generations' then 'generation_id' else 'run_key' end;
 key_type:=case when key_column in('job_id','generation_id') then 'uuid' else 'text' end;
 select 1000-b.backup_rows,16777216-b.backup_bytes,b.athlete_alias into rows_left,bytes_left,batch_alias
  from onflows_private.analysis_cleanup_batches b where b.batch_id=p_batch and b.transaction_id=pg_catalog.txid_current();
 if not found then raise exception 'Invalid cleanup budget transaction';end if;
 foreach selected_key in array p_keys loop
  if pg_catalog.cardinality(accepted)>=p_limit then exit;end if;
  execute format('select pg_catalog.to_jsonb(r) from public.%I r where r.%I=$1::%s for update skip locked',p_table,key_column,key_type)
   into document using selected_key;
  if document is null then continue;end if;
  if document->>'athlete_alias' is distinct from batch_alias then raise exception 'Cleanup crossed athlete boundary';end if;
  select * into cost from onflows_private.analysis_cleanup_candidate_cost(p_batch,p_table,document);
  if cost.required_rows<=rows_left and cost.required_bytes<=bytes_left then
   accepted:=pg_catalog.array_append(accepted,selected_key);rows_left:=rows_left-cost.required_rows;bytes_left:=bytes_left-cost.required_bytes;
  else
   update onflows_private.analysis_cleanup_batches b set policy=pg_catalog.jsonb_set(b.policy,'{budget_skips}',
    coalesce(b.policy->'budget_skips','[]'::jsonb)||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
     'table',p_table,'key',selected_key,'required_rows',cost.required_rows,'required_bytes',cost.required_bytes,'reason','RECOVERY_BUDGET')))
    where b.batch_id=p_batch;
  end if;
 end loop;
 return accepted;
end $$;
revoke all on function onflows_private.analysis_cleanup_candidate_cost(uuid,text,jsonb),
 onflows_private.budget_analysis_cleanup_keys(uuid,text,text[],integer) from public,anon,authenticated,service_role;

-- The archive is a separate Storage lifecycle. Retain a row and its locator
-- together until object GC/restoration is separately proven. Canonical JSONB
-- text exposes the exact ASCII marker anywhere without deep-copying arrays;
-- a marker in ordinary text conservatively retains an extra technical row.
create function onflows_private.has_analysis_archive_locator(p_payload jsonb)
returns boolean language sql immutable parallel safe set search_path='' as $$
 select coalesce(pg_catalog.strpos(p_payload::text,'onflows-history-object-xz-v1')>0,false)
$$;
revoke all on function onflows_private.has_analysis_archive_locator(jsonb) from public,anon,authenticated,service_role;

-- A previously unavailable deterministic key may be published again without a
-- later history INSERT. Its ledger remains a retention root in its own right.
-- Preserve the current catalog parent (including its projection pointers) of a
-- reappeared input/run, even before another history append creates normal pins.
create function onflows_private.legacy_history_keeps_activity(p_alias text,p_activity_ref text)
returns boolean language sql stable set search_path='' as $$
 select exists(select 1 from onflows_private.legacy_unavailable_activity_references l
  where l.athlete_alias=p_alias and l.kind='activity' and l.reference_key=p_activity_ref)
 or exists(select 1 from public.onflows_activity_model_inputs i
  join onflows_private.legacy_unavailable_activity_references l
   on l.athlete_alias=i.athlete_alias and l.kind='input' and l.reference_key=i.input_key
  where i.athlete_alias=p_alias and i.activity_ref=p_activity_ref)
 or exists(select 1 from public.onflows_activity_derived_runs d
  join onflows_private.legacy_unavailable_activity_references l
   on l.athlete_alias=d.athlete_alias and l.kind='run' and l.reference_key=d.run_key
  where d.athlete_alias=p_alias and d.activity_ref=p_activity_ref)
 or exists(select 1 from public.onflows_activity_canonical_runs c
  join onflows_private.legacy_unavailable_activity_references l
   on l.athlete_alias=c.athlete_alias and l.kind='run' and l.reference_key=c.run_key
  where c.athlete_alias=p_alias and c.activity_ref=p_activity_ref)
$$;
revoke all on function onflows_private.legacy_history_keeps_activity(text,text) from public,anon,authenticated,service_role;

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
  cleanup_batch uuid;
  previous_batch_setting text;
  journal_rows integer;
  activity_rows integer := 0;
  generation_rows integer := 0;
  job_rows integer := 0;
  shadow_rows integer := 0;
  canonical_rows integer := 0;
  input_rows integer := 0;
  catalog_rows integer := 0;
begin
  if p_athlete_alias is null or p_athlete_alias=''
     or p_keep_superseded is null or p_batch_limit is null or p_terminal_older_than is null
     or p_keep_superseded not between 1 and 50
     or p_batch_limit not between 1 and 1000
     or p_terminal_older_than < interval '30 days'
     or p_terminal_older_than > interval '365 days' then
    raise exception 'invalid analysis retention policy';
  end if;

  if not exists(select 1 from onflows_private.analysis_retention_coverage c where c.athlete_alias=p_athlete_alias) then
    raise exception 'Historical activity retention coverage has not been verified';
  end if;
  if exists(select 1 from onflows_private.history_activity_cleanup_blocks b where b.athlete_alias=p_athlete_alias) then
    return query select 0,0,0,0,0,0,0;return;
  end if;
  if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended('history-activity-pins:'||p_athlete_alias,0)) then
    raise lock_not_available;
  end if;
  -- Recheck after acquiring the same guard used by every NEW historical pin.
  if exists(select 1 from onflows_private.history_activity_cleanup_blocks b where b.athlete_alias=p_athlete_alias) then
    return query select 0,0,0,0,0,0,0;return;
  end if;
  previous_batch_setting:=pg_catalog.current_setting('onflows.cleanup_batch_id',true);
  if coalesce(previous_batch_setting,'')<>'' then raise exception 'Analysis cleanup cannot nest';end if;
  insert into onflows_private.analysis_cleanup_batches(athlete_alias,transaction_id,policy)
   values(p_athlete_alias,pg_catalog.txid_current(),pg_catalog.jsonb_build_object(
    'keep_superseded',p_keep_superseded,'terminal_older_than',p_terminal_older_than::text,
    'requested_batch_limit',p_batch_limit,'effective_phase_limit',least(p_batch_limit,10),'candidate_scan_limit',50,
    'max_preimages',1000,'max_logical_backup_bytes',16777216,'recovery_days',14)) returning batch_id into cleanup_batch;
  perform pg_catalog.set_config('onflows.cleanup_batch_id',cleanup_batch::text,true);

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
      and not exists(select 1 from public.onflows_training_snapshots snapshot where snapshot.generation_id=g.generation_id)
      and not exists (
        select 1 from onflows_private.history_generation_pins pin
        where pin.athlete_alias = g.athlete_alias
          and pin.generation_id = g.generation_id
      )
    order by g.created_at, g.generation_id
    limit 50
    for update of g skip locked
  )
  select coalesce(array_agg(c.generation_id), '{}'::uuid[])
  into candidate_ids
  from candidates c;
  candidate_ids:=array(select k::uuid from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
    cleanup_batch,'onflows_analysis_generations',candidate_ids::text[],least(p_batch_limit,10))) selected(k));

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
    limit 50
    for update of j skip locked
  ), budgeted_candidates as (
    select k as job_id from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
      cleanup_batch,'onflows_sync_jobs',array(select c.job_id::text from candidates c),least(p_batch_limit,10))) selected(k)
  )
  delete from public.onflows_sync_jobs j
  using budgeted_candidates c
  where j.job_id = c.job_id::uuid;
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
      and not exists(select 1 from onflows_private.history_activity_pins pin
        where pin.athlete_alias=catalog.athlete_alias and pin.activity_ref=catalog.activity_ref)
      and not onflows_private.legacy_history_keeps_activity(catalog.athlete_alias,catalog.activity_ref)
    order by catalog.last_seen_at, catalog.activity_ref
    limit 50
    for update of catalog skip locked
  ), budgeted_candidates as (
    select k as activity_ref from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
      cleanup_batch,'onflows_activity_catalog',array(select c.activity_ref::text from candidates c),least(p_batch_limit,10))) selected(k)
  )
  update public.onflows_activity_catalog catalog
  set latest_shadow_run_key = null,
      latest_canonical_run_key = null,
      metadata_synced_at = now()
  from budgeted_candidates c
  where catalog.athlete_alias = p_athlete_alias
    and catalog.activity_ref = c.activity_ref;

  with candidates as (
    select d.run_key
    from public.onflows_activity_derived_runs d
    where d.athlete_alias = p_athlete_alias
      and d.created_at < now() - p_terminal_older_than
      and not onflows_private.has_analysis_archive_locator(d.result_payload)
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.shadow_run_key = d.run_key
      )
      and not exists (
        select 1 from public.onflows_activity_catalog catalog
        where catalog.latest_shadow_run_key = d.run_key
      )
      and not exists(select 1 from onflows_private.history_shadow_pins pin
        where pin.athlete_alias=d.athlete_alias and pin.run_key=d.run_key)
      and not exists(select 1 from onflows_private.legacy_unavailable_activity_references l
        where l.athlete_alias=d.athlete_alias and l.kind='run' and l.reference_key=d.run_key)
    order by d.created_at, d.run_key
    limit 50
    for update of d skip locked
  ), budgeted_candidates as (
    select k as run_key from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
      cleanup_batch,'onflows_activity_derived_runs',array(select c.run_key::text from candidates c),least(p_batch_limit,10))) selected(k)
  )
  delete from public.onflows_activity_derived_runs d
  using budgeted_candidates c
  where d.run_key = c.run_key;
  get diagnostics shadow_rows = row_count;

  with candidates as (
    select canonical.run_key
    from public.onflows_activity_canonical_runs canonical
    where canonical.athlete_alias = p_athlete_alias
      and canonical.created_at < now() - p_terminal_older_than
      and not onflows_private.has_analysis_archive_locator(canonical.result_payload)
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.canonical_run_key = canonical.run_key
      )
      and not exists (
        select 1 from public.onflows_activity_catalog catalog
        where catalog.latest_canonical_run_key = canonical.run_key
      )
      and not exists(select 1 from onflows_private.history_canonical_pins pin
        where pin.athlete_alias=canonical.athlete_alias and pin.run_key=canonical.run_key)
      and not exists(select 1 from onflows_private.legacy_unavailable_activity_references l
        where l.athlete_alias=canonical.athlete_alias and l.kind='run' and l.reference_key=canonical.run_key)
    order by canonical.created_at, canonical.run_key
    limit 50
    for update of canonical skip locked
  ), budgeted_candidates as (
    select k as run_key from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
      cleanup_batch,'onflows_activity_canonical_runs',array(select c.run_key::text from candidates c),least(p_batch_limit,10))) selected(k)
  )
  delete from public.onflows_activity_canonical_runs canonical
  using budgeted_candidates c
  where canonical.run_key = c.run_key;
  get diagnostics canonical_rows = row_count;

  with candidates as (
    select model_input.input_key
    from public.onflows_activity_model_inputs model_input
    where model_input.athlete_alias = p_athlete_alias
      and model_input.created_at < now() - p_terminal_older_than
      and not onflows_private.has_analysis_archive_locator(model_input.input_payload)
      and not exists (
        select 1 from public.onflows_analysis_generation_activities retained
        where retained.input_key = model_input.input_key
      )
      and not exists (
        select 1 from public.onflows_activity_derived_runs derived
        where derived.input_key = model_input.input_key
      )
      and not exists(select 1 from onflows_private.history_input_pins pin
        where pin.athlete_alias=model_input.athlete_alias and pin.input_key=model_input.input_key)
      and not exists(select 1 from onflows_private.legacy_unavailable_activity_references l
        where l.athlete_alias=model_input.athlete_alias and l.kind='input' and l.reference_key=model_input.input_key)
    order by model_input.created_at, model_input.input_key
    limit 50
    for update of model_input skip locked
  ), budgeted_candidates as (
    select k as input_key from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
      cleanup_batch,'onflows_activity_model_inputs',array(select c.input_key::text from candidates c),least(p_batch_limit,10))) selected(k)
  )
  delete from public.onflows_activity_model_inputs model_input
  using budgeted_candidates c
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
      and not exists(select 1 from onflows_private.history_activity_pins pin
        where pin.athlete_alias=catalog.athlete_alias and pin.activity_ref=catalog.activity_ref)
      and not onflows_private.legacy_history_keeps_activity(catalog.athlete_alias,catalog.activity_ref)
    order by catalog.last_seen_at, catalog.activity_ref
    limit 50
    for update of catalog skip locked
  ), budgeted_candidates as (
    select k as activity_ref from pg_catalog.unnest(onflows_private.budget_analysis_cleanup_keys(
      cleanup_batch,'onflows_activity_catalog',array(select c.activity_ref::text from candidates c),least(p_batch_limit,10))) selected(k)
  )
  delete from public.onflows_activity_catalog catalog
  using budgeted_candidates c
  where catalog.athlete_alias = p_athlete_alias
    and catalog.activity_ref = c.activity_ref;
  get diagnostics catalog_rows = row_count;

  perform pg_catalog.set_config('onflows.cleanup_batch_id',coalesce(previous_batch_setting,''),true);
  update onflows_private.analysis_cleanup_batches b set counts=pg_catalog.jsonb_build_object(
    'deleted_generations',generation_rows,'deleted_activity_rows',activity_rows,'deleted_jobs',job_rows,
    'deleted_shadow_runs',shadow_rows,'deleted_canonical_runs',canonical_rows,'deleted_model_inputs',input_rows,
    'deleted_catalog_rows',catalog_rows) where b.batch_id=cleanup_batch returning backup_rows into journal_rows;
  if journal_rows=0 then delete from onflows_private.analysis_cleanup_batches b where b.batch_id=cleanup_batch and not(b.policy ? 'budget_skips');end if;
  return query select generation_rows, activity_rows, job_rows,
    shadow_rows, canonical_rows, input_rows, catalog_rows;
end;
$$;


-- Deployment/backfill is not authorization to enable cleanup.
revoke execute on function public.prune_onflows_analysis_generations(text,integer,interval,integer) from service_role;

-- The worker receives only the fixed safe maintenance policy, never arbitrary
-- pruning parameters. All coverage, historical-pin, opaque, freshness, budget
-- and recovery guards remain in the single implementation above. Old workers
-- cannot use their revoked pruning RPC; deployment of the new worker follows
-- owner bootstrap/coverage and a verified synthetic restore.
create function public.maintain_onflows_analysis_history(p_alias text)
returns table(
 deleted_generations integer,deleted_activity_rows integer,deleted_jobs integer,
 deleted_shadow_runs integer,deleted_canonical_runs integer,deleted_model_inputs integer,deleted_catalog_rows integer
)
language sql security definer set search_path='' as $$
 select * from public.prune_onflows_analysis_generations(p_alias,5,interval '30 days',10)
$$;
revoke all on function public.maintain_onflows_analysis_history(text) from public,anon,authenticated,service_role;
grant execute on function public.maintain_onflows_analysis_history(text) to service_role;
commit;
