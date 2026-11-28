-- Add provenance protection before considering restoration of optional cleanup.
-- Bootstrap only: immutable history is backfilled in bounded owner-only batches.
begin;
set local lock_timeout='2s';
set local statement_timeout='60s';
lock table public.onflows_activity_catalog,public.onflows_activity_model_inputs,
  public.onflows_activity_derived_runs,public.onflows_activity_canonical_runs,
  public.onflows_model_entries,public.onflows_response_entries,
  public.onflows_management_entries,public.onflows_management_plan_revisions
  in share row exclusive mode nowait;

-- Fresh activities have a catalog stub from resolve_onflows_activity_ref before
-- input publication. Deferred NO ACTION also preserves populated account cleanup.
alter table public.onflows_activity_model_inputs
  add constraint onflows_activity_model_inputs_catalog_fk
  foreign key(athlete_alias,activity_ref)
  references public.onflows_activity_catalog(athlete_alias,activity_ref)
  on update restrict on delete no action deferrable initially deferred;
alter table public.onflows_activity_derived_runs
  add constraint onflows_activity_derived_runs_scoped_input_fk
  foreign key(input_key,athlete_alias,activity_ref)
  references public.onflows_activity_model_inputs(input_key,athlete_alias,activity_ref)
  on update restrict on delete no action deferrable initially deferred;
alter table public.onflows_activity_catalog
  add constraint onflows_activity_catalog_shadow_fk
  foreign key(latest_shadow_run_key,athlete_alias,activity_ref)
  references public.onflows_activity_derived_runs(run_key,athlete_alias,activity_ref)
  on update restrict on delete no action deferrable initially deferred,
  add constraint onflows_activity_catalog_canonical_fk
  foreign key(latest_canonical_run_key,athlete_alias,activity_ref)
  references public.onflows_activity_canonical_runs(run_key,athlete_alias,activity_ref)
  on update restrict on delete no action deferrable initially deferred;

-- EXPLAIN of all three retained-run anti-joins showed a full 85,328-row scan.
-- These indexes cover the actual lookup; catalog's small projection is unchanged.
create index onflows_generation_activities_shadow_reference
 on public.onflows_analysis_generation_activities(shadow_run_key)
 where shadow_run_key is not null;
create index onflows_generation_activities_canonical_reference
 on public.onflows_analysis_generation_activities(canonical_run_key)
 where canonical_run_key is not null;
create index onflows_generation_activities_input_reference
 on public.onflows_analysis_generation_activities(input_key)
 where input_key is not null;

create table onflows_private.history_activity_pins(
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 activity_ref text not null,
 primary key(athlete_alias,activity_ref),
 foreign key(athlete_alias,activity_ref) references public.onflows_activity_catalog(athlete_alias,activity_ref)
 on update cascade on delete no action deferrable initially deferred
);
create table onflows_private.history_input_pins(
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 input_key text not null,activity_ref text not null,
 primary key(athlete_alias,input_key),
 foreign key(input_key,athlete_alias,activity_ref)
 references public.onflows_activity_model_inputs(input_key,athlete_alias,activity_ref)
 on update cascade on delete no action deferrable initially deferred
);
create table onflows_private.history_shadow_pins(
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 run_key text not null,activity_ref text not null,
 primary key(athlete_alias,run_key),
 foreign key(run_key,athlete_alias,activity_ref)
 references public.onflows_activity_derived_runs(run_key,athlete_alias,activity_ref)
 on update cascade on delete no action deferrable initially deferred
);
create table onflows_private.history_canonical_pins(
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 run_key text not null,activity_ref text not null,
 primary key(athlete_alias,run_key),
 foreign key(run_key,athlete_alias,activity_ref)
 references public.onflows_activity_canonical_runs(run_key,athlete_alias,activity_ref)
 on update cascade on delete no action deferrable initially deferred
);
-- Explicit evidence of already unavailable legacy provenance, never invented
-- replacement records. Only the owner backfill may introduce these exceptions.
create table onflows_private.legacy_unavailable_activity_references(
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 kind text not null check(kind in('run','input','activity')),reference_key text not null,
 recorded_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,kind,reference_key)
);
-- Opaque compressed/archive documents cannot be interpreted by PostgreSQL.
-- Their presence blocks alias cleanup until an owner proves hydrated coverage.
create table onflows_private.history_activity_cleanup_blocks(
 athlete_alias text primary key references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 reason text not null check(reason='OPAQUE_HISTORY_REFERENCE'),
 recorded_at timestamptz not null default clock_timestamp()
);
create table onflows_private.legacy_opaque_activity_references(
 athlete_alias text not null references public.onflows_athlete_settings(athlete_alias) on update cascade on delete cascade,
 opaque_sha256 text not null check(opaque_sha256 ~ '^[a-f0-9]{64}$'),
 recorded_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,opaque_sha256)
);
do $$ declare table_name text; begin
 foreach table_name in array array['history_activity_pins','history_input_pins','history_shadow_pins',
  'history_canonical_pins','legacy_unavailable_activity_references','history_activity_cleanup_blocks','legacy_opaque_activity_references'] loop
  execute format('alter table onflows_private.%I enable row level security',table_name);
  execute format('revoke all on onflows_private.%I from public,anon,authenticated,service_role',table_name);
 end loop;
