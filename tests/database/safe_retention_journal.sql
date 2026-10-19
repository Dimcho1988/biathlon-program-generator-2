-- Owner-only destructive operations on ephemeral synthetic fixtures; all rollback.
begin;
set local statement_timeout='90s';
set local lock_timeout='5s';
\ir history_generation_fixture.sql
\ir safe_retention_fixture.sql

-- The worker can invoke only the fixed, fully guarded policy. It cannot tune
-- retention, bypass legacy coverage, or use the older owner-only prune RPC.
do $$
declare
 f jsonb:=pg_temp.safe_retention_fixture('retention-worker-wrapper');
 a text:=f->>'alias'; batch uuid; worker_counts jsonb; restored jsonb;
 original_graph jsonb; original_history jsonb; denied boolean:=false; message text;
 role_name text;
begin
 assert (select count(*)=1 and bool_and(pronargs=1 and prosecdef)
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname='maintain_onflows_analysis_history'),
  'Worker maintenance exposed an overload, variable policy, or invoker function';
 assert has_function_privilege('service_role','public.maintain_onflows_analysis_history(text)','execute');
 foreach role_name in array array['anon','authenticated'] loop
  assert not has_function_privilege(role_name,'public.maintain_onflows_analysis_history(text)','execute');
 end loop;
 foreach role_name in array array['service_role','anon','authenticated'] loop
  assert not has_function_privilege(role_name,'public.prune_onflows_analysis_generations(text,integer,interval,integer)','execute');
 end loop;

 delete from onflows_private.analysis_retention_coverage where athlete_alias=a;
 original_graph:=pg_temp.safe_retention_graph(a);
 original_history:=pg_temp.safe_retention_history_rows(a);
 execute 'set local role service_role';
 begin perform * from public.maintain_onflows_analysis_history(a);
 exception when raise_exception then
  get stacked diagnostics message=message_text;
  assert message='Historical activity retention coverage has not been verified';
  denied:=true;
 end;
 execute 'reset role';
 assert denied,'Service maintenance bypassed the legacy coverage gate';
 assert pg_temp.safe_retention_graph(a)=original_graph;
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 assert not exists(select 1 from onflows_private.analysis_cleanup_batches where athlete_alias=a);

 perform pg_temp.prepare_safe_retention_control(f);
 original_graph:=pg_temp.safe_retention_graph(a);
 original_history:=pg_temp.safe_retention_history_rows(a);
 execute 'set local role service_role';
 select to_jsonb(result) into worker_counts from public.maintain_onflows_analysis_history(a) result;
 execute 'reset role';
 assert worker_counts='{"deleted_generations":1,"deleted_activity_rows":1,"deleted_jobs":1,"deleted_shadow_runs":1,"deleted_canonical_runs":1,"deleted_model_inputs":1,"deleted_catalog_rows":1}',
  'Service maintenance changed the seven-count result or guarded deletion';
 select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
 assert (select b.counts=worker_counts and b.backup_rows=8
  and b.policy @> '{"keep_superseded":5,"terminal_older_than":"30 days","requested_batch_limit":10,"effective_phase_limit":10,"recovery_days":14}'::jsonb
  from onflows_private.analysis_cleanup_batches b where batch_id=batch),
  'Worker policy differs from fixed keep-five, thirty-day, ten-per-phase recovery policy';
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 restored:=onflows_private.restore_analysis_cleanup_batch(batch);
 assert restored->>'restored'='true' and restored->>'rows'='8';
 assert pg_temp.safe_retention_graph(a)=original_graph,'Service maintenance cannot be restored exactly';
 assert pg_temp.safe_retention_history_rows(a)=original_history,'Service maintenance/restore rewrote history';
end $$;

do $$
declare
 f jsonb:=pg_temp.safe_retention_fixture('retention-journal-sql');
 a text:=f->>'alias'; batch uuid; next_batch uuid;
 original_graph jsonb; original_history jsonb; before_conflict jsonb;
 prune_counts jsonb; restored jsonb; original_catalog jsonb; manifest jsonb; batch_manifest jsonb;
 denied boolean; message text; expired integer; name text; role_name text; permission text;
