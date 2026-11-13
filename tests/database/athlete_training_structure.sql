-- Synthetic athlete-only source extraction and atomic proposal fences.
begin;
set local plpgsql.check_asserts='on';
insert into auth.users(id) values
 ('99113000-0000-4000-8000-000000000001'),('99113000-0000-4000-8000-000000000002');
insert into public.onflows_profiles(user_id,display_name) values
 ('99113000-0000-4000-8000-000000000001','Structure owner'),
 ('99113000-0000-4000-8000-000000000002','Structure coach');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('structure-source-test','structure-source-provider','synthetic-not-a-token','REVOKED'),
 ('structure-other-test','structure-other-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 select a,array[100,120,140,155,170,190],190,'UTC' from unnest(array['structure-source-test','structure-other-test']) a;
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99113000-0000-4000-8000-000000000001','structure-source-test',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,view_plan,edit_plan) values
 ('99113000-0000-4000-8000-000000000001','99113000-0000-4000-8000-000000000002',true,true);

do $$
declare week jsonb; second_week jsonb; frozen jsonb; f regprocedure; r text;
begin
 select jsonb_agg(jsonb_build_object('date',('2026-11-02'::date+i)::text,'status','TRAINING',
   'sessions',jsonb_build_array(jsonb_build_object('method_id','METHOD-'||i,'sport','RUN',
    'total_minutes',500+i,'dose_evidence',jsonb_build_object('PRIVATE','must not escape'),
    'method_library',jsonb_build_object('id','METHOD-'||i,'PRIVATE','source evidence')))))
 into week from generate_series(0,6) i;
 week:=jsonb_set(week,'{0}', '{"date":"2026-11-02","status":"TRAINING","session":{"method_id":"LEGACY","sport":"RUN","total_minutes":500}}');
 week:=jsonb_set(week,'{2}', '{"date":"2026-11-04","status":"REST","sessions":[],"session":{"method_id":"IGNORE-LEGACY"}}');
 week:=jsonb_set(week,'{4}', '{"date":"2026-11-06","status":"TRAINING","sessions":[{"method_id":"PERSONAL-ONE","sport":"RUN","method_library":{"id":"PERSONAL-ONE","personal_variant":{"base_method_id":"SOURCE-ONE","notes":"PRIVATE notes"}}}]}');
 week:=jsonb_set(week,'{5}', '{"date":"2026-11-07","status":"TRAINING","sessions":[{"method_id":"COMBINED-ONE","sport":"RUN","method_library":{"id":"COMBINED-ONE","components":[{"id":"METHOD-1","PRIVATE":1},{"id":"METHOD-2","PRIVATE":2}]},"execution_descriptor":{"id":"COMBINED-ONE","structure":"COMBINED","parts":[{"id":"METHOD-1","structure":"CONTINUOUS","PRIVATE":3},{"id":"METHOD-2","structure":"CONTINUOUS","PRIVATE":4}],"version":"PRIVATE"}}]}');
 select jsonb_agg(jsonb_build_object('date',('2026-11-09'::date+i)::text,'status','REST','session',null))
 into second_week from generate_series(0,6) i;
 frozen:=jsonb_build_object('status','ACTIVE','plan',jsonb_build_object('days',week),'proposal',null,'PRIVATE',repeat('do not project ',10000));
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload) values
  ('structure-source-test',1,'ACTIVATE','99113000-0000-4000-8000-000000000001',frozen),
  ('structure-source-test',2,'ADAPT','99113000-0000-4000-8000-000000000001',jsonb_build_object('status','ACTIVE','plan',jsonb_build_object('days',second_week))),
  ('structure-source-test',3,'REVIEW','99113000-0000-4000-8000-000000000001','{"status":"REVIEW_REQUIRED","plan":{"days":[{"date":"2026-11-02","status":"TRAINING","sessions":[{"method_id":"LATEST","sport":"RUN"}]},{"date":"2026-11-03","status":"EXISTING_ACTIVITY","sessions":[]},{"date":"2026-11-05","status":"EXISTING_ACTIVITY","session":null},{"date":"2026-11-17","status":"EXISTING_ACTIVITY","sessions":null}],"lookahead_days":[{"date":"2026-11-16","status":"TRAINING","sessions":[{"method_id":"PROVISIONAL"}]}]},"proposal":{"days":[{"date":"2026-11-02","status":"TRAINING","sessions":[{"method_id":"UNAPPLIED"}]}]}}'),
  ('structure-other-test',99,'ACTIVATE','99113000-0000-4000-8000-000000000001',frozen);
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by) values
  ('structure-source-test',3,'ACTIVE','99113000-0000-4000-8000-000000000001');
 foreach f in array array[
  'public.read_onflows_training_structure(text,date,date,integer[])'::regprocedure,
  'public.save_onflows_training_proposal(text,uuid,uuid,text,text,text,jsonb,date,integer,integer,text)'::regprocedure
 ] loop
  assert not (select prosecdef from pg_proc where oid=f),'Public RPC bypasses invoker grants';
  assert (select proconfig @> array['search_path=""'] from pg_proc where oid=f),'Unpinned search path';
  assert has_function_privilege('service_role',f,'EXECUTE');
  foreach r in array array['anon','authenticated'] loop
   assert not has_function_privilege(r,f,'EXECUTE'),'Browser can use service-only source or write RPC';
  end loop;
 end loop;
 assert (select provolatile='s' from pg_proc where oid='public.read_onflows_training_structure(text,date,date,integer[])'::regprocedure);
