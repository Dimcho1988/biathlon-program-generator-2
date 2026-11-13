-- Real migrated PostgreSQL contract. Synthetic fixtures and all writes roll back.
begin;
\ir history_generation_fixture.sql

do $$
declare
 f jsonb := pg_temp.history_generation_fixture('checkpoint-analysis-revision');
 a text := f->>'alias';
 actor uuid := (f->>'actor')::uuid;
 checkpoint jsonb;
 fresh jsonb;
 payload jsonb;
 result jsonb;
 original_rows jsonb;
 original_state jsonb;
 role_name text;
begin
 assert not (select prosecdef from pg_proc where oid='public.onflows_management_checkpoint(text)'::regprocedure),
   'Checkpoint must remain security invoker';
 assert (select provolatile='s' and proconfig=array['search_path=""'] from pg_proc
   where oid='public.onflows_management_checkpoint(text)'::regprocedure), 'Checkpoint volatility/search_path changed';
 assert has_function_privilege('service_role','public.onflows_management_checkpoint(text)','EXECUTE'),
   'API role lost the checkpoint';
 foreach role_name in array array['anon','authenticated'] loop
   assert not has_function_privilege(role_name,'public.onflows_management_checkpoint(text)','EXECUTE'),
     'Checkpoint exposed to a public role';
 end loop;

 execute 'set local role service_role';
 checkpoint := public.onflows_management_checkpoint(a);
 assert checkpoint->'analysis_revision'='100'::jsonb, 'Checkpoint omitted the pointer revision';
 assert checkpoint->>'generation_id'=f->>'active', 'Checkpoint used another generation';
 assert public.onflows_management_checkpoint(f->>'other_alias')->'analysis_revision'='null'::jsonb,
   'Absent state must remain explicit null, without leaking another athlete revision';
 execute 'reset role';
 insert into public.onflows_athlete_analysis_state(athlete_alias) values(f->>'other_alias');
 execute 'set local role service_role';
 assert public.onflows_management_checkpoint(f->>'other_alias')->'analysis_revision'='0'::jsonb,
   'An existing pre-analysis state must retain its real zero revision';

 payload := jsonb_build_object('status','ACTIVE','mode','AUTO','approved_by',actor,
   'approved_profile_revision',1,'evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text,
   'plan',jsonb_build_object('activation_eligible',true,'source',jsonb_build_object(
     'readiness_known',true,'generation_id',f->>'active'),
     'input_snapshot',jsonb_build_object('generation_id',f->>'active','analysis_revision',100)));
 result := public.save_onflows_management_plan(a,payload,0,actor,'ACTIVATE',1,checkpoint,false);
 assert result->>'saved'='true' and result->>'revision'='1', 'Unchanged checkpoint publication failed';
 original_rows := pg_temp.history_generation_rows(a);
 select to_jsonb(s) into original_state from public.onflows_management_plan_state s where athlete_alias=a;

 -- Both transitions use the supported rollback RPC and increase the revision;
 -- no fixture resets a revision or changes an immutable generation payload.
 perform public.rollback_onflows_analysis_generation(a,(f->>'rollback')::uuid);
 perform public.rollback_onflows_analysis_generation(a,(f->>'active')::uuid);
 fresh := public.onflows_management_checkpoint(a);
 assert fresh->'analysis_revision'='102'::jsonb, 'Supported rollback was not monotonic';
 assert fresh-'analysis_revision'=checkpoint-'analysis_revision',
   'Reproduction changed more than the pointer revision';

 result := public.save_onflows_management_plan(a,payload,1,actor,'ADAPT',1,checkpoint,false);
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Full writer accepted a late rollback';
 result := public.save_onflows_management_plan_compact(a,payload,1,actor,'ADAPT',1,checkpoint,false);
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Compact writer accepted a late rollback';
 result := public.record_onflows_management_check(a,1,actor,1,checkpoint,gen_random_uuid());
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Unchanged check accepted a late rollback';
 assert pg_temp.history_generation_rows(a)=original_rows, 'Rejected publication rewrote historical rows';
 assert (select to_jsonb(s) from public.onflows_management_plan_state s where athlete_alias=a)=original_state,
   'Rejected publication changed the pointer or scheduling state';
 assert not exists(select 1 from onflows_private.management_plan_checks where athlete_alias=a),
   'Rejected unchanged check inserted an audit event';

 -- An in-flight old projection fails closed instead of silently omitting the
 -- new comparison. Normal callers acquire the new complete checkpoint first.
 result := public.save_onflows_management_plan(a,payload,1,actor,'ADAPT',1,fresh-'analysis_revision',false);
 assert result='{"conflict":true,"reason":"INPUTS_CHANGED"}'::jsonb, 'Incomplete old checkpoint accepted';
 payload := jsonb_set(payload,'{plan,input_snapshot,analysis_revision}','102');
 result := public.save_onflows_management_plan_compact(a,payload,1,actor,'ADAPT',1,fresh,false);
 assert result->>'saved'='true' and result->>'revision'='2', 'Fresh revision could not be published';
 result := public.record_onflows_management_check(a,2,actor,1,fresh,gen_random_uuid());
 assert result->>'unchanged'='true' and result->>'checked_revision'='2', 'Fresh unchanged check failed';
 assert (select count(*)=1 from onflows_private.management_plan_checks where athlete_alias=a),
   'Fresh unchanged check did not create exactly one event';
 execute 'reset role';
end $$;

rollback;
