-- Included inside safe_retention_journal.sql's rolled-back owner transaction.
-- These roots were genuinely absent when historical coverage was recorded.
-- Recovering a deterministic technical key must protect it immediately, even
-- when no further model/response/plan revision is ever appended.
do $$
declare
 f jsonb; a text; variant text; root_key text; activity text; payload jsonb;
 ledger_kind text; source_history jsonb; retained_graph jsonb; original_graph jsonb; cleanup_counts jsonb;
 batch uuid; role_name text;
begin
 foreach role_name in array array['service_role','anon','authenticated'] loop
  assert not has_function_privilege(role_name,'onflows_private.legacy_history_keeps_activity(text,text)','execute');
 end loop;
 foreach variant in array array['shadow','canonical','input','activity'] loop
  f:=pg_temp.safe_retention_fixture('legacy-reappearance-'||variant);
  a:=f->>'alias';
  perform pg_temp.prepare_safe_retention_control(f);
  perform onflows_private.pin_history_generations(a,array[(f->>'control')::uuid]);
  perform onflows_private.pin_history_activity_references(a,
   jsonb_build_object('run_keys',jsonb_build_array(f->>'control_shadow',f->>'control_canonical')));
  activity:='act_'||md5(a||':reappeared:orphan');
  if variant in ('shadow','canonical') then
   root_key:=encode(sha256(convert_to(a||':reappeared:'||variant,'UTF8')),'hex');
   payload:=jsonb_build_object('source_run_key',root_key);ledger_kind:='run';
  elsif variant='input' then
   root_key:=encode(sha256(convert_to(a||':reappeared:input','UTF8')),'hex');
   payload:=jsonb_build_object('source_input_key',root_key);ledger_kind:='input';
  else
   root_key:=activity;payload:=jsonb_build_object('activity_ref',root_key);ledger_kind:='activity';
  end if;
  perform onflows_private.pin_history_activity_references(a,
   onflows_private.history_activity_references(payload),true);
  assert exists(select 1 from onflows_private.legacy_unavailable_activity_references
   where athlete_alias=a and kind=ledger_kind and reference_key=root_key);
  insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
   values(a,'SPEED_TEST','legacy-source',1,payload,(f->>'actor')::uuid);
  source_history:=pg_temp.safe_retention_history_rows(a);

  -- Recover only technical rows. There is deliberately no new history INSERT
  -- after this point to populate the normal append-time pin triggers.
  f:=pg_temp.add_safe_retention_orphan(f,'reappeared');
  if variant in ('canonical','input') then
   delete from public.onflows_activity_derived_runs where run_key=f->>'reappeared_shadow';
  end if;
  if variant='canonical' then
   delete from public.onflows_activity_model_inputs where input_key=f->>'reappeared_input';
   insert into public.onflows_activity_canonical_runs(
    run_key,athlete_alias,activity_ref,scientific_input_hash,result_hash,
    schema_version,model_version,result_payload,created_at)
   values(root_key,a,activity,root_key,root_key,'synthetic-retention-v1','synthetic-v1','{}',clock_timestamp()-interval '89 days');
  elsif variant='activity' then
   -- A legacy activity root must also preserve its current projection pointer,
   -- otherwise the pointer-release phase can silently discard its source run.
   update public.onflows_activity_catalog set latest_shadow_run_key=f->>'reappeared_shadow'
    where athlete_alias=a and activity_ref=activity;
  end if;
  assert not exists(select 1 from onflows_private.history_shadow_pins where athlete_alias=a and run_key=f->>'reappeared_shadow');
  assert not exists(select 1 from onflows_private.history_canonical_pins where athlete_alias=a and run_key=root_key);
  assert not exists(select 1 from onflows_private.history_input_pins where athlete_alias=a and input_key=f->>'reappeared_input');
  assert not exists(select 1 from onflows_private.history_activity_pins where athlete_alias=a and activity_ref=activity);
  assert onflows_private.legacy_history_keeps_activity(a,activity),
   format('Reappeared %s root did not retain its catalog parent',variant);
  assert not onflows_private.legacy_history_keeps_activity(f->>'other_alias',activity),
   'Legacy root retention crossed the athlete boundary';
  retained_graph:=pg_temp.safe_retention_graph(a);
  f:=pg_temp.add_safe_retention_orphan(f,'unreferenced');
  original_graph:=pg_temp.safe_retention_graph(a);
  execute 'set local role service_role';
  select to_jsonb(result) into cleanup_counts from public.maintain_onflows_analysis_history(a) result;
  execute 'reset role';
  assert cleanup_counts='{"deleted_generations":0,"deleted_activity_rows":0,"deleted_jobs":0,"deleted_shadow_runs":1,"deleted_canonical_runs":0,"deleted_model_inputs":1,"deleted_catalog_rows":1}',
   format('Reappeared %s root was deleted or independent cleanup stopped',variant);
  assert pg_temp.safe_retention_graph(a)=retained_graph,
   format('Cleanup changed reappeared %s source or its parent closure/pointers',variant);
  assert pg_temp.safe_retention_history_rows(a)=source_history,
   format('Reappearance/cleanup rewrote immutable %s history',variant);
  select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
  assert not exists(select 1 from onflows_private.analysis_cleanup_rows
   where batch_id=batch and (row_key->>'run_key'=root_key or row_key->>'input_key'=root_key
    or before_row->>'activity_ref'=activity)), 'A recovered historical root reached the cleanup journal';
  perform onflows_private.restore_analysis_cleanup_batch(batch);
  assert pg_temp.safe_retention_graph(a)=original_graph,'Unrelated cleanup did not restore exactly';
  assert pg_temp.safe_retention_history_rows(a)=source_history;
 end loop;
end $$;