end $$;

create function onflows_private.history_activity_references(p_payload jsonb)
returns jsonb language plpgsql immutable parallel safe set search_path='' as $$
declare item record; value_text text; path pg_catalog.jsonpath;
 runs text[]:='{}';inputs text[]:='{}';activities text[]:='{}';opaque_hashes text[]:='{}';
begin
 -- Fast known-object prefilter avoids copying every large JSON ancestor.
 -- Unknown suffix keys conservatively select the complete object scanner.
 if pg_catalog.regexp_replace(p_payload::text,
  $known$(?<!\\)"(?:run_key|source_run_key|shadow_run_key|canonical_run_key|input_key|source_input_key)"[[:space:]]*:$known$,
  '', 'g') ~ $unknown$_(?:run_key|input_key)"[[:space:]]*:$unknown$ then
  path:='strict $.** ? (@.type()=="object").keyvalue() ? (@.key like_regex "(^|_)(run_key|input_key)$")'::pg_catalog.jsonpath;
 else
  path:='strict $.** ? (@.type()=="object" && (exists(@.run_key)||exists(@.source_run_key)||exists(@.shadow_run_key)||exists(@.canonical_run_key)||exists(@.input_key)||exists(@.source_input_key))).keyvalue() ? (@.key like_regex "(^|_)(run_key|input_key)$")'::pg_catalog.jsonpath;
 end if;
 for item in select v.value->>'key' as name,v.value->'value' as value
  from pg_catalog.jsonb_path_query(p_payload,path) v(value) loop
  if item.value='null'::jsonb then continue; end if;
  value_text:=item.value#>>'{}';
  if pg_catalog.jsonb_typeof(item.value)<>'string' or value_text!~'^[a-f0-9]{64}$' then
   raise exception 'Historical activity reference is unavailable; reload and try again'
    using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
  end if;
  if item.name ~ '(^|_)input_key$' then inputs:=pg_catalog.array_append(inputs,value_text);
  else runs:=pg_catalog.array_append(runs,value_text);end if;
 end loop;
 -- Activity identity can occur in arrays, learning observation.id, session
 -- continuation fields and future key names. Match scalar identities anywhere.
 for value_text in select v.value#>>'{}' from pg_catalog.jsonb_path_query(p_payload,
  'strict $.** ? (@.type()=="string" && @ like_regex "^(act_[a-f0-9]{32}|shadow-[a-f0-9]{32})$")'::pg_catalog.jsonpath) v(value)
 loop activities:=pg_catalog.array_append(activities,value_text);end loop;
 for value_text in select pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(v.value::text,'UTF8')),'hex')
  from pg_catalog.jsonb_path_query(p_payload,'strict $.** ? (@.type()=="object" && exists(@.codec))'::pg_catalog.jsonpath) v(value)
 loop opaque_hashes:=pg_catalog.array_append(opaque_hashes,value_text);end loop;
 return pg_catalog.jsonb_build_object(
  'run_keys',array(select distinct v from pg_catalog.unnest(runs) r(v) order by v),
  'input_keys',array(select distinct v from pg_catalog.unnest(inputs) r(v) order by v),
  'activity_refs',array(select distinct v from pg_catalog.unnest(activities) r(v) order by v),
  'opaque',pg_catalog.cardinality(opaque_hashes)>0,
  'opaque_hashes',array(select distinct v from pg_catalog.unnest(opaque_hashes) r(v) order by v));
