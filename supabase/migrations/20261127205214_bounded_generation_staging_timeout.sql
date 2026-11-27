-- The authenticated API default is 8 s. Bulk immutable generation staging can
-- exceed it even when all activity calculations are reused. This background
-- RPC gets its own bounded budget; lock timeout and all publication fences stay
-- unchanged. Deploy the worker's 35 s transport budget before applying this.
alter function public.stage_onflows_analysis_generation(
 uuid,uuid,uuid,jsonb,text,date,date,date,jsonb,jsonb,boolean
) set statement_timeout to '30s';
notify pgrst,'reload schema';