end $$;

set local role service_role;
do $$
declare result jsonb; before_count integer; rejected boolean; input integer[];
begin
 select count(*) into before_count from public.onflows_management_plan_revisions where athlete_alias='structure-source-test';
 result:=public.read_onflows_training_structure('structure-source-test','2026-11-02','2026-11-15');
 assert jsonb_array_length(result->'days')=14 and result->'missing_dates'='[]'::jsonb;
 assert result->'plan_revisions'='[1,2,3]'::jsonb,'Source history was truncated or crossed athletes';
 assert result#>>'{days,0,sessions,0,method_id}'='LATEST','Did not choose the latest saved prescription';
 assert result#>'{days,2,sessions}'='[]'::jsonb,'Explicit rest lost priority over legacy alias';
 assert result#>>'{days,1,revision}'='1' and result#>>'{days,1,sessions,0,method_id}'='METHOD-1','Empty activity marker hid an earlier prescription';
 assert result#>>'{days,3,revision}'='1' and result#>>'{days,3,sessions,0,method_id}'='METHOD-3','Legacy activity marker hid an earlier prescription';
 assert result#>>'{days,4,sessions,0,method_library,base_method_id}'='SOURCE-ONE','Personal canonical lineage was lost';
 assert result#>'{days,5,sessions,0,method_library,components}'='[{"id":"METHOD-1","base_method_id":null},{"id":"METHOD-2","base_method_id":null}]'::jsonb;
 assert result#>'{days,5,sessions,0,execution_descriptor,parts}'='[{"id":"METHOD-1","structure":"CONTINUOUS"},{"id":"METHOD-2","structure":"CONTINUOUS"}]'::jsonb;
 assert result::text not like '%PRIVATE%' and result::text not like '%total_minutes%' and result::text not like '%UNAPPLIED%';
 assert octet_length(result::text)<10000,'Structural projection leaked old dose/evidence';
 result:=public.read_onflows_training_structure('structure-source-test','2026-11-02','2026-11-08',array[1,2]);
 assert result#>>'{days,0,sessions,0,method_id}'='LEGACY' and result->'plan_revisions'='[1]'::jsonb;
 result:=public.read_onflows_training_structure('structure-source-test','2026-11-16','2026-11-16');
 assert result->'days'='[]'::jsonb and result->'missing_dates'='["2026-11-16"]'::jsonb,'Lookahead became a saved source';
 result:=public.read_onflows_training_structure('structure-source-test','2026-11-17','2026-11-17');
 assert result->'days'='[]'::jsonb and result->'missing_dates'='["2026-11-17"]'::jsonb,'Recorded-only day without a prescription was invented';
 result:=public.read_onflows_training_structure('structure-source-test','2026-11-05','2026-11-05',array[3]);
 assert result->'days'='[]'::jsonb,'Historical fallback escaped the explicit revision allowlist';
 result:=public.read_onflows_training_structure('structure-source-test','2026-11-02','2026-11-02',array[99]);
 assert result->'days'='[]'::jsonb,'Revision allowlist crossed athlete scope';
 result:=public.read_onflows_training_structure('missing-structure-athlete','2026-11-02','2026-11-02');
 assert result->'missing_dates'='["2026-11-02"]'::jsonb;
 rejected:=false;
 begin perform public.read_onflows_training_structure('structure-source-test','2026-11-02','2026-11-03');
 exception when invalid_parameter_value then rejected:=true; end;
 assert rejected,'Noncanonical source range accepted';
 rejected:=false;
 begin perform public.read_onflows_training_structure('structure-source-test','-infinity','infinity');
 exception when invalid_parameter_value then rejected:=true; end;
 assert rejected,'Unbounded date range accepted';
 foreach input slice 1 in array array[[0,1],[1,1],[1,null]] loop
  rejected:=false;
  begin perform public.read_onflows_training_structure('structure-source-test','2026-11-02','2026-11-02',input);
  exception when invalid_parameter_value then rejected:=true; end;
  assert rejected,'Invalid revision identity accepted';
 end loop;
 assert (select count(*)=before_count from public.onflows_management_plan_revisions where athlete_alias='structure-source-test'),'Source read mutated immutable history';