end $$;
revoke all on function onflows_private.history_activity_references(jsonb) from public,anon,authenticated,service_role;

create function onflows_private.pin_history_activity_references(
 p_alias text,p_references jsonb,p_allow_legacy_missing boolean default false
) returns void language plpgsql security definer set search_path='' as $$
declare root record; row_data record; row_found boolean;
 runs text[];inputs text[];activities text[];opaque_hashes text[];new_runs text[]:='{}';new_inputs text[]:='{}';new_activities text[]:='{}';
begin
 if p_alias is null or p_alias='' or p_allow_legacy_missing is null then
  raise exception 'Invalid historical activity pin request';end if;
 select coalesce(array_agg(value),'{}') into runs from pg_catalog.jsonb_array_elements_text(p_references->'run_keys') r(value);
 select coalesce(array_agg(value),'{}') into inputs from pg_catalog.jsonb_array_elements_text(p_references->'input_keys') r(value);
 select coalesce(array_agg(value),'{}') into activities from pg_catalog.jsonb_array_elements_text(p_references->'activity_refs') r(value);
 select coalesce(array_agg(value),'{}') into opaque_hashes from pg_catalog.jsonb_array_elements_text(p_references->'opaque_hashes') r(value);
 if not p_allow_legacy_missing and exists(select 1 from pg_catalog.unnest(opaque_hashes) r(k)
   where not exists(select 1 from onflows_private.legacy_opaque_activity_references p where p.athlete_alias=p_alias and p.opaque_sha256=r.k)) then
  raise foreign_key_violation;end if;
 -- Existing pins carry complete immutable parent closure and require no lock.
 if not exists(select 1 from pg_catalog.unnest(runs) r(k) where
    not exists(select 1 from onflows_private.history_shadow_pins p where p.athlete_alias=p_alias and p.run_key=r.k)
    and not exists(select 1 from onflows_private.history_canonical_pins p where p.athlete_alias=p_alias and p.run_key=r.k)
    and not exists(select 1 from onflows_private.legacy_unavailable_activity_references p where p.athlete_alias=p_alias and p.kind='run' and p.reference_key=r.k
      and not exists(select 1 from public.onflows_activity_derived_runs d where d.run_key=r.k)
      and not exists(select 1 from public.onflows_activity_canonical_runs c where c.run_key=r.k)))
  and not exists(select 1 from pg_catalog.unnest(inputs) r(k) where
    not exists(select 1 from onflows_private.history_input_pins p where p.athlete_alias=p_alias and p.input_key=r.k)
    and not exists(select 1 from onflows_private.legacy_unavailable_activity_references p where p.athlete_alias=p_alias and p.kind='input' and p.reference_key=r.k
      and not exists(select 1 from public.onflows_activity_model_inputs i where i.input_key=r.k)))
  and not exists(select 1 from pg_catalog.unnest(activities) r(k) where
    not exists(select 1 from onflows_private.history_activity_pins p where p.athlete_alias=p_alias and p.activity_ref=r.k)
    and not exists(select 1 from onflows_private.legacy_unavailable_activity_references p where p.athlete_alias=p_alias and p.kind='activity' and p.reference_key=r.k
      and not exists(select 1 from public.onflows_activity_catalog c where c.activity_ref=r.k)))
  and (not coalesce((p_references->>'opaque')::boolean,false)
    or (exists(select 1 from onflows_private.history_activity_cleanup_blocks b where b.athlete_alias=p_alias)
      and not exists(select 1 from pg_catalog.unnest(opaque_hashes) r(k) where not exists(
       select 1 from onflows_private.legacy_opaque_activity_references p where p.athlete_alias=p_alias and p.opaque_sha256=r.k)))) then return;end if;
 if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended('history-activity-pins:'||p_alias,0)) then
  raise lock_not_available;end if;
 if coalesce((p_references->>'opaque')::boolean,false) then
  insert into onflows_private.legacy_opaque_activity_references(athlete_alias,opaque_sha256)
   select p_alias,k from pg_catalog.unnest(opaque_hashes) r(k) order by k on conflict do nothing;
  insert into onflows_private.history_activity_cleanup_blocks(athlete_alias,reason)
   values(p_alias,'OPAQUE_HISTORY_REFERENCE') on conflict(athlete_alias) do nothing;
 end if;
 -- All new references for one athlete serialize non-blockingly across calls,
 -- preventing unique-index deadlocks in multirow inserts/backfill transactions.
 for root in select 'run' as kind,k from pg_catalog.unnest(runs) r(k)
  union select 'input',k from pg_catalog.unnest(inputs) r(k)
  union select 'activity',k from pg_catalog.unnest(activities) r(k)
  order by 1,2 loop
  row_found:=false;
  if root.kind='run' then
   for row_data in select d.run_key,d.input_key,d.activity_ref,d.athlete_alias from public.onflows_activity_derived_runs d
     where d.run_key=root.k for key share of d nowait loop
    if row_data.athlete_alias<>p_alias then raise foreign_key_violation;end if;
    new_runs:=pg_catalog.array_append(new_runs,row_data.run_key);
    inputs:=pg_catalog.array_append(inputs,row_data.input_key);
    activities:=pg_catalog.array_append(activities,row_data.activity_ref);row_found:=true;
   end loop;
   for row_data in select c.run_key,c.activity_ref,c.athlete_alias from public.onflows_activity_canonical_runs c
     where c.run_key=root.k for key share of c nowait loop
    if row_data.athlete_alias<>p_alias then raise foreign_key_violation;end if;
    insert into onflows_private.history_canonical_pins(athlete_alias,run_key,activity_ref)
     values(p_alias,row_data.run_key,row_data.activity_ref) on conflict do nothing;
    activities:=pg_catalog.array_append(activities,row_data.activity_ref);row_found:=true;
   end loop;
  elsif root.kind='input' then
   select true into row_found from public.onflows_activity_model_inputs i where i.input_key=root.k and i.athlete_alias=p_alias for key share of i nowait;
   if not coalesce(row_found,false) and exists(select 1 from public.onflows_activity_model_inputs i where i.input_key=root.k) then raise foreign_key_violation;end if;
  else
   select true into row_found from public.onflows_activity_catalog c where c.activity_ref=root.k and c.athlete_alias=p_alias for key share of c nowait;
   if not coalesce(row_found,false) and exists(select 1 from public.onflows_activity_catalog c where c.activity_ref=root.k) then raise foreign_key_violation;end if;
  end if;
  if not coalesce(row_found,false) then
   if p_allow_legacy_missing then
    insert into onflows_private.legacy_unavailable_activity_references(athlete_alias,kind,reference_key)
     values(p_alias,root.kind,root.k) on conflict do nothing;
   elsif not exists(select 1 from onflows_private.legacy_unavailable_activity_references p
     where p.athlete_alias=p_alias and p.kind=root.kind and p.reference_key=root.k) then raise foreign_key_violation;end if;
  end if;
 end loop;
 -- Close every existing root over its immutable input and catalog identity.
 for row_data in select i.input_key,i.activity_ref from public.onflows_activity_model_inputs i
   where i.athlete_alias=p_alias and i.input_key=any(inputs) order by i.input_key for key share of i nowait loop
  new_inputs:=pg_catalog.array_append(new_inputs,row_data.input_key);
  activities:=pg_catalog.array_append(activities,row_data.activity_ref);
 end loop;
 for row_data in select c.activity_ref from public.onflows_activity_catalog c
   where c.athlete_alias=p_alias and c.activity_ref=any(activities) order by c.activity_ref for key share of c nowait loop
  new_activities:=pg_catalog.array_append(new_activities,row_data.activity_ref);
 end loop;
 insert into onflows_private.history_activity_pins select p_alias,k from pg_catalog.unnest(new_activities) r(k) order by k on conflict do nothing;
 insert into onflows_private.history_input_pins
  select p_alias,i.input_key,i.activity_ref from public.onflows_activity_model_inputs i where i.athlete_alias=p_alias and i.input_key=any(new_inputs) order by i.input_key on conflict do nothing;
 insert into onflows_private.history_shadow_pins
  select p_alias,d.run_key,d.activity_ref from public.onflows_activity_derived_runs d where d.athlete_alias=p_alias and d.run_key=any(new_runs) order by d.run_key on conflict do nothing;
