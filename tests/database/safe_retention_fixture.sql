-- Synthetic immutable graph shared by retention acceptance and real races.
-- Load history_generation_fixture.sql first. All callers own cleanup/rollback.
create or replace function pg_temp.safe_retention_fixture(p_prefix text)
returns jsonb language plpgsql as $$
declare
 f jsonb := pg_temp.history_generation_fixture(p_prefix);
 alias text := f->>'alias';
 name text;
 scoped_alias text;
 activity text;
 input_id text;
 shadow_id text;
 canonical_id text;
 input_digest text;
 result_digest text;
begin
 foreach name in array array['active','parent','source','root','extra','control','rollback','ready','foreign'] loop
  scoped_alias := case when name='foreign' then f->>'other_alias' else alias end;
  activity := 'act_'||md5(alias||':'||name||':activity');
  input_id := md5(alias||':'||name||':input')||md5(name||':'||alias||':input');
  shadow_id := md5(alias||':'||name||':shadow')||md5(name||':'||alias||':shadow');
  canonical_id := md5(alias||':'||name||':canonical')||md5(name||':'||alias||':canonical');
  input_digest := md5(alias||':'||name||':input-hash')||md5(name||':'||alias||':input-hash');
  result_digest := md5(alias||':'||name||':result-hash')||md5(name||':'||alias||':result-hash');
  f := f||jsonb_build_object(name||'_activity',activity,name||'_input',input_id,
    name||'_shadow',shadow_id,name||'_canonical',canonical_id,name||'_input_hash',input_digest,
    name||'_result_hash',result_digest);
  insert into public.onflows_activity_catalog
   (activity_ref,athlete_alias,provider_activity_key,start_at_utc,local_date,sport,
    first_seen_at,last_seen_at,canonical_summary,intervals)
   values(activity,scoped_alias,md5(alias||':'||name||':provider')||md5(name||':'||alias||':provider'),
    clock_timestamp()-interval '90 days',current_date-90,'Run',
    clock_timestamp()-interval '90 days',clock_timestamp()-interval '90 days',
    jsonb_build_object('synthetic_retention_fixture',name),'[]');
  insert into public.onflows_activity_model_inputs
   (input_key,athlete_alias,activity_ref,input_hash,schema_version,input_payload,created_at)
   values(input_id,scoped_alias,activity,input_digest,'synthetic-retention-v1',
    jsonb_build_object('input_hash',input_digest,'samples',jsonb_build_array(jsonb_build_object('elapsed',1,'speed',3.2))),
    clock_timestamp()-interval '90 days');
  insert into public.onflows_activity_derived_runs
   (run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload,created_at)
   values(shadow_id,input_id,scoped_alias,activity,result_digest,'synthetic-retention-v1',
    jsonb_build_object('result_hash',result_digest,'timeseries',jsonb_build_array(jsonb_build_object('elapsed',1)),
      'zone_summary',jsonb_build_array(jsonb_build_object('zone','Z1','seconds',60))),
    clock_timestamp()-interval '90 days');
  insert into public.onflows_activity_canonical_runs
   (run_key,athlete_alias,activity_ref,scientific_input_hash,result_hash,schema_version,model_version,result_payload,created_at)
   values(canonical_id,scoped_alias,activity,input_digest,result_digest,'synthetic-retention-v1','synthetic-retention-v1',
    jsonb_build_object('synthetic_retention_fixture',name),clock_timestamp()-interval '90 days');
  update public.onflows_activity_catalog set latest_shadow_run_key=shadow_id,latest_canonical_run_key=canonical_id
   where activity_ref=activity;
  if name in ('active','control') then
   insert into public.onflows_analysis_generation_activities
    (generation_id,athlete_alias,activity_ref,local_date,catalog_payload,payload_hash,input_key,shadow_run_key,canonical_run_key)
    values((f->>name)::uuid,scoped_alias,activity,current_date-90,
     jsonb_build_object('activity_ref',activity),repeat('f',64),input_id,shadow_id,canonical_id);
  end if;
 end loop;
 return f;