end $$;

do $$
declare result jsonb; owner_id uuid:='99113000-0000-4000-8000-000000000001';
 coach_id uuid:='99113000-0000-4000-8000-000000000002';
 op uuid:='99113000-0000-4000-8000-000000000010';
begin
 result:=public.save_onflows_training_proposal('structure-source-test',coach_id,op,repeat('a',64),
  'MANAGEMENT_PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',null,0,2);
 assert result->>'reason'='PLAN_REVISION_CHANGED','Stale active plan was accepted';
 assert not exists(select 1 from public.onflows_management_entries where athlete_alias='structure-source-test');
 assert public.onflows_assistant_operation_status('structure-source-test',coach_id,op)->>'status'='NOT_FOUND','Rejected preview wrote a receipt';
 result:=public.save_onflows_training_proposal('structure-source-test',coach_id,op,repeat('a',64),
  'MANAGEMENT_PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',null,0,3);
 assert result->>'saved'='true' and result->>'revision'='1','Authorized coach proposal failed';
 assert result->>'kind'='MANAGEMENT_PROFILE' and not result ? 'payload','Receipt exposed profile data or new kind';
end $$;
reset role;
insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
 select athlete_alias,4,'ADAPT',actor_id,payload from public.onflows_management_plan_revisions where athlete_alias='structure-source-test' and revision=3;
update public.onflows_management_plan_state set revision=4 where athlete_alias='structure-source-test';

set local role service_role;
do $$
declare result jsonb; original jsonb; coach_id uuid:='99113000-0000-4000-8000-000000000002';
 op uuid:='99113000-0000-4000-8000-000000000010';