exception when lock_not_available or foreign_key_violation then
 raise exception 'Historical activity reference is unavailable; reload and try again'
  using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
end $$;
revoke all on function onflows_private.pin_history_activity_references(text,jsonb,boolean) from public,anon,authenticated,service_role;

create function onflows_private.pin_history_activity_payload()
returns trigger language plpgsql security definer set search_path='' as $$
declare reference_document jsonb;
begin
 -- SESSION entry_key itself is provenance even if no explicit payload key.
 reference_document:=pg_catalog.jsonb_build_object('payload',new.payload,'entry_key',to_jsonb(new)->'entry_key');
 if tg_table_name='onflows_model_entries' then
  perform onflows_private.pin_history_generations(new.athlete_alias,
   onflows_private.history_generation_references(new.payload));
 end if;
 perform onflows_private.pin_history_activity_references(new.athlete_alias,
  onflows_private.history_activity_references(reference_document),false);
 return new;
end $$;
revoke all on function onflows_private.pin_history_activity_payload() from public,anon,authenticated,service_role;
create trigger history_activity_pins_model_entry after insert on public.onflows_model_entries
 for each row execute function onflows_private.pin_history_activity_payload();
create trigger history_activity_pins_response_entry after insert on public.onflows_response_entries
 for each row execute function onflows_private.pin_history_activity_payload();
