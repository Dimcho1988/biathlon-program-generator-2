-- Disposable database only: all fixtures and changes are rolled back.
begin;
insert into auth.users(id) values
 ('99120000-0000-4000-8000-000000000001'),('99120000-0000-4000-8000-000000000002'),('99120000-0000-4000-8000-000000000003');
insert into public.onflows_profiles(user_id,display_name) values
 ('99120000-0000-4000-8000-000000000001','Assistant app athlete'),
 ('99120000-0000-4000-8000-000000000002','Assistant app editor'),
 ('99120000-0000-4000-8000-000000000003','Assistant app viewer');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
values('assistant-app-test','assistant-app-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
values('assistant-app-test',array[90,120,140,160,180,200],200,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
values('99120000-0000-4000-8000-000000000001','assistant-app-test',true);
insert into public.onflows_sharing_grants(owner_user_id,viewer_user_id,view_plan,edit_plan) values
 ('99120000-0000-4000-8000-000000000001','99120000-0000-4000-8000-000000000002',true,true),
 ('99120000-0000-4000-8000-000000000001','99120000-0000-4000-8000-000000000003',true,false);
do $$
declare rpc regprocedure := 'public.save_onflows_assistant_app_operation(text,uuid,uuid,text,text,text,jsonb,integer)'::regprocedure;
begin
 assert not (select prosecdef from pg_proc where oid=rpc), 'App RPC must be invoker';
 assert has_function_privilege('service_role',rpc,'EXECUTE'), 'Service execution missing';
 assert not has_function_privilege('anon',rpc,'EXECUTE'), 'Anonymous execution exposed';
 assert not has_function_privilege('authenticated',rpc,'EXECUTE'), 'Client execution exposed';
end $$;
set local role service_role;
do $$
declare
 a constant text := 'assistant-app-test';
 owner_id constant uuid := '99120000-0000-4000-8000-000000000001';
 editor_id constant uuid := '99120000-0000-4000-8000-000000000002';
 viewer_id constant uuid := '99120000-0000-4000-8000-000000000003';
 op constant uuid := '99120000-0000-4000-8000-000000000010';
 settings jsonb := '{"hr_zone_bounds":[88,120,140,160,180,195],"timezone":"UTC","hrmax_bpm":195,"hr_zone_source":"MANUAL","hr_zone_percentages":null,"model_preferences":{"leading_indicator":"HEART_RATE"}}';
 result jsonb; first_result jsonb; checkpoint jsonb; plan jsonb; args jsonb; denied boolean;
begin
 result := public.save_onflows_assistant_app_operation(a,viewer_id,op,repeat('a',64),'ATHLETE_SETTINGS','athlete-settings',settings,0);
 assert result->>'forbidden'='true', 'Read-only viewer changed settings';
 first_result := public.save_onflows_assistant_app_operation(a,editor_id,op,repeat('a',64),'ATHLETE_SETTINGS','athlete-settings',settings,0);
 assert first_result->>'saved'='true' and first_result->>'revision'='1', 'Plan editor could not save form-equivalent settings';
 result := public.save_onflows_assistant_app_operation(a,editor_id,op,repeat('a',64),'ATHLETE_SETTINGS','athlete-settings',settings,0);
 assert result=first_result, 'Settings replay changed its receipt';
 assert (select settings_revision from public.onflows_athlete_settings where athlete_alias=a)=1, 'Replay changed settings';
 update public.onflows_athlete_settings set timezone='Europe/Sofia' where athlete_alias=a;
 assert (select settings_revision from public.onflows_athlete_settings where athlete_alias=a)=2, 'Form update did not change revision';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000011',repeat('b',64),'ATHLETE_SETTINGS','athlete-settings',settings,1);
 assert result->>'status'='CONFLICT', 'Stale chat settings overwrote a form edit';
 assert (select timezone from public.onflows_athlete_settings where athlete_alias=a)='Europe/Sofia', 'Timezone overwritten';
 result := public.save_onflows_assistant_app_operation(a,editor_id,op,repeat('c',64),'ATHLETE_SETTINGS','athlete-settings',settings,2);
 assert result->>'reason'='OPERATION_REUSED', 'Operation ID reused for a different change';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000012',repeat('d',64),'RECOVERY_CONFIG','recovery-v2','{"schema_version":"recovery-config-v2","zones":{}}',0);
 assert result->>'forbidden'='true', 'Plan-only editor wrote recovery settings';
 result := public.save_onflows_assistant_app_operation(a,owner_id,'99120000-0000-4000-8000-000000000012',repeat('d',64),'RECOVERY_CONFIG','recovery-v2','{"schema_version":"recovery-config-v2","zones":{}}',0);
 assert result->>'saved'='true' and result->>'revision'='1', 'Recovery canonical save failed';
 result := public.save_onflows_assistant_app_operation(a,owner_id,'99120000-0000-4000-8000-000000000013',repeat('e',64),'RECOVERY_CONFIG','recovery-v2','{"schema_version":"recovery-config-v2","zones":{}}',0);
 assert result->>'status'='CONFLICT', 'Model CAS bypassed';
 result := public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{}',0,owner_id);
 assert result->>'saved'='true', 'Profile fixture failed';
 -- A new draft uses the existing durable job and never activates a plan.
 args := jsonb_build_object('start_date',(clock_timestamp() at time zone 'Europe/Sofia')::date::text,
   'expected_profile_revision',1,'expected_draft_revision',0);
 first_result := public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000017',repeat('3',64),
   'PLAN_GENERATE',args->>'start_date',jsonb_build_object('request',args),1);
 assert first_result->>'saved'='true' and first_result->>'queued'='true', 'Draft enqueue failed';
 assert first_result#>>'{job,status}'='QUEUED', 'Existing draft job not returned';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000017',repeat('3',64),
   'PLAN_GENERATE',args->>'start_date',jsonb_build_object('request',args),1);
 assert result=first_result, 'Draft queue replay changed job';
 assert not exists(select 1 from public.onflows_management_plan_state where athlete_alias=a), 'Generation activated a plan';
 checkpoint := public.onflows_management_checkpoint(a);
 plan := jsonb_build_object('status','ACTIVE','evaluated_on',(clock_timestamp() at time zone 'Europe/Sofia')::date::text,
   'approved_by',editor_id,'approved_profile_revision',1,
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('readiness_known',true,'generation_id',null)));
 args := jsonb_build_object('p_alias',a,'p_actor',editor_id,'p_expected_revision',0,'p_payload',plan,
   'p_operation','ACTIVATE','p_expected_profile_revision',1,'p_checkpoint',checkpoint,'p_automatic',false);
 first_result := public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000014',repeat('f',64),'PLAN_ACTIVATE','active-plan',
   jsonb_build_object('rpc','save_onflows_management_plan_compact','arguments',args),0);
 assert first_result->>'saved'='true' and first_result->>'revision'='1', 'Canonical plan activation failed';
 assert first_result->>'plan_status'='ACTIVE', 'Receipt lost plan result';
 result := public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000014',repeat('f',64),'PLAN_ACTIVATE','active-plan',
   jsonb_build_object('rpc','save_onflows_management_plan_compact','arguments',args),0);
 assert result=first_result, 'Plan replay changed result';
 assert (select count(*) from public.onflows_management_plan_revisions where athlete_alias=a)=1, 'Plan replay appended a revision';
 result := public.onflows_assistant_operation_status(a,editor_id,'99120000-0000-4000-8000-000000000014');
 assert result=first_result, 'Plan-only editor cannot recover their receipt';
 denied := false;
 begin
  perform public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000015',repeat('1',64),'PLAN_ACTION','active-plan',
   jsonb_build_object('rpc','delete_anything','arguments',args||'{"p_expected_revision":1}'),1);
 exception when invalid_parameter_value then denied:=true; end;
 assert denied, 'Arbitrary RPC dispatch accepted';
 denied := false;
 begin
  perform public.save_onflows_assistant_app_operation(a,editor_id,'99120000-0000-4000-8000-000000000016',repeat('2',64),'PLAN_ACTION','active-plan',
   jsonb_build_object('rpc','save_onflows_management_plan_compact','arguments',args||'{"p_expected_revision":1,"p_alias":"other-athlete"}'),1);
 exception when invalid_parameter_value then denied:=true; end;
 assert denied, 'Nested athlete identity substitution accepted';
 assert not exists(select 1 from onflows_private.assistant_operations where athlete_alias=a and operation_id in
   ('99120000-0000-4000-8000-000000000015','99120000-0000-4000-8000-000000000016')), 'Rejected request wrote a receipt';
