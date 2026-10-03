-- Run against a migrated local database: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f this-file.
-- Fixtures and every mutation are rolled back. No real athlete records are read.
begin;

insert into auth.users(id) values
 ('99000000-0000-0000-0000-000000000001'),
 ('99000000-0000-0000-0000-000000000002'),
 ('99000000-0000-0000-0000-000000000003');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000001','Checkpoint test athlete'),
 ('99000000-0000-0000-0000-000000000002','Checkpoint test coach'),
 ('99000000-0000-0000-0000-000000000003','Checkpoint test outsider');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('checkpoint-test-athlete','checkpoint-test-provider','synthetic-not-a-token','REVOKED'),
 ('checkpoint-test-other','checkpoint-test-other-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('checkpoint-test-athlete',array[100,120,140,155,170,190],190,'UTC'),
 ('checkpoint-test-other',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000001','checkpoint-test-athlete',true),
 ('99000000-0000-0000-0000-000000000003','checkpoint-test-other',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,edit_plan,view_plan) values
 ('99000000-0000-0000-0000-000000000001','99000000-0000-0000-0000-000000000002',true,true);

do $$
declare function_id regprocedure; role_name text;
begin
  foreach function_id in array array[
    'public.onflows_management_checkpoint(text)'::regprocedure,
    'public.save_onflows_response_entry(text,text,text,date,jsonb,integer,uuid)'::regprocedure,
    'public.save_onflows_management_entry(text,text,text,jsonb,integer,uuid,integer,uuid,boolean,jsonb)'::regprocedure,
    'public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean)'::regprocedure
  ] loop
    assert not (select prosecdef from pg_proc where oid=function_id), 'Public RPC must remain security invoker';
    assert has_function_privilege('service_role', function_id, 'EXECUTE'), 'Service role lost RPC permission';
    foreach role_name in array array['anon','authenticated'] loop
      assert not has_function_privilege(role_name, function_id, 'EXECUTE'), 'Public role can execute protected RPC';
    end loop;
  end loop;
  assert (select count(*) from pg_proc where pronamespace='public'::regnamespace
    and proname='save_onflows_management_entry')=1, 'Ambiguous PostgREST overload';
  assert (select relrowsecurity from pg_class where oid='public.onflows_response_entries'::regclass), 'Response RLS disabled';
  assert not has_table_privilege('authenticated','public.onflows_response_entries','SELECT'), 'Observation data exposed';
  assert not has_table_privilege('service_role','public.onflows_management_plan_revisions','INSERT'), 'Append-only publication bypass';
end $$;

set local role service_role;
do $$
declare
  athlete constant text := 'checkpoint-test-athlete';
  owner_id constant uuid := '99000000-0000-0000-0000-000000000001';
  coach_id constant uuid := '99000000-0000-0000-0000-000000000002';
  outsider_id constant uuid := '99000000-0000-0000-0000-000000000003';
  snapshot jsonb;
  current_snapshot jsonb;
  result jsonb;
  draft jsonb := '{"start_date":"2026-09-27"}';
  active_plan jsonb;
  denied boolean;
  observation_kind text;
begin
  snapshot := public.onflows_management_checkpoint(athlete);
  assert snapshot->'responses'='[]'::jsonb, 'Empty response set must be an array';
  assert snapshot ?& array['settings','generation_id','models'], 'Existing checkpoint fields changed';

  result := public.save_onflows_response_entry(athlete,'WEIGHT','2026-09-27','2026-09-27','{"morning_kg":70}',0,owner_id);
  assert result='{"saved":true,"revision":1}'::jsonb, 'Weight save failed';
  -- This is the first write RPC in this transaction: the response writer itself
  -- must hold the exact per-athlete publication lock, before its entry lock.
  assert exists(select 1 from pg_locks
    where locktype='advisory' and coalesce(pid,0)=pg_backend_pid() and granted and objsubid=1
      and classid=((hashtextextended('management:' || athlete,0) >> 32) & 4294967295)::oid
      and objid=(hashtextextended('management:' || athlete,0) & 4294967295)::oid),
    'Response writer did not take the publication lock';
  -- A legacy six-argument call still saves a profile after the RPC replacement.
  result := public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1','{}',0,owner_id);
  assert result->>'saved'='true' and result->>'revision'='1', 'Legacy profile call failed';
  result := public.save_onflows_response_entry(athlete,'WEIGHT','2026-09-27','2026-09-27','{"morning_kg":70}',0,owner_id);
  assert result='{"saved":true,"revision":1}'::jsonb, 'Lost-response retry created a revision';
  result := public.save_onflows_response_entry(athlete,'WEIGHT','2026-09-27','2026-09-27','{"morning_kg":69}',0,owner_id);
  assert result='{"conflict":true,"revision":1}'::jsonb, 'Stale response edit was accepted';
  result := public.save_onflows_response_entry(athlete,'WEIGHT','2026-09-27','2026-09-27','{"morning_kg":69}',1,owner_id);
  assert result='{"saved":true,"revision":2}'::jsonb, 'Response correction failed';

  -- All observation families participate, with only the newest identity revision.
  foreach observation_kind in array array['DAILY','SESSION','BLOCK','TEST','LAB'] loop
    result := public.save_onflows_response_entry(athlete,observation_kind,'2026-09-27','2026-09-27',
      case when observation_kind='BLOCK' then '{"start":"2026-09-27","recovery_end":"2026-09-28"}'::jsonb else '{}'::jsonb end,
      0,owner_id);
    assert result->>'saved'='true', 'Observation kind was not accepted';
  end loop;
  current_snapshot := public.onflows_management_checkpoint(athlete);
  assert current_snapshot->'responses'='[
    {"kind":"BLOCK","entry_key":"2026-09-27","revision":1},
    {"kind":"DAILY","entry_key":"2026-09-27","revision":1},
    {"kind":"LAB","entry_key":"2026-09-27","revision":1},
    {"kind":"SESSION","entry_key":"2026-09-27","revision":1},
    {"kind":"TEST","entry_key":"2026-09-27","revision":1},
    {"kind":"WEIGHT","entry_key":"2026-09-27","revision":2}
  ]'::jsonb, 'Checkpoint must be compact, sorted and latest-only';
  assert (snapshot - 'responses')=(current_snapshot - 'responses'), 'Response edit changed another checkpoint field';
  assert public.onflows_management_checkpoint('checkpoint-test-other')->'responses'='[]'::jsonb,
    'Checkpoint leaked another athlete response';

  -- A response arriving after generation blocks both draft persistence and activation.
  result := public.save_onflows_management_entry(athlete,'DRAFT','2026-09-27',draft,0,owner_id,1,null,false,snapshot->'responses');
  assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Stale response draft was accepted';
  assert not exists(select 1 from public.onflows_management_entries where athlete_alias=athlete and kind='DRAFT'),
    'Rejected draft was inserted';
  active_plan := jsonb_build_object('status','ACTIVE','evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,
    'approved_by',owner_id,'approved_profile_revision',1,
    'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('readiness_known',true,'generation_id',null)));
  result := public.save_onflows_management_plan(athlete,active_plan,0,owner_id,'ACTIVATE',1,snapshot,false);
  assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Stale response plan was published';
  assert not exists(select 1 from public.onflows_management_plan_state where athlete_alias=athlete),
    'Rejected plan changed active pointer';
  result := public.save_onflows_management_plan(athlete,active_plan,0,owner_id,'ACTIVATE',1,current_snapshot,false);
  assert result->>'saved'='true' and result->>'revision'='1', 'Current checkpoint plan was rejected';

  result := public.save_onflows_management_entry(athlete,'DRAFT','2026-09-27',draft,0,owner_id,1,null,false,current_snapshot->'responses');
  assert result->>'saved'='true' and result->>'revision'='1', 'Current checkpoint draft was rejected';
  assert result#>'{payload,persistence,response_revisions}'=current_snapshot->'responses', 'Draft lost frozen response evidence';
  result := public.save_onflows_management_entry(athlete,'DRAFT','2026-09-27',draft,0,owner_id,1,null,false,current_snapshot->'responses');
  assert result->>'unchanged'='true' and result->>'revision'='1', 'Draft lost-response retry was not idempotent';
  -- Existing callers that omit the new optional comparison still resolve unambiguously.
  result := public.save_onflows_management_entry(athlete,'DRAFT','2026-09-28','{"start_date":"2026-09-28"}',0,owner_id,1,null,false);
  assert result->>'saved'='true', 'Legacy nine-argument draft call failed';

  -- Confirm unchanged authorization, including coach-only observation families.
  denied := false;
  begin
    perform public.save_onflows_response_entry(athlete,'WEIGHT','2026-09-28','2026-09-28','{}',0,coach_id);
  exception when raise_exception then denied := sqlerrm='Only the athlete can provide self reports'; end;
  assert denied, 'Coach could save an athlete self report';
  result := public.save_onflows_response_entry(athlete,'LAB','2026-09-28','2026-09-28','{}',0,coach_id);
  assert result->>'saved'='true', 'Authorized coach could not save laboratory context';
  denied := false;
  begin
    perform public.save_onflows_response_entry(athlete,'LAB','2026-09-29','2026-09-29','{}',0,outsider_id);
  exception when raise_exception then denied := sqlerrm='Observation context requires planning access'; end;
  assert denied, 'Outsider could save laboratory context';
  denied := false;
  begin
    perform public.save_onflows_management_entry(athlete,'DRAFT','2026-09-29','{"start_date":"2026-09-29"}',0,outsider_id,1);
  exception when insufficient_privilege then denied := true; end;
  assert denied, 'Outsider could save a management draft';
  result := public.save_onflows_response_entry(athlete,'BLOCK','2026-09-28','2026-09-28',
    '{"start":"2026-09-28","recovery_end":"2026-09-30"}',0,owner_id);
  assert result->>'conflict'='true', 'Block overlap protection was lost';
end $$;

rollback;