begin
 foreach name in array array['analysis_retention_coverage','analysis_cleanup_batches','analysis_cleanup_rows'] loop
  assert (select relrowsecurity from pg_class where oid=('onflows_private.'||name)::regclass);
  foreach role_name in array array['service_role','anon','authenticated'] loop
   foreach permission in array array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] loop
    assert not has_table_privilege(role_name,'onflows_private.'||name,permission),
     format('Journal exposed %s to %s',permission,role_name);
   end loop;
  end loop;
 end loop;
 foreach role_name in array array['service_role','anon','authenticated'] loop
  assert not has_function_privilege(role_name,'onflows_private.restore_analysis_cleanup_batch(uuid)','execute');
  assert not has_function_privilege(role_name,'public.prune_onflows_analysis_generations(text,integer,interval,integer)','execute');
 end loop;
 assert has_function_privilege('service_role','public.expire_onflows_analysis_cleanup_recovery(integer)','execute');
 assert not has_function_privilege('anon','public.expire_onflows_analysis_cleanup_recovery(integer)','execute');
 assert not has_function_privilege('authenticated','public.expire_onflows_analysis_cleanup_recovery(integer)','execute');
 assert (select count(*)=8 from pg_trigger where tgname='analysis_cleanup_journal' and not tgisinternal),
  'Journal does not cover all eight directly/cascade-mutated technical tables';

 assert (select evidence->>'source'='NEW_ALIAS_EMPTY_HISTORY'
  from onflows_private.analysis_retention_coverage where athlete_alias=a),
  'Fresh empty-history account did not initialize safe retention coverage';
 -- Simulate a preexisting/unverified account; only owner verification can reopen it.
 delete from onflows_private.analysis_retention_coverage where athlete_alias=a;
 original_graph:=pg_temp.safe_retention_graph(a);
 denied:=false;
 begin perform * from public.prune_onflows_analysis_generations(a,1,interval '30 days',1);
 exception when raise_exception then
  get stacked diagnostics message=message_text;
  assert message='Historical activity retention coverage has not been verified';denied:=true;
 end;
 assert denied,'Cleanup accepted unverified history coverage';
 assert pg_temp.safe_retention_graph(a)=original_graph;
 assert not exists(select 1 from onflows_private.analysis_cleanup_batches where athlete_alias=a);
 perform pg_temp.prepare_safe_retention_control(f);
 original_graph:=pg_temp.safe_retention_graph(a);
 original_history:=pg_temp.safe_retention_history_rows(a);
 select to_jsonb(result) into prune_counts from public.prune_onflows_analysis_generations(a,1,interval '30 days',1) result;
 assert prune_counts='{"deleted_generations":1,"deleted_activity_rows":1,"deleted_jobs":1,"deleted_shadow_runs":1,"deleted_canonical_runs":1,"deleted_model_inputs":1,"deleted_catalog_rows":1}',
  'Seven-count prune contract or isolated graph deletion changed';
 select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
 assert (select prune_counts=b.counts and b.backup_rows=8 and b.backup_bytes between 1 and 16777216
  and abs(extract(epoch from (b.recoverable_until-b.created_at))-1209600)<1
  and b.policy->>'effective_phase_limit'='1' from onflows_private.analysis_cleanup_batches b where batch_id=batch),
  'Journal counts, complete first-preimage bounds or 14-day recovery deadline incorrect';
 assert (select count(*)=8 and count(distinct table_name)=8 and bool_and(before_row is not null)
  and bool_and(before_sha256=encode(sha256(convert_to(before_row::text,'UTF8')),'hex'))
  and bool_and(schema_sha256~'^[a-f0-9]{64}$') and bool_and(final_operation='DELETE')
  from onflows_private.analysis_cleanup_rows where batch_id=batch), 'Missing/corrupt cascade or direct preimage';
 assert (select b.backup_bytes=sum(r.logical_bytes) from onflows_private.analysis_cleanup_batches b
  join onflows_private.analysis_cleanup_rows r using(batch_id) where batch_id=batch group by b.backup_bytes),
  'First-preimage byte accounting does not match journal rows';
 assert (select count(*)=1 and bool_and(before_row->>'latest_shadow_run_key'=f->>'control_shadow')
  and bool_and(before_row->>'latest_canonical_run_key'=f->>'control_canonical')
  from onflows_private.analysis_cleanup_rows where batch_id=batch and table_name='onflows_activity_catalog'),
  'Catalog UPDATE then DELETE lost the original pointer preimage';
 assert pg_temp.safe_retention_history_rows(a)=original_history,'Prune changed model/response/DRAFT/plan payload or tuple identity';
 assert coalesce(current_setting('onflows.cleanup_batch_id',true),'')='','Journal context leaked beyond the prune function';

 restored:=onflows_private.restore_analysis_cleanup_batch(batch);
 assert restored->>'restored'='true' and restored->>'replayed'='false' and restored->>'rows'='8';
 assert pg_temp.safe_retention_graph(a)=original_graph,'Restoration did not recover the exact original technical graph';
 assert pg_temp.safe_retention_history_rows(a)=original_history,'Restore rewrote historical decisions';
 restored:=onflows_private.restore_analysis_cleanup_batch(batch);
 assert restored->>'restored'='true' and restored->>'replayed'='true';
 assert pg_temp.safe_retention_graph(a)=original_graph,'Idempotent restore changed recovered data';

 -- A replacement row must not be overwritten, and failure must be atomic.
 perform * from public.prune_onflows_analysis_generations(a,1,interval '30 days',1);
 select batch_id into strict next_batch from onflows_private.analysis_cleanup_batches where athlete_alias=a and restored_at is null;
 select before_row into strict original_catalog from onflows_private.analysis_cleanup_rows
  where batch_id=next_batch and table_name='onflows_activity_catalog';
 insert into public.onflows_activity_catalog
  select (jsonb_populate_record(null::public.onflows_activity_catalog,
   original_catalog||jsonb_build_object('name','newer replacement','latest_shadow_run_key',null,'latest_canonical_run_key',null))).*;
 before_conflict:=pg_temp.safe_retention_graph(a);
 denied:=false;
 begin perform onflows_private.restore_analysis_cleanup_batch(next_batch);
 exception when raise_exception then denied:=true;end;
 assert denied and pg_temp.safe_retention_graph(a)=before_conflict,'Restore overwrote a newer occupied row';
 assert (select restored_at is null from onflows_private.analysis_cleanup_batches where batch_id=next_batch);
 delete from public.onflows_activity_catalog where activity_ref=f->>'control_activity';

 -- An alternate unique identity also aborts without a partial reconstructed graph.
 insert into public.onflows_activity_catalog
  select (jsonb_populate_record(null::public.onflows_activity_catalog,
   original_catalog||jsonb_build_object('activity_ref','act_'||repeat('c',32),
    'latest_shadow_run_key',null,'latest_canonical_run_key',null))).*;
 before_conflict:=pg_temp.safe_retention_graph(a);
 denied:=false;
 begin perform onflows_private.restore_analysis_cleanup_batch(next_batch);
 exception when unique_violation then denied:=true;end;
 assert denied and pg_temp.safe_retention_graph(a)=before_conflict,'Alternate unique-key conflict left partial restoration';
 delete from public.onflows_activity_catalog where activity_ref='act_'||repeat('c',32);
 before_conflict:=pg_temp.safe_retention_graph(a);
 update onflows_private.analysis_cleanup_rows set schema_sha256=repeat('0',64)
  where batch_id=next_batch and table_name='onflows_activity_catalog';
 denied:=false;
 begin perform onflows_private.restore_analysis_cleanup_batch(next_batch);
 exception when raise_exception then denied:=true;end;
 assert denied and pg_temp.safe_retention_graph(a)=before_conflict,'Schema-drift rejection changed the graph';
 update onflows_private.analysis_cleanup_rows set schema_sha256=onflows_private.analysis_cleanup_schema_hash(table_name)
  where batch_id=next_batch and table_name='onflows_activity_catalog';
 update onflows_private.analysis_cleanup_rows set before_sha256=repeat('0',64)
  where batch_id=next_batch and table_name='onflows_activity_catalog';
 denied:=false;
 begin perform onflows_private.restore_analysis_cleanup_batch(next_batch);
 exception when raise_exception then denied:=true;end;
 assert denied and pg_temp.safe_retention_graph(a)=before_conflict,'Corrupt-preimage rejection changed the graph';
 update onflows_private.analysis_cleanup_rows set before_sha256=encode(sha256(convert_to(before_row::text,'UTF8')),'hex')
  where batch_id=next_batch and table_name='onflows_activity_catalog';
 perform onflows_private.restore_analysis_cleanup_batch(next_batch);
 assert pg_temp.safe_retention_graph(a)=original_graph;

 -- Simulate passage of the recovery deadline on a new, unrestored synthetic batch.
 perform * from public.prune_onflows_analysis_generations(a,1,interval '30 days',1);
 select batch_id into strict next_batch from onflows_private.analysis_cleanup_batches where athlete_alias=a and restored_at is null;
 update onflows_private.analysis_cleanup_batches set recoverable_until=clock_timestamp()-interval '1 second' where batch_id=next_batch;
 before_conflict:=pg_temp.safe_retention_graph(a);
 denied:=false;
 begin perform onflows_private.restore_analysis_cleanup_batch(next_batch);
 exception when raise_exception then denied:=true;end;
 assert denied and pg_temp.safe_retention_graph(a)=before_conflict,'Expired recovery restored or changed technical rows';
 select jsonb_agg(to_jsonb(r)-'before_row' order by table_name,row_key) into manifest
  from onflows_private.analysis_cleanup_rows r where batch_id=next_batch;
 select to_jsonb(b)-'payload_expired_at' into batch_manifest from onflows_private.analysis_cleanup_batches b where batch_id=next_batch;
 execute 'set local role service_role';
 expired:=public.expire_onflows_analysis_cleanup_recovery(1);
 execute 'reset role';
 assert expired=1;
 assert (select bool_and(before_row is null) and count(*)=8 from onflows_private.analysis_cleanup_rows where batch_id=next_batch);
 assert (select jsonb_agg(to_jsonb(r)-'before_row' order by table_name,row_key)=manifest
  from onflows_private.analysis_cleanup_rows r where batch_id=next_batch), 'Expiry removed permanent keys/hashes/schema/operation metadata';
 assert (select to_jsonb(b)-'payload_expired_at'=batch_manifest and payload_expired_at is not null
  from onflows_private.analysis_cleanup_batches b where batch_id=next_batch), 'Expiry changed the permanent batch manifest';
 assert public.expire_onflows_analysis_cleanup_recovery(1)=0,'Expiry was not bounded/idempotent';
 assert pg_temp.safe_retention_graph(a)=before_conflict;
 assert pg_temp.safe_retention_history_rows(a)=original_history,'Expiry rewrote training history';
 foreach expired in array array[0,51,null] loop
  denied:=false;
  begin perform public.expire_onflows_analysis_cleanup_recovery(expired);
  exception when raise_exception then denied:=true;end;
  assert denied,'Invalid expiry batch limit accepted';
 end loop;

 -- Empty cleanup records no backup; opaque aliases are excluded even with coverage.
 perform onflows_private.pin_history_activity_references(a,
  onflows_private.history_activity_references('{"codec":"synthetic-opaque","data":"unknown"}'),true);
 select to_jsonb(result) into prune_counts from public.prune_onflows_analysis_generations(a,1,interval '30 days',1000) result;
 assert prune_counts='{"deleted_generations":0,"deleted_activity_rows":0,"deleted_jobs":0,"deleted_shadow_runs":0,"deleted_canonical_runs":0,"deleted_model_inputs":0,"deleted_catalog_rows":0}';
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 set constraints all deferred;
 delete from public.onflows_intervals_connections where athlete_alias=a;
 set constraints all immediate;
 assert not exists(select 1 from onflows_private.analysis_retention_coverage where athlete_alias=a);
 assert not exists(select 1 from onflows_private.analysis_cleanup_batches where athlete_alias=a);
 assert not exists(select 1 from onflows_private.analysis_cleanup_rows where batch_id in(batch,next_batch));
