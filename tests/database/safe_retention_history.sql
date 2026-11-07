-- Real migrated PostgreSQL; all fixtures and assertions roll back.
begin;
set local statement_timeout='90s';
set local lock_timeout='5s';
\ir history_generation_fixture.sql
\ir safe_retention_fixture.sql

do $$
declare
 f jsonb := pg_temp.safe_retention_fixture('retention-history-sql');
 a text := f->>'alias'; actor uuid := (f->>'actor')::uuid;
 refs jsonb; payload jsonb; bad jsonb; result jsonb;
 original_history jsonb; foreign_history jsonb;
 denied boolean; hint text; role_name text; permission text; table_name text;
 missing_key text := repeat('e',64); another_missing text := repeat('f',64);
 stub text;
begin
 foreach table_name in array array['history_activity_pins','history_input_pins','history_shadow_pins',
  'history_canonical_pins','legacy_unavailable_activity_references','history_activity_cleanup_blocks',
  'legacy_opaque_activity_references'] loop
  assert (select relrowsecurity from pg_class where oid=('onflows_private.'||table_name)::regclass),
   'Private retention RLS disabled';
  foreach role_name in array array['service_role','anon','authenticated'] loop
   foreach permission in array array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] loop
    assert not has_table_privilege(role_name,'onflows_private.'||table_name,permission),
     format('Retention table exposed %s to %s',permission,role_name);
   end loop;
  end loop;
 end loop;
 foreach role_name in array array['service_role','anon','authenticated'] loop
  assert not has_function_privilege(role_name,'onflows_private.history_activity_references(jsonb)','execute');
  assert not has_function_privilege(role_name,'onflows_private.pin_history_activity_references(text,jsonb,boolean)','execute');
  assert not has_function_privilege(role_name,'public.prune_onflows_analysis_generations(text,integer,interval,integer)','execute'),
   'Bootstrap accidentally granted pruning';
 end loop;
 assert (select count(*)=4 from pg_constraint where conname in
  ('onflows_activity_model_inputs_catalog_fk','onflows_activity_derived_runs_scoped_input_fk',
   'onflows_activity_catalog_shadow_fk','onflows_activity_catalog_canonical_fk')
  and contype='f' and confdeltype='a' and condeferrable and condeferred),
  'Native reference FKs must be deferred NO ACTION';
 assert (select count(*)=3 from pg_index i join pg_class c on c.oid=i.indexrelid
  where i.indrelid='public.onflows_analysis_generation_activities'::regclass
  and c.relname in ('onflows_generation_activities_shadow_reference',
   'onflows_generation_activities_canonical_reference','onflows_generation_activities_input_reference')
  and i.indpred is not null), 'Retained run/input anti-join indexes missing';

 payload := jsonb_build_object('source_run_key',f->>'control_shadow',
  'foo_"source_run_key',f->>'source_canonical',
  'nested',jsonb_build_array(jsonb_build_object('future_input_key',f->>'root_input'),
    jsonb_build_array(f->>'extra_activity',f->>'extra_activity')),
  'memory',jsonb_build_object('methods',jsonb_build_object('observations',
    jsonb_build_array(jsonb_build_object('id',f->>'parent_activity')))),
  'continuation_of_confirmed_activity',f->>'rollback_activity',
  'activity_refs',jsonb_build_array(f->>'control_activity'));
 refs := onflows_private.history_activity_references(payload);
 assert refs->'run_keys' @> jsonb_build_array(f->>'control_shadow',f->>'source_canonical')
  and jsonb_array_length(refs->'run_keys')=2, 'Known or escaped unknown run key missed';
 assert refs->'input_keys'=jsonb_build_array(f->>'root_input'), 'Nested future input key missed';
 assert refs->'activity_refs' @> jsonb_build_array(f->>'extra_activity',f->>'parent_activity',
  f->>'rollback_activity',f->>'control_activity') and jsonb_array_length(refs->'activity_refs')=4,
  'Learning, continuation or array identities missed/duplicated';
 assert refs->>'opaque'='false';
 assert onflows_private.history_activity_references('{"source_run_key":null,"input_key":null}')=
  '{"run_keys":[],"input_keys":[],"activity_refs":[],"opaque":false,"opaque_hashes":[]}'::jsonb;
 foreach bad in array array['{"source_run_key":""}'::jsonb,'{"input_key":42}'::jsonb,
  '{"future_run_key":"not-a-run"}'::jsonb,'{"nested":[{"source_input_key":{}}]}'::jsonb] loop
  denied:=false;
  begin perform onflows_private.history_activity_references(bad);
  exception when sqlstate 'PT409' then
   get stacked diagnostics hint=pg_exception_hint;
   assert hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT'; denied:=true;
  end;
  assert denied,'Malformed recognized run/input key was silently ignored';
 end loop;
 -- Compatibility: arbitrary context activity labels are not new provider IDs.
 perform onflows_private.history_activity_references('{"activity_ref":"planned-session-label"}');

 -- The supported model writer preserves every revision, including disabled tests.
 execute 'set local role service_role';
 payload:=jsonb_build_object('source_run_key',f->>'control_shadow','activity_ref',f->>'control_activity',
  'input_hash',f->>'control_input_hash','source_generation_id',f->>'root','enabled',true);
 result:=public.save_onflows_model_entry(a,'SPEED_TEST','retention-source',payload,0,actor);
 assert result->>'saved'='true';
 result:=public.save_onflows_model_entry(a,'SPEED_TEST','retention-source',payload||'{"enabled":false}',1,actor);
 assert result->>'revision'='2';
 execute 'reset role';
 assert exists(select 1 from onflows_private.history_generation_pins where athlete_alias=a and generation_id=(f->>'root')::uuid),
  'Model history generation root was not pinned';
 assert exists(select 1 from onflows_private.history_shadow_pins where athlete_alias=a and run_key=f->>'control_shadow');
 assert exists(select 1 from onflows_private.history_input_pins where athlete_alias=a and input_key=f->>'control_input');
 assert exists(select 1 from onflows_private.history_activity_pins where athlete_alias=a and activity_ref=f->>'control_activity');

 -- SESSION entry_key, non-DRAFT management and plan/proposal histories each guard their own source.
 execute 'set local role service_role';
 result:=public.save_onflows_response_entry(a,'SESSION',f->>'source_activity',current_date,'{}',0,actor);
 assert result->>'saved'='true';
 result:=public.save_onflows_management_entry(a,'PROFILE','management-profile-v1',
  jsonb_build_object('input_snapshot',jsonb_build_object('input_key',f->>'root_input')),1,actor);
 assert result->>'saved'='true';
 execute 'reset role';
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,1,'REVIEW',actor,jsonb_build_object('proposal',jsonb_build_object('learning',
   jsonb_build_array(jsonb_build_object('canonical_run_key',f->>'extra_canonical')))));
 assert exists(select 1 from onflows_private.history_activity_pins where athlete_alias=a and activity_ref=f->>'source_activity');
 assert exists(select 1 from onflows_private.history_input_pins where athlete_alias=a and input_key=f->>'root_input');
 assert exists(select 1 from onflows_private.history_activity_pins where athlete_alias=a and activity_ref=f->>'root_activity');
 assert exists(select 1 from onflows_private.history_canonical_pins where athlete_alias=a and run_key=f->>'extra_canonical');
 original_history:=pg_temp.safe_retention_history_rows(a);
 foreign_history:=pg_temp.safe_retention_history_rows(f->>'other_alias');
 perform onflows_private.pin_history_activity_references(a,
  onflows_private.history_activity_references(payload),true);
 assert pg_temp.safe_retention_history_rows(a)=original_history,
  'Pin/backfill changed historical payload, hash, xmin or ctid';

 -- Missing/foreign new references abort history atomically; owner exceptions are explicit.
 foreach bad in array array[jsonb_build_object('source_run_key',missing_key),
  jsonb_build_object('input_key',missing_key),jsonb_build_object('activity_ref','act_'||repeat('e',32)),
  jsonb_build_object('source_run_key',f->>'foreign_shadow'),
  jsonb_build_object('input_key',f->>'foreign_input'),jsonb_build_object('activity_ref',f->>'foreign_activity')] loop
  denied:=false;
  begin
   execute 'set local role service_role';
   perform public.save_onflows_model_entry(a,'SPEED_TEST','rejected-reference',bad,0,actor);
  exception when sqlstate 'PT409' then
   get stacked diagnostics hint=pg_exception_hint;
   assert hint='ONFLOWS_HISTORY_REFERENCE_CONFLICT'; denied:=true;
  end;
  execute 'reset role';
  assert denied,'Missing or foreign reference inserted new history';
  assert pg_temp.safe_retention_history_rows(a)=original_history,'Rejected reference changed historical tuples';
 end loop;
 denied:=false;
 begin
  perform onflows_private.pin_history_activity_references(a,
   jsonb_build_object('run_keys',jsonb_build_array(f->>'foreign_shadow')),true);
 exception when sqlstate 'PT409' then denied:=true;end;
 assert denied,'Legacy mode accepted another athlete reference';
 perform onflows_private.pin_history_activity_references(a,
  jsonb_build_object('run_keys',jsonb_build_array(missing_key)),true);
 assert exists(select 1 from onflows_private.legacy_unavailable_activity_references
  where athlete_alias=a and kind='run' and reference_key=missing_key), 'Legacy absence was not recorded';
 execute 'set local role service_role';
 result:=public.save_onflows_model_entry(a,'SPEED_TEST','legacy-exact-copy',jsonb_build_object('source_run_key',missing_key),0,actor);
 execute 'reset role';
 assert result->>'saved'='true','Known exact legacy absence could not be preserved';
 denied:=false;
 begin
  insert into public.onflows_model_entries values(a,'SPEED_TEST','different-absence',1,
   jsonb_build_object('source_run_key',another_missing),actor,clock_timestamp());
 exception when sqlstate 'PT409' then denied:=true;end;
 assert denied,'One tombstone authorized unrelated absent provenance';

 -- An absence exception cannot bypass pinning if its exact source later reappears.
 insert into public.onflows_activity_derived_runs
  (run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload)
  values(missing_key,f->>'root_input',a,f->>'root_activity',missing_key,'synthetic-recovered-v1','{}');
 execute 'set local role service_role';
 result:=public.save_onflows_model_entry(a,'SPEED_TEST','legacy-source-recovered',
  jsonb_build_object('source_run_key',missing_key),0,actor);
 execute 'reset role';
 assert result->>'saved'='true';
 assert exists(select 1 from onflows_private.history_shadow_pins where athlete_alias=a and run_key=missing_key),
  'Recovered exact legacy source bypassed full provenance pinning';
 assert exists(select 1 from onflows_private.history_input_pins where athlete_alias=a and input_key=f->>'root_input');
 assert exists(select 1 from onflows_private.history_activity_pins where athlete_alias=a and activity_ref=f->>'root_activity');
 perform onflows_private.pin_history_activity_references(a,
  jsonb_build_object('run_keys',jsonb_build_array(another_missing)),true);
 insert into public.onflows_activity_derived_runs
  (run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload)
  values(another_missing,f->>'foreign_input',f->>'other_alias',f->>'foreign_activity',another_missing,'synthetic-recovered-v1','{}');
 denied:=false;
 begin
  insert into public.onflows_model_entries values(a,'SPEED_TEST','legacy-key-now-foreign',1,
   jsonb_build_object('source_run_key',another_missing),actor,clock_timestamp());
 exception when sqlstate 'PT409' then denied:=true;end;
 assert denied,'Legacy absence bypassed a reappearing foreign source';
 assert not exists(select 1 from onflows_private.history_shadow_pins where athlete_alias=a and run_key=another_missing);

 -- Only an exact owner-recorded legacy opaque object may be copied forward.
 payload:='{"nested":{"codec":"synthetic-history-codec","data":"opaque-synthetic-bytes"}}';
 denied:=false;
 begin
  insert into public.onflows_model_entries values(a,'SPEED_TEST','new-opaque-source',1,payload,actor,clock_timestamp());
 exception when sqlstate 'PT409' then denied:=true;end;
 assert denied,'New opaque object bypassed exact legacy coverage';
 refs:=onflows_private.history_activity_references(payload);
 assert refs->>'opaque'='true' and jsonb_array_length(refs->'opaque_hashes')=1;
 perform onflows_private.pin_history_activity_references(a,refs,true);
 insert into public.onflows_model_entries values(a,'SPEED_TEST','known-opaque-source',1,payload,actor,clock_timestamp());
 denied:=false;
 begin
  insert into public.onflows_model_entries values(a,'SPEED_TEST','changed-opaque-source',1,
   '{"nested":{"codec":"synthetic-history-codec","data":"different-synthetic-bytes"}}',actor,clock_timestamp());
 exception when sqlstate 'PT409' then denied:=true;end;
 assert denied,'Known codec name authorized a different opaque object';
 assert exists(select 1 from onflows_private.history_activity_cleanup_blocks where athlete_alias=a),
  'Opaque history did not block cleanup';

 original_history:=pg_temp.safe_retention_history_rows(a);

 -- Supported workers still publish a native input/run graph after resolving its catalog stub.
 execute 'set local role service_role';
 stub:=public.resolve_onflows_activity_ref(a,repeat('b',64));
 perform public.publish_onflows_activity_shadow(repeat('1',64),a,stub,repeat('2',64),'synthetic-retention-v1','{}',
  repeat('3',64),repeat('4',64),'synthetic-retention-v1',null,null,null,null,null,'{}');
 execute 'reset role';
 set constraints all immediate;
 assert exists(select 1 from public.onflows_activity_derived_runs where athlete_alias=a and run_key=repeat('3',64));
 set constraints all deferred;
 denied:=false;
 begin
  insert into public.onflows_activity_model_inputs(input_key,athlete_alias,activity_ref,input_hash,schema_version,input_payload)
   values(repeat('5',64),a,'act_'||repeat('d',32),repeat('6',64),'test','{}');
  set constraints onflows_activity_model_inputs_catalog_fk immediate;
 exception when foreign_key_violation then denied:=true;end;
 assert denied,'Native orphan input escaped its FK';
 denied:=false;
 begin
  insert into public.onflows_activity_derived_runs
   (run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload)
   values(repeat('9',64),f->>'foreign_input',a,f->>'control_activity',repeat('a',64),'test','{}');
  set constraints onflows_activity_derived_runs_scoped_input_fk immediate;
 exception when foreign_key_violation then denied:=true;end;
 assert denied,'Foreign source input escaped the native scoped parent FK';
 denied:=false;
 begin
  update public.onflows_activity_catalog set latest_shadow_run_key=f->>'foreign_shadow'
   where activity_ref=f->>'control_activity';
  set constraints onflows_activity_catalog_shadow_fk immediate;
 exception when foreign_key_violation then denied:=true;end;
 assert denied,'Foreign native shadow pointer escaped its scoped FK';
 denied:=false;
 begin
  update public.onflows_activity_catalog set latest_canonical_run_key=f->>'source_canonical'
   where activity_ref=f->>'control_activity';
  set constraints onflows_activity_catalog_canonical_fk immediate;
 exception when foreign_key_violation then denied:=true;end;
 assert denied,'Wrong-activity canonical pointer escaped its scoped FK';

 assert pg_temp.safe_retention_history_rows(a)=original_history,'Native worker/FK checks rewrote immutable history';

 -- A populated account can still be deleted atomically through its existing cascade.
 delete from public.onflows_intervals_connections where athlete_alias=a;
 set constraints all immediate;
 assert not exists(select 1 from public.onflows_activity_catalog where athlete_alias=a);
 assert not exists(select 1 from public.onflows_activity_model_inputs where athlete_alias=a);
 assert not exists(select 1 from public.onflows_activity_derived_runs where athlete_alias=a);
 assert not exists(select 1 from public.onflows_activity_canonical_runs where athlete_alias=a);
 assert not exists(select 1 from onflows_private.history_activity_pins where athlete_alias=a);
 assert not exists(select 1 from onflows_private.history_input_pins where athlete_alias=a);
 assert not exists(select 1 from onflows_private.history_shadow_pins where athlete_alias=a);
 assert not exists(select 1 from onflows_private.history_canonical_pins where athlete_alias=a);
 assert not exists(select 1 from onflows_private.legacy_unavailable_activity_references where athlete_alias=a);
 assert not exists(select 1 from onflows_private.history_activity_cleanup_blocks where athlete_alias=a);
 assert not exists(select 1 from onflows_private.legacy_opaque_activity_references where athlete_alias=a);
 assert pg_temp.safe_retention_history_rows(f->>'other_alias')=foreign_history,'Account cascade touched another athlete';
end $$;
rollback;
