-- Full approved + proposed microcycles can outlive PostgREST's inherited 8 s
-- statement budget. Both public entry points share the existing checked writer.
-- Keep role/database defaults, lock timeouts, CAS, permissions and pins intact.
begin;
set local lock_timeout='2s';
alter function public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
 set statement_timeout='30s';
alter function public.save_onflows_management_plan_compact(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
 set statement_timeout='30s';
notify pgrst, 'reload schema';
commit;