end $$;
do $$
declare
 a constant text := 'assistant-app-test';
 actor constant uuid := '99120000-0000-4000-8000-000000000001';
 operation constant uuid := '99120000-0000-4000-8000-000000000020';
 chat uuid; result jsonb; denied boolean := false;
begin
 result := public.onflows_assistant_conversation('BEGIN',a,actor,null,operation,'{"message":"Настрой профила"}');
 chat := (result->>'conversation_id')::uuid;
 result := public.onflows_assistant_conversation('FINISH',a,actor,chat,operation,
   jsonb_build_object('response',jsonb_build_object('message','Преглед'), 'state',jsonb_build_object('pending_preview',repeat('я',10000))));
 assert result->>'message'='Преглед', 'Reviewed state above old16KiB limit was not saved';
 assert (select octet_length(state::text)>16000 from public.onflows_assistant_conversations where id=chat), 'Larger context was truncated';
 result := public.onflows_assistant_conversation('BEGIN',a,actor,chat,'99120000-0000-4000-8000-000000000021','{"message":"Още настройки"}');
 begin
  perform public.onflows_assistant_conversation('FINISH',a,actor,chat,'99120000-0000-4000-8000-000000000021',
   jsonb_build_object('response',jsonb_build_object('message','Не трябва да се запише'),'state',jsonb_build_object('data',repeat('я',35000))));
 exception when check_violation then denied:=true; end;
 assert denied, 'Unbounded conversation state accepted';
 assert (select status='PENDING' from public.onflows_assistant_turns where operation_id='99120000-0000-4000-8000-000000000021'), 'Oversized state partially completed turn';
end $$;
rollback;
