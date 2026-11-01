# Background draft preparation and durable history projection

Manual draft preparation now uses `generation-jobs`: HTTP 202 stores a small
request; the existing sync worker consumes it. The web client polls bounded,
no-store status reads, pauses polling in hidden tabs, resumes an outstanding
request after navigation, and retrieves the exact published draft revision.
Acceptance is not completion. A draft never activates a training program.

## Concurrency and publication

`onflows_private.planning_jobs` holds one current job per athlete, not an
unbounded event history. The same actor and request returns the existing job.
A different pending request returns a conflict. Workers use SKIP LOCKED,
90-second token leases, 20-second heartbeats and at most three attempts.
Temporary store failures retry after 30 seconds; input/access conflicts require
a fresh user request. An uncertain browser POST is read back, never replayed.

Completion checks the claim's settings/model/source/response checkpoint, then
calls the existing authoritative draft writer in the same transaction as the
job receipt. Its actor grants, profile/draft compare-and-swap checks, generation
and response checks, and history pins remain in force. A lost lease cannot
publish. Repeating completion returns the original receipt.

One drainable planning consumer runs beside imports on the existing worker.
Imports and planning share the existing model-work limiter. SIGTERM stops new
claims and lets current work drain within the configured 300-second shutdown
window. A killed process is recovered after lease expiry.

## Derived history cache

`onflows_private.load_projection_cache` holds at most one derived JSON document
per athlete, with a two-day expiry and an 8 MiB payload ceiling. Reads require
the existing complete fingerprint: athlete, repository namespace, immutable
generation/catalog, settings, model/test inputs, policy versions and evaluation
day. A mismatch recomputes using the unchanged reconstruction. Store failures
fall back to computation. Successful imports warm the active projection.
The cache can be reused after API restarts without decoding every recording.
Initial misses in different processes may still compute concurrently.

Both tables deny direct browser/service-role table access; only server RPCs
have explicit service-role execution grants. Public RPC wrappers are security
invoker functions. RLS is enabled on both private tables.

## Scope and validation

This changes task scheduling and derived-data reuse, not training models,
formulas, program approval, paid plans or service counts. Existing active-plan
refresh/resume/day actions retain their current synchronous contracts. Queueing
does not increase the CPU's calculation throughput or establish capacity for
1,000 simultaneous users.

Pre-deploy validation: 514 relevant backend tests; all 730 web tests; lint,
TypeScript and production build; SQL contracts against synthetic staging
fixtures inside a rolled-back transaction. The frozen history benchmark
matches exactly across 126 activities and 450 daily rows. SQL contracts cover
duplicate acceptance/completion, stale leases, revoked grants, profile and
settings changes, retry backoff, cache key/expiry and private RPC access.

## Staging operations

Deploy the additive database migrations, worker, API, then web. Keep existing
service plans and automatic deploy settings. Verify a queued request reaches
SUCCEEDED, returns the exact draft revision and remains unactivated; verify
page reload resumes progress. Check cache metadata and hit logs after an API
restart, plus worker/API memory and failure events.

For rollback, deploy the preceding web/API/worker commits. The additive tables
can remain in place; do not drop queued jobs or cached data during an incident.
A worker using the new code is required to consume outstanding manual jobs.
