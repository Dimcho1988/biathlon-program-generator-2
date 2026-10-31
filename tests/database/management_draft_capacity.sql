-- Exercise the real save and publication RPCs. All synthetic data rolls back.
begin;
insert into auth.users(id) values
 ('99000000-0000-0000-0000-000000000071'),
 ('99000000-0000-0000-0000-000000000072');
insert into public.onflows_profiles(user_id,display_name) values
 ('99000000-0000-0000-0000-000000000071','Draft capacity owner'),
 ('99000000-0000-0000-0000-000000000072','Draft capacity outsider');
insert into public.onflows_intervals_connections
 (athlete_alias,provider_athlete_id,encrypted_access_token,status) values
 ('draft-capacity-test-athlete','draft-capacity-test-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone) values
 ('draft-capacity-test-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner) values
 ('99000000-0000-0000-0000-000000000071','draft-capacity-test-athlete',true);

set local role service_role;
do $$
declare
 athlete constant text := 'draft-capacity-test-athlete';
 actor constant uuid := '99000000-0000-0000-0000-000000000071';
 outsider constant uuid := '99000000-0000-0000-0000-000000000072';
 day text := (clock_timestamp() at time zone 'UTC')::date::text;
 draft jsonb; saved jsonb; result jsonb; checkpoint jsonb; active_payload jsonb;
 rejected boolean; rejected_constraint text;
begin
 perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1','{}',0,actor);
 checkpoint := public.onflows_management_checkpoint(athlete);
 draft := jsonb_build_object('start_date',day,'activation_eligible',true,
   'source',jsonb_build_object('generation_id',null,'readiness_known',true),
   'days',jsonb_build_array(jsonb_build_object('prescription','Preserve the complete training prescription',
     'evidence',repeat('z',750000))),
   'lookahead_days',jsonb_build_array(jsonb_build_object('evidence',repeat('z',750000))));
 assert octet_length(draft::text)>1048576 and octet_length(draft::text)<2097152;
 result := public.save_onflows_management_entry(athlete,'DRAFT',day,draft,0,actor,1,null,true,checkpoint->'responses');
 assert result->>'saved'='true' and result->>'revision'='1', 'Complete draft was rejected';
 saved := result->'payload';
 assert saved-'persistence'=draft, 'Draft prescription or evidence changed';
 assert (select payload=saved from public.onflows_management_entries
   where athlete_alias=athlete and kind='DRAFT' and revision=1), 'Draft readback differs';
 result := public.save_onflows_management_entry(athlete,'DRAFT',day,draft,0,actor,1,null,true,checkpoint->'responses');
 assert result->>'unchanged'='true' and result->>'revision'='1', 'Retry appended another draft';

 active_payload := jsonb_build_object('status','ACTIVE','mode','AUTO','evaluated_on',day,
   'approved_settings_fingerprint','same-rules','approved_by',actor,'approved_profile_revision',1,'plan',saved);
 result := public.save_onflows_management_plan(athlete,active_payload,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->>'saved'='true' and result->'payload'->'plan'=saved, 'Large draft could not be activated losslessly';
 active_payload := active_payload || jsonb_build_object('status','REVIEW_REQUIRED','proposal',saved);
 assert octet_length(active_payload::text)>2097152 and octet_length(active_payload::text)<4194304;
 result := public.save_onflows_management_plan(athlete,active_payload,1,actor,'REVIEW',1,checkpoint,false);
 assert result->>'saved'='true' and result->'payload'=active_payload, 'Approved plan and proposal could not coexist';

 result := public.save_onflows_management_entry(athlete,'DRAFT',day,draft || '{"changed":true}',0,actor,1);
 assert result->>'reason'='REVISION_CHANGED', 'Stale draft revision was accepted';
 result := public.save_onflows_management_entry(athlete,'DRAFT',day,draft,1,actor,2);
 assert result->>'reason'='PROFILE_CHANGED', 'Changed profile was accepted';
 result := public.save_onflows_management_entry(athlete,'DRAFT',day,draft,1,actor,1,
   '99000000-0000-0000-0000-000000000079',true);
 assert result->>'reason'='ANALYSIS_CHANGED', 'Changed analysis was accepted';

 rejected := false;
 begin
   perform public.save_onflows_management_entry(athlete,'DRAFT',day,
     draft || jsonb_build_object('excess',repeat('z',2097152)),1,actor,1);
 exception when check_violation then
   get stacked diagnostics rejected_constraint=constraint_name;
   rejected := rejected_constraint='onflows_management_entries_check';
 end;
 assert rejected, 'Draft storage became unbounded';
 rejected := false;
 begin
   perform public.save_onflows_management_entry(athlete,'PROFILE','management-profile-v1',
     jsonb_build_object('excess',repeat('z',65536)),1,actor);
 exception when check_violation then rejected := true; end;
 assert rejected, 'Profile limit no longer holds';
 rejected := false;
 begin
   perform public.save_onflows_management_entry(athlete,'DRAFT',day,'[]',1,actor,1);
 exception when invalid_parameter_value then rejected := true; end;
 assert rejected, 'Non-object draft accepted';
 rejected := false;
 begin
   perform public.save_onflows_management_entry(athlete,'DRAFT',day,draft,1,outsider,1);
 exception when insufficient_privilege then rejected := true; end;
 assert rejected, 'Outsider could save a large draft';
 assert (select count(*)=1 from public.onflows_management_entries
   where athlete_alias=athlete and kind='DRAFT'), 'Rejected writes changed draft history';
end $$;
reset role;
rollback;
