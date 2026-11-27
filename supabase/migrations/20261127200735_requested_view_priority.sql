-- Extend the existing atomic claim, retaining all current publication guards
-- (including HR calibration), leases, cooldowns and access grants.
do $$
declare definition text; original text;
begin
 original:=pg_get_functiondef('onflows_private.prepared_view(text,text,text,jsonb,uuid,bigint,date,text,jsonb)'::regprocedure);
 definition:=replace(original,'''CLAIM''','''CLAIM'',''CLAIM_REQUESTED'',''CLAIM_BACKGROUND''');
 definition:=replace(definition,
  'if p_operation=''CLAIM'',''CLAIM_REQUESTED'',''CLAIM_BACKGROUND'' then',
  'if p_operation in (''CLAIM'',''CLAIM_REQUESTED'',''CLAIM_BACKGROUND'') then');
 if position('if p_operation in (''CLAIM'',''CLAIM_REQUESTED'',''CLAIM_BACKGROUND'') then' in definition)=0
  or position('and (v.lease_until is null' in definition)=0 then
  raise exception 'Prepared view claim definition changed';
 end if;
 definition:=replace(definition,'and (v.lease_until is null',
  'and (p_operation=''CLAIM''
    or (p_operation=''CLAIM_REQUESTED'' and v.last_requested_at>clock_timestamp()-interval ''5 minutes'')
    or (p_operation=''CLAIM_BACKGROUND'' and (v.last_requested_at is null or v.last_requested_at<=clock_timestamp()-interval ''5 minutes'')))
    and (v.lease_until is null');
 execute definition;
end $$;
notify pgrst,'reload schema';