create trigger history_activity_pins_management_entry after insert on public.onflows_management_entries
 for each row execute function onflows_private.pin_history_activity_payload();
create trigger history_activity_pins_plan_revision after insert on public.onflows_management_plan_revisions
 for each row execute function onflows_private.pin_history_activity_payload();

-- App roles can INSERT immutable runs/inputs, not update their identity links.
-- Still protect owner migrations and future writers: a changed retained link
-- extends its closure atomically. The unconditional nonblocking graph guard
-- also excludes first-pin/link-update races before the pin becomes visible.
create function onflows_private.extend_history_activity_parents()
returns trigger language plpgsql security definer set search_path='' as $$
declare retained boolean; parent_inputs text[]:='{}';parent_activities text[]:='{}';
begin
 if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended('history-activity-pins:'||new.athlete_alias,0)) then
  raise exception 'Historical activity reference is unavailable; reload and try again'
   using errcode='PT409',hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';end if;
 if tg_table_name='onflows_activity_derived_runs' then
  select exists(select 1 from onflows_private.history_shadow_pins p where p.athlete_alias=new.athlete_alias and p.run_key=new.run_key) into retained;
  parent_inputs:=array[new.input_key];
 elsif tg_table_name='onflows_activity_canonical_runs' then
  select exists(select 1 from onflows_private.history_canonical_pins p where p.athlete_alias=new.athlete_alias and p.run_key=new.run_key) into retained;
 else
  select exists(select 1 from onflows_private.history_input_pins p where p.athlete_alias=new.athlete_alias and p.input_key=new.input_key) into retained;
 end if;
 if retained then
  perform onflows_private.pin_history_activity_references(new.athlete_alias,
   pg_catalog.jsonb_build_object('run_keys','[]'::jsonb,'input_keys',parent_inputs,'activity_refs',array[new.activity_ref],'opaque',false),false);
 end if;
 return new;
end $$;
revoke all on function onflows_private.extend_history_activity_parents() from public,anon,authenticated,service_role;
create trigger history_activity_shadow_parent_links after update of input_key,activity_ref on public.onflows_activity_derived_runs
 for each row when(old.input_key is distinct from new.input_key or old.activity_ref is distinct from new.activity_ref)
 execute function onflows_private.extend_history_activity_parents();
create trigger history_activity_canonical_parent_links after update of activity_ref on public.onflows_activity_canonical_runs
 for each row when(old.activity_ref is distinct from new.activity_ref)
 execute function onflows_private.extend_history_activity_parents();
create trigger history_activity_input_parent_links after update of activity_ref on public.onflows_activity_model_inputs
 for each row when(old.activity_ref is distinct from new.activity_ref)
 execute function onflows_private.extend_history_activity_parents();

-- Owner backfill uses exactly the same reference document as the trigger,
-- with p_allow_legacy_missing=true; every exception remains separately visible.
-- This bootstrap is not cleanup authorization. Full coverage, opaque exclusions,
-- FK account cleanup and real concurrency acceptance must precede any grant.
revoke execute on function public.prune_onflows_analysis_generations(text,integer,interval,integer) from service_role;
commit;
