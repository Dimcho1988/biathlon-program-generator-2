# Bounded technical retention with historical provenance and recovery

## Status and scope

This is an additive implementation prepared for database acceptance. It is not
evidence that a live cleanup ran. The staging pruning permission remains revoked;
neither migration grants it back. No training rule, plan approval, historical
model/response/DRAFT/plan JSON, archive object or READY planning result is deleted
or rewritten by this work.

The implementation is split into migrations
`20261019201602_protect_historical_activity_references.sql` and
`20261019201842_recoverable_bounded_analysis_retention.sql`. Existing generation
pins and their base/activity-set ancestor closure remain in force.

## Read-only findings before implementation

The 19 October inventory replicated the existing 30-day/keep-five policy with
SELECTs. These were time-bounded observations, not an approved deletion manifest:

| Existing cleanup category | Eligible records observed | Important effects |
| --- | ---: | --- |
| Analysis generations | 0 | No activity cascades, job-pointer resets or base-pointer resets in this observation |
| Terminal jobs | 0 | No proposed deletion |
| Catalog pointer release | 0 | No proposed pointer update |
| Shadow runs | 466 | Approximately 132,110,773 bytes of stored result JSON; 466 summary rows would cascade |
| Canonical runs | 0 | No proposed deletion |
| Model inputs | 0 initially | Seven inputs, 379,396 stored payload bytes, become candidates only after all 466 shadow candidates disappear |
| Catalog rows | 0 | No proposed deletion |

The first ten shadow candidates and their summaries required approximately
2,295,125 bytes of full-row JSON before the journal's key/metadata envelope. The
old 100-shadow batch contained 27,342,215 stored result bytes. Relation size and
JSON storage measurements are not immediate disk-reclamation promises.

Those 466 SQL candidates were **not all historically unused**. Twenty-two direct
model payloads contained a `source_run_key` (eight distinct references); fourteen
occurrences pointed at current shadow candidates. Historical management payloads
copy model entries into input snapshots and plans/proposals. A bounded known-key
census found 7,849 source-run occurrences in queried plan-revision ranges 1–700, another
30 in management entries and 22 in the model/response inventory, with no malformed
recognized values. This census used the known-key fast path; suffix fallback,
activity identities and decoded opaque content require the complete bootstrap
coverage checks before cleanup is enabled. A separate metadata read observed 659
plan rows with maximum revision 659; these individual reads were not one pinned
snapshot and do not replace the final cutoff verification.

One original run was already absent: two model revisions of one test, recorded
on 12 and 13 September 2026, refer to that same unavailable source. No original
payload has been reconstructed. The new legacy-unavailable ledger records the
missing identifier explicitly; it does not manufacture replayability.

The live preflight found no missing/foreign catalog pointers, input/catalog
identity mismatch, derived/input identity mismatch, or cross-athlete base edge.
There were 635 generation pins over 660 generations and 85,328 generation/activity
links. Generic FK-shaped anti-join EXPLAINs used a full activity-link scan without
leading shadow/canonical/input indexes. The new three partial indexes address
those measured access paths. They are not proof of the exact cause of the earlier
`57014` cancellation or a measured speedup of the complete cleanup transaction.

The two planning slots were READY. Their elapsed lease is not an expiry policy:
the existing contract reuses READY results for the identical key regardless of
lease age. This work leaves those slots unchanged.

## Historical retention boundary

Private monotone pins retain exact shadow runs, canonical runs, model inputs and
catalog activities. A retained shadow closes over its input and catalog identity;
canonical runs and inputs close over their catalog identity. Deferred native FKs
also protect catalog latest-run pointers, input/catalog identity and the complete
derived/input identity. Fresh activity publication still follows catalog stub,
input/shadow, canonical result, then catalog metadata. Populated account deletion
continues through the existing connection/settings cascade.

The scanner walks every model, response, management and plan-revision payload,
including disabled/superseded revisions, nested plan/proposal/input snapshots,
learning observations and arrays. Recognized `run_key`/`*_run_key` and
`input_key`/`*_input_key` values must be null or lower-case 64-character hashes.
Activity identities matching `act_` or `shadow-` plus 32 hexadecimal characters
are recognized in scalar values anywhere, including observation IDs and session
continuations. SESSION entry keys are included separately. Other logical activity
identifiers are left compatible with their existing contracts.

An owner backfill may explicitly record an already missing legacy identifier.
New missing or foreign references fail with `PT409` and the exact hint
`ONFLOWS_HISTORY_REFERENCE_CONFLICT`; only an exact recorded legacy unavailable
identifier may be copied again. No application role can write the pin/exception
tables or invoke their helpers directly. Model history also receives generation
reference protection.

Opaque `codec` objects are hashed in full. An existing opaque object may be
registered only by the owner bootstrap; its presence blocks the athlete's cleanup.
An application cannot introduce a new opaque hash, while an exact pre-existing
opaque object may be copied without inventing decoded references. Hydration and
decompression must use the existing bounded, checksum-validated codecs. Current
numeric producers alone do not establish the contents of arbitrary legacy opaque
data. No owner should mark coverage complete until that inventory is resolved.