end $$;

create or replace function pg_temp.safe_retention_history_rows(p_alias text)
returns jsonb language sql as $$
 select pg_temp.history_generation_rows(p_alias)||coalesce(jsonb_agg(jsonb_build_object(
  'table','model_entries','row',to_jsonb(m),'xmin',m.xmin::text,'ctid',m.ctid::text,
  'payload_sha256',encode(sha256(convert_to(m.payload::text,'UTF8')),'hex'))
  order by m.kind,m.entry_key,m.revision),'[]'::jsonb)
 from public.onflows_model_entries m where m.athlete_alias=p_alias;
$$;

create or replace function pg_temp.safe_retention_graph(p_alias text)
returns jsonb language sql as $$
 select jsonb_build_object(
  'catalog',(select coalesce(jsonb_agg(to_jsonb(c) order by c.activity_ref),'[]')
    from public.onflows_activity_catalog c where c.athlete_alias=p_alias),
  'inputs',(select coalesce(jsonb_agg(to_jsonb(i) order by i.input_key),'[]')
    from public.onflows_activity_model_inputs i where i.athlete_alias=p_alias),
  'shadow',(select coalesce(jsonb_agg(to_jsonb(s) order by s.run_key),'[]')
    from public.onflows_activity_derived_runs s where s.athlete_alias=p_alias),
  'canonical',(select coalesce(jsonb_agg(to_jsonb(c) order by c.run_key),'[]')
    from public.onflows_activity_canonical_runs c where c.athlete_alias=p_alias),
  'generations',(select coalesce(jsonb_agg(to_jsonb(g) order by g.generation_id),'[]')
    from public.onflows_analysis_generations g where g.athlete_alias=p_alias),
  'activity_rows',(select coalesce(jsonb_agg(to_jsonb(g) order by g.generation_id,g.activity_ref),'[]')
    from public.onflows_analysis_generation_activities g where g.athlete_alias=p_alias),
  'summaries',(select coalesce(jsonb_agg(to_jsonb(s) order by s.run_key),'[]')
    from public.onflows_activity_run_summaries s where s.athlete_alias=p_alias),
  'jobs',(select coalesce(jsonb_agg(to_jsonb(j) order by j.job_id),'[]')
    from public.onflows_sync_jobs j where j.athlete_alias=p_alias)
 );
$$;

-- Leave exactly the FAILED control generation/run graph eligible. This owner
-- helper marks only synthetic coverage and never runs on a maintained database.
create or replace function pg_temp.prepare_safe_retention_control(f jsonb)
returns void language plpgsql as $$
declare name text; a text:=f->>'alias'; actor uuid:=(f->>'actor')::uuid;
begin
 foreach name in array array['active','parent','source','root','extra','rollback','ready'] loop
  perform onflows_private.pin_history_generations(a,array[(f->>name)::uuid]);
  perform onflows_private.pin_history_activity_references(a,
   jsonb_build_object('run_keys',jsonb_build_array(f->>(name||'_shadow'),f->>(name||'_canonical'))),false);
 end loop;
 insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
  values(a,'SPEED_TEST','retained-test',1,jsonb_build_object('source_run_key',f->>'parent_shadow'),actor);
 insert into public.onflows_response_entries(athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id)
  values(a,'SESSION',f->>'source_activity',current_date,1,'{}',actor);
 insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
  values(a,'DRAFT','2098-01-01',1,jsonb_build_object('start_date','2098-01-01','source',
   jsonb_build_object('generation_id',f->>'root','source_run_key',f->>'extra_shadow')),actor);
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,1,'REVIEW',actor,jsonb_build_object('proposal',jsonb_build_object('canonical_run_key',f->>'rollback_canonical')));
 insert into onflows_private.analysis_retention_coverage(athlete_alias,evidence)
  values(a,'{"synthetic_fixture":true,"all_history_revisions_scanned":true}')
  on conflict(athlete_alias) do nothing;
end $$;

