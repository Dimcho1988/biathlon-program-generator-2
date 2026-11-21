# Planning across a calendar-day change

## Reproduced staging problem

On 2026-10-08 an authorized athlete view had an active analysis through
2026-10-07, 40 completed days per component, supported partial-HR estimates,
and a saved draft starting 2026-10-08. The weekly page hid the stale draft and
required a new import. The live outlook rendered its periods but aerobic Q
targets were missing. This was a day-boundary eligibility error, not evidence
that several days without a workout should stop training preparation.

Three different checks required today's analysis: frontend guidance, weekly
readiness support, and the live outlook. Day-start readiness uses completed
calendar days; today's unfinished day is not a historical coverage gap.

## Changes

- Share a backend completed-day lag check: coverage through yesterday is
  current; older snapshots still require an import of the uncovered days.
- Apply the same cutoff to frontend guidance. Recalculate an outdated draft
  before applying its old input warnings to the current analysis.
- Include the inclusive snapshot end date in the component coverage check.
  Missing entries on the final completed day cannot grant known readiness.
- Version the eligibility change so previous saved drafts are recalculated.
  Existing approved plans retain their lifecycle approval checks.

No physiological formulas, dosing parameters, component limits, methodology,
athlete settings, access rules, or stored historical loads change. No database
migration or historical rewrite is required. The API and adaptation worker
must use the same engine version.

## Validation

New synthetic regressions reproduce the original frontend block, weekly
limited-plan result, missing outlook Q targets, and skipped final-day coverage.
Three- and seven-day confirmed pauses retain actionable plan proposals; the
same day-start inputs give the same first-day task, and repeated generation is
deterministic. Partial HR retains exactly the same long-term targets when only
the open day's zero rows are removed. Component budgets remain enforced.

Removing today's measured zero row is a change in input: subsequent rolling
denominators can differ. No measured zero is fabricated to force later forecast
parity. Forecasts remain conditional on actual training being imported.

Real staging verification is performed after API, worker and web deployment;
the pull request records the deployment SHA and check results.

## Scope and remaining constraints

Confirmed days without training are different from days with unknown provider
coverage. The latter still require import; they are not silently assumed to be
rest. The existing configurable long-break/re-entry policy, illness/pain
review, current-sport exposure, capacity support, recovery and component gates
remain in force. This fix restores daily adaptation after short confirmed
pauses; it does not claim a globally optimal schedule or validated capacity for
100 simultaneous users.
