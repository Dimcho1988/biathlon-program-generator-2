-- Restore the SELECT-only contract from the original generation migration.
-- Immutable snapshot identity is required by compact planning-state reads.
-- All supported writes remain through the existing SECURITY DEFINER RPCs.
-- No rows, table definitions, user grants, or function grants are changed.
revoke all on table public.onflows_analysis_generations from service_role;
grant select on table public.onflows_analysis_generations to service_role;
