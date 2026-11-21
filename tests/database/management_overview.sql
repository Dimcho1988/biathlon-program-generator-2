-- Service-only presentation projection; all synthetic data rolls back.
begin;
insert into auth.users(id) values ('99000000-0000-0000-0000-000000000031');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('compact-overview-test','compact-overview-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('compact-overview-test',array[100,120,140,155,170,190],190,'UTC');

do $$
declare approved jsonb; proposal jsonb; payload jsonb;
begin
 approved := '{"schema_version":"planning-draft-v1","status":"DRAFT","engine_version":"same-engine",
   "start_date":"2026-10-19","end_date":"2026-10-25",
   "days":[{"date":"2026-10-19","status":"TRAINING","session":{"total_minutes":4,
     "blocks":[{"zone":"Z1","duration_min":3},{"zone":"NMS","duration_min":1}]}}],
   "history_comparison":[{"start_date":"2026-10-12","end_date":"2026-10-18","actual_minutes":200,"covered_days":7}]}'::jsonb;
 proposal := approved || '{"days":[
   {"date":"2026-10-19","status":"TRAINING","session":{"total_minutes":999,"PRIVATE":"ignored alias"},
     "sessions":[{"total_minutes":6,"blocks":[{"zone":"Z4","duration_min":5,"PRIVATE":"block evidence"},
       {"zone":"NMS","duration_min":1}],"PRIVATE":"dose evidence"},
       {"total_minutes":7,"blocks":[{"zone":"Z2","duration_min":7}]}]},
   {"date":"2026-10-20","status":"REST","session":null},
   {"date":"2026-10-21","status":"SKIPPED","sessions":[],"session":{"PRIVATE":"ignored legacy"}}],
   "lookahead_days":[{"PRIVATE":"provisional days must not become approved volume"}],
   "history_comparison":[{"start_date":"2026-10-12","end_date":"2026-10-18","actual_minutes":null,
     "covered_days":6,"PRIVATE":"ignored history evidence"}]}'::jsonb;
 payload := jsonb_build_object('schema_version','active-plan-v2','status','ACTIVE',
   'processed_input_fingerprint','same-inputs','plan',approved,'proposal',null,
   'outcomes',jsonb_build_array(jsonb_build_object('date','2026-10-18','planned_minutes',50,
     'actual_minutes',null,'PRIVATE','outcome observations')),'PRIVATE',repeat('hidden evidence',30000));
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload) values
   ('compact-overview-test',1,'ACTIVATE','99000000-0000-0000-0000-000000000031',payload),
   ('compact-overview-test',2,'REVIEW','99000000-0000-0000-0000-000000000031',
     payload || jsonb_build_object('status','REVIEW_REQUIRED','proposal',proposal,
       'monitoring',jsonb_build_object('history_comparison',jsonb_build_array(jsonb_build_object(
         'start_date','2026-10-12','end_date','2026-10-18','actual_minutes',240,'covered_days',7)))));
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by) values
   ('compact-overview-test',1,'ACTIVE','99000000-0000-0000-0000-000000000031');
 assert not (select prosecdef from pg_proc where oid='public.read_onflows_management_overview(text)'::regprocedure);
 assert (select provolatile='s' and proconfig @> array['search_path=""'] from pg_proc
   where oid='public.read_onflows_management_overview(text)'::regprocedure), 'Read RPC must be STABLE with a fixed path';
 assert has_function_privilege('service_role','public.read_onflows_management_overview(text)','EXECUTE');
 assert not has_function_privilege('anon','public.read_onflows_management_overview(text)','EXECUTE');
 assert not has_function_privilege('authenticated','public.read_onflows_management_overview(text)','EXECUTE');
 assert not has_table_privilege('service_role','public.onflows_management_plan_revisions','UPDATE');
end $$;

