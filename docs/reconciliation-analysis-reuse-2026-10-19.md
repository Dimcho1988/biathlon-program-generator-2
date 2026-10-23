# Reusing captured analysis for execution reconciliation

## Implemented scope

Plan refresh now takes the reconciliation history from its initial immutable
analysis capture. The former planning-calendar read fetched the full analysis,
generation metadata and activity catalog; reconciliation consumed only
`snapshot_payload.load_history`. That history already includes the recorded
activities used for calendar-day matching. The unused catalog did not filter
or augment this history.

Reuse requires a nonempty generation ID, a positive exact-integer analysis
revision, an object snapshot, and matching captured/frozen identities. Mutable
legacy sources, missing readers and invalid or mismatched captures retain the
original planning/activity-calendar fallback. No data is cached globally and
no new persisted representation is introduced.

The normal refresh path reuses one initial analysis for outcomes and calculation.
Standalone generation and real engine calculations still read their required
activity catalogs. Fresh post-calculation input comparison, profile revision
checks, SQL publication checkpoint and final current-state reads are unchanged.

## Correctness boundary

Both supported analysis activation and rollback increment `active_revision`.
A reachable G1/revision1 to G2/revision2 to G1/revision3 transition before the
fresh input comparison changes the input hash and rejects publication. A test
that restores revision1 after revision2 is not a supported rollback scenario.

This change does not create a database-wide snapshot. The existing publication
checkpoint checks generation identity, not analysis revision. A rollback to
the same immutable generation after the final input read may leave an already
stale fingerprint. The removed calendar read did not prevent this existing
freshness limitation; strengthening that SQL contract is outside this change.

No methodological rule, engine version, public HTTP schema, database function,
history record, training dose or approval is changed. Technical output parity
does not establish methodological correctness.

## Measurement and acceptance

The verified baseline is staging `539515c` (PR #168), with API and worker live
at that commit. Two new warm checks at 15:14 UTC took 4,628 and 4,110 ms, both
with 23 store calls and 7,085,921 decoded store response bytes. The removed
live responses were 448,331 bytes
of analysis, 92 bytes of generation metadata and 371,008 bytes of catalog:
819,431 bytes in total. These sizes predict 20 calls and approximately
6,266,490 decoded bytes for the same warm READY-result scenario. Catalog
pagination and actual engine work can change the saving in other scenarios.

Acceptance compares the complete HTTP result, immutable record, computation
key, historical outcomes and decisions with the former calendar path. Races
must still reject changed inputs before publication. Mutable-source fallbacks
remain covered. Full CI, actual live commits, refresh measurements, unchanged
revision/payload/review status, visible check history and long-term overview
parity are recorded in the associated pull request after rollout.

Decoded response bytes are not compressed network traffic or physical database
storage. Sparse before/after timings cannot establish a general speedup.

## Rollout and recovery

Deploy the tested commit to API and worker after CI passes. The frontend tree,
database and configuration are unchanged; no frontend rebuild or migration is
required. Verify the live deployment IDs and perform real manual checks without
approving the pending proposal.

Recovery consists of reverting this scoped lifecycle change and redeploying
API/worker. No data rollback, deletion or historical rewrite is required.