end $$;

-- Large candidates are skipped while independent eligible work still progresses.
do $$
declare f jsonb; a text; kind text; original_graph jsonb; original_history jsonb;
 prune_counts jsonb; batch uuid;
begin
 foreach kind in array array['bytes','rows'] loop
  f:=pg_temp.safe_retention_fixture('retention-progress-'||kind);a:=f->>'alias';
  perform pg_temp.prepare_safe_retention_control(f);
  if kind='bytes' then
   update public.onflows_activity_derived_runs set result_payload=jsonb_build_object('padding',repeat('x',16777216))
    where run_key=f->>'control_shadow';
  else
   perform pg_temp.add_safe_retention_fanout(f,(f->>'control')::uuid,1001,'oversized');
  end if;
  f:=pg_temp.add_safe_retention_orphan(f,'small');
  original_graph:=pg_temp.safe_retention_graph(a);original_history:=pg_temp.safe_retention_history_rows(a);
  select to_jsonb(result) into prune_counts from public.prune_onflows_analysis_generations(a,1,interval '30 days',1000) result;
  assert not exists(select 1 from public.onflows_activity_derived_runs where run_key=f->>'small_shadow'),
   'A large candidate starved an independent small orphan';
  assert exists(select 1 from public.onflows_activity_derived_runs where run_key=f->>'control_shadow'),
   'Oversized generation/run evidence was deleted';
  if kind='rows' then
   assert prune_counts->>'deleted_generations'='0' and prune_counts->>'deleted_activity_rows'='0';
   assert exists(select 1 from public.onflows_analysis_generations where generation_id=(f->>'control')::uuid);
  end if;
  select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
  assert (select b.backup_rows between 1 and 1000 and b.backup_bytes between 1 and 16777216
   and b.policy->'budget_skips' @> jsonb_build_array(jsonb_build_object('reason','RECOVERY_BUDGET',
    'table',case kind when 'bytes' then 'onflows_activity_derived_runs' else 'onflows_analysis_generations' end,
    'key',case kind when 'bytes' then f->>'control_shadow' else f->>'control' end))
   from onflows_private.analysis_cleanup_batches b where batch_id=batch), 'Skip reason or bounded recovery evidence missing';
  assert not exists(select 1 from onflows_private.analysis_cleanup_rows where batch_id=batch
   and table_name='onflows_activity_derived_runs' and row_key=jsonb_build_object('run_key',f->>'control_shadow')),
   'Skipped large payload was copied into recovery storage';
  assert pg_temp.safe_retention_history_rows(a)=original_history,'Progressive cleanup rewrote historical tuples';
  perform onflows_private.restore_analysis_cleanup_batch(batch);
  assert pg_temp.safe_retention_graph(a)=original_graph,'Progressive partial batch did not restore exactly';
  assert pg_temp.safe_retention_history_rows(a)=original_history;
 end loop;