set local role service_role;
do $$
declare result jsonb;
begin
 assert public.read_onflows_management_overview('missing-athlete')='null'::jsonb, 'Missing pointer is explicit JSON null';
 result := public.read_onflows_management_overview('compact-overview-test');
 assert result->'revision'='1'::jsonb and result->'record_revision'='1'::jsonb, 'Projection did not pin the current revision';
 assert result->'has_proposal'='false'::jsonb and result->>'status'='ACTIVE';
 assert result#>'{days,0,sessions,0,blocks}'='[{"zone":"Z1","duration_min":3},{"zone":"NMS","duration_min":1}]'::jsonb,
   'Legacy single session or block order changed';
 assert result#>'{outcomes,0,actual_minutes}'='null'::jsonb, 'Unknown actual time became zero';
 assert result#>>'{history_comparison,0,actual_minutes}'='200';
 assert result::text not like '%PRIVATE%', 'Projection leaked unused evidence';
 assert octet_length(result::text)<5000, 'Large immutable evidence leaked into the presentation';
end $$;
reset role;
update public.onflows_management_plan_state set revision=2,status='REVIEW_REQUIRED' where athlete_alias='compact-overview-test';

set local role service_role;
do $$
declare result jsonb; before_payload jsonb;
begin
 select payload into before_payload from public.onflows_management_plan_revisions
   where athlete_alias='compact-overview-test' and revision=2;
 result := public.read_onflows_management_overview('compact-overview-test');
 assert result->'revision'='2'::jsonb and result->'has_proposal'='true'::jsonb;
 assert result->>'status'='REVIEW_REQUIRED' and result->>'engine_version'='same-engine';
 assert jsonb_array_length(result->'days')=1, 'An unapproved proposal replaced the approved calendar';
 assert result#>>'{days,0,sessions,0,total_minutes}'='4', 'Approved dose changed on presentation';
 assert result#>>'{history_comparison,0,actual_minutes}'='240', 'Monitoring history must update independently of the frozen prescriptions';
 assert result::text not like '%PRIVATE%', 'Projection leaked evidence';
 perform public.read_onflows_management_overview('compact-overview-test');
 assert (select payload=before_payload from public.onflows_management_plan_revisions
   where athlete_alias='compact-overview-test' and revision=2), 'Reading changed immutable evidence';
 assert (select count(*)=2 from public.onflows_management_plan_revisions where athlete_alias='compact-overview-test');
end $$;
reset role;
do $$
declare original jsonb; broken jsonb; result jsonb; n integer;
begin
 select payload into original from public.onflows_management_plan_revisions
   where athlete_alias='compact-overview-test' and revision=2;
 for n in 3..6 loop
   broken := case n
     when 3 then original #- '{plan,days}'
     when 4 then original - 'outcomes'
     when 5 then original #- '{plan,days,0,session,blocks}'
     else original - 'proposal' end;
   insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
     values ('compact-overview-test',n,'REVIEW','99000000-0000-0000-0000-000000000031',broken);
   update public.onflows_management_plan_state set revision=n where athlete_alias='compact-overview-test';
   result := public.read_onflows_management_overview('compact-overview-test');
   -- Keep invalid required fields visible to the application validator;
   -- never replace them with a valid-looking empty history/session list.
   assert case n when 3 then result->'days'='null'::jsonb
     when 4 then result->'outcomes'='null'::jsonb
     when 5 then result#>'{days,0,sessions,0,blocks}'='null'::jsonb
     else result->'proposal_type'='null'::jsonb end,
     'A missing required field was masked as valid empty data';
 end loop;
 assert (select payload=original from public.onflows_management_plan_revisions
   where athlete_alias='compact-overview-test' and revision=2), 'A later projection changed earlier evidence';
end $$;
set local role authenticated;
do $$
declare denied boolean := false;
begin
 begin perform public.read_onflows_management_overview('compact-overview-test');
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Browser role directly accessed service-only plan projection';
end $$;
reset role;
rollback;
