-- Exercise the real approval-aware writer behind the training lease fence.
-- Synthetic fixtures only; no worker can see these uncommitted tasks.
begin;
set local plpgsql.check_asserts='on';
insert into auth.users(id) values('99002000-0000-0000-0000-000000000042');
insert into public.onflows_profiles(user_id,display_name)
 values('99002000-0000-0000-0000-000000000042','Prepared publication contract');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('prepared-publication-athlete','prepared-publication-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('prepared-publication-athlete',array[100,120,140,155,170,190],190,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 values('99002000-0000-0000-0000-000000000042','prepared-publication-athlete',true);

set local role service_role;
do $$ declare a constant text:='prepared-publication-athlete'; actor uuid:='99002000-0000-0000-0000-000000000042';
 checkpoint jsonb; fixed jsonb; candidate jsonb; pending jsonb; approved jsonb; result jsonb; claim jsonb;
 token uuid:=gen_random_uuid();
begin
 perform public.save_onflows_management_entry(a,'PROFILE','management-profile-v1','{"adaptation_mode":"REVIEW"}',0,actor);
 checkpoint:=public.onflows_management_checkpoint(a);
 fixed:=jsonb_build_object('status','ACTIVE','mode','REVIEW','approved_settings_fingerprint','fixed-rules',
  'evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,'approved_by',actor,'approved_profile_revision',1,
  'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object('generation_id',null,'readiness_known',true)));
 result:=public.save_onflows_management_plan_compact(a,fixed,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->>'saved'='true' and result->>'revision'='1';

 reset role;
 insert into onflows_private.training_plan_updates(athlete_alias) values(a);
 set local role service_role;
 claim:=public.onflows_training_plan_update('CLAIM',a,token);
 assert claim->>'revision'='1';
 candidate:=(fixed->'plan')||'{"contract_variant":"new-training-proposal"}'::jsonb;
 pending:=fixed||jsonb_build_object('status','REVIEW_REQUIRED','proposal',candidate,'proposal_mode','REVIEW');
 result:=public.save_onflows_management_plan_after_training(a,pending,1,actor,'REVIEW',1,checkpoint,true,token,1);
 assert result->>'saved'='true' and result->>'revision'='2','Fenced proposal publication failed';
 assert not result ? 'payload','Compact response echoed a full plan';
 assert (select payload->'plan'=fixed->'plan' and payload->'proposal'=candidate
  from public.onflows_management_plan_revisions where athlete_alias=a and revision=2),
  'Background publication replaced the fixed prescription';
 perform public.onflows_training_plan_update('COMPLETE',a,token);

 approved:=pending||jsonb_build_object('status','ACTIVE','plan',candidate,'proposal',null);
 result:=public.save_onflows_management_plan_compact(a,approved,2,actor,'APPROVE',1,checkpoint,false);
 assert result->>'saved'='true' and result->>'revision'='3';
 assert (select payload->'plan'=candidate from public.onflows_management_plan_revisions where athlete_alias=a and revision=3);
 assert (select payload=fixed from public.onflows_management_plan_revisions where athlete_alias=a and revision=1),
  'Original prescription was rewritten';
 assert public.save_onflows_management_plan_after_training(a,approved,3,actor,'REVIEW',1,checkpoint,true,token,1)->>'conflict'='true',
  'Completed lease published again';
end $$;
reset role;
set constraints all immediate;
rollback;
