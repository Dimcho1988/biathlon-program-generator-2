# Integrated race and load planning

The v24 planner uses one individual curve for race references, standardized CS,
speed boundaries and supported interval capacity. This does not change the
normative curve or the exact-anchor / 5% continuation contract.

## Measurements and estimates

- CS at 3 and 12 minutes requires an admitted maximal 2–5-minute test and an
  8–20-minute test. Both coordinates come from the existing curve, retaining
  every real anchor. They are labelled measured, interpolated or extrapolated;
  they never enter the test ledger. Direct test regression remains visible.
- Speed exposure is a separate, generation-pinned 40-day view. It uses the
  sport-specific independent Vflat samples and terrain/quality masks, without
  requiring HR. It does not add another copy of load to measured HR Q/E.
- Missing-HR intervals can use supported individual speed-zone boundaries.
  Converted HR and Q remain estimates. Uncovered intervals retain the existing
  duration-based expert fallback. Observed HR samples are never replaced.
- Progression references use measured Q only. At least 90% duration-weighted
  measured HR coverage permits a complete historical window to support the
  reference. This is an explicit coaching data-quality policy, not a claim that
  the missing portion has been measured. Minor old gaps no longer invalidate
  the entire current history. Poorly covered history retains its time envelope.

## Race methods and lactate

For a supported event distance, invert the curve for race duration. Reference
speeds at 1.25, 1 and 0.8 times that duration provide below-race, race and
above-race choices. These are versioned coaching choices, not metabolic
thresholds. Repeat duration, number, rests and reserve are prescribed separately
and pass the existing dose, Q/E, 7/40, Recovery and availability checks.
Automatic intervals and explicit individual interval profiles retain control.

Personal lactate tests may contain a complete ascending speed axis, HR axis or
both. Speed/lactate interpolation is confined to the recorded protocol range,
sport and assessment date. It is not an estimate of blood lactate after the
planned repetitions. The interface shows absent support when there is no
matching test; it never derives mmol/L from the speed–duration curve.

## Coherent objectives

Desired direct Q, reconciled direct Q and scheduled Q are separate quantities.
Project desired Q through canonical cascade/spill to establish a compatible E
budget. Automatic budgets remain within the 7/40 ceiling of 2; explicit manual
E ceilings and unloading budgets remain unchanged. If the complete Q vector
does not fit, scale it down together and retain the desired target for review.
The uniform-week projection cannot authorize a session: each actual composition
still passes its dose-dependent canonical calculation and daily checks.

The weekly plan and long-term outlook call the same reconciliation. An unmet
key slot is reconsidered on future eligible dates using updated Recovery and
rolling budgets. The algorithm does not promise full coverage or force missed
work into later days. Legacy drafts remain frozen until regenerated.

Regression gates cover exact and generated anchors, three-point calibration,
independent exposure, optional-HR lactate tests, race methods, manual ceilings,
unloading, partial history and deferred key work. Repository API, canonical and
web CI suites remain the release gates.
