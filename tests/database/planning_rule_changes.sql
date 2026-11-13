-- Reproducible local/staging contracts. All synthetic state rolls back.
begin;
set local statement_timeout='30s';
insert into auth.users(id) values('99006000-0000-0000-0000-000000000001');
insert into public.onflows_profiles(user_id,display_name) values('99006000-0000-0000-0000-000000000001','Prepared views contract');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('prepared-views-contract','prepared-views-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('prepared-views-contract',array[100,120,140,155,170,190],190,'Europe/Sofia');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 values('99006000-0000-0000-0000-000000000001','prepared-views-contract',true);
insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
 values('prepared-views-contract','PROFILE','management-profile-v1',1,'{"adaptation_mode":"AUTO"}','99006000-0000-0000-0000-000000000001');
insert into public.onflows_sync_jobs(job_id,athlete_alias,job_kind,idempotency_key,request_sequence)
 values('99006000-0000-0000-0000-000000000002','prepared-views-contract','FULL_SYNC',repeat('a',64),1);
insert into public.onflows_analysis_generations(generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
 base_revision,activity_set_generation_id,activated_revision,status,snapshot_schema_version,snapshot_hash,snapshot_payload,
 period_start,period_end,as_of,provenance,activity_count,ready_at,activated_at)
 values('99006000-0000-0000-0000-000000000003','prepared-views-contract','99006000-0000-0000-0000-000000000002',1,'FULL_SYNC',1,
 0,'99006000-0000-0000-0000-000000000003',1,'ACTIVE','athlete-snapshot-v1',repeat('b',64),
 '{"schema_version":"athlete-snapshot-v1","training_status":{"athlete_id":"prepared-views-contract"},"load_history":{"athlete_id":"prepared-views-contract"}}',
 current_date,current_date,current_date,'{}',0,clock_timestamp(),clock_timestamp());
insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision)
 values('prepared-views-contract','99006000-0000-0000-0000-000000000003',1);

-- Model-rule changes are real inputs, separate from unrelated deployment commits.
do $$ declare a constant text:='prepared-views-contract'; actor uuid:='99006000-0000-0000-0000-000000000001';
 token uuid:=gen_random_uuid(); wrong_token uuid:=gen_random_uuid(); claim jsonb; n bigint; role_name text; attempt integer;
 d date:=(clock_timestamp() at time zone 'Europe/Sofia')::date;
 v74 jsonb:='{"engine":"v74"}'; v75 jsonb:='{"engine":"v75"}'; v76 jsonb:='{"engine":"v76"}'; v77 jsonb:='{"engine":"v77"}';
