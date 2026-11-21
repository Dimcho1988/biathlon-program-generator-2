-- Preserve manual sync, existing retry deadlines and all authorization gates.
-- This index supports the actor-scoped cooldown without reading job payloads.
create index if not exists onflows_sync_jobs_full_cooldown
 on public.onflows_sync_jobs(athlete_alias,requested_at desc) where job_kind='FULL_SYNC';

create or replace function onflows_private.queue_management_import(p_alias text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare state public.onflows_management_plan_state%rowtype; profile jsonb; local_day text; slot text; result jsonb;
begin
 select * into state from public.onflows_management_plan_state where athlete_alias=p_alias for share;
 if not found or state.status not in ('ACTIVE','REVIEW_REQUIRED')
   or not onflows_private.can_edit_management(p_alias,state.approved_by) then return jsonb_build_object('queued',false); end if;
 select payload into profile from public.onflows_management_entries where athlete_alias=p_alias and kind='PROFILE' order by revision desc limit 1;
 if coalesce((profile->>'auto_import_enabled')::boolean,true)=false then return jsonb_build_object('queued',false); end if;
 -- Every FULL sync refetches the entire 90-day provider history. Reserve
 -- provider quota for manual updates; automatic imports run at most 4/day.
 if exists(select 1 from public.onflows_sync_jobs j
   where j.athlete_alias=p_alias and j.job_kind='FULL_SYNC'
     and j.requested_at > clock_timestamp()-interval '6 hours') then
   return jsonb_build_object('queued',false,'reason','AUTOMATIC_IMPORT_COOLDOWN');
 end if;
 if not exists(select 1 from public.onflows_intervals_connections where athlete_alias=p_alias and status='CONNECTED') then
   return jsonb_build_object('queued',false,'reason','CONNECTION_REQUIRED'); end if;
 select (clock_timestamp() at time zone timezone)::date::text into local_day from public.onflows_athlete_settings where athlete_alias=p_alias;
 if local_day > profile->>'program_end' then return jsonb_build_object('queued',false); end if;
 slot := p_alias||':'||to_char(clock_timestamp() at time zone 'UTC','YYYY-MM-DD-HH24');
 select to_jsonb(j) into result from public.enqueue_onflows_sync_job(p_alias,'FULL_SYNC',
   md5('management-import:'||slot)||md5('v2:'||slot),
   jsonb_build_object('schema_version','sync-request-v1','scope','FULL','as_of',local_day)) j;
 return jsonb_build_object('queued',true,'deduplicated',result->'deduplicated');
end $$;
