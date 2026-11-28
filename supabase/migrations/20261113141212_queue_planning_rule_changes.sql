-- Schedule one canonical check when calculation rules change within the same day.
-- Input/event/day scheduling, actor grants, publication fences and retry limits stay authoritative.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';

alter table onflows_private.training_plan_updates
 add column last_checked_rule_versions jsonb,
 add column claimed_rule_versions jsonb;

create or replace function onflows_private.training_plan_update(p_operation text,p_alias text default null,p_token uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare a text; d date; result jsonb;
begin
 if p_operation='CLAIM' then
  -- Both supported modes prepare against the new local day. The canonical
  -- planner retains a fixed horizon when appropriate; pauses/completion stay put.
  for a,d in select s.athlete_alias,(clock_timestamp() at time zone z.timezone)::date
   from public.onflows_management_plan_state s
   join public.onflows_management_plan_revisions r using(athlete_alias,revision)
   join public.onflows_athlete_settings z using(athlete_alias)
   join lateral (select payload from public.onflows_management_entries e where e.athlete_alias=s.athlete_alias
    and e.kind='PROFILE' and e.entry_key='management-profile-v1' order by revision desc limit 1) p on true
   left join onflows_private.training_plan_updates t using(athlete_alias)
   where s.status in ('ACTIVE','REVIEW_REQUIRED')
    and (p_alias is null or s.athlete_alias=p_alias)
    and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
    and coalesce(t.last_evaluated_on,(r.payload->>'evaluated_on')::date,'-infinity'::date)<(clock_timestamp() at time zone z.timezone)::date
    and (t.athlete_alias is null or t.requested_revision=t.completed_revision)
   limit 10 loop
   insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
    values(a,(select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=a))
   on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
    requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null
    where onflows_private.training_plan_updates.requested_revision=onflows_private.training_plan_updates.completed_revision;
  end loop;
 end if;
 result:=onflows_private.training_plan_update_core_v1(p_operation,p_alias,p_token);
 if p_operation='CLAIM' and result is not null then
  select (clock_timestamp() at time zone timezone)::date into d from public.onflows_athlete_settings where athlete_alias=result->>'athlete_alias';
  update onflows_private.training_plan_updates set claimed_day=d,claimed_actor=(result->>'actor_id')::uuid,
   claimed_plan_revision=requested_plan_revision,claimed_manual=requested_manual,claimed_record_check=requested_record_check,
   claimed_rule_versions=null
   where athlete_alias=result->>'athlete_alias' and lease_token=p_token;
  select result||jsonb_build_object('manual',claimed_manual,'expected_plan_revision',claimed_plan_revision,
   'record_unchanged_check',claimed_record_check) into result from onflows_private.training_plan_updates
   where athlete_alias=result->>'athlete_alias';
 elsif p_operation in ('COMPLETE','REJECT') and result->>'state' in ('READY','REJECTED') then
  update onflows_private.training_plan_updates set last_evaluated_on=claimed_day,
   last_checked_rule_versions=case when p_operation='COMPLETE'
    then claimed_rule_versions else last_checked_rule_versions end,
   requested_manual=case when requested_revision=completed_revision then false else requested_manual end,
   requested_actor=case when requested_revision=completed_revision then null else requested_actor end,
   requested_plan_revision=case when requested_revision=completed_revision then null else requested_plan_revision end,
   requested_record_check=case when requested_revision=completed_revision then false else requested_record_check end
   where athlete_alias=p_alias;
 end if;
 if p_operation in ('COMPLETE','REJECT','FAIL') and p_alias is not null then
  perform onflows_private.request_prepared_views(p_alias,false);
 end if;
 return result;
end $$;
revoke all on function onflows_private.training_plan_update(text,text,uuid) from public,anon,authenticated;
grant execute on function onflows_private.training_plan_update(text,text,uuid) to service_role;

-- A new name keeps the existing three-argument RPC unambiguous for old workers
-- during rolling deployment. Only CLAIM supplies a calculation identity.
create function onflows_private.claim_training_plan_update(
 p_token uuid,p_rule_versions jsonb,p_alias text default null
) returns jsonb language plpgsql security definer set search_path='' as $$
declare a text; result jsonb;
begin
 if p_token is null or jsonb_typeof(p_rule_versions) is distinct from 'object'
  or jsonb_typeof(p_rule_versions->'engine') is distinct from 'string'
  or length(p_rule_versions->>'engine')=0 or octet_length(p_rule_versions::text)>16384 then
  raise exception 'Invalid planning rule identity' using errcode='22023'; end if;
 for a in select s.athlete_alias
  from public.onflows_management_plan_state s
  join public.onflows_management_plan_revisions r using(athlete_alias,revision)
  left join onflows_private.training_plan_updates t using(athlete_alias)
  where s.status in ('ACTIVE','REVIEW_REQUIRED')
   and (p_alias is null or s.athlete_alias=p_alias)
   and onflows_private.can_prepare_plan(s.athlete_alias,s.approved_by)
   and (t.athlete_alias is null or t.requested_revision=t.completed_revision)
   and coalesce(t.last_checked_rule_versions,r.payload#>'{proposal,input_snapshot,rule_versions}',
    r.payload#>'{plan,input_snapshot,rule_versions}') is distinct from p_rule_versions
  order by s.athlete_alias limit 10 loop
  insert into onflows_private.training_plan_updates(athlete_alias,generation_id)
   values(a,(select active_generation_id from public.onflows_athlete_analysis_state where athlete_alias=a))
  on conflict(athlete_alias) do update set requested_revision=onflows_private.training_plan_updates.requested_revision+1,
   requested_at=clock_timestamp(),available_at=clock_timestamp(),attempts=0,error_code=null
   -- A competing worker may already have queued/claimed after the SELECT.
   where onflows_private.training_plan_updates.requested_revision=onflows_private.training_plan_updates.completed_revision;
 end loop;
 result:=onflows_private.training_plan_update('CLAIM',p_alias,p_token);
 if result is not null then
  update onflows_private.training_plan_updates set claimed_rule_versions=p_rule_versions
   where athlete_alias=result->>'athlete_alias' and lease_token=p_token
    and claimed_revision=(result->>'revision')::bigint;
 end if;
 return result;
end $$;
revoke all on function onflows_private.claim_training_plan_update(uuid,jsonb,text) from public,anon,authenticated;
grant execute on function onflows_private.claim_training_plan_update(uuid,jsonb,text) to service_role;
create function public.claim_onflows_training_plan_update(p_token uuid,p_rule_versions jsonb,p_alias text default null)
returns jsonb language sql security invoker set search_path='' as $$
 select onflows_private.claim_training_plan_update(p_token,p_rule_versions,p_alias)
$$;
revoke all on function public.claim_onflows_training_plan_update(uuid,jsonb,text) from public,anon,authenticated;
grant execute on function public.claim_onflows_training_plan_update(uuid,jsonb,text) to service_role;
notify pgrst,'reload schema';
commit;
