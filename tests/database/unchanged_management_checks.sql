-- Synthetic fixtures only. Every mutation, including fault injection and
-- cleanup checks, is rolled back. No real athlete history is read or changed.
begin;

insert into auth.users(id) values
 ('99000000-0000-0000-0000-000000000061'),
 ('99000000-0000-0000-0000-000000000062'),
 ('99000000-0000-0000-0000-000000000063');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000061','Unchanged check owner'),
 ('99000000-0000-0000-0000-000000000062','Unchanged check coach'),
 ('99000000-0000-0000-0000-000000000063','Unchanged check outsider');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('unchanged-check-test','unchanged-check-provider','synthetic-not-a-token','REVOKED'),
 ('unchanged-check-other','unchanged-check-other-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('unchanged-check-test',array[100,120,140,155,170,190],190,'UTC'),
 ('unchanged-check-other',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000061','unchanged-check-test',true),
 ('99000000-0000-0000-0000-000000000063','unchanged-check-other',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,edit_plan,view_plan) values
 ('99000000-0000-0000-0000-000000000061','99000000-0000-0000-0000-000000000062',true,true);

do $$
declare function_id regprocedure; role_name text;
begin
 foreach function_id in array array[
   'public.record_onflows_management_check(text,integer,uuid,integer,jsonb,uuid)'::regprocedure,
   'public.read_onflows_management_checks(text,bigint,integer)'::regprocedure
 ] loop
   assert not (select prosecdef from pg_proc where oid=function_id), 'Public check RPC must be security invoker';
   assert (select proconfig @> array['search_path=""'] from pg_proc where oid=function_id), 'Check RPC search path is not fixed';
   assert has_function_privilege('service_role',function_id,'EXECUTE'), 'Service cannot use checks';
   foreach role_name in array array['anon','authenticated'] loop
     assert not has_function_privilege(role_name,function_id,'EXECUTE'), 'Public role can use checks';
   end loop;
 end loop;
 assert (select count(*) from pg_proc where pronamespace='public'::regnamespace
   and proname='record_onflows_management_check')=1, 'Ambiguous check-write overload';
 assert (select count(*) from pg_proc where pronamespace='public'::regnamespace
   and proname='read_onflows_management_checks')=1, 'Ambiguous check-read overload';
 assert (select pronargdefaults=0 from pg_proc
   where oid='onflows_private.save_management_plan_core(text,jsonb,integer,uuid,text,integer,jsonb,boolean,uuid)'::regprocedure),
   'Shared core nonce must not have a default';
 assert (select prosecdef from pg_proc
   where oid='onflows_private.save_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean)'::regprocedure),
   'Legacy private writer lost its security contract';
 assert (select relrowsecurity from pg_class where oid='onflows_private.management_plan_checks'::regclass), 'Check RLS disabled';
 assert has_table_privilege('service_role','onflows_private.management_plan_checks','SELECT'), 'Service cannot read checks';
 assert not has_table_privilege('service_role','onflows_private.management_plan_checks','INSERT')
   and not has_table_privilege('service_role','onflows_private.management_plan_checks','UPDATE')
   and not has_table_privilege('service_role','onflows_private.management_plan_checks','DELETE')
   and not has_table_privilege('service_role','onflows_private.management_plan_checks','TRUNCATE'), 'Audit writer bypass granted';
 assert not has_table_privilege('authenticated','onflows_private.management_plan_checks','SELECT'), 'Audit details exposed';
 assert not has_sequence_privilege('service_role','onflows_private.management_plan_checks_sequence_seq','USAGE'), 'Audit sequence writer bypass granted';
 assert not exists(select 1 from information_schema.columns where table_schema='onflows_private'
   and table_name='management_plan_checks' and data_type in ('json','jsonb')), 'Check table duplicates plan/checkpoint JSON';
 assert not has_table_privilege('service_role','public.onflows_management_plan_revisions','INSERT'), 'Plan writer bypass granted';
end $$;

set local role service_role;
do $$
declare
 athlete constant text := 'unchanged-check-test';
 actor constant uuid := '99000000-0000-0000-0000-000000000061';
 coach constant uuid := '99000000-0000-0000-0000-000000000062';
 outsider constant uuid := '99000000-0000-0000-0000-000000000063';
 nonce1 constant uuid := '99000000-0000-0000-0000-000000000071';
 nonce2 constant uuid := '99000000-0000-0000-0000-000000000072';
 nonce3 constant uuid := '99000000-0000-0000-0000-000000000073';
 nonce4 constant uuid := '99000000-0000-0000-0000-000000000074';
 checkpoint jsonb; plan_payload jsonb; review_payload jsonb; result jsonb; original_ack jsonb;
 page jsonb; next_page jsonb; denied boolean; deferred_at timestamptz; bound bigint;
begin
 perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1','{}',0,actor);
 checkpoint := public.onflows_management_checkpoint(athlete);
 plan_payload := jsonb_build_object('status','ACTIVE','mode','AUTO','approved_settings_fingerprint','same-rules',
   'evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,'approved_by',actor,'approved_profile_revision',1,
   'processed_input_fingerprint',repeat('a',64),
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,'readiness_known',true),
     'audit_evidence',jsonb_build_object('prescribed_seconds',40,'padding',repeat('evidence',10000))));
 result := public.save_onflows_management_plan(athlete,plan_payload,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->'saved'='true'::jsonb and result->'payload'=plan_payload and result->>'revision'='1', 'Legacy publication changed';
 assert public.read_onflows_management_checks(athlete)='{"checks":[],"next_before":null}'::jsonb, 'Empty audit page is invalid';
 select next_check_at into deferred_at from public.onflows_management_plan_state where athlete_alias=athlete;
 original_ack := public.record_onflows_management_check(athlete,1,actor,1,checkpoint,nonce1);
 assert original_ack->'unchanged'='true'::jsonb and original_ack->>'checked_revision'='1'
   and original_ack->>'check_id'=nonce1::text and original_ack->'replayed'='false'::jsonb, 'Invalid unchanged acknowledgement';
 assert (select count(*) from jsonb_object_keys(original_ack))=5, 'Unexpected unchanged acknowledgement fields';
 assert not (original_ack ?| array['saved','revision','payload']), 'Unchanged check pretends to save a plan';
 assert (original_ack->>'recorded_at')::timestamptz=(select recorded_at from onflows_private.management_plan_checks
   where athlete_alias=athlete and check_id=nonce1), 'Acknowledgement does not identify its audit event';
 assert (select next_check_at>deferred_at and revision=1 and status='ACTIVE' and approved_by=actor
   from public.onflows_management_plan_state where athlete_alias=athlete), 'Check changed plan identity or did not defer';
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=athlete)=1, 'Check appended a full revision';
 assert (select r.payload=plan_payload from public.onflows_management_plan_revisions r
   where athlete_alias=athlete and revision=1), 'Check changed the immutable payload';
 assert (select checkpoint_sha256=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(checkpoint::text,'UTF8')),'hex')
   and payload_sha256=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(plan_payload::text,'UTF8')),'hex')
   and profile_revision=1 and actor_id=actor and evaluated_on=(clock_timestamp() at time zone 'UTC')::date
   from onflows_private.management_plan_checks where athlete_alias=athlete and check_id=nonce1), 'Audit provenance was lost';
 assert (select octet_length(to_jsonb(c)::text)<1024 from onflows_private.management_plan_checks c
   where athlete_alias=athlete and check_id=nonce1), 'Small check copied full evidence';
 select next_check_at into deferred_at from public.onflows_management_plan_state where athlete_alias=athlete;
 result := public.record_onflows_management_check(athlete,1,actor,1,checkpoint,nonce1);
 assert result-'replayed'=original_ack-'replayed' and result->'replayed'='true'::jsonb, 'Nonce replay changed its acknowledgement';
 assert (select count(*) from onflows_private.management_plan_checks where athlete_alias=athlete)=1, 'Nonce replay duplicated audit event';
 assert (select next_check_at=deferred_at from public.onflows_management_plan_state where athlete_alias=athlete), 'Replay extended the original check';
 result := public.record_onflows_management_check(athlete,1,coach,1,checkpoint,nonce1);
 assert result='{"conflict":true,"reason":"CHECK_ID_REUSED"}'::jsonb, 'Nonce was reused by a different authorized actor';
 result := public.record_onflows_management_check(athlete,0,actor,1,checkpoint,nonce1);
 assert result='{"conflict":true,"reason":"REVISION_CHANGED"}'::jsonb, 'Stale check bypassed CAS';
 result := public.record_onflows_management_check(athlete,1,actor,2,checkpoint,nonce1);
 assert result='{"conflict":true,"reason":"PROFILE_CHANGED"}'::jsonb, 'Replay bypassed profile validation';
 result := public.record_onflows_management_check(athlete,1,actor,1,checkpoint || '{"generation_id":"changed"}'::jsonb,nonce1);
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Replay bypassed checkpoint validation';
 denied := false;
 begin perform public.record_onflows_management_check(athlete,1,outsider,1,checkpoint,nonce1);
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Replay bypassed actor authorization';
 denied := false;
 begin perform public.record_onflows_management_check(athlete,1,actor,1,checkpoint,null);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Missing nonce was accepted';
 denied := false;
 begin perform public.record_onflows_management_check('unchanged-check-other',0,outsider,1,
   public.onflows_management_checkpoint('unchanged-check-other'),nonce1);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Missing current plan was treated as an unchanged check';
 perform public.record_onflows_management_check(athlete,1,actor,1,checkpoint,nonce2);

 -- Old writers keep appending, including genuinely identical payloads.
 result := public.save_onflows_management_plan(athlete,plan_payload,1,actor,'ADAPT',1,checkpoint,true);
 assert result->>'revision'='2' and result->'payload'=plan_payload, 'Old full writer no longer appends';
 result := public.record_onflows_management_check(athlete,1,actor,1,checkpoint,nonce1);
 assert result='{"conflict":true,"reason":"REVISION_CHANGED"}'::jsonb, 'Old nonce skipped CAS after publication';
 result := public.record_onflows_management_check(athlete,2,actor,1,checkpoint,nonce1);
 assert result='{"conflict":true,"reason":"CHECK_ID_REUSED"}'::jsonb, 'Nonce was reused for a different revision';
 perform public.record_onflows_management_check(athlete,2,actor,1,checkpoint,nonce3);
 page := public.read_onflows_management_checks(athlete,null,2);
 assert jsonb_array_length(page->'checks')=2 and page#>>'{checks,0,id}'=nonce3::text
   and page#>>'{checks,1,id}'=nonce2::text, 'Audit page order/limit changed';
 bound := (page->>'next_before')::bigint;
 assert bound=(page#>>'{checks,1,sequence}')::bigint and bound>0, 'Invalid audit cursor';
 next_page := public.read_onflows_management_checks(athlete,bound,2);
 assert jsonb_array_length(next_page->'checks')=1 and next_page#>>'{checks,0,id}'=nonce1::text
   and next_page->'next_before'='null'::jsonb, 'Cursor skips or duplicates checks';
 assert (select count(*) from jsonb_object_keys(page#>'{checks,0}'))=6
   and page#>>'{checks,0,reason}'='Проверката приключи. Програмата остава без промяна.'
   and page#>>'{checks,0,operation}'='CHECK_NO_CHANGE', 'Audit DTO leaked provenance or changed shape';
 assert public.read_onflows_management_checks('unchanged-check-other')='{"checks":[],"next_before":null}'::jsonb,
   'Audit page leaked another athlete';
 denied := false;
 begin perform public.read_onflows_management_checks(athlete,null,51);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Unbounded audit page accepted';
 denied := false;
 begin perform public.read_onflows_management_checks(athlete,0,10);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Invalid audit cursor accepted';

 review_payload := plan_payload || jsonb_build_object('status','REVIEW_REQUIRED','proposal',jsonb_build_object('review_required',true,'audit_evidence',40));
 result := public.save_onflows_management_plan_compact(athlete,review_payload,2,actor,'REVIEW',1,checkpoint);
 assert result->>'revision'='3' and not result ? 'payload', 'Old compact writer changed';
 perform public.record_onflows_management_check(athlete,3,actor,1,checkpoint,nonce4);
 assert (select revision=3 and status='REVIEW_REQUIRED' from public.onflows_management_plan_state where athlete_alias=athlete), 'Check approved a proposal';
 assert (select r.payload=review_payload from public.onflows_management_plan_revisions r where athlete_alias=athlete and revision=3), 'Check changed a proposal';
 denied := false;
 begin perform onflows_private.save_management_plan_core(athlete,null,3,actor,'CHECK_NO_CHANGE',1,checkpoint,true,nonce4);
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Automatic CHECK_NO_CHANGE was accepted';
 denied := false;
 begin perform onflows_private.save_management_plan_core(athlete,review_payload,3,actor,'REVIEW',1,checkpoint,false,nonce4);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Nonce changed an existing save operation';
 denied := false;
 begin perform public.save_onflows_management_plan(athlete,review_payload,3,actor,'CHECK_NO_CHANGE',1,checkpoint,false);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Legacy RPC can opt into unchanged checks';
 result := public.save_onflows_management_plan(athlete,review_payload || '{"status":"PAUSED"}',3,actor,'PAUSE',1,checkpoint);
 assert result->>'revision'='4', 'Old writer cannot follow a check';
 denied := false;
 begin perform public.record_onflows_management_check(athlete,4,actor,1,checkpoint,nonce4);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Paused plan accepted an unchanged check';
 result := public.save_onflows_management_plan(athlete,review_payload || '{"status":"COMPLETED"}',4,actor,'COMPLETE',1,checkpoint);
 assert result->>'revision'='5', 'Old completion changed';
 denied := false;
 begin perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce4);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Completed plan accepted an unchanged check';
 assert (select count(*) from onflows_private.management_plan_checks where athlete_alias=athlete)=4, 'Rejected check left an event';
end $$;

-- Controlled corruption of synthetic immutable rows tests defenses that normal
-- publication correctly makes impossible. No production row is touched.
reset role;
do $$
declare
 athlete constant text := 'unchanged-check-test';
 actor constant uuid := '99000000-0000-0000-0000-000000000061';
 coach constant uuid := '99000000-0000-0000-0000-000000000062';
 nonce constant uuid := '99000000-0000-0000-0000-000000000075';
 checkpoint jsonb; plan_payload jsonb; result jsonb; denied boolean;
begin
 select r.payload || '{"status":"REVIEW_REQUIRED"}'::jsonb into plan_payload
   from public.onflows_management_plan_revisions r where athlete_alias=athlete and revision=5;
 update public.onflows_management_plan_revisions r set payload=plan_payload where athlete_alias=athlete and revision=5;
 update public.onflows_management_plan_state set status='REVIEW_REQUIRED' where athlete_alias=athlete;
 checkpoint := public.onflows_management_checkpoint(athlete);
 perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 update public.onflows_management_plan_revisions r set payload=jsonb_set(plan_payload,'{evaluated_on}','"2000-01-01"')
   where athlete_alias=athlete and revision=5;
 result := public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 assert result='{"conflict":true,"reason":"LOCAL_DATE_CHANGED"}'::jsonb, 'Nonce replay bypassed local date';
 update public.onflows_management_plan_revisions r set payload=jsonb_set(plan_payload,'{approved_by}',to_jsonb(coach::text))
   where athlete_alias=athlete and revision=5;
 denied := false;
 begin perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Nonce replay bypassed approval identity';
 update public.onflows_management_plan_revisions r set payload=plan_payload || '{"status":"ACTIVE"}'::jsonb
   where athlete_alias=athlete and revision=5;
 update public.onflows_management_plan_state set status='ACTIVE' where athlete_alias=athlete;
 update public.onflows_management_plan_revisions r set payload=jsonb_set(plan_payload || '{"status":"ACTIVE"}',
   '{plan,activation_eligible}','false') where athlete_alias=athlete and revision=5;
 denied := false;
 begin perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Check bypassed current ACTIVE eligibility';
 update public.onflows_management_plan_revisions r set payload=jsonb_set(plan_payload || '{"status":"ACTIVE"}',
   '{plan,source,generation_id}','"wrong-generation"') where athlete_alias=athlete and revision=5;
 denied := false;
 begin perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Check bypassed current ACTIVE generation';
 update public.onflows_management_plan_revisions r set payload=jsonb_set(plan_payload || '{"status":"ACTIVE"}',
   '{approved_profile_revision}','2') where athlete_alias=athlete and revision=5;
 denied := false;
 begin perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Check bypassed current ACTIVE approved profile';
 update public.onflows_management_plan_revisions r set payload=plan_payload where athlete_alias=athlete and revision=5;
 denied := false;
 begin perform public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 exception when invalid_parameter_value then denied := true; end;
 assert denied, 'Inconsistent current pointer was treated as unchanged';
 update public.onflows_management_plan_state set status='REVIEW_REQUIRED' where athlete_alias=athlete;
 update public.onflows_athlete_settings set hrmax_bpm=191 where athlete_alias=athlete;
 result := public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Replay skipped a real input change';
 result := public.record_onflows_management_check(athlete,5,actor,1,public.onflows_management_checkpoint(athlete),nonce);
 assert result='{"conflict":true,"reason":"CHECK_ID_REUSED"}'::jsonb, 'Nonce was reused with another valid checkpoint';
 update public.onflows_athlete_settings set hrmax_bpm=190 where athlete_alias=athlete;
 update public.onflows_management_plan_revisions r set payload=plan_payload || '{"different_evidence":true}'::jsonb
   where athlete_alias=athlete and revision=5;
 result := public.record_onflows_management_check(athlete,5,actor,1,checkpoint,nonce);
 assert result='{"conflict":true,"reason":"CHECK_ID_REUSED"}'::jsonb, 'Replay lost its whole-payload provenance';
 update public.onflows_management_plan_revisions r set payload=plan_payload where athlete_alias=athlete and revision=5;
 perform public.record_onflows_management_check(athlete,5,coach,1,checkpoint,'99000000-0000-0000-0000-000000000076');
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=athlete)=5, 'Audit fault tests appended full evidence';
end $$;

set local role authenticated;
do $$
declare denied boolean;
begin
 denied := false;
 begin perform public.record_onflows_management_check('unchanged-check-test',5,
   '99000000-0000-0000-0000-000000000061',1,'{}','99000000-0000-0000-0000-000000000077');
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Authenticated role can record checks directly';
 denied := false;
 begin perform public.read_onflows_management_checks('unchanged-check-test');
 exception when insufficient_privilege then denied := true; end;
 assert denied, 'Authenticated role can read check history directly';
end $$;
reset role;

-- New audit metadata must not add a restriction to existing account/athlete
-- cleanup. A deleted coach's UUID remains historical evidence until the parent
-- athlete history itself is removed through its existing cascade.
delete from auth.users where id='99000000-0000-0000-0000-000000000062';
do $$ begin
 assert exists(select 1 from onflows_private.management_plan_checks
   where athlete_alias='unchanged-check-test' and actor_id='99000000-0000-0000-0000-000000000062'),
   'Coach deletion unexpectedly removed athlete decision trace';
end $$;
delete from public.onflows_athlete_settings where athlete_alias='unchanged-check-test';
do $$ begin
 assert not exists(select 1 from onflows_private.management_plan_checks where athlete_alias='unchanged-check-test'),
   'Existing athlete cleanup leaves orphaned check evidence';
 assert exists(select 1 from public.onflows_athlete_settings where athlete_alias='unchanged-check-other'),
   'Athlete cleanup crossed athlete scope';
end $$;
rollback;