begin
 insert into auth.users(id) values(wrong_token);
 delete from onflows_private.training_plan_updates where athlete_alias=a;
 insert into public.onflows_management_plan_revisions(athlete_alias,revision,operation,actor_id,payload)
  values(a,1,'ACTIVATE',actor,jsonb_build_object('mode','AUTO','status','ACTIVE','evaluated_on',d,
   'plan',jsonb_build_object('input_snapshot',jsonb_build_object('rule_versions',v74))));
 insert into public.onflows_management_plan_state(athlete_alias,revision,status,approved_by) values(a,1,'ACTIVE',actor);
 foreach role_name in array array['anon','authenticated'] loop
  assert not has_function_privilege(role_name,'public.claim_onflows_training_plan_update(uuid,jsonb,text)','EXECUTE');
  assert not has_function_privilege(role_name,'onflows_private.claim_training_plan_update(uuid,jsonb,text)','EXECUTE');
 end loop;
 assert not (select prosecdef from pg_proc where oid='public.claim_onflows_training_plan_update(uuid,jsonb,text)'::regprocedure);
 assert public.claim_onflows_training_plan_update(token,v74,a) is null,'Unchanged rules queued a same-day plan';
 claim:=public.claim_onflows_training_plan_update(token,v75,a);
 assert claim is not null and claim->>'manual'='false','Changed rules did not queue a same-day canonical check';
 n:=(claim->>'revision')::bigint;
 assert (select claimed_rule_versions=v75 and last_checked_rule_versions is null from onflows_private.training_plan_updates where athlete_alias=a);
 assert public.claim_onflows_training_plan_update(wrong_token,v76,a) is null,'A second worker acquired a running check';
 assert (select requested_revision=n from onflows_private.training_plan_updates where athlete_alias=a),'A pending check was duplicated';
 assert public.onflows_training_plan_update('COMPLETE',a,wrong_token)->>'state'='LOST';
 assert (select last_checked_rule_versions is null from onflows_private.training_plan_updates where athlete_alias=a),'A lost worker certified rules';
 perform public.onflows_training_plan_update('FAIL',a,token);
 assert public.claim_onflows_training_plan_update(token,v75,a) is null,'Rule checks bypassed retry cooldown';
 assert (select requested_revision=n and last_checked_rule_versions is null from onflows_private.training_plan_updates where athlete_alias=a);
 update onflows_private.training_plan_updates set available_at=clock_timestamp()-interval '1 second' where athlete_alias=a;
 claim:=public.claim_onflows_training_plan_update(token,v75,a);
 assert (claim->>'revision')::bigint=n,'A retry created another request';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert (select last_checked_rule_versions=v75 from onflows_private.training_plan_updates where athlete_alias=a);
 assert public.claim_onflows_training_plan_update(token,v75,a) is null,'A completed same-rule check repeated';

 -- A rejected obsolete manual request has not evaluated new rules. Its one
 -- automatic successor uses the canonical calculation and existing FAIL retries.
 perform public.queue_onflows_plan_refresh(a,actor,1,false);
 claim:=public.claim_onflows_training_plan_update(token,v76,a);
 assert claim->>'manual'='true';
 perform public.onflows_training_plan_update('REJECT',a,token);
 assert (select last_checked_rule_versions=v75 from onflows_private.training_plan_updates where athlete_alias=a),'Rejected manual input certified uncomputed rules';
 claim:=public.claim_onflows_training_plan_update(token,v76,a);
 assert claim is not null and claim->>'manual'='false','Rejected manual request stranded rule refresh';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.claim_onflows_training_plan_update(token,v76,a) is null,'Rejected manual request produced a repeat loop';

 update public.onflows_management_plan_state set status='PAUSED' where athlete_alias=a;
 assert public.claim_onflows_training_plan_update(token,v77,a) is null,'A rule change resumed a paused program';
 update public.onflows_management_plan_state set status='COMPLETED' where athlete_alias=a;
 assert public.claim_onflows_training_plan_update(token,v77,a) is null,'A rule change reopened a completed program';
 update public.onflows_management_plan_state set status='ACTIVE',approved_by=wrong_token where athlete_alias=a;
 assert public.claim_onflows_training_plan_update(token,v77,a) is null,'A rule change bypassed editor authorization';
 update public.onflows_management_plan_state set approved_by=actor where athlete_alias=a;

 -- An old worker can finish an existing request, but cannot certify new rules
 -- accidentally using a claimed identity left behind by another worker.
 claim:=public.claim_onflows_training_plan_update(token,v77,a);
 perform public.onflows_training_plan_update('FAIL',a,token);
 update onflows_private.training_plan_updates set available_at=clock_timestamp()-interval '1 second' where athlete_alias=a;
 claim:=public.onflows_training_plan_update('CLAIM',a,token);
 assert (select claimed_rule_versions is null from onflows_private.training_plan_updates where athlete_alias=a);
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert (select last_checked_rule_versions is null from onflows_private.training_plan_updates where athlete_alias=a),
  'A legacy completion retained a certificate for unknown calculations';
 claim:=public.claim_onflows_training_plan_update(token,v77,a);
 assert claim is not null,'An old worker falsely certified a new calculation';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.claim_onflows_training_plan_update(token,v77,a) is null;

 -- A legacy completion can still publish current rules. Clearing its unknown
 -- certificate falls back to the actual saved rules without forcing a rerun.
 perform public.queue_onflows_plan_refresh(a,actor,1,false);
 claim:=public.onflows_training_plan_update('CLAIM',a,token);
 update public.onflows_management_plan_revisions set payload=jsonb_set(payload,'{plan,input_snapshot,rule_versions}',v77)
  where athlete_alias=a and revision=1;
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert (select last_checked_rule_versions is null from onflows_private.training_plan_updates where athlete_alias=a);
 assert public.claim_onflows_training_plan_update(token,v77,a) is null,'Current legacy publication caused an unnecessary rule check';
 -- Publishing older rules must not inherit a previous successful certificate.
 perform public.queue_onflows_plan_refresh(a,actor,1,false);
 claim:=public.onflows_training_plan_update('CLAIM',a,token);
 update public.onflows_management_plan_revisions set payload=jsonb_set(payload,'{plan,input_snapshot,rule_versions}',v76)
  where athlete_alias=a and revision=1;
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 claim:=public.claim_onflows_training_plan_update(token,v77,a);
 assert claim is not null,'Older legacy publication inherited a current-rule certificate';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.claim_onflows_training_plan_update(token,v77,a) is null;

 update onflows_private.training_plan_updates set last_evaluated_on=d-1 where athlete_alias=a;
 claim:=public.claim_onflows_training_plan_update(token,v77,a);
 assert claim is not null,'Rules tracking suppressed the existing daily update';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.claim_onflows_training_plan_update(token,v77,a) is null;

 update public.onflows_management_plan_revisions set payload=payload#-'{plan,input_snapshot,rule_versions}' where athlete_alias=a and revision=1;
 update onflows_private.training_plan_updates set last_checked_rule_versions=null where athlete_alias=a;
 claim:=public.claim_onflows_training_plan_update(token,v77,a);
 assert claim is not null,'Legacy rule provenance did not receive one canonical check';
 perform public.onflows_training_plan_update('COMPLETE',a,token);
 assert public.claim_onflows_training_plan_update(token,v77,a) is null,'Missing legacy provenance created a repeated check';
 for attempt in 1..3 loop
  claim:=public.claim_onflows_training_plan_update(token,'{"engine":"v78"}',a);
  assert claim is not null;
  n:=(claim->>'revision')::bigint;
  perform public.onflows_training_plan_update('FAIL',a,token);
  update onflows_private.training_plan_updates set available_at=clock_timestamp()-interval '1 second' where athlete_alias=a;
 end loop;
 assert public.claim_onflows_training_plan_update(token,'{"engine":"v99"}',a) is null,
  'A new rule identity bypassed the bounded retry limit for pending work';
 assert (select attempts=3 and requested_revision=n and last_checked_rule_versions=v77
  from onflows_private.training_plan_updates where athlete_alias=a);
end $$;
rollback;
