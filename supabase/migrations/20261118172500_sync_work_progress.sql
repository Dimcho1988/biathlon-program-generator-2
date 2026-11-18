-- Publish observed work only; an expired/superseded worker cannot update progress.
begin;
set local lock_timeout='3s';
create function public.report_onflows_sync_progress(
 p_job_id uuid, p_generation_id uuid, p_lease_token uuid,
 p_stage text, p_percent integer
) returns boolean language plpgsql security definer set search_path='' as $$
declare changed integer;
begin
 if p_stage is null or p_stage !~ '^(PROVIDER|MODELS|PUBLISHING|ACTIVITIES:[0-9]+:[0-9]+)$'
    or length(p_stage)>64 or p_percent is null or p_percent not between 0 and 90 then
  raise exception 'Invalid sync progress' using errcode='22023';
 end if;
 update public.onflows_sync_jobs j set progress_stage=p_stage,
   progress_percent=p_percent, updated_at=now()
 where j.job_id=p_job_id and j.current_generation_id=p_generation_id
   and j.lease_token=p_lease_token and j.status='RUNNING' and j.lease_expires_at>=now();
 get diagnostics changed=row_count;
 return changed=1;
end $$;
revoke all on function public.report_onflows_sync_progress(uuid,uuid,uuid,text,integer) from public,anon,authenticated;
grant execute on function public.report_onflows_sync_progress(uuid,uuid,uuid,text,integer) to service_role;
notify pgrst,'reload schema';
commit;
