-- Retention must find referenced draft origins without decoding every saved
-- multi-megabyte plan for each obsolete-draft candidate.
begin;
set local lock_timeout='3s';
set local statement_timeout='120s';
create index onflows_management_plan_draft_origin
 on public.onflows_management_plan_revisions(athlete_alias,
  (payload#>>'{origin,start_date}'),(payload#>>'{origin,draft_revision}'));
analyze public.onflows_management_plan_revisions;
commit;
