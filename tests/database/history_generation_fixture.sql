-- Shared synthetic setup for the SQL and real multi-session acceptance tests.
-- Only pg_temp helpers are installed. The caller owns transaction/cleanup.
create or replace function pg_temp.history_generation_fixture(p_prefix text)
returns jsonb language plpgsql as $$
declare
  f jsonb := '{}'::jsonb;
  alias text := p_prefix || '-' || substr(replace(gen_random_uuid()::text,'-',''),1,12);
  other_alias text;
  actor uuid := gen_random_uuid();
  name text;
  generation uuid;
  job uuid;
  n integer := 0;
  generation_status text;
  job_status text;
  lease uuid := gen_random_uuid();
begin
  other_alias := alias || '-other';
  f := jsonb_build_object('alias',alias,'other_alias',other_alias,'actor',actor,'lease',lease);
  foreach name in array array['active','parent','source','root','extra','control','rollback','ready','foreign'] loop
    f := f || jsonb_build_object(name,gen_random_uuid(),name || '_job',gen_random_uuid());
  end loop;
  insert into auth.users(id) values(actor);
  insert into public.onflows_profiles(user_id,display_name) values(actor,'Synthetic history preservation test');
  insert into public.onflows_intervals_connections
    (athlete_alias,provider_athlete_id,encrypted_access_token,status)
    values(alias,alias,'synthetic-not-a-token','REVOKED'),
          (other_alias,other_alias,'synthetic-not-a-token','REVOKED');
  insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
    values(alias,array[100,120,140,155,170,190],190,'UTC'),
          (other_alias,array[100,120,140,155,170,190],190,'UTC');
  insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
    values(actor,alias,true),(actor,other_alias,true);

  foreach name in array array['active','parent','source','root','extra','control','rollback','ready','foreign'] loop
    n := n + 1;
    generation := (f->>name)::uuid;
    job := (f->>(name || '_job'))::uuid;
    generation_status := case name when 'active' then 'ACTIVE' when 'ready' then 'READY'
      when 'control' then 'FAILED' else 'SUPERSEDED' end;
    job_status := case name when 'ready' then 'RUNNING' when 'control' then 'FAILED' else 'SUCCEEDED' end;
    insert into public.onflows_sync_jobs
      (job_id,athlete_alias,job_kind,idempotency_key,request_sequence,status,
       lease_token,lease_owner,lease_expires_at,started_at,completed_at,requested_at)
      values(job,case when name='foreign' then other_alias else alias end,'FULL_SYNC',
        md5(alias || name)||md5(name || alias),n,job_status,
        case when name='ready' then lease end,case when name='ready' then 'synthetic-test-worker' end,
        case when name='ready' then clock_timestamp()+interval '1 hour' end,
        clock_timestamp()-interval '90 days',
        case when name<>'ready' then clock_timestamp()-interval '89 days' end,
        clock_timestamp()-interval '90 days');
    insert into public.onflows_analysis_generations
      (generation_id,athlete_alias,job_id,attempt_no,job_kind,request_sequence,
       base_generation_id,base_revision,activity_set_generation_id,activated_revision,status,
       snapshot_schema_version,snapshot_hash,snapshot_payload,period_start,period_end,as_of,
       provenance,activity_count,created_at,ready_at,activated_at,superseded_at)
      values(generation,case when name='foreign' then other_alias else alias end,job,1,'FULL_SYNC',n,
        case name when 'root' then (f->>'parent')::uuid when 'ready' then (f->>'active')::uuid end,
        case when name='ready' then 100 else 0 end,
        case when name='root' then (f->>'source')::uuid else generation end,
        case when name='active' then 100 when name not in ('ready','control') then n end,generation_status,
        'synthetic-retention-v1',repeat('a',64),jsonb_build_object('synthetic_fixture',name),
        current_date-90,current_date,current_date,'{}'::jsonb,0,
        clock_timestamp()-interval '90 days',clock_timestamp()-interval '90 days',
        case when name not in ('ready','control') then clock_timestamp()-interval '89 days'+n*interval '1 second' end,
        case when generation_status='SUPERSEDED' then clock_timestamp()-interval '89 days' end);
    if name='ready' then
      update public.onflows_sync_jobs set current_generation_id=generation where job_id=job;
    end if;
  end loop;
  insert into public.onflows_athlete_analysis_state(athlete_alias,active_generation_id,active_revision,request_sequence)
    values(alias,(f->>'active')::uuid,100,8);
  insert into public.onflows_training_snapshots(athlete_alias,payload,generation_id,revision,activated_at)
    select alias,snapshot_payload,generation_id,100,clock_timestamp()
    from public.onflows_analysis_generations where generation_id=(f->>'active')::uuid;
  insert into public.onflows_management_entries(athlete_alias,kind,entry_key,revision,payload,actor_id)
    values(alias,'PROFILE','management-profile-v1',1,'{}'::jsonb,actor);
  return f;
end $$;

create or replace function pg_temp.history_generation_rows(p_alias text)
returns jsonb language sql as $$
  select coalesce(jsonb_agg(row_evidence order by table_name,row_key),'[]'::jsonb)
  from (
    select 'management_entries' as table_name,kind || ':' || entry_key || ':' || revision as row_key,
      jsonb_build_object('table','management_entries','row',to_jsonb(e),'xmin',e.xmin::text,'ctid',e.ctid::text,
        'payload_sha256',encode(sha256(convert_to(payload::text,'UTF8')),'hex')) as row_evidence
      from public.onflows_management_entries e where athlete_alias=p_alias
    union all
    select 'management_plan_revisions',revision::text,
      jsonb_build_object('table','management_plan_revisions','row',to_jsonb(r),'xmin',r.xmin::text,'ctid',r.ctid::text,
        'payload_sha256',encode(sha256(convert_to(payload::text,'UTF8')),'hex'))
      from public.onflows_management_plan_revisions r where athlete_alias=p_alias
    union all
    select 'response_entries',kind || ':' || entry_key || ':' || revision,
      jsonb_build_object('table','response_entries','row',to_jsonb(o),'xmin',o.xmin::text,'ctid',o.ctid::text,
        'payload_sha256',encode(sha256(convert_to(payload::text,'UTF8')),'hex'))
      from public.onflows_response_entries o where athlete_alias=p_alias
  ) rows;
$$;
