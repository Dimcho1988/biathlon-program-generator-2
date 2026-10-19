# Reusing measured-history projection in the generator

## Implemented scope

`generate_plan` now consumes the existing `PlanningSourceResult` returned by
`planning_source_details`. It uses the same planning source and evidence, plus
the measured history that the helper already projects. Previously the generator
called the two-value wrapper and then projected the original measured history
again with the same component contexts.

The measured result is captured before confirmed rest and temporary estimated
load are added. Both later preparation layers preserve that result. It is used
directly, including an empty object; falling back to the planning source would
incorrectly feed planning assumptions into measured-history consumers.

The existing two-value `planning_source` wrapper remains compatible. No dosing,
Recovery, allocation, engine-version, computation-key, search-policy, database,
dependency or public-interface change is introduced. There are no global caches,
new persisted fields or rewritten historical records. Repository requests and
decoded response sizes are unchanged by this CPU-only optimization.

## Correctness and acceptance

Acceptance compares the complete serialized generator result with an adapter
that restores the former second projection from the original envelope. Cases
include complete, estimated, unsupported and already projected history, declared
rest, conflicting recorded activity and absent/null/empty history. Source and
profile mutation, measured-history fingerprints and corrupt-ledger/context
failure behavior are checked separately. Nonempty histories require one
projection instead of two. An empty history requires none instead of the
previous no-op call; both paths retain the same empty measured result.

The helper's first projection still validates complete activity Q, persisted
daily ledgers and component contexts. Reuse does not bypass those checks.
Measured history remains separate from temporary planning coverage and estimates.

The surrounding search retains its existing count limits and wall-time budget.
A faster replay can leave time for an already permitted alternative near that
budget boundary. Exact single-generator equality therefore does not promise an
identical number of time-budgeted search trials under every runtime condition.

## Measurement boundary

The baseline is staging `3c54e56` (PR #169). Its warm manual refreshes used an
existing shared READY result: 20 store calls and 6,266,490 decoded response bytes.
Those requests do not execute the changed generator and cannot measure this
optimization's computational saving.

A private benchmark runs the original generator extracted from that commit
against the new generator with identical current helpers. Every run gets a fresh
repository/read phase, frozen time and identical inputs. Full serialized JSON
and original-input preservation must match. Warm-ups are excluded; alternating
old/new pairs record wall time, process CPU time and projection calls.

The historical replay is only partially reconstructed: some TI and response
observations are absent. It measures actual local plan generation, not end-to-end
staging latency, database wait, worker queue delay or universal athlete behavior.
Timing results, exact CI counts and live deployment acceptance are recorded in
the associated pull request.

The uncontended benchmark used two warm-ups and three alternating pairs. Means:

| Measurement | Previous generator | Reused projection |
| --- | ---: | ---: |
| Complete generation, wall seconds | 5.679 | 5.753 |
| Complete generation, process CPU seconds | 5.676 | 5.744 |
| All history-projection calls, wall seconds | 0.728 | 0.273 |
| Projection calls per generation | 2 | 1 |

No consistent overall speedup is established by these samples. Exactly one
repeated projection is removed. All eight runs, including warm-ups, returned
identical canonical JSON: 1,137,341 bytes, SHA256
`42c2da06b47fcb505ba3e7ee99bc2c88b4ed25666605cd752d246d4c0606129a`.
Each produced six sessions and the same six mocked store requests, with the
original profile, repository source and entire input fixture unchanged. An
earlier run overlapping local tests was retained separately and excluded.

## Rollout and recovery

After complete CI succeeds, deploy the tested commit to API and worker. Web
source, database and settings are unchanged, so no web rebuild or migration is
needed. Verify actual LIVE commits and application reads, preserving the current
revision, proposal, review status and history. A warm refresh can verify these
contracts but must not be reported as a new-generator speed measurement.

Recovery is a revert of this scoped change followed by API/worker redeployment.
No database rollback, history restoration or stored-format conversion is required.
