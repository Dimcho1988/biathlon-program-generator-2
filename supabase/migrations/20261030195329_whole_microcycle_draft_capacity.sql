-- Complete microcycles and their frozen evidence can exceed the original
-- 1 MiB draft limit (observed: 1,268,620 bytes before persistence metadata).
-- Active revisions already allow 4 MiB for the plan and proposed replacement.
-- Give a draft a finite 2 MiB envelope; keep profiles at 64 KiB and require
-- JSON objects. Authorization, publication checkpoints and history are unchanged.
alter table public.onflows_management_entries
  drop constraint onflows_management_entries_check,
  add constraint onflows_management_entries_check check (
    jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 2097152
    and (kind <> 'PROFILE' or octet_length(payload::text) <= 65536)
  );