end $$;

-- A normal multi-generation batch with hundreds of dependent rows still advances.
do $$
declare f jsonb:=pg_temp.safe_retention_fixture('retention-moderate-fanout');a text:=f->>'alias';
 original_graph jsonb;original_history jsonb;prune_counts jsonb;batch uuid;name text;
begin
 perform pg_temp.prepare_safe_retention_control(f);
 f:=pg_temp.add_safe_retention_generation(f,'control2',10);
 f:=pg_temp.add_safe_retention_generation(f,'control3',11);
 foreach name in array array['control','control2','control3'] loop
  perform pg_temp.add_safe_retention_fanout(f,(f->>name)::uuid,150,name);
 end loop;
 original_graph:=pg_temp.safe_retention_graph(a);original_history:=pg_temp.safe_retention_history_rows(a);
 select to_jsonb(result) into prune_counts from public.prune_onflows_analysis_generations(a,1,interval '30 days',1000) result;
 assert prune_counts->>'deleted_generations'='3' and prune_counts->>'deleted_activity_rows'='453',
  'Ordinary three-generation/150-row fanout batch did not progress';
 select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
 assert (select backup_rows between 453 and 1000 and backup_bytes between 1 and 16777216
  and policy->>'effective_phase_limit'='10' from onflows_private.analysis_cleanup_batches where batch_id=batch);
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 perform onflows_private.restore_analysis_cleanup_batch(batch);
 assert pg_temp.safe_retention_graph(a)=original_graph;
 assert pg_temp.safe_retention_history_rows(a)=original_history;
