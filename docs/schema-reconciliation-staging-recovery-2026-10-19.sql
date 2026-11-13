-- REVIEW ONLY: staging format-only recovery, not an automatic down migration.
-- Restores exactly the 18 captured pre-reconciliation function definitions.
-- FK validation and sequence ACLs already had their target state in staging;
-- this recovery keeps validated integrity and never grants broader access.
begin;
set local lock_timeout='2s';
set local statement_timeout='60s';
do $onflows_reconcile_recovery$
declare
  expected record;
  function_oid oid;
  actual_definition text;
  actual_source text;
  restored_definition text;
  old_enqueue_body text := $old_enqueue_body$
declare
  state_row public.onflows_athlete_analysis_state%rowtype;
  existing_job public.onflows_sync_jobs%rowtype;
  inserted_job public.onflows_sync_jobs%rowtype;
begin
  if p_job_kind not in ('FULL_SYNC', 'WELLNESS_SYNC', 'RECOVERY_RESTORE')
     or length(p_idempotency_key) <> 64
     or jsonb_typeof(p_request_payload) <> 'object' then
    raise exception 'invalid sync job request';
  end if;

  if not exists (
    select 1
    from public.onflows_intervals_connections c
    where c.athlete_alias = p_athlete_alias
      and c.status = 'CONNECTED'
  ) then
    raise exception 'athlete connection is unavailable';
  end if;

  insert into public.onflows_athlete_analysis_state (athlete_alias)
  values (p_athlete_alias)
  on conflict on constraint onflows_athlete_analysis_state_pkey do nothing;

  select *
  into state_row
  from public.onflows_athlete_analysis_state s
  where s.athlete_alias = p_athlete_alias
  for update;

  select *
  into existing_job
  from public.onflows_sync_jobs j
  where j.athlete_alias = p_athlete_alias
    and j.job_kind = p_job_kind
    and (
      j.idempotency_key = p_idempotency_key
      or j.status in ('QUEUED', 'RETRY_WAIT')
    )
  order by
    case when j.idempotency_key = p_idempotency_key then 0 else 1 end,
    j.requested_at desc
  limit 1;

  if found then
    return query
    select
      existing_job.job_id,
      existing_job.athlete_alias,
      existing_job.job_kind,
      existing_job.status,
      existing_job.request_sequence,
      true;
    return;
  end if;

  update public.onflows_athlete_analysis_state s
  set request_sequence = s.request_sequence + 1,
      updated_at = now()
  where s.athlete_alias = p_athlete_alias
  returning * into state_row;

  insert into public.onflows_sync_jobs (
    athlete_alias,
    job_kind,
    idempotency_key,
    request_sequence,
    request_payload
  )
  values (
    p_athlete_alias,
    p_job_kind,
    p_idempotency_key,
    state_row.request_sequence,
    p_request_payload
  )
  returning * into inserted_job;

  return query
  select
    inserted_job.job_id,
    inserted_job.athlete_alias,
    inserted_job.job_kind,
    inserted_job.status,
    inserted_job.request_sequence,
    false;