NEW pin writers use a nonblocking athlete mutex and KEY SHARE NOWAIT on parent
records. Existing committed pins can be reused without that mutex. Changed
retained parent links extend closure under the same mutex; the guard also covers
the first-pin/link-update race. Ordinary immutable app writers have no direct
identity-link UPDATE privilege.

## Batching and recovery contract

The 30-day minimum and five retained superseded generations remain the defaults.
The public function keeps its seven integer return fields. Each phase examines a
bounded prefix of 50 eligible parent keys and selects at most ten, respecting a
smaller caller limit. Before mutation it costs all first preimages, including
generation activity cascades, job/base pointer changes and shadow summary cascades.
It packs fitting keys within the remaining 1,000-row/16-MiB logical recovery
budget. Oversized keys are retained with a small budget-reason record, and other
keys/phases can continue. A prefix of fifty oversized candidates still requires
owner review; no larger implicit deletion budget is introduced.

The final capture trigger enforces the same whole-transaction budget. Any
unanticipated extra preimage aborts the entire cleanup. Parent selection uses
FOR UPDATE SKIP LOCKED; catalog-pointer predicates are rechecked under that lock.
The cleanup uses the same athlete mutex as new activity-history pins. Native FKs
independently reject stale deletions and cross-athlete identity links.

The journal captures the original full row **before its first change** in all
eight affected tables, including updates followed by deletion and implicit FK
effects. It retains the final operation and after-state hash for compare-and-swap
restoration. Historical source JSON itself is outside the deletion scope.

Private `restore_analysis_cleanup_batch(uuid)` restores an entire batch in one
transaction. It checks the original preimage hash and current table-schema
fingerprint, defers the graph constraints, and restores rows in dependency order.
A deleted key can be inserted only when vacant or already exactly restored. A
changed pointer can be restored only when its current row still matches the
recorded after-state hash. Newer rows, alternate unique-key conflicts or schema
drift abort restoration; they are not overwritten. Derived-run summary projection
must match the original summary in acceptance. A changed schema/incorrect legacy
summary may require a separately reviewed restore adapter.

Recovery preimages have a fixed **14-day window** from batch creation. Restoration
does not extend it. After that window, the service-only global expiry RPC removes
only `before_row` payloads and marks the expiry time. It retains batch identity,
row keys, original hashes, schema hashes, operations, byte/count metadata and
policy evidence. It does not expire training history, pins, archive blobs or READY
results. Thus temporary duplicate technical payloads have a bounded recovery
policy rather than becoming a second permanent archive. The worker calls the
bounded expiry RPC between jobs on its 900-second schedule; failures, long jobs
or a stopped worker can delay physical removal. The restore deadline remains
fixed independently of worker availability.

## Acceptance, live rollout and rollback

Required acceptance includes real PostgreSQL execution, both publication/deletion
race orders, new/existing pin reuse, changed-parent races, fresh activity writer
order, populated account cascade, all seven return counts, exact eight-table
restore, update-then-delete first preimages, schema/CAS refusal, budget overflow
rollback, oversized-candidate progress, 14-day payload expiry and preserved small
manifests. Technical tests do not establish methodological correctness.

The live sequence must remain explicit:

1. Deploy additive guards with prune still revoked; verify private ACLs/FKs and
   publication behavior.
2. Backfill immutable history in bounded natural-primary-key batches with a
   recorded cutoff. Call the private pin helpers only; do not rewrite history.
   Record missing legacy references and opaque hashes separately.
3. Verify every root, parent closure and every decoded opaque dependency. Repeat
   a final cutoff census while new INSERT triggers protect later records. Existing
   aliases receive an owner coverage record only after this evidence is complete.
   New settings rows start covered only when all four history sources are empty.
4. Present the exact selected deletion/pointer-change scope and the private
   recovery batch contract before an owner grants or invokes cleanup.
5. Start with the bounded transaction, inspect the committed manifest and verify
   history hashes, current pointers and normal application behavior. Compare
   actual SQL duration and reclaimable storage after autovacuum; do not promise
   immediate file shrinkage.

Rollback first revokes prune EXECUTE again, preventing calls from either worker
version. Keep additive pins/FKs and journal metadata; reverting application code
does not require removing them. Within fourteen days an owner can restore an
eligible batch using its private identifier. If any current state or schema has
changed, resolve that concrete conflict before a separately reviewed restore.
After payload expiry, this journal alone cannot reconstruct deleted technical
rows. No automatic grant restoration, pin removal, history rewrite, blob deletion,
VACUUM FULL or training-method change is part of this package.

The associated pull request records executed CI, live bootstrap/coverage, measured
cleanup duration and any eventual grant or deletion. Until recorded there, those
steps remain unverified.
