# Planning rationalization: third performance batch

This batch removes work whose output is already available or unused by the
requesting screen. It changes neither training rules nor the engine version.
It does not delete code compatibility, training history, plan evidence or
stored fields. PRs #163 and #164 provide the preceding baseline at `692cde0`.

## Implemented and checked locally

| Work removed | Evidence | Expected effect and boundary |
| --- | --- | --- |
| Full plan envelope and revision list on the long-term overview | The screen needs selected days' clock minutes and zone blocks, outcomes, comparison history and freshness/status. The complete live envelope is 1,912,566 bytes of PostgreSQL JSON text; its presentation projection is 10,559 bytes. | Transfer and parse less data. Preserve all stored evidence; weekly details still read the full plan. HTTP adapter verifies 17 rather than 18 store calls. |
| Second measured-history projection in `outlook` | Both calls used the same measured source and component contexts. Full outlook results match before and after reuse, including declared rest and estimated history. | Project once and retain measured data separately from temporary planning estimates. Local four-run warm benchmark means: 1.867 to 1.414 seconds; this is not a staging latency claim. |
| Three separate final settings reads after draft calculation | The final phase reads the same settings row for four purposes. HTTP-adapter tests exercise edits during calculation and after the final read. | Use one new settings bundle after calculation: 31 to 28 calls in the warm refresh fixture. Never reuse the calculation's earlier read session. Retain the final input comparison and database publication checkpoint. |

The new overview is an explicit opt-in `projection=overview-v1` response, with
its own typed frontend contract. It does not masquerade as a complete draft.
The selected proposal takes precedence over the approved plan, exactly as in
the full view. Stale inputs or an engine mismatch suppress compiled future
volume while keeping measured outcomes visible. Unknown actual time remains
unknown; known rest, skipped and unavailable days remain zero. Session-array
precedence, legacy single-session data, block order and unknown zone handling
are covered by frontend parity tests. The unused profile-history calculation
is skipped only on this opt-in route.

The read-only SQL RPC pins the current pointer and record in one statement.
It is STABLE, SECURITY INVOKER, has an empty search path and is executable only
by the existing service role. It grants no new table write rights. Malformed
required fields fail rather than becoming valid-looking empty arrays. Default
overview and weekly API responses retain their full contract. A new frontend
can use an old API's full response if the projection query was ignored.

The measured projection is retained before declared rest and temporary load
estimates are added. Tests verify mutation isolation. The generator still uses
its existing two-result planning-source interface; only outlook consumes the
additional measured result. There is no global or cross-request result cache.

## Verification before rollout

Focused suites passed: 171 related API tests, 528 web tests, frontend type
checking and lint. These overlap other focused runs and must not be summed.
New tests cover presentation parity, stale inputs, legacy data, failed reads,
settings/timezone changes during calculation, and publication races. Independent
review found no blocker after missing-array validation was tightened. Full CI
and the transactional SQL acceptance suite must pass before deployment.

The SQL acceptance test inserts synthetic identities into an isolated CI
database and rolls back. It checks proposal selection, legacy sessions, exact
unchanged stored evidence, role grants, unknown values and malformed fields.
It is not a staging data-edit script.

## Measurement and current evidence

Existing request timing stays available. One additional bounded detail line
records fixed resource labels and fixed route-stage names; query parameters,
athlete identifiers, model keys, headers and payloads are not logged. Stage wall
times include their nested operations and cannot be added together. CPU time is
for the calling thread, not the entire process. Store timings include HTTP and
service wait; decoded response bytes are neither compressed wire bytes nor
database write volume. Historical `pg_stat_statements` aggregates do not isolate
the current request and are not sufficient to blame PostgreSQL or network time.

On the preceding deployment, three overview requests took 5,543, 5,350 and
5,441 ms, each with 18 calls and 3,618,725 decoded store bytes. A warm refresh
took 7,810 ms, 31 calls and 7,574,525 bytes. Those observations did not establish
a latency improvement from PR #164. New measurements must use this batch's
deployed API and frontend, distinguishing overview from weekly requests and
warm shared-computation reuse from fresh algorithm calculation.

Revision 655 was already current before this rollout. Its source generation
differs from revision 654, so whole-envelope equality with 654 is not an
appropriate acceptance assertion. Their proposal `days` and `lookahead_days`
are equal. Live acceptance should compare against the current input generation
and preserve all earlier revisions, without approving the proposal.

## Rollout and recovery

After all CI passes, apply the additive overview-RPC migration, deploy the same
tested commit to API and worker, and only then deploy the web frontend. Verify
live service commits and the opt-in overview's data, status, comparison rows and
volume tables. Measure several page reads and one controlled review-only warm
refresh if current input state permits a comparable measurement. Check the
result against immutable revision hashes independently of the UI. No approval
action is part of acceptance.

Recovery starts by deploying the previous web version, then reverting API and
worker if needed. The old full route remains supported throughout. The unused
read RPC can remain installed; it changes no stored rows or write functions.
No history restoration or destructive down-migration is necessary.

## Proposed separately; not implemented

The next storage proposal is a versioned representation that stops writing the
same first session twice through `day.session` and `day.sessions[0]`. The inspected
envelope contains approximately 332 KB of that duplicate JSON. Existing Python,
frontend and export readers still use the legacy field; one scenario is not
evidence that it is unused. Reader compatibility must be deployed to API and
worker before any new-format write. Existing immutable revisions remain intact.
The roughly 97 KB locked-day snapshot has a replay/audit purpose and stays.
Once a compact format is written, rollback must retain its compatibility reader;
a plain revert to a pre-reader version would be unsafe. Discuss this exact scope
and recovery before changing the persisted representation.

Sharing the initial draft context with the calculation could remove further
reads, but requires an explicit captured-input interface and race checks. It is
deferred rather than weakening the fresh publication boundary. Server upgrades
are not justified by reduced byte counts alone; the new stage measurements must
first establish whether the remaining time is CPU work, remote wait or both.