create or replace function pg_temp.add_safe_retention_orphan(f jsonb,p_role text,p_padding integer default 0)
returns jsonb language plpgsql as $$
declare a text:=f->>'alias'; activity text:='act_'||md5(a||':'||p_role||':orphan');
 input_id text:=encode(sha256(convert_to(a||':'||p_role||':input','UTF8')),'hex');
 run_id text:=encode(sha256(convert_to(a||':'||p_role||':shadow','UTF8')),'hex');
begin
 insert into public.onflows_activity_catalog(activity_ref,athlete_alias,provider_activity_key,first_seen_at,last_seen_at)
  values(activity,a,encode(sha256(convert_to(a||':'||p_role||':provider','UTF8')),'hex'),
   clock_timestamp()-interval '89 days',clock_timestamp()-interval '89 days');
 insert into public.onflows_activity_model_inputs(input_key,athlete_alias,activity_ref,input_hash,schema_version,input_payload,created_at)
  values(input_id,a,activity,input_id,'synthetic-retention-v1','{}',clock_timestamp()-interval '89 days');
 insert into public.onflows_activity_derived_runs(run_key,input_key,athlete_alias,activity_ref,result_hash,schema_version,result_payload,created_at)
  values(run_id,input_id,a,activity,run_id,'synthetic-retention-v1',
   jsonb_build_object('padding',repeat('x',p_padding),'zone_summary','[]'::jsonb),clock_timestamp()-interval '89 days');
 return f||jsonb_build_object(p_role||'_activity',activity,p_role||'_input',input_id,p_role||'_shadow',run_id);
end $$;

create or replace function pg_temp.add_safe_retention_fanout(f jsonb,p_generation uuid,p_count integer,p_salt text)
returns void language plpgsql as $$
declare a text:=f->>'alias';
begin
 insert into public.onflows_activity_catalog(activity_ref,athlete_alias,provider_activity_key)
  select 'act_'||md5(a||':'||p_salt||':'||n),a,
   encode(sha256(convert_to(a||':'||p_salt||':'||n,'UTF8')),'hex') from generate_series(1,p_count) n;
 insert into public.onflows_analysis_generation_activities(generation_id,athlete_alias,activity_ref,catalog_payload,payload_hash)
  select p_generation,a,'act_'||md5(a||':'||p_salt||':'||n),'{}',repeat('a',64) from generate_series(1,p_count) n;
end $$;

create or replace function pg_temp.add_safe_retention_generation(f jsonb,p_role text,p_sequence integer)
returns jsonb language plpgsql as $$
declare a text:=f->>'alias'; generation uuid:=gen_random_uuid(); job uuid:=gen_random_uuid(); document jsonb;
begin
 select to_jsonb(j) into strict document from public.onflows_sync_jobs j where job_id=(f->>'control_job')::uuid;
 insert into public.onflows_sync_jobs select (jsonb_populate_record(null::public.onflows_sync_jobs,
  document||jsonb_build_object('job_id',job,'request_sequence',p_sequence,'current_generation_id',null,
   'idempotency_key',encode(sha256(convert_to(a||':'||p_role,'UTF8')),'hex')))).*;
 select to_jsonb(g) into strict document from public.onflows_analysis_generations g where generation_id=(f->>'control')::uuid;
 insert into public.onflows_analysis_generations select (jsonb_populate_record(null::public.onflows_analysis_generations,
  document||jsonb_build_object('generation_id',generation,'job_id',job,'request_sequence',p_sequence,
   'activity_set_generation_id',generation,'snapshot_payload',jsonb_build_object('synthetic_fixture',p_role)))).*;
 insert into public.onflows_analysis_generation_activities
  (generation_id,athlete_alias,activity_ref,local_date,catalog_payload,payload_hash,input_key,shadow_run_key,canonical_run_key)
  values(generation,a,f->>'control_activity',current_date-90,'{}',repeat('a',64),f->>'control_input',f->>'control_shadow',f->>'control_canonical');
 return f||jsonb_build_object(p_role,generation,p_role||'_job',job);
end $$;
