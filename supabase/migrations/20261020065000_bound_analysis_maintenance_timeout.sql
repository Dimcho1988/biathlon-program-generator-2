-- Live bounded cleanup took 18.465 s; PostgREST otherwise inherits the
-- authenticator's 8 s limit. Scope this allowance to the service-only fixed
-- maintenance RPC. Ordinary requests retain their existing timeouts.
begin;
set local lock_timeout='2s';
alter function public.maintain_onflows_analysis_history(text)
 set statement_timeout='30s';
notify pgrst, 'reload schema';
commit;
