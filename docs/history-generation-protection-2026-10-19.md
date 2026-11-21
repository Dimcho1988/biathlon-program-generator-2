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
checking pins and extend the closure atomically. A deferred composite FK is the
final deletion boundary. All three
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

## Verified staging application — 19 October 2026

PR #172 merged as `ca4cb62d45741160329d2863d806c6439c576fde`; its tree
`9fbf791bc8a562f57089b09d180f8954e5646723` matches the tested merge. Final
CI passed 2,401 API, 786 canonical and 578 Web tests (3,765 total), lint,
type-check, production build, complete migration rebuild and all database
contracts. All ten real PostgreSQL competing-transaction cases passed,
including both parent-link/new-pin orders. This proves the tested concurrency
contract, not every future production workload or methodological result.

| Service | Deploy | LIVE UTC |
| --- | --- | --- |
| API | `dep-dbb5isks728c73fct9p0` | 17:56:13 |
| Worker | `dep-dbb5itvavr4c73803q80` | 17:55:57 |

Both services run `ca4cb62`; API startup and `/health` succeeded. Web source
was unchanged and did not need a rebuild.

Supabase recorded the exact checked bootstrap SQL under migration version
`20261019193059`. The repository filename is aligned to that actual version;
the originally tested timestamp was `20261019172610`. Both names order after
the previous migration `20261019163204`, and no SQL content changed:
22,139 bytes, SHA256
`d23d44da0b3f020c03da2636fd4d769232cfb951f0a808c2a42444d0fbc6b05e`.
The stored SQL MD5 is `6638ac669a24564235142504a3d0b29e`.
Remote migration-history metadata was not edited. The only active accessible
project was onflows-staging; the original timestamp was not applied there.

After bootstrap, the captured manifest still contained 658 plans, 23 DRAFTs
and 11 responses, with no future-dated records. Plans were processed in 22
committed primary-key ranges: 8, then 16, then at most 32 rows per batch.
Total measured tool round trips for those plan batches were 158.6 seconds;
that includes transport and helper execution and is not server-only time.
DRAFTs and responses were processed separately through bounded natural keys.
Only private identifier pins were inserted; no history payload was updated.

A final unrestricted scan of all three committed sources found 560 distinct
athlete/generation root pairs, zero unpinned roots, zero missing/foreign roots
and zero unpinned base/activity-set parent edges. There are 635 pinned
generations out of 660 total. The other 25 are not automatically safe deletion
candidates. The pin table plus its primary-key index occupies 163,840 bytes
(160 KiB). These results establish preservation coverage; no storage was freed
and no application speedup is claimed for this protection step.

The frozen prefix through 17:20 UTC retained identical full-key/xmin/ctid/time
fingerprints: 657 plans, 23 DRAFTs and 11 responses. Revision 658, created
independently at 17:22 UTC, remains ACTIVE with identical full payload, plan
and proposal hashes at the 19:37 UTC database check. No approval was performed
by this delivery. Anonymous, authenticated and service roles have no pin-table
SELECT/INSERT/UPDATE/DELETE, helper EXECUTE or prune EXECUTE permissions.

UI and final deployment observations are attached to the rollout PR. A warm
refresh supplements database acceptance; it cannot demonstrate a fresh plan
generation or exercise every new historical INSERT trigger against real data.
Synthetic INSERT/deletion concurrency was executed only in isolated CI.
