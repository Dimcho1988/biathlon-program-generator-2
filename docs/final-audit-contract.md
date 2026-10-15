# Final audit implementation — 15 October 2026

The current contract uses 60% of each method's base budget for maintenance and supporting work, without another Recovery multiplier. Developing work uses `base × (0.5 + 0.5 × Recovery/100)`. The 50% eligibility guard for work above Z1 remains; the former 90% role switch is removed. Complete repetitions/circuits, time availability, actual load and Q/E constraints still apply. Re-entry uses the maintenance budget. Combined components receive half of their own role budgets.

| Audit area | Implementation |
|---|---|
| C1: athlete/account identity | Management requests carry the actor and athlete captured by their page. Routes compare these with the current authorized context; mismatches return 412. Account/athlete switches invalidate old forms and late responses. Approval is never replayed. |
| C2: calendar horizon | Lifecycle reads, refreshes and day actions use the same effective horizon as draft generation. Automatic import scheduling resolves main-race end plus transition, with the same 365-day bound. Completed plans require manual review. |
| C3: estimated history | SQL activation and adaptation use the same strict boolean/policy eligibility predicate as Python. Revision, profile, generation, checkpoint, local-day and approval checks remain. |
| C4: executable methods | Continuous progression starts at the first admissible point inside its source range; AO31 no longer starts below the dose minimum. An insufficient range has an explicit unavailable reason. All 164 numeric recipes have acceptance coverage. |
| C5: threshold preferences | A shared classifier covers continuous, progressive continuous, interval and double-threshold methods in current and future selection. |
| C6: strength quota | Actual, locked and proposed strength exposures count together. The manual maximum remains binding. A third session requires a strength accent and at least one maintenance session. Whole circuits are quantized before selection. |
| C7: Recovery coverage | A short incomplete-data indicator accompanies missing days/partial activities. No zero load is invented and no Recovery mathematics is replaced. |
| C8: alternative allocation | Up to three full causal replays reduce optional easy work, preserving fixed decisions and every engine constraint. Ranking prioritizes accents, minimum supporting coverage, overall coverage, then stability. Exhausting the bounded search is explicitly not proof of impossibility. Strength target and the quota/method upper bound are reported separately. |
| C9: shared computation | Durable per-athlete input keys, cached identical results, renewable 90-second leases and fenced completion join UI and worker requests. Live input checks and publication CAS still run separately. One computation per process bounds memory/CPU. A dedicated planning lane and durable post-import wake-up keep imports independent. |

## Verification

- Full API suite: 1809 passing before the final additional acceptance case; CI verifies the final commit.
- Canonical suite: 786 passing (one isolated-entrypoint test required the local environment to expose its installed dependencies).
- Web suite: 495 passing; TypeScript checked. CI verifies the final UI additions.
- SQL acceptance ran successfully against staging, using synthetic fixtures inside rolled-back transactions: estimated activation/adaptation, rejected stale/unsupported inputs, duplicate publication, owner fencing, failed-task retry and expired-lease takeover.
- New private computation storage has RLS with no direct client policies/permissions; only restricted RPCs can access it.
- Controlled local API reads while the actual planner runs: p95 0.431 s / 0.668 s / 1.241 s at 1 / 10 / 25 concurrent reads. Reproduce with `python scripts/benchmark_planning_contention.py` using development dependencies. These are synthetic local measurements, not staging or production latency guarantees.

The alternative search is intentionally bounded and does not claim a globally optimal schedule. An old approved plan is not silently approved under the new dosing rules. Production deployment is outside this staging change.

## Application review follow-up

- Recovery UI describes 90% as the model's recovery reference, not an eligibility gate. Method details explain the current 60% maintenance and half-deficit developing budgets.
- Identical execution prose and lactate references appear once per applicable group. Every interval, duration, pace/HR target and distinct instruction remains visible.
- Weekly session maxima and the actual daily slot count are distinguished above the plan details. A strength target beyond the current quota/method bound is explicitly linked to profile settings. Missing coverage is not silently removed or filled by overriding the coach's calendar.
- Capacity reads share one request snapshot across sports and skip charts/functional diagnostics. A bounded five-minute cache keys the database, athlete, local day, settings, full current calendar, tests and admitted history; fresh inputs and authorization are still read on every request. Raw activity streams are never retained in that cache.
- Speed-load sampling overlaps at most two ten-activity reads, preserving activity/sample order and request context. Logs separate capacity calculation, database wait and integration time; no athlete identifiers or measurements are logged.
- Regression coverage compares full and compact model capacities for running, skiing and cycling, verifies evidence/athlete/database cache isolation, and checks bounded concurrent reads. Web verification includes interval guidance, stale responses during athlete switches and mobile calendar markup (automated DOM checks, not a physical-device run).

A private replay experiment also tried restoring easy-volume caps while keeping an alternative schedule's preferred methods. It did not improve the real-data fixture, so it was not retained. Search remains bounded to three alternatives; no added planning work is shipped without a demonstrated benefit.
