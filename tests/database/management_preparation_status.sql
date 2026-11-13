begin;
-- Transaction-local substitute proves the polling wrapper shares READ and never
-- exposes its payload, including when the saved result is large.
create or replace function onflows_private.prepared_view(
 p_operation text,p_alias text default null,p_kind text default null,p_parameters jsonb default '{}',
 p_token uuid default null,p_revision bigint default null,p_day date default null,
 p_model_key text default null,p_payload jsonb default null
) returns jsonb language plpgsql security definer set search_path='' as $$
begin
 if p_operation<>'STATUS' or p_kind<>'management-view' then raise exception 'Wrong status path'; end if;
 if p_alias='week' and p_parameters<>'{}'::jsonb then raise exception 'Wrong week variant'; end if;
 if p_alias='overview' and p_parameters<>'{"view":"overview","projection":"overview-v1"}'::jsonb then raise exception 'Wrong overview variant'; end if;
 return jsonb_build_object('ready',p_alias<>'pending','preparation',null,'payload',repeat('private',300000));
end $$;
do $$ declare result jsonb; i integer; begin
 if has_function_privilege('anon','public.onflows_management_preparation_status(text,text,text)','EXECUTE')
 or has_function_privilege('authenticated','public.onflows_management_preparation_status(text,text,text)','EXECUTE') then raise exception 'Public polling grant'; end if;
 if not has_function_privilege('service_role','public.onflows_management_preparation_status(text,text,text)','EXECUTE') then raise exception 'Missing service grant'; end if;
 result:=public.onflows_management_preparation_status('week',repeat('a',64));
 if result->>'ready'<>'true' or result ? 'payload' or octet_length(result::text)>256 then raise exception 'Oversized status'; end if;
 result:=public.onflows_management_preparation_status('overview',repeat('a',64),'overview');
 result:=public.onflows_management_preparation_status('pending',repeat('a',64));
 if result->>'ready'<>'false' then raise exception 'False readiness'; end if;
 begin
  perform public.onflows_management_preparation_status('week',repeat('a',64),'invalid');
  raise exception 'Invalid variant accepted';
 exception when invalid_parameter_value then null; end;
 -- Bounded synthetic cohorts; no network, model work or real athlete writes.
 for i in 1..100 loop
  result:=public.onflows_management_preparation_status('week',repeat('a',64));
  if octet_length(result::text)>256 then raise exception 'Unbounded status'; end if;
 end loop;
end $$;
rollback;
