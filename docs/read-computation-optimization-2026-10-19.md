# Management read and computation optimization

## Scope

This change reduces repeated reads and deterministic numerical work. It does
not change dose rules, approved prescriptions, stored history, schema, grants,
dependencies, or the engine version.

1. `PlanStore.current` reads the publication pointer and its referenced immutable
   revision in one PostgREST LEFT embedding through the composite foreign key.
   Missing or mismatched referenced records still fail validation.
2. Read-only state polling can reuse the exact snapshot digest already frozen
   in a saved plan/proposal when the active generation ID, analysis revision and
   analysis date match. The existing analysis RPC supplies compact metadata.
   Every other planning input is freshly read. Missing/invalid hints, different
   generations and mutable legacy snapshots retain full read-and-hash behavior.
3. The request-scoped read session shares one athlete-settings row across its
   physiology, calendar, planning preferences and accent preferences. Parsing
   remains field-specific and lazy. Ordinary repository reads and final checks
   before publication remain fresh.
4. Each capacity predictor retains up to 2048 exact speed-to-HR results for its
   own immutable curve. Failures are not cached; different contexts are isolated.

## Correctness boundary

Generation-backed snapshots are immutable through supported writes. The stage
RPC can write snapshot payload only while a generation is BUILDING. READY retry
accepts only the same hash, and activation/rollback changes identity/status
without editing the payload. The service role has SELECT-only table access.
Legacy snapshots have no immutable generation ID and cannot use a saved digest.

The reused digest is the existing Python hash of the PostgreSQL read-back JSON,
not a newly substituted database-side hash. This preserves exact fingerprint
semantics, including numerical JSON normalization. If a new generation activates
between metadata and fallback reads, the full read's identity and hash are used
together. This optimization is confined to state polling; computation, approval,
and final publication checks retain their original full live snapshot reads.

## Validation and measurement

Tests compare complete input-state results with and without digest reuse; detect
same-plan-revision edits to physiology, date, profile, models, responses, calendar,
preferences and accents; cover generation changes, legacy mutation, activation
races, invalid metadata, escaped aliases and missing related records. Read-session
checks cover exact values, fewer requests, tenant isolation and uncached failures.
Capacity tests compare complete evidence and prevent cross-curve cache reuse.

A private, partially reconstructed historical replay produced exactly identical
full JSON across cached and uncached predictor runs. In that local example,
curve inversions fell from 927 to 627. Two alternating warm measurements averaged
4.40 versus 3.99 seconds; the small sample and shared CPU do not establish a
staging-wide speedup. Fresh state-poll bytes/request count and live page loading
must be measured after rollout. A cached shared plan refresh must not be compared
with an uncached calculation as evidence of numerical algorithm acceleration.

## Rollout and recovery

Run focused and complete API/canonical/frontend CI. Deploy the same tested commit
to staging API and worker. Verify actual PostgREST relationship and RPC projection
support through live page/state requests, unchanged plan/proposal fingerprints,
review status and subsequent polling. Compare equivalent before/after samples;
distinguish store time, total API time and local CPU measurements.

Recovery is a code revert and API/worker redeploy. There is no migration, backfill,
deletion, persistent cache, history rewrite or data restoration step.
