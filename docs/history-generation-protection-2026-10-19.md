# Historical generation protection — scope and acceptance

## Implemented scope

The private `history_generation_pins` relation stores only athlete/generation
identifiers, once per pair. Immutable plan revisions, DRAFTs and response
entries keep their original payloads, keys, timestamps and revisions. INSERT
triggers recursively identify UUID-valued `generation_id` and
`*_generation_id` fields, including nested learning episodes and saved TEST
load windows. JSON null is absent; malformed, missing and foreign references
produce a controlled conflict rather than disappearing from the dependency set.

Pins conservatively include both base-generation and activity-set ancestors.
This preserves SQL lineage as well as direct JSON provenance. Existing pins
remain until athlete/settings cleanup. This scope can retain more generations
than a snapshot-only policy; it is not a claim that all ancestors are needed
for every current numerical calculation.

New pin creation uses a nonblocking per-athlete transaction guard and ordered
KEY SHARE NOWAIT row locks. Already committed roots need no new generation
lock. Parent-link changes acquire the same nonblocking transaction guard before
checking pins and extend the closure atomically. A deferred composite FK is the final deletion boundary. All three
historical tables are guarded, including supported direct service-role INSERT.
Private helpers and pins receive no application DML/EXECUTE grants.

A concurrent new pin can temporarily refuse a generation link update for the
same athlete, including staging; this conservative serialization is deliberate.
The worker's existing retry boundary handles an unsuccessful staging attempt.
No write is automatically repeated by the API transport. Already committed
root pins do not require that transaction guard for another history append.

Only the exact PT409 / ONFLOWS_HISTORY_REFERENCE_CONFLICT signal on supported
history-write RPCs becomes the existing API 409 conflict. Other errors,
authorization, freshness checks and POST retry behavior are unchanged.

The pruning function only adds an indexed pin exclusion before LIMIT. Its
application EXECUTE permission remains revoked. No cleanup is authorized or
executed by this delivery, and no training-method rule changes.

## Baseline and measured boundary

Read-only staging census observed 658 plan revisions, 23 DRAFTs, 11 responses
and 660 analysis generations. Recognized references occurred 6,150 times in
plans (544 distinct athlete/generation pairs) and 150 times in DRAFTs (17
distinct pairs); these sets may overlap. Existing responses contained no
recognized generation reference. No malformed, missing or foreign reference
was found, and all generation base/activity-set links were valid and scoped
to the same athlete. Canonical-text inspection found no unknown suffix-key
payload among those three tables. This is a time-bounded census, not evidence
that future response windows or unknown keys can be ignored.

A generic recursive keyvalue scan on eight latest real plans took 7,108 ms
server time. Filtering known-key objects first took 291 ms for the same
195 occurrences. Unknown suffix names still select the generic extraction;
text is only a conservative selector, never the UUID parser. These compare
two protection prototypes, not old versus new application latency.

Historical rows recorded by 17:20 UTC were fingerprinted through their full
keys, xmin, ctid and recorded_at. The baseline covers 657 plan revisions,
23 DRAFTs and 11 responses. Revision 658 appeared during the investigation;
it was not created by this work. Repeat the frozen-prefix fingerprints after
application, rather than misclassifying a natural new revision as a rewrite.

## Staged application

1. Pass real PostgreSQL tests, including malformed/foreign references,
   dependency closure, account cascade and competing transactions. Static
   source inspection is not an execution substitute.
2. Deploy the narrow API conflict mapping, then install the bootstrap
   migration with transaction-local lock/statement bounds and NOWAIT DDL locks.
   If busy, abort and retry a clear transaction; do not lengthen global timeouts.
3. Backfill only identifier pins through owner-controlled, primary-key-bounded
   batches. New history is already guarded; pruning stays disabled. A failed
   batch leaves historical data unchanged and can be retried.
4. Verify all three sources and complete ancestor closure, unchanged frozen
   history fingerprints, current plan/review state, actual pin size and ACL.
   Record concrete CI and live acceptance in the pull request.

No global historical JSON scan runs while bootstrap DDL locks are held.
No cache, public projection or computation result is treated as immutable
decision history. Preserving each source generation also preserves its existing
activity-set/run/input dependency graph through existing foreign keys.

## Recovery and next boundary

Rollback keeps pruning disabled. The new API mapping is backward compatible
with the previous database. If the protection itself must be removed, first
remove its triggers, then private functions/table in a separately reviewed
migration; none of the historical payloads requires restoration. Removing
pins must never implicitly restore the old unsafe cleanup permission.

Before any later deletion, present exact candidates and a recoverable manifest
including dependency rows and prior job/catalog/base-pointer values. Any
narrower lineage/archive policy is a separate decision. Re-enabling cleanup,
adding orphan-reference indexes and changing batch/time limits are not part
of this preservation bootstrap.