begin
 original:=public.onflows_assistant_operation_status('structure-source-test',coach_id,op);
 result:=public.save_onflows_training_proposal('structure-source-test',coach_id,op,repeat('a',64),
  'MANAGEMENT_PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',null,0,3);
 assert result=original,'Later current plan broke lost-response receipt replay';
 result:=public.save_onflows_training_proposal('structure-source-test',coach_id,op,repeat('b',64),
  'MANAGEMENT_PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',null,0,3);
 assert result->>'reason'='OPERATION_REUSED','New hash bypassed receipt replay before active CAS';
 result:=public.save_onflows_training_proposal('structure-source-test',coach_id,'99113000-0000-4000-8000-000000000011',repeat('c',64),
  'MANAGEMENT_PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',null,0,4);
 assert result->>'reason'='REVISION_CHANGED','Profile CAS was not enforced';
 result:=public.save_onflows_training_proposal('structure-source-test',coach_id,'99113000-0000-4000-8000-000000000012',repeat('d',64),
  'MANAGEMENT_PROFILE','management-profile-v1','{"adaptation_mode":"AUTO"}',null,1,null);
 assert result->>'saved'='true' and result->>'revision'='2','Method-only proposal unexpectedly requires active CAS';
 assert (select count(*)=2 from public.onflows_management_entries where athlete_alias='structure-source-test' and kind='PROFILE');
end $$;
reset role;
update public.onflows_sharing_grants set edit_plan=false where owner_user_id='99113000-0000-4000-8000-000000000001';
set local role service_role;
do $$ declare result jsonb; begin
 result:=public.save_onflows_training_proposal('structure-source-test','99113000-0000-4000-8000-000000000002',
  '99113000-0000-4000-8000-000000000010',repeat('a',64),'MANAGEMENT_PROFILE','management-profile-v1','{}',null,0,3);
 assert result->>'forbidden'='true','Revoked editor replayed an apply operation';
end $$;
reset role;
-- Malformed latest prescriptions stay visible rather than selecting older
-- valid data. Partial days retain their original session identity.
insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload) values
 ('structure-source-test',5,'ADAPT','99113000-0000-4000-8000-000000000001',
  '{"plan":{"days":[{"date":"2026-12-01","status":"TRAINING","sessions":[{"method_id":"EARLIER","sport":"RUN"}]},{"date":"2026-12-02","status":"TRAINING","sessions":[{"method_id":"EARLIER","sport":"RUN"}]}]}}'),
 ('structure-source-test',6,'ADAPT','99113000-0000-4000-8000-000000000001',
  '{"plan":{"days":[{"date":"2026-12-01","status":"TRAINING","sessions":{}},{"date":"2026-12-02","status":"REST","sessions":[]},{"date":"2026-12-02","status":"TRAINING","sessions":[]},{"date":"2026-12-03","status":"TRAINING","actual_sessions":1,"sessions":[{"method_id":"AFTERNOON","sport":"RUN","slot":2}]}]}}');
set local role service_role;
do $$ declare result jsonb; begin
 result:=public.read_onflows_training_structure('structure-source-test','2026-12-01','2026-12-01');
 assert result#>>'{days,0,revision}'='6' and result#>'{days,0,sessions}'='null'::jsonb,'Malformed latest prescription silently fell back';
 result:=public.read_onflows_training_structure('structure-source-test','2026-12-02','2026-12-02');
 assert result#>>'{days,0,revision}'='6' and result#>'{days,0,status}'='null'::jsonb,'Duplicate source date was accepted as unambiguous';
 result:=public.read_onflows_training_structure('structure-source-test','2026-12-03','2026-12-03');
 assert result#>>'{days,0,sessions,0,slot}'='2' and result#>>'{days,0,actual_sessions}'='1','Partial source day was renumbered';
end $$;
reset role;
set local role authenticated;
do $$ declare denied boolean; begin
 denied:=false;
 begin perform public.read_onflows_training_structure('structure-source-test','2026-11-02','2026-11-02');
 exception when insufficient_privilege then denied:=true; end;
 assert denied,'Browser directly read private source projection';
 denied:=false;
 begin perform public.save_onflows_training_proposal('structure-source-test','99113000-0000-4000-8000-000000000001',
  '99113000-0000-4000-8000-000000000010',repeat('a',64),'MANAGEMENT_PROFILE','management-profile-v1','{}',null,0,3);
 exception when insufficient_privilege then denied:=true; end;
 assert denied,'Browser directly invoked canonical profile save';
end $$;
reset role;
rollback;
