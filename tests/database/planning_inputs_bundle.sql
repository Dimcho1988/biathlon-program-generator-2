-- Migrated PostgreSQL contract. All fixtures are synthetic and roll back.
begin;
\ir history_generation_fixture.sql

do $$
declare
 f jsonb := pg_temp.history_generation_fixture('planning-inputs-bundle');
 a text := f->>'alias';
 other_a text := f->>'other_alias';
 actor uuid := (f->>'actor')::uuid;
 bundle jsonb;
 expected_analysis jsonb;
 expected_settings jsonb;
 expected_profile jsonb;
 hint jsonb;
 bad_hint jsonb;
 role_name text;
 rejected boolean;
 original_rows jsonb;
 analysis_state_privileges jsonb;
begin
 assert (select not prosecdef and provolatile='s' and proconfig=array['search_path=""']
   from pg_proc where oid='public.read_onflows_planning_inputs(text,text,jsonb)'::regprocedure),
   'Planning inputs must remain STABLE SECURITY INVOKER with an empty search path';
 assert has_function_privilege('service_role','public.read_onflows_planning_inputs(text,text,jsonb)','EXECUTE'),
   'API role cannot read planning inputs';
 foreach role_name in array array['anon','authenticated'] loop
   assert not has_function_privilege(role_name,'public.read_onflows_planning_inputs(text,text,jsonb)','EXECUTE'),
     'Planning inputs exposed to a public role';
 end loop;
 -- The September accounts migration grants ALL on existing public tables;
 -- the later generation read-contract migration narrows only generations.
 -- Do not invent a SELECT-only state baseline which this read RPC never set.
 select jsonb_object_agg(permission,has_table_privilege('service_role',
   'public.onflows_athlete_analysis_state',permission)) into analysis_state_privileges
 from unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) permission;
 assert not has_table_privilege('service_role','public.onflows_analysis_generations','UPDATE'),
   'Immutable generation write access was widened';

 -- Latest revisions win despite clock anomalies. Response identity order comes
 -- from the first raw-history occurrence, not the winning revision timestamp.
 insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id,recorded_at) values
   (a,'SPEED_TEST','a',1,'{"winner":false}',actor,'2026-01-03T00:00:00Z'),
   (a,'SPEED_TEST','a',2,'{"winner":true}',actor,'2026-01-01T00:00:00Z'),
   (a,'RECOVERY','recovery-v2',1,'{}',actor,'2026-01-02T00:00:00Z'),
   (other_a,'SPEED_TEST','other-model',1,'{"other_athlete":true}',actor,'2026-01-04T00:00:00Z');
 insert into public.onflows_response_entries
   (athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id,recorded_at) values
   (a,'DAILY','a','2026-01-03',1,'{"winner":false}',actor,'2026-01-03T00:00:00Z'),
   (a,'DAILY','a','2026-01-01',2,'{"winner":true}',actor,'2026-01-01T00:00:00Z'),
   (a,'DAILY','b','2026-01-02',1,'{}',actor,'2026-01-02T00:00:00Z'),
   (a,'BLOCK','c','2026-01-02',1,'{}',actor,'2026-01-02T00:00:00Z'),
   (other_a,'DAILY','other-response','2026-01-04',1,'{"other_athlete":true}',actor,'2026-01-04T00:00:00Z');
 insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id,recorded_at)
   values(a,'PROFILE','management-profile-v1',2,'{"latest_profile":true}',actor,'1970-01-01T00:00:00Z');
 select jsonb_build_array(to_jsonb(s)) into expected_settings from (
   select hr_zone_bounds,timezone,hrmax_bpm,hr_zone_source,hr_zone_percentages,
     planning_profile,mesocycle_accent_preferences,planning_calendar
   from public.onflows_athlete_settings where athlete_alias=a
 ) s;
 select jsonb_build_array(to_jsonb(p)) into expected_profile from (
   select kind,entry_key,revision,payload,actor_id,recorded_at from public.onflows_management_entries
   where athlete_alias=a and kind='PROFILE' and entry_key='management-profile-v1' order by revision desc limit 1
 ) p;
 original_rows := pg_temp.history_generation_rows(a);

 execute 'set local role service_role';
 bundle := public.read_onflows_planning_inputs(a);
 assert (select jsonb_object_agg(permission,has_table_privilege('service_role',
   'public.onflows_athlete_analysis_state',permission))
   from unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) permission)
   =analysis_state_privileges, 'Read bundle changed existing analysis-state privileges';
 assert bundle ?& array['schema_version','athlete_alias','settings_rows','profile_rows',
     'model_entries','model_history_exhausted','response_entries','response_history_exhausted','analysis']
   and bundle-array['schema_version','athlete_alias','settings_rows','profile_rows',
     'model_entries','model_history_exhausted','response_entries','response_history_exhausted','analysis']='{}'::jsonb,
   'Planning input envelope schema changed';
 assert bundle->>'schema_version'='planning-inputs-v1' and bundle->>'athlete_alias'=a,
   'Planning input version or athlete identity changed';
 assert bundle->'settings_rows'=expected_settings and bundle->'profile_rows'=expected_profile,
   'Raw settings whitelist or latest planning profile changed';
 assert bundle->'model_history_exhausted'='false'::jsonb and bundle->'response_history_exhausted'='false'::jsonb,
   'Small histories were marked exhausted';
 assert jsonb_array_length(bundle->'model_entries')=2
   and bundle#>>'{model_entries,0,kind}'='RECOVERY'
   and bundle#>>'{model_entries,1,entry_key}'='a'
   and bundle#>'{model_entries,1,revision}'='2'::jsonb
   and bundle#>'{model_entries,1,recorded_at}'=to_jsonb('2026-01-01T00:00:00Z'::timestamptz)
   and bundle#>'{model_entries,1,payload,winner}'='true'::jsonb,
   'Model projection changed latest revision or identity ordering';
 assert (bundle#>'{model_entries,1}')-array['kind','entry_key','revision','payload','recorded_at']='{}'::jsonb
   and (select count(*)=5 from jsonb_object_keys(bundle#>'{model_entries,1}')),
   'Model projection changed its exact selected columns';
 assert jsonb_array_length(bundle->'response_entries')=3
   and bundle#>>'{response_entries,0,entry_key}'='a'
   and bundle#>'{response_entries,0,revision}'='2'::jsonb
   and bundle#>>'{response_entries,0,observed_date}'='2026-01-01'
   and bundle#>'{response_entries,0,recorded_at}'=to_jsonb('2026-01-01T00:00:00Z'::timestamptz)
   and bundle#>'{response_entries,0,payload,winner}'='true'::jsonb
   and bundle#>>'{response_entries,1,kind}'='BLOCK'
   and bundle#>>'{response_entries,2,entry_key}'='b',
   'Response winner or first-occurrence/tied-time identity order changed';
 assert (bundle#>'{response_entries,0}')-array['kind','entry_key','observed_date','revision','payload','recorded_at']='{}'::jsonb
   and (select count(*)=6 from jsonb_object_keys(bundle#>'{response_entries,0}')),
   'Response projection changed its exact selected columns';
 select to_jsonb(x) into expected_analysis from public.active_onflows_analysis(a) x;
 assert bundle->'analysis'=jsonb_build_object('mode','FULL','row',expected_analysis),
   'Default FULL mode changed the original active analysis row';

 hint := jsonb_build_object('generation_id',expected_analysis->'generation_id',
   'analysis_revision',expected_analysis->'revision','analysis_as_of',expected_analysis->'analysis_as_of',
   'snapshot_fingerprint',repeat('b',64));
 bundle := public.read_onflows_planning_inputs(a,'HINTED',jsonb_build_array(hint));
 assert bundle->'analysis'=jsonb_build_object('mode','METADATA','row',expected_analysis-'snapshot_payload'),
   'Exact immutable hint did not select metadata without a snapshot';
 assert public.read_onflows_planning_inputs(a,'FULL',jsonb_build_array(hint))->'analysis'
     =jsonb_build_object('mode','FULL','row',expected_analysis),
   'FULL mode reused a hint instead of reading the snapshot';
 assert public.read_onflows_planning_inputs(a,'HINTED',null)->'analysis'
     =jsonb_build_object('mode','FULL','row',expected_analysis), 'SQL NULL hints were not a full read';

 -- Scalar/partial hints, malformed identity strings and wrong JSON types must
 -- fall back in this same RPC, without casts normalizing them into a match.
 for bad_hint in select value from jsonb_array_elements(jsonb_build_array(
   'null'::jsonb,'{}'::jsonb,'"not-an-array"'::jsonb,'[]'::jsonb,
   '[null,1,true,[]]'::jsonb,
   jsonb_build_array(hint-'generation_id'),jsonb_build_array(hint-'analysis_revision'),
   jsonb_build_array(hint-'snapshot_fingerprint'),
   jsonb_build_array(hint||'{"generation_id":123}'::jsonb),
   jsonb_build_array(hint||'{"generation_id":"not-a-uuid"}'::jsonb),
   jsonb_build_array(hint||'{"analysis_revision":"100"}'::jsonb),
   jsonb_build_array(hint||'{"analysis_revision":100.0}'::jsonb),
   jsonb_build_array(hint||'{"analysis_revision":true}'::jsonb),
   jsonb_build_array(hint||'{"analysis_revision":0}'::jsonb),
   jsonb_build_array(hint||'{"analysis_revision":-1}'::jsonb),
   jsonb_build_array(hint||'{"analysis_revision":999999999999999999999999999999999999}'::jsonb),
   jsonb_build_array(hint||'{"analysis_as_of":null}'::jsonb),
   jsonb_build_array(hint||'{"analysis_as_of":true}'::jsonb),
   jsonb_build_array(hint||jsonb_build_object('analysis_as_of',replace(expected_analysis->>'analysis_as_of','-','/'))),
   jsonb_build_array(hint||jsonb_build_object('snapshot_fingerprint',repeat('B',64))),
   jsonb_build_array(hint||jsonb_build_object('snapshot_fingerprint',repeat('b',63))),
   jsonb_build_array(hint||'{"snapshot_fingerprint":true}'::jsonb)
 )) loop
   assert public.read_onflows_planning_inputs(a,'HINTED',bad_hint)->'analysis'
       =jsonb_build_object('mode','FULL','row',expected_analysis),
     'Malformed or unmatched hint omitted the live snapshot';
 end loop;
 if upper(expected_analysis->>'generation_id')<>expected_analysis->>'generation_id' then
   assert public.read_onflows_planning_inputs(a,'HINTED',jsonb_build_array(
       hint||jsonb_build_object('generation_id',upper(expected_analysis->>'generation_id'))))->'analysis'
       =jsonb_build_object('mode','FULL','row',expected_analysis),
     'Uppercase UUID hint was normalized into an exact-string match';
 end if;
 assert public.read_onflows_planning_inputs(a,'HINTED',jsonb_build_array('null'::jsonb,hint))->'analysis'
     =jsonb_build_object('mode','METADATA','row',expected_analysis-'snapshot_payload'),
   'A malformed companion hid another exact valid hint';
 rejected := false;
 begin perform public.read_onflows_planning_inputs(a,'METADATA');
 exception when invalid_parameter_value then rejected := true; end;
 assert rejected, 'Unknown mode was accepted';
 rejected := false;
 begin perform public.read_onflows_planning_inputs(a,null);
 exception when invalid_parameter_value then rejected := true; end;
 assert rejected, 'NULL mode was accepted';
 assert pg_temp.history_generation_rows(a)=original_rows, 'Planning reads changed immutable evidence';

 -- An old same-generation hint cannot survive supported monotonic rollback.
 perform public.rollback_onflows_analysis_generation(a,(f->>'rollback')::uuid);
 perform public.rollback_onflows_analysis_generation(a,(f->>'active')::uuid);
 bundle := public.read_onflows_planning_inputs(a,'HINTED',jsonb_build_array(hint));
 assert bundle#>>'{analysis,mode}'='FULL' and bundle#>'{analysis,row,revision}'='102'::jsonb
   and bundle#>'{analysis,row,generation_id}'=expected_analysis->'generation_id',
   'Same-generation monotonic revision change reused an old hint';

 bundle := public.read_onflows_planning_inputs(a||'-missing');
 assert bundle->'settings_rows'='[]'::jsonb and bundle->'profile_rows'='[]'::jsonb
   and bundle->'model_entries'='[]'::jsonb and bundle->'response_entries'='[]'::jsonb
   and bundle->'analysis'='null'::jsonb, 'Missing athlete sections changed';
 bundle := public.read_onflows_planning_inputs(other_a,'HINTED',jsonb_build_array(hint));
 assert bundle->'analysis'='null'::jsonb and bundle->'profile_rows'='[]'::jsonb
   and jsonb_array_length(bundle->'model_entries')=1
   and bundle#>>'{model_entries,0,entry_key}'='other-model'
   and jsonb_array_length(bundle->'response_entries')=1
   and bundle#>>'{response_entries,0,entry_key}'='other-response', 'Athlete sections leaked across tenants';

 execute 'reset role';
 insert into public.onflows_training_snapshots(athlete_alias,payload,activated_at)
   values(other_a,'{"legacy_fixture":true}','2026-01-01T00:00:00Z');
 execute 'set local role service_role';
 select to_jsonb(x) into expected_analysis from public.active_onflows_analysis(other_a) x;
 bundle := public.read_onflows_planning_inputs(other_a,'HINTED',jsonb_build_array(hint));
 assert bundle->'analysis'=jsonb_build_object('mode','FULL','row',expected_analysis)
   and bundle#>'{analysis,row,generation_id}'='null'::jsonb and bundle#>'{analysis,row,revision}'='0'::jsonb,
   'Mutable legacy analysis did not retain its full original row';
 execute 'reset role';
 insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision)
   values(other_a,(f->>'foreign')::uuid,1);
 execute 'set local role service_role';
 assert public.read_onflows_planning_inputs(other_a)->'analysis'='null'::jsonb,
   'Unavailable nonnull pointer incorrectly fell back to legacy data';
 execute 'reset role';

 -- Bound raw revisions, even when every revision collapses to one identity.
 insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
   select other_a,'SPEED_TEST','other-model',n,'{}'::jsonb,actor from generate_series(2,9999) n;
 insert into public.onflows_response_entries(athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id)
   select other_a,'DAILY','other-response','2026-01-01'::date,n,'{}'::jsonb,actor from generate_series(2,9999) n;
 execute 'set local role service_role';
 bundle := public.read_onflows_planning_inputs(other_a);
 assert bundle->'model_history_exhausted'='false'::jsonb
   and bundle->'response_history_exhausted'='false'::jsonb
   and jsonb_array_length(bundle->'model_entries')=1 and bundle#>'{model_entries,0,revision}'='9999'::jsonb
   and jsonb_array_length(bundle->'response_entries')=1 and bundle#>'{response_entries,0,revision}'='9999'::jsonb,
   '9,999 raw revisions did not preserve the bounded latest projection';
 execute 'reset role';
 insert into public.onflows_model_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
   values(other_a,'SPEED_TEST','other-model',10000,'{}',actor);
 execute 'set local role service_role';
 bundle := public.read_onflows_planning_inputs(other_a);
 assert bundle->'model_history_exhausted'='true'::jsonb
   and bundle->'response_history_exhausted'='false'::jsonb
   and bundle->'model_entries'='[]'::jsonb and bundle#>'{response_entries,0,revision}'='9999'::jsonb,
   'One exhausted history invalidated another lazy section';
 execute 'reset role';
 insert into public.onflows_response_entries(athlete_alias,kind,entry_key,observed_date,revision,payload,actor_id)
   values(other_a,'DAILY','other-response','2026-01-01',10000,'{}',actor);
 execute 'set local role service_role';
 bundle := public.read_onflows_planning_inputs(other_a);
 assert bundle->'model_history_exhausted'='true'::jsonb and bundle->'response_history_exhausted'='true'::jsonb
   and bundle->'model_entries'='[]'::jsonb and bundle->'response_entries'='[]'::jsonb,
   'Exactly 10,000 raw revisions did not signal lazy bounded-history failure';
 execute 'reset role';
end $$;

rollback;
