-- Real migrated PostgreSQL acceptance; synthetic rows and every change roll back.
begin;
\ir history_generation_fixture.sql

do $$
declare
  f jsonb := pg_temp.history_generation_fixture('history-pin-sql');
  a text := f->>'alias';
  actor uuid := (f->>'actor')::uuid;
  root_id uuid := (f->>'root')::uuid;
  refs uuid[];
  payload jsonb;
  bad jsonb;
  before_rows jsonb;
  before_pins bigint;
  denied boolean;
  hint text;
  role_name text;
  permission text;
  result jsonb;
  pruned record;
  stale jsonb;
  removed jsonb;
begin
  assert (select relrowsecurity from pg_class where oid='onflows_private.history_generation_pins'::regclass),
    'History pin RLS disabled';
  foreach role_name in array array['service_role','anon','authenticated'] loop
    foreach permission in array array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] loop
      assert not has_table_privilege(role_name,'onflows_private.history_generation_pins',permission),
        format('Pin table exposed %s to %s',permission,role_name);
    end loop;
    assert not has_function_privilege(role_name,'onflows_private.history_generation_references(jsonb)','EXECUTE'),
      'Private scanner exposed';
    assert not has_function_privilege(role_name,'onflows_private.pin_history_generations(text,uuid[])','EXECUTE'),
      'Private pin writer exposed';
    assert not has_function_privilege(role_name,
      'public.prune_onflows_analysis_generations(text,integer,interval,integer)','EXECUTE'),
      'Protection work restored application pruning';
  end loop;
  assert exists(select 1 from pg_constraint where conrelid='onflows_private.history_generation_pins'::regclass
    and contype='f' and confrelid='public.onflows_analysis_generations'::regclass
    and confdeltype='a' and condeferrable), 'Generation pin FK must reject deletion, not cascade away evidence';
  assert exists(select 1 from pg_constraint where conrelid='onflows_private.history_generation_pins'::regclass
    and contype='f' and confrelid='public.onflows_athlete_settings'::regclass and confdeltype='c'),
    'Athlete settings cleanup lost its cascade';

  payload := jsonb_build_object('start_date','2098-01-01',
    'input_snapshot',jsonb_build_object('generation_id',root_id),
    'learning',jsonb_build_object('provenance',jsonb_build_object('generation_id',root_id)),
    'future_generation_id',f->>'source',
    'foo_"source_generation_id',f->>'source',
    'nested',jsonb_build_array(jsonb_build_object('source_generation_id',f->>'source'),
      jsonb_build_array(jsonb_build_object('analysis_generation_id',root_id))));
  refs := onflows_private.history_generation_references(payload);
  assert cardinality(refs)=2 and refs @> array[root_id,(f->>'source')::uuid],
    'Nested array/learning references were missed or duplicated';
  assert cardinality(onflows_private.history_generation_references(
    '{"generation_id":null,"unrelated":{"note":"not-a-generation"}}'))=0,
    'Null or unrelated metadata became a generation reference';
  foreach bad in array array[
    '{"generation_id":""}'::jsonb,
    '{"generation_id":"not-a-uuid"}'::jsonb,
    '{"future_generation_id":""}'::jsonb,
    '{"nested":[{"source_generation_id":123}]}'::jsonb,
    '{"provenance":{"analysis_generation_id":{}}}'::jsonb
  ] loop
    denied := false;
    begin
      perform onflows_private.history_generation_references(bad);
    exception when sqlstate 'PT409' then
      get stacked diagnostics hint=pg_exception_hint;
      assert hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT', 'Malformed reference has no conflict hint';
      denied := true;
    end;
    assert denied, 'Malformed generation reference silently ignored';
  end loop;

  -- The actual supported DRAFT writer also runs the protection trigger. It
  -- freezes the profile in persistence without changing the supplied evidence.
  execute 'set local role service_role';
  result := public.save_onflows_management_entry(a,'DRAFT','2098-01-01',payload,0,actor,1,null,false);
  execute 'reset role';
  assert result->>'saved'='true', 'Supported draft writer failed';
  insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
    values(a,1,'REVIEW',actor,jsonb_build_object('status','REVIEW_REQUIRED',
      'plan',jsonb_build_object('source',jsonb_build_object('generation_id',root_id)),
      'proposal',jsonb_build_object('learning',jsonb_build_object('provenance',jsonb_build_object('generation_id',root_id)))));
  execute 'set local role service_role';
  result := public.save_onflows_response_entry(a,'DAILY','nested-window',current_date,
    jsonb_build_object('response_window',jsonb_build_object('baseline',jsonb_build_object('source_generation_id',root_id),
      'responses',jsonb_build_array(jsonb_build_object('analysis_generation_id',f->>'source')))),0,actor);
  execute 'reset role';
  assert result->>'saved'='true', 'Supported response writer failed';
  assert (select count(*)=3 from onflows_private.history_generation_pins where athlete_alias=a
    and generation_id in (root_id,(f->>'parent')::uuid,(f->>'source')::uuid)),
    'Initial history did not pin the complete base/activity-set closure';

  before_rows := pg_temp.history_generation_rows(a);
  select count(*) into before_pins from onflows_private.history_generation_pins where athlete_alias=a;
  -- Exercise all three trigger entry points with malformed, absent and foreign
  -- UUIDs. A failed append must not leave either a row or a partial pin behind.
  foreach bad in array array[
    jsonb_build_object('generation_id',''),
    jsonb_build_object('generation_id',gen_random_uuid()),
    jsonb_build_object('generation_id',f->>'foreign')
  ] loop
    denied := false;
    begin
      insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
        values(a,'DRAFT','2098-02-01',1,jsonb_build_object('start_date','2098-02-01','source',bad),actor);
    exception when sqlstate 'PT409' then
      get stacked diagnostics hint=pg_exception_hint;
      assert hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT';
      denied := true;
    end;
    assert denied, 'DRAFT accepted a malformed/missing/foreign source';
    denied := false;
    begin
      insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
        values(a,2,'REVIEW',actor,jsonb_build_object('proposal',jsonb_build_object('source',bad)));
    exception when sqlstate 'PT409' then denied := true; end;
    assert denied, 'Plan revision accepted a malformed/missing/foreign source';
    denied := false;
    begin
      insert into public.onflows_response_entries(athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id)
        values(a,'DAILY','invalid-window',current_date,1,jsonb_build_object('response_window',bad),actor);
    exception when sqlstate 'PT409' then denied := true; end;
    assert denied, 'Observation accepted a malformed/missing/foreign source';
  end loop;
  assert pg_temp.history_generation_rows(a)=before_rows, 'Rejected append changed immutable history';
  assert (select count(*)=before_pins from onflows_private.history_generation_pins where athlete_alias=a),
    'Rejected append leaked a partial pin';

  -- Re-pinning existing immutable evidence is the runtime backfill operation;
  -- compare physical tuple identity too, not only equal JSON after a rewrite.
  perform onflows_private.pin_history_generations(a,onflows_private.history_generation_references(p.payload))
    from (select e.payload from public.onflows_management_entries e where e.athlete_alias=a and e.kind='DRAFT'
      union all select r.payload from public.onflows_management_plan_revisions r where r.athlete_alias=a
      union all select o.payload from public.onflows_response_entries o where o.athlete_alias=a) p;
  assert pg_temp.history_generation_rows(a)=before_rows, 'Pin backfill rewrote historical tuples/payloads';
  update public.onflows_analysis_generations
    set base_generation_id=(f->>'extra')::uuid,activity_set_generation_id=(f->>'rollback')::uuid
    where generation_id=root_id;
  assert (select count(*)=2 from onflows_private.history_generation_pins where athlete_alias=a
    and generation_id in ((f->>'extra')::uuid,(f->>'rollback')::uuid)),
    'A later parent/activity-set link did not extend historical closure';
  -- A legal FK cycle must terminate without dropping the already pinned roots.
  update public.onflows_analysis_generations set base_generation_id=root_id
    where generation_id=(f->>'source')::uuid;
  perform onflows_private.pin_history_generations(a,array[root_id,root_id]);
  assert pg_temp.history_generation_rows(a)=before_rows, 'Closure maintenance rewrote history';

  -- Owner-only execution on this ephemeral fixture: service pruning stays
  -- revoked. All old generations except FAILED control are now pinned.
  select * into pruned from public.prune_onflows_analysis_generations(a,1,interval '30 days',100);
  assert pruned.deleted_generations=1, 'Prune did not delete only the unpinned control';
  assert not exists(select 1 from public.onflows_analysis_generations where generation_id=(f->>'control')::uuid),
    'Eligible unreferenced control was retained';
  assert (select count(*)=6 from public.onflows_analysis_generations where athlete_alias=a
    and generation_id in (root_id,(f->>'parent')::uuid,(f->>'source')::uuid,(f->>'extra')::uuid,
      (f->>'rollback')::uuid,(f->>'active')::uuid)), 'Prune removed protected historical evidence';
  assert pg_temp.history_generation_rows(a)=before_rows, 'Prune rewrote immutable history';

  -- Simulate an already-selected UUID becoming referenced before DELETE. The
  -- FK, rather than a fresh candidate query alone, must stop that stale choice.
  stale := pg_temp.history_generation_fixture('history-pin-stale');
  insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
    values(stale->>'alias','DRAFT','2098-01-01',1,
      jsonb_build_object('start_date','2098-01-01','source',jsonb_build_object('generation_id',stale->>'control')),
      (stale->>'actor')::uuid);
  denied := false;
  begin
    delete from public.onflows_analysis_generations where generation_id=(stale->>'control')::uuid;
    set constraints all immediate;
  exception when foreign_key_violation then denied := true; end;
  assert denied, 'Stale selection deleted a now-referenced generation';
  assert exists(select 1 from public.onflows_analysis_generations where generation_id=(stale->>'control')::uuid);
  assert (select count(*)=1 from public.onflows_management_entries where athlete_alias=stale->>'alias' and kind='DRAFT');

  removed := pg_temp.history_generation_fixture('history-pin-settings');
  perform onflows_private.pin_history_generations(removed->>'alias',array[(removed->>'root')::uuid]);
  delete from public.onflows_athlete_settings where athlete_alias=removed->>'alias';
  set constraints all immediate;
  assert not exists(select 1 from onflows_private.history_generation_pins where athlete_alias=removed->>'alias'),
    'Settings deletion left pins that outlive management history';
  assert not exists(select 1 from public.onflows_management_entries where athlete_alias=removed->>'alias');
  assert exists(select 1 from public.onflows_analysis_generations where athlete_alias=removed->>'alias'),
    'Settings-only cleanup unexpectedly deleted analysis';

  set constraints all deferred;
  removed := pg_temp.history_generation_fixture('history-pin-connection');
  perform onflows_private.pin_history_generations(removed->>'alias',array[(removed->>'root')::uuid]);
  delete from public.onflows_intervals_connections where athlete_alias=removed->>'alias';
  set constraints all immediate;
  assert not exists(select 1 from onflows_private.history_generation_pins where athlete_alias=removed->>'alias');
  assert not exists(select 1 from public.onflows_analysis_generations where athlete_alias=removed->>'alias'),
    'Pins broke atomic populated connection deletion';
  raise notice 'Historical generation protection acceptance passed';
end $$;
rollback;
