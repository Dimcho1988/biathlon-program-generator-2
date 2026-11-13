# Outlook reads and compact publication acknowledgements

This second performance batch follows PR #163. It removes redundant transfers
without changing training rules, engine version, saved plan JSON or history.

## Outlook calendar reuse

The overview previously read the activity calendar once for the speed model and
again for planning. Both Supabase readers use the same generation-backed catalog.
The second traversal repeats the active-analysis RPC, generation metadata and
catalog page. The current fixture contains 133 activities and 112 TI keys.

`management_service.outlook` now shares the speed-model inputs and reuses their
full calendar only for the same athlete, local day, immutable generation,
revision and matching analysis provenance. It filters inclusively from today
minus 89 days through today, retaining ordering and all activity metadata.
Invalid hints, legacy sources and unsupported repositories keep the old read
path and its failure behavior. The authoritative analysis snapshot is retained.
There is no cross-request cache. An activation after a coherent read can leave
the view on that captured generation; evidence from two generations is never
combined.

The realistic HTTP-adapter test produces exactly identical complete overview
JSON with 18 instead of 21 store requests. Other requests remain necessary:
base overview reads, TI summaries in batches of 50, progression reference and
learning memory. Live latency and decoded JSON bytes require rollout measurement.

## Compact save acknowledgement

The old publication RPC returns the entire saved envelope. HTTP actions discard
that result and then read the current plan again; the daily worker also discards
it. The new `save_onflows_management_plan_compact` RPC delegates to exactly the
same private writer and removes only `payload` from its response. It is SECURITY
INVOKER with an empty search path and service-role-only execution. The original
RPC and default Python full-response behavior remain available.

Only callers that discard the saved payload explicitly opt in. A compact success
must contain `saved=true`, exactly the expected next integer revision and a valid
timestamp with a time zone, without a payload echo. Conflicts and malformed
acknowledgements remain failures. There is no automatic retry through another
writer after an ambiguous response. HTTP actions retain the subsequent fresh
current-plan read, including its concurrency behavior.

No change is made to authorization, publication locks, CAS checks, approved-rule
checks, the stored envelope, history or the 4 MiB storage-size constraint.

## Evidence and validation

Before rollout, live revisions 652 and 653 contain exactly the same complete
envelope: 1,912,566 UTF-8 bytes of PostgreSQL JSON text. The old save echo therefore
transfers approximately 1.91 MB unnecessarily; the compact acknowledgement is
under 200 bytes. This reduces returned data, not the JSON submitted or stored.

The controlled refresh at 09:20:31 UTC on the preceding version took 7,272 ms,
31 store calls and 9,487,276 decoded store JSON bytes. The shared computation
remained READY with its original 08:25:47 update time and unchanged result.
This is a warm-result publication benchmark, not fresh algorithm execution.
Recent baseline overview requests took 4,804 and 5,269 ms, each with 21 calls
and 4,438,164 decoded bytes. API timing includes store/network wait; it is not a
browser paint measurement or isolated PostgreSQL execution time.

Focused validation passed: 180 tests covering outlook and related read/model
paths, and 98 covering compact saves and lifecycle/routes/state. These suites
overlap. New cases cover date boundaries, empty/legacy data, generation races,
full-result equality, malformed acknowledgements and failed-publication reads.
The SQL acceptance transaction uses synthetic identities and rolls back. It
checks exact stored JSON/history, old-client compatibility, role access, CAS
conflicts, approved-rule protection, ineligible activation and size-limit failure.
Full CI and live acceptance are recorded in the pull request.

## Rollout and recovery

Run full API, canonical, web and database CI. Apply the additive compact-RPC
migration before deploying the tested code to API and worker. Verify current
service commits, role grants, successful page reads and a controlled review-only
refresh. Compare plan content and retained historical revisions independently.

Recovery is a code revert and API/worker redeploy. The old RPC remains present
and works after compact writes, so the unused new wrapper can remain. No data
restoration, migration reversal or history deletion is needed.

## Deferred storage-format proposal

The current format repeats the first session through `day.session` and
`day.sessions[0]`; the inspected envelope contains approximately 332 KB of such
duplicate JSON text. The locked-day operational context adds approximately
97 KB, with a separate audit purpose. These are evidence of duplication, not
permission to remove compatibility or replay fields.

A separate format proposal must define a versioned representation, compatibility
readers for every existing revision, exact replay of locked days, immutable
source references and recovery by the old reader. It needs comparison across
single/double days, combined methods, forecast continuation and historical
versions before any new storage format is approved. Existing revisions remain
untouched. This batch does not reduce database write volume or stored history.
