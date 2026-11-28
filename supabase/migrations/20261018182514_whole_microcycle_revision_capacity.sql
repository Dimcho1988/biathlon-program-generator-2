-- A revision retains the approved plan and the proposed replacement together.
-- Complete microcycles add up to six provisional days to each plan; the
-- previous 2 MiB envelope rejected valid v48 proposals during publication.
-- Keep a finite 4 MiB envelope and the existing JSON-object requirement.
alter table public.onflows_management_plan_revisions
  drop constraint onflows_management_plan_revisions_payload_check,
  add constraint onflows_management_plan_revisions_payload_check
    check (jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 4194304);