end;
$old_enqueue_body$;
begin
  for expected in select * from (values
    ('public.activate_onflows_analysis_generation(uuid,uuid,uuid)','b4c30df9930d0407253cf9e1e831ea79eaa570937695e43504cc3ef6c9b69d5c','670c9f879e0a55e20ce8b7c01f435300f728737457d3dacf2daf6417df02d655'),
    ('public.active_onflows_activity(text,text)','26a1bcf3a96399c333fb0c72270a2337e3d6c84f989181846914a0f6fabcfd66','65ff8f6b304c523a4f531c92536168cfe5801ef0e57294bf54aa35a6435b225d'),
    ('public.active_onflows_activity_shadow_index(text)','3e579c085af406510842265c4fd416e9fe6d737c8a46d1cca5ecb3fc39bdbb5b','63b7857a66d386e2bb89f238d128faff417d8d905381ec087f902e55c9deef04'),
    ('public.active_onflows_activity_view(text,text)','ff2f50dc3d565c335af625d55176976417f865a046bc5ea5955823b11cb1d263','d9a6a49097e5e369975618a8462284c065d4154ee6b19ead2f2722530fea39ad'),
    ('public.active_onflows_analysis(text)','3703e56bd41eead8b7055790cd567580d8be1add4231a0a515dc09a52b8954a0','aef211323b5ebeca83bcd4f6276718579c26e32b831c1d4ddea40f9b6a6fc1df'),
    ('public.claim_onflows_sync_job(text,integer)','906cc54258eede2bd595526b38db9d408d9686afe7ebcf2d28ac773d8ecc9268','e07748a4f1ec23f88b2f4654a6cb4e8190465218809ea7bf149764e5586c450c'),
    ('public.consume_onflows_login_ticket(text)','96ade1c849052f71d242227e1ebb0d5a5359eda500be7532e662475309438f5e','1963244cbf1ea2897ed1e88ef41fe2f179add1f7f3390082b80188df6f5ebdd1'),
    ('public.consume_onflows_oauth_state(text)','ea34b33d9854071f60618d4756bc9bf24cd20630b9775dc8d4e7687b4f0627d3','d2c8188756cd75da1b97bb42d6ba8c874b47088612d43c44cc78ab0a5ac3d9aa'),
    ('public.enqueue_onflows_sync_job(text,text,text,jsonb)','fd01ad22f02fdc8ea3ba609b26bc8091efdd25acd51ba8f0005559cd1bd3bbd3','2c4d20688d4df371af8438afa4cd86260160e7c710afe86c50489211af6ec8dc'),
    ('public.fail_onflows_sync_job(uuid,uuid,uuid,text,boolean,integer)','75c52f5cdf290fe18807e2c69b9675a4661b24ec40e8c6dc087bab654011db6f','20ce95e969c695084cd2f5565ccb2d2c88b22f48457f3aba759c1aa7e59ca9b8'),
    ('public.onflows_sync_state(text)','772e1b58862056e588c691418a0a0f962f2b98bc2086ae48533568029d335dba','340329a9fbac7a17c31f24c0489e70b2aaaef98829810db962caab70a4415889'),
    ('public.publish_onflows_activity_shadow(text,text,text,text,text,jsonb,text,text,text,text,text,text,text,text,jsonb)','f2e74169be296e01ea159194cb0fd4966c4c12c6c48d996c16b287bbf8d26484','7a504d250af2dad608ed70c6a4f8f070fdcc2d5ddf365903cef694fd87a257a1'),
    ('public.publish_onflows_canonical_activity_result(text,text,text,text,text,text,text,jsonb)','8392d87683df679b81e05bcf5cd0781514c15e3d7813b8e76357a9952cbb9758','81b166ea3e2f7cb1d312254f59712d7ad9f8c54f0d1d6ba76058db4921522abe'),
    ('public.renew_onflows_sync_lease(uuid,uuid,uuid,integer)','9336dcb576efe24d9b8c1d2b83e2a01d3b4164526c35359431a045a0496fa534','a947d910eb3de4e6e52d2144d30f40928595623a3a387d6a1c89bf9d6c94690a'),
    ('public.resolve_onflows_activity_ref(text,text)','fa75c6e9b90fa291d686fdd21319cbb5412a8937261ec920e73e25cda78b0751','5a4bc72775c6bd1a80bfd953efd610b1dd39b802d0c30fe8d3ece6f16f8b12a7'),
    ('public.rollback_onflows_analysis_generation(text,uuid)','e32c06d4a4e29de62a41ee2830461d88ca7b453db591f05d22e335f5bbdbdb84','1b3653dfe8003e6ad0dd3f6831d764e15e373e0ec89a8f68081c65528070ec2f'),
    ('public.stage_onflows_analysis_generation(uuid,uuid,uuid,jsonb,text,date,date,date,jsonb,jsonb,boolean)','3b214bcbdf48a90b455b9f3db6f64e534682cea3e80e236aee59ce8d0c3c03da','4c9adce24905373b0a2465b0342f811bc332b20a1da53a78dfb862cd50e3f669'),
    ('public.store_onflows_canonical_activity_result(text,text,text,text,text,text,text,jsonb)','7ab63f11a2843ca7ced8a4c451f4b87df1820084f5927fdf57d28e5adcab4886','92b0641eb5c6f5a3bc91874b85d9eedc4632ec71a4166b74b99bb75e06ba9748')
  ) as definitions(signature,before_sha256,target_sha256)
  loop
    function_oid := pg_catalog.to_regprocedure(expected.signature);
    if function_oid is null then raise exception 'Recovery function is missing'; end if;
    if not exists(select 1 from pg_catalog.pg_proc p where p.oid=function_oid
       and pg_catalog.pg_get_userbyid(p.proowner)='postgres' and p.prosecdef and not p.proleakproof
       and p.proacl='{postgres=X/postgres,service_role=X/postgres}'::pg_catalog.aclitem[]) then
      raise exception 'Recovery function ownership or permissions changed';
    end if;
    actual_definition := pg_catalog.pg_get_functiondef(function_oid);
    if pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(actual_definition,'UTF8')),'hex')
         =expected.before_sha256 then continue; end if;
    if pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(actual_definition,'UTF8')),'hex')
         <>expected.target_sha256 then raise exception 'Recovery function definition changed'; end if;
    select prosrc into actual_source from pg_catalog.pg_proc where oid=function_oid;
    if expected.signature='public.enqueue_onflows_sync_job(text,text,text,jsonb)' then
      restored_definition := pg_catalog.replace(actual_definition,actual_source,
        pg_catalog.replace(old_enqueue_body,E'\n',E'\r\n'));
    else
      restored_definition := pg_catalog.replace(actual_definition,actual_source,
        pg_catalog.replace(actual_source,E'\n',E'\r\n'));
    end if;
    if pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(restored_definition,'UTF8')),'hex')
         <>expected.before_sha256 then raise exception 'Recovery target differs from captured definition'; end if;
    execute restored_definition;
    if pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.pg_get_functiondef(function_oid),'UTF8')),'hex')
         <>expected.before_sha256 then raise exception 'Recovery verification failed'; end if;
  end loop;
end;
$onflows_reconcile_recovery$;
revoke execute on function public.prune_onflows_analysis_generations(text,integer,interval,integer) from service_role;
commit;