end $$;

-- More than ten oversized old candidates must not hide a later fitting generation.
do $$
declare f jsonb:=pg_temp.safe_retention_fixture('retention-budget-lookahead');a text:=f->>'alias';
 i integer;name text;original_graph jsonb;original_history jsonb;prune_counts jsonb;batch uuid;
begin
 perform pg_temp.prepare_safe_retention_control(f);
 for i in 1..11 loop
  name:='oversized-'||i;
  f:=pg_temp.add_safe_retention_generation(f,name,20+i);
  update public.onflows_analysis_generations set created_at=clock_timestamp()-interval '100 days'+i*interval '1 second'
   where generation_id=(f->>name)::uuid;
  perform pg_temp.add_safe_retention_fanout(f,(f->>name)::uuid,1001,name);
 end loop;
 original_graph:=pg_temp.safe_retention_graph(a);original_history:=pg_temp.safe_retention_history_rows(a);
 select to_jsonb(result) into prune_counts from public.prune_onflows_analysis_generations(a,1,interval '30 days',1000) result;
 assert prune_counts->>'deleted_generations'='1' and prune_counts->>'deleted_activity_rows'='1',
  'Oversized prefix prevented the later fitting generation from progressing';
 assert not exists(select 1 from public.onflows_analysis_generations where generation_id=(f->>'control')::uuid);
 for i in 1..11 loop
  assert exists(select 1 from public.onflows_analysis_generations where generation_id=(f->>('oversized-'||i))::uuid),
   'Oversized dependent graph was deleted';
 end loop;
 select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
 assert (select jsonb_array_length(policy->'budget_skips')>=11 and backup_rows between 1 and 1000
  from onflows_private.analysis_cleanup_batches where batch_id=batch);
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 perform onflows_private.restore_analysis_cleanup_batch(batch);
 assert pg_temp.safe_retention_graph(a)=original_graph;
 assert pg_temp.safe_retention_history_rows(a)=original_history;
