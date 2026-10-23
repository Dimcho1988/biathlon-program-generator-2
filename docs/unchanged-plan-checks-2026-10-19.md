# Manual refresh checks without duplicate plan revisions

## Scope and equality boundary

A reader-capable client may opt a manual `REFRESH` into
`record_unchanged_check`. The lifecycle still calculates the complete candidate,
including outcomes, decisions, inputs, explanations and proposal. It compares
the SHA-256 hashes of canonical JSON for the entire candidate and current
immutable envelope. Array order and JSON types matter; `true` and `1`, missing
and null, or differing evidence are not treated as equal. Equality does not mean
the methodological result has been approved or verified by a coach.

Only an identical manual refresh of an ACTIVE or REVIEW_REQUIRED plan may append
a small check event. Activation, approval, resume, pause, day decisions,
completion and automatic worker actions retain their existing behavior. A real
change still saves a complete revision. No existing history is rewritten,
compacted or removed, and the pending proposal remains pending.

The trusted API is the candidate equality authority. The small database request
does not send the candidate or assert a cross-language canonical hash protocol.
The database pins the referenced immutable revision under the same lock and CAS
as full writes, hydrates it locally, and executes the shared publication checks:
actor authorization, current profile, checkpoint, local date, activation
eligibility and approved identity/settings. An input or revision race returns a
conflict. It never silently falls back to saving another full revision.

## Storage and API contracts

The new private append-only check table records an identity, sequence, actor,
checked revision, time and bounded proof metadata. Database hashes are hashes of
database JSON text, not the API's canonical candidate hash. The event and the
existing next-check deferral commit atomically. The plan pointer, full revision
count, approved plan, proposal and old revision timestamps are unchanged.
The revision foreign key has a matching index so existing parent cleanup does
not repeatedly scan an athlete's entire check history. Its cost is one small
index entry per event; this is not claimed as a current page-load improvement.

A server-generated UUID identifies one RPC attempt. A same-nonce replay can
return the original acknowledgement only after authorization and all current
publication guards still pass; mismatched nonce reuse conflicts. The repository
does not retry POST automatically. This is RPC replay protection, not a promise
that a separately repeated HTTP action creates no second audit event.

The check acknowledgement is distinct from a save acknowledgement: it reports
`checked_revision`, not a new revision. The HTTP action response reloads current
state and adds a transient `action_result`. A concurrent later publication can
therefore be newer than the checked revision. The UI reports both versions in
that case and does not claim the newer version was checked.

The API advertises `capabilities.unchanged_checks`. Only a frontend that includes
the new history reader sends the opt-in flag, and only on the manual refresh
button. A new web client against an old API omits the flag. Old web clients
against the new API retain full-save behavior. No feature environment setting
is required.

`GET /api/v2/athlete/management/checks` reads metadata lazily when its history
section opens, reopens or is explicitly refreshed. Pages default to ten events,
at most fifty, with a descending sequence cursor. The endpoint is scoped to the
resolved athlete and has no-store caching. It returns no private checkpoint or
actor metadata. Opening a page, normal polling and the overview add no checks
query. The ten full revision entries remain a separate history.

## Validation and performance acceptance

Tests cover whole-envelope equality, evidence-only changes, strict response
validation, old-client behavior, publication races, RPC nonce replay and role
grants. The database acceptance test uses synthetic data in an isolated CI
database and rolls its transaction back; it is not a staging fixture script.

Before rollout, run complete API, canonical, web and database CI, plus lint,
types and production build. After rollout, perform a real manual refresh without
approval, inspect the visible check history and independently compare revision
count and plan/proposal hashes. Compare the actual small publication request
against the preceding 1,912,566-byte database-text envelope. JSON request sizes,
logical stored data, physical table size, decoded response bytes and latency are
different measurements. Do not claim this removes recalculation or existing
read costs. Unchanged normal page query counts are also an acceptance condition.

## Rollout and recovery

Apply the additive migration after CI. Deploy API and worker first while the old
web still does full writes, then deploy the web reader and opt-in together. Check
the actual live commit on all three services. No plan approval is part of this
process.

To disable new checks, deploy the preceding web version first, then revert API
and worker if required. The old full-write RPCs remain compatible. Keep the
additive table and its reader so any events already written remain accessible;
do not use a destructive down-migration. Recovery requires no restoration or
rewriting of training history. No new dependency, paid resource or cache is part
of this change.

## Status

Implementation and verification results are recorded in the pull request.
Deployment and live acceptance remain unverified until explicitly recorded
there. This optimization makes no change to dose, Recovery or microcycle rules;
the methodological audit findings remain separate work.
