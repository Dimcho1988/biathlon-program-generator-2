# Weekly developmental frequency and component coverage

The planner retains a 100% component objective and the existing 90–110%
acceptance interval. Actual execution and remaining prescriptions are assessed
together in the same complete, anchored microcycle. Excess in one component
does not compensate for a different component's deficit.

## Coach frequency policy

- BUILD and STRESS microcycles request at least two developmental sessions and
  aim for the configured `max_key_sessions_per_week` count, with two as the
  minimum desired count. The existing high-zone key-session ceiling remains
  independent: aerobic or strength development may satisfy this objective.
- RECOVERY and MAINTAIN keep maintenance roles. Recovery retains its existing
  complementary focus on a component outside the preceding loading emphasis.
- Limited history, re-entry, taper and genuinely truncated/mixed cycles do not
  compress a full weekly developmental requirement into a few remaining days.
- A session counts only when its resolved building work exceeds that method's
  maintenance dose. Warm-up, spillover and a BUILDING label alone cannot count.
  A combined session counts once; an explicit double-threshold pair contains
  two separate sessions. Confirmed completed methods count; unknown execution
  roles are disclosed rather than inferred from brief high-zone exposure.

This is a coach allocation policy, not a physiological dose limit. All original
method, whole-repetition, readiness, time, Q/E and shared daily intensity checks
remain mandatory. The developmental core is protected from optional duration
balancing only within an already allowable candidate dose.

## Allocation and diagnostics

Developmental candidates take priority while the complete loading cycle still
needs them. Dose sharing preserves feasible whole developmental doses instead
of replacing them with several small maintenance doses. Search compares
component-range gaps first, then developmental minimum/desired shortages, then
distance from 100%. It also runs when coverage is acceptable but frequency is
unmet. Exhausted computation remains an unfinished search, not proof that the
target is physiologically impossible.

The report exposes `allocation.development`, including actual/planned counts,
unknown actual roles, minimum/desired shortages and observed constraints.
The web summary shows these for the same complete cycles as component coverage.
The report does not block a safe incomplete plan or claim that all targets were
achieved when its component or frequency requirements remain unmet.

Automatic strength targets are excluded when strength training is disabled,
after progression/recovery have resolved the goals. History and actual work
remain visible. A conflicting positive manual strength target is preserved and
reported explicitly; it is never silently zeroed to manufacture full coverage.

## Verification scope

Regression scenarios cover feasible two/three developmental occasions, a
previously fragmented aerobic dose, maintenance/recovery roles and complementary
focus, completed/locked sessions, double threshold, partial cycles, inactive
strength and explicit target conflicts. Comparisons use frozen synthetic input
histories with the real component and daily dose checks. Such comparisons do
not establish live-athlete coverage or production latency; deployment checks
must inspect the regenerated aggregate plan results separately.
