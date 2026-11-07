# Temporary generation-pruning preservation guard

## Implemented scope

Migration `20261019163204_suspend_unsafe_generation_pruning.sql` revokes only
`service_role` EXECUTE on
`public.prune_onflows_analysis_generations(text,integer,interval,integer)`.
The pre-change function ACL was
`{postgres=X/postgres,service_role=X/postgres}`; public, anonymous and
authenticated clients had no execution permission. The staging migration
version is `20261019163204`. Post-application ACL verification found
`{postgres=X/postgres}`: service, anonymous and authenticated execution was
false, while owner execution remained true. No active prune transaction was
observed before application.

The function body, records, schemas, training rules and publication contracts
are unchanged. This suspends the entire optional RPC: generation/activity-row,
terminal-job, shadow-run, canonical-run, model-input and catalog cleanup.
These operations share one transaction and are not selectively re-enabled.
Sync claims, staging, activation, plan adaptation and private history archival
use separate operations and keep their existing permissions.

The worker already treats housekeeping exceptions as non-fatal. A denied prune
attempt therefore produces the existing housekeeping warning, without undoing
a successful activation or preventing subsequent claims. Its per-athlete
six-hour attempt timer is process-local and resets on worker restart. The ACL
protects future calls from both old and new workers using `service_role`; it does
not cancel an invocation already executing or restrict the database owner.
Subsequent live deployment/UI acceptance is recorded in the associated pull
request; ACL verification does not itself prove every application flow.

## Evidence and limits

On 19 October 2026, worker logs showed a successful activation followed by a
prune cancellation with SQLSTATE `57014`. The cancellation preceded the CPU-only
generator-projection optimization. The exact cancellation cause and statement
phase were not established.

A SELECT-only inventory of 657 historical plan revisions found 544 distinct
generation references across the explicitly queried plan/proposal `source` and
`input_snapshot` paths; none was missing. Of 23 DRAFT rows, five records contained
references across three generations. A SELECT-only replica of the current
retention conditions found one eligible generation referenced by one historical
DRAFT. No prune or deletion was invoked for this investigation. These are
time-bounded observations, not a guarantee about every athlete or payload path.

The existing prune candidate conditions omit management-history references.
Source review identifies three primary paths in a DRAFT:

- `source.generation_id`
- `input_snapshot.generation_id`
- `persistence.analysis_generation_id`

The same paths may occur under both `plan` and `proposal` in a plan revision:
nine paths across the two record types. Activation copies the complete saved
DRAFT, so nested `plan.persistence.analysis_generation_id` is a real path. The
earlier four-path plan inventory did not independently cover these persistence
paths and must not be described as exhaustive.

Nested learning episodes also preserve older `provenance.generation_id` values;
computed contexts can contain `source_generation_id`. Reviewed consumers use
saved numerical evidence and provenance identifiers. Whether full independent
generation retention is required for those nested references remains a separate
acceptance question. The guard preserves existing data while that scope is
resolved; it does not claim to have implemented a complete reference registry.

Live indexes and `EXPLAIN (ANALYZE FALSE)` showed estimated scans of approximately
87,610 generation/activity links in orphan checks, without leading indexes on
their shadow, canonical or input reference keys. These estimates identify a
possible cost, not measured statement duration or the cause of `57014`.
Indexes, larger batches and longer timeouts remain deferred: making the old
function succeed faster could delete the demonstrated historical reference.

## Verification and follow-up boundary

Database acceptance must assert that `service_role`, `anon` and `authenticated`
cannot execute prune, while the five other generation writers and separate
history-archive operations retain their permissions. Exercise permission denial
with synthetic inputs only; do not execute owner-level cleanup against history.
The focused existing worker tests passed: `pytest
tests/api/test_sync_worker.py -q -k housekeeping` returned four passed and 20
deselected. They cover attempt timing, once-mode exclusion and mocked failure
without undoing activation or stopping further claims. They do not execute the
live permission-denied RPC. Live UI/ACL checks supplement these tests; they do
not prove methodological correctness or a performance improvement.

A later retention fix must protect historical references during concurrent
publication as well as during candidate selection. A JSON `NOT EXISTS` check
alone can race an INSERT. DRAFT also permits direct service-role INSERT, so a
new advisory lock used only by RPCs is insufficient. Generation locks must
respect the existing rollback order (generation before analysis state) or use
an explicitly handled non-blocking conflict boundary. These are requirements
for the next reviewed proposal, not changes introduced here.

## Recovery and operational cost

Suspension temporarily retains rows that housekeeping would otherwise remove;
storage growth and expected permission warnings are the principal costs. No
historical payload needs restoration or recomputation for this migration.

Do not automatically restore EXECUTE during an application rollback. The old
prune implementation remains unsafe. Re-enable it only in a separate reviewed
migration after reference coverage, concurrent INSERT/deletion behavior,
existing retention rules and account cleanup have passed real database tests.
That migration can restore the original `service_role` EXECUTE grant; no other
role needs a new grant.

Before any later cleanup is run against real history, record its concrete
candidates and recovery scope. A restore plan must include deleted dependency
rows and prior values of modified job, catalog and generation pointers, not
only the generation rows. Keep the current guard while that work is prepared.