end $$;

-- Synthetic locator JSON only: no Storage API/object operation is performed.
-- Exact archive marker matching conservatively retains an ordinary-text match.
create function pg_temp.safe_retention_archived_rows(p_alias text)
returns jsonb language sql as $$
 select jsonb_build_object(
  'shadow',(select coalesce(jsonb_agg(jsonb_build_object('row',to_jsonb(s),'xmin',s.xmin::text,'ctid',s.ctid::text)
   order by s.run_key),'[]') from public.onflows_activity_derived_runs s where s.athlete_alias=p_alias
   and onflows_private.has_analysis_archive_locator(s.result_payload)),
  'input',(select coalesce(jsonb_agg(jsonb_build_object('row',to_jsonb(i),'xmin',i.xmin::text,'ctid',i.ctid::text)
   order by i.input_key),'[]') from public.onflows_activity_model_inputs i where i.athlete_alias=p_alias
   and onflows_private.has_analysis_archive_locator(i.input_payload)),
  'canonical',(select coalesce(jsonb_agg(jsonb_build_object('row',to_jsonb(c),'xmin',c.xmin::text,'ctid',c.ctid::text)
   order by c.run_key),'[]') from public.onflows_activity_canonical_runs c where c.athlete_alias=p_alias
   and onflows_private.has_analysis_archive_locator(c.result_payload))
 );
$$;
do $$
declare f jsonb:=pg_temp.safe_retention_fixture('retention-archive-lookahead');a text:=f->>'alias';
 i integer;role text;run_id text;locator jsonb;original_graph jsonb;original_history jsonb;archives jsonb;
 prune_counts jsonb;batch uuid;role_name text;
