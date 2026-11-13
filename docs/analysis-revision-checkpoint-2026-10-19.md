# Publication checkpoint and late analysis rollback

Status: narrow implementation and regression tests prepared; real PostgreSQL
and staging acceptance are recorded separately. No live database write or
training-plan approval was used for the investigation.

## Proven boundary and reproduction

`management_service.input_state` includes both `generation_id` and
`analysis_revision` in the input fingerprint. `build_draft` compares a new
post-calculation input phase with the captured inputs. Supported activation and
rollback increment `onflows_athlete_analysis_state.active_revision`; returning
to an earlier immutable generation does not restore its earlier revision.

The preceding `onflows_management_checkpoint` projection includes settings,
generation identity, latest model revisions and latest response revisions, but
omits the active analysis revision. The active save core first takes the
per-athlete management advisory lock and current-plan row lock, validates the
profile revision, then holds settings/analysis-state row SHARE locks and a
model-table SHARE lock while comparing the full checkpoint. The same core
serves full save, compact save and CHECK_NO_CHANGE.

A concrete HTTP regression reproduces this interleaving with the actual
repository, route and lifecycle:

1. Capture G1/revision N and finish the fresh post-calculation check.
2. Before the save, move to G2/revision N+1 and return to G1/revision N+2.
3. The old projection accepts the unchanged generation identity and saves a
   revision. The route's fresh final current read immediately returns
   `stale: true`.

This is a stale publication/provenance gap, not evidence of a different dose:
G1's immutable snapshot is still the same. Its frequency and cost in staging
are unmeasured. The same late settings, model or response changes are already
rejected by the existing checkpoint. There is no claim that this caused the
previous 90.163-second request.

## Minimal change

Migration `20261020062208_include_analysis_revision_in_management_checkpoint.sql`
adds only `analysis_revision`, read directly from the analysis state's
`active_revision`. The single STABLE SQL statement gives the generation and
revision a consistent read snapshot; the existing save core compares them
after acquiring its existing locks. There is no new lock, RPC signature,
application request, storage format, engine version or training-rule change.

An absent state row yields explicit JSON null; an existing pre-analysis row
retains numeric zero. These are projection semantics, not a new guarantee
against concurrent creation of a previously missing state row. Unsupported
direct SQL resetting a monotonic revision is outside this fix.

The API already passes the complete opaque checkpoint to full, compact and
unchanged-check RPCs. A request carrying the preceding field set during a
deployment safely receives an ordinary INPUTS_CHANGED conflict and must be
started again by its caller. It does not retry or fall back to another write.

## Existing settings, model, profile and draft guarantees

| Input | Active publication boundary already in place |
| --- | --- |
| Settings/calendar/preferences | Existing settings row SHARE lock plus exact checkpoint values; an edit committed before validation conflicts, and a later edit waits until publication completes. |
| Models | Table SHARE lock plus the latest per-kind/key revisions. It also blocks model INSERTs for another athlete during that short transaction; this broader contention follows from the source, but its live contribution to latency is not measured. |
| Management profile | Its writer and publication share the management advisory lock; publication compares the latest profile revision. |
| Responses | Supported response RPC and publication share the management advisory lock; latest response revisions participate in the checkpoint. |
| Analysis | Existing state row SHARE lock; the added monotonic revision closes the same-generation rollback omission. A legitimate change after transaction commit can still make the saved plan stale. |

The new local-only PostgreSQL concurrency harness explicitly observes these
blocking edges through `pg_blocking_pids`; it does not infer correctness from
sleep duration. Its settings/model/profile/response cases exercise the
existing protection, without changing or removing the global model lock.

Draft persistence deliberately has a weaker, documented READ_COMPARISON
contract. It freezes the profile, generation and response evidence; its history
reader marks changed input fingerprints without rewriting that evidence.
Settings or model edits after its final read may leave a stale historical
draft. Activation separately requires the latest selected draft, an exact
live fingerprint and the locked full active-publication checkpoint. This fix
does not add an atomic-current promise, extra RPC or broader lock to drafts.

## Verification and acceptance

- HTTP regressions compare former/new complete canonical JSON and request
  counts for unchanged READY and OWNED results. Late monotonic rollback changes
  the former 200/stale result to 409 with no new revision or retry.
  The focused HTTP/store/initial-and-final-read selection passed 202 tests
  locally; this is not a full API-suite or locked-runtime acceptance result.
- The rolled-back SQL fixture tests the real projection's null/zero semantics,
  service-only invoker contract, two supported rollback calls, rejection by all
  three active writer forms, unchanged historical tuples/pointer/scheduling and
  fresh publication/check success.
- Nine real multi-session SQL scenarios are wired into database CI: late
  rollback against full/compact/check, plus publication-first settings, model,
  other-athlete model, profile, response and rollback races. Fixtures restrict
  the DSN to loopback Supabase, use unique synthetic identities and clean up
  only after all held sessions have ended.
- Real PostgreSQL execution and staging acceptance remain pending until their
  exact CI commit/results and deployed migration version are recorded. This
  worktree has no local PostgreSQL or Docker executable.

The key source boundaries are
`apps/api/management_service.py:input_state/build_draft`,
`apps/api/management_lifecycle.py:activate/refresh`,
`20260927141548_management_response_checkpoint.sql`,
`20261019112003_unchanged_management_checks.sql:save_management_plan_core`, and
the supported activation/rollback functions in
`202608270001_sync_queue_generations.sql`. Existing regression coverage includes
`test_management_initial_read_phase.py`, `test_management_final_read_phase.py`
and the draft-history stale-fingerprint test in `test_management_api.py`.

## Recovery

Restore only the preceding checkpoint function definition, preserving its
invoker/security/search-path/grants, with a new reviewed migration. Do not
delete the applied migration ledger entry. A checkpoint captured across either
definition change may conflict once and can be acquired again on a new action.
No historical row, immutable payload, pin, approval or source generation needs
rewriting or restoration. Keep pruning disabled. Removing the new comparison
reopens the documented late rollback gap; the preferred response to a test or
deployment failure is to stop publication and diagnose it before that rollback.
