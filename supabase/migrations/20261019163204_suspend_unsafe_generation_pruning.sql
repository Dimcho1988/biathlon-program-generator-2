-- Temporary preservation guard: current pruning does not protect immutable management history references.
-- Original ACL: {postgres=X/postgres,service_role=X/postgres}.
-- No training records or function bodies are modified.
REVOKE EXECUTE ON FUNCTION public.prune_onflows_analysis_generations(text,integer,interval,integer) FROM service_role;