begin
 assert not onflows_private.has_analysis_archive_locator(null);
 assert not onflows_private.has_analysis_archive_locator('{"samples":[1,2,3],"codec":"ordinary-inline-codec"}');
 assert onflows_private.has_analysis_archive_locator('{"note":"onflows-history-object-xz-v1"}'),
  'Archive marker in ordinary text must conservatively retain a row';
 foreach role_name in array array['service_role','anon','authenticated'] loop
  assert not has_function_privilege(role_name,'onflows_private.has_analysis_archive_locator(jsonb)','execute');
 end loop;
 perform pg_temp.prepare_safe_retention_control(f);
 -- Keep the preexisting control graph out of this independent archive test.
 perform onflows_private.pin_history_generations(a,array[(f->>'control')::uuid]);
 perform onflows_private.pin_history_activity_references(a,
  jsonb_build_object('run_keys',jsonb_build_array(f->>'control_shadow',f->>'control_canonical')),false);
 for i in 1..51 loop
  role:='archive-shadow-'||i;f:=pg_temp.add_safe_retention_orphan(f,role);
  locator:=jsonb_build_object('codec','onflows-history-object-xz-v1','bucket','onflows-history',
   'path','synthetic/shadow/'||(f->>(role||'_shadow'))||'.xz','sha256',repeat('1',64));
  update public.onflows_activity_derived_runs set result_payload=jsonb_build_object('nested',jsonb_build_object('timeseries',locator))
   where run_key=f->>(role||'_shadow');

  -- No run points at these inputs: the input archive guard is their only exclusion.
  role:='archive-input-'||i;f:=pg_temp.add_safe_retention_orphan(f,role);
  delete from public.onflows_activity_derived_runs where run_key=f->>(role||'_shadow');
  locator:=jsonb_build_object('codec','onflows-history-object-xz-v1','bucket','onflows-history',
   'path','synthetic/input/'||(f->>(role||'_input'))||'.xz','sha256',repeat('2',64));
  update public.onflows_activity_model_inputs set input_payload=jsonb_build_object('samples',locator)
   where input_key=f->>(role||'_input');

  role:='archive-canonical-'||i;f:=pg_temp.add_safe_retention_orphan(f,role);
  delete from public.onflows_activity_derived_runs where run_key=f->>(role||'_shadow');
  delete from public.onflows_activity_model_inputs where input_key=f->>(role||'_input');
  run_id:=encode(sha256(convert_to(a||':'||role||':canonical','UTF8')),'hex');
  locator:=jsonb_build_object('codec','onflows-history-object-xz-v1','bucket','onflows-history',
   'path','synthetic/canonical/'||run_id||'.xz','sha256',repeat('3',64));
  insert into public.onflows_activity_canonical_runs
   (run_key,athlete_alias,activity_ref,scientific_input_hash,result_hash,schema_version,model_version,result_payload,created_at)
   values(run_id,a,f->>(role||'_activity'),run_id,run_id,'synthetic-archive-v1','synthetic-archive-v1',
    jsonb_build_object('payload',locator),clock_timestamp()-interval '89 days');
 end loop;
 -- This later ordinary graph falls beyond 50 old archived candidates in all
 -- three guarded phases. Exclusion AFTER raw LIMIT would starve these rows.
 f:=pg_temp.add_safe_retention_orphan(f,'inline-after-archives');
 run_id:=encode(sha256(convert_to(a||':inline-canonical','UTF8')),'hex');
 insert into public.onflows_activity_canonical_runs
  (run_key,athlete_alias,activity_ref,scientific_input_hash,result_hash,schema_version,model_version,result_payload,created_at)
  values(run_id,a,f->>'inline-after-archives_activity',run_id,run_id,'synthetic-inline-v1','synthetic-inline-v1',
   '{"ordinary_inline":true}',clock_timestamp()-interval '89 days');
 archives:=pg_temp.safe_retention_archived_rows(a);
 assert jsonb_array_length(archives->'shadow')=51 and jsonb_array_length(archives->'input')=51
  and jsonb_array_length(archives->'canonical')=51,'Archive fixtures are not independently eligible by table';
 original_graph:=pg_temp.safe_retention_graph(a);original_history:=pg_temp.safe_retention_history_rows(a);
 select to_jsonb(result) into prune_counts from public.prune_onflows_analysis_generations(a,1,interval '30 days',1000) result;
 assert prune_counts='{"deleted_generations":0,"deleted_activity_rows":0,"deleted_jobs":0,"deleted_shadow_runs":1,"deleted_canonical_runs":1,"deleted_model_inputs":1,"deleted_catalog_rows":1}',
  'Archive exclusion starved the later inline graph or deleted archived evidence';
 assert pg_temp.safe_retention_archived_rows(a)=archives,'Archived row/locator/xmin/ctid changed';
 assert not exists(select 1 from public.onflows_activity_derived_runs where run_key=f->>'inline-after-archives_shadow');
 assert not exists(select 1 from public.onflows_activity_model_inputs where input_key=f->>'inline-after-archives_input');
 assert not exists(select 1 from public.onflows_activity_canonical_runs where run_key=run_id);
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 select batch_id into strict batch from onflows_private.analysis_cleanup_batches where athlete_alias=a;
 assert (select backup_rows=5 from onflows_private.analysis_cleanup_batches where batch_id=batch);
 assert not exists(select 1 from onflows_private.analysis_cleanup_rows where batch_id=batch
  and before_row::text like '%onflows-history-object-xz-v1%'),'Archived payload entered deletion/recovery journal';
 perform onflows_private.restore_analysis_cleanup_batch(batch);
 assert pg_temp.safe_retention_graph(a)=original_graph,'Inline graph restoration changed archived data';
 assert pg_temp.safe_retention_archived_rows(a)=archives;
 assert pg_temp.safe_retention_history_rows(a)=original_history;
end $$;

-- Test-only adversarial fanout AFTER preflight verifies the hard transactional
-- guard independently from the selector. No production trigger is installed.
create function pg_temp.inject_retention_fanout_after_preflight()
returns trigger language plpgsql as $$
declare fixture jsonb;
begin
 if old.athlete_alias=current_setting('onflows.test_retention_alias',true)
  and old.generation_id::text=current_setting('onflows.test_retention_generation',true) then
  fixture:=jsonb_build_object('alias',old.athlete_alias);
  perform pg_temp.add_safe_retention_fanout(fixture,old.generation_id,1001,'after-preflight');
 end if;
 return old;
end $$;
create trigger zz_retention_test_late_fanout before delete on public.onflows_analysis_generations
 for each row execute function pg_temp.inject_retention_fanout_after_preflight();
do $$
declare f jsonb:=pg_temp.safe_retention_fixture('retention-hard-budget');a text:=f->>'alias';
 original_graph jsonb;original_history jsonb;denied boolean:=false;message text;
begin
 perform pg_temp.prepare_safe_retention_control(f);
 original_graph:=pg_temp.safe_retention_graph(a);original_history:=pg_temp.safe_retention_history_rows(a);
 perform set_config('onflows.test_retention_alias',a,true);
 perform set_config('onflows.test_retention_generation',f->>'control',true);
 begin perform * from public.prune_onflows_analysis_generations(a,1,interval '30 days',1000);
 exception when raise_exception then
  get stacked diagnostics message=message_text;
  assert message='Cleanup recovery budget exceeded';denied:=true;
 end;
 assert denied,'Hard preimage guard failed to reject underestimated fanout';
 assert pg_temp.safe_retention_graph(a)=original_graph,'Hard guard committed a partial deletion/late fanout';
 assert pg_temp.safe_retention_history_rows(a)=original_history;
 assert not exists(select 1 from onflows_private.analysis_cleanup_batches where athlete_alias=a),
  'Hard guard left a partial backup batch';
 assert coalesce(current_setting('onflows.cleanup_batch_id',true),'')='';
end $$;
rollback;
