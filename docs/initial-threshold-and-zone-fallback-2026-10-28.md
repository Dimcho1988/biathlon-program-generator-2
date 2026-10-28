# Initial threshold and fallback implementation

The approved initial estimate is implemented as `heuristic_v0.1_unvalidated`:

`p = 90 * (1 + H)^(-0.04) * (v / 10)^(0.05) * (1 + S)^(-0.02)`

`v_threshold_estimated = v / (p / 100)`

H includes completed training duration from every sport, including strength.
v uses matching raw distance and moving time for the selected sport. The same
end-exclusive calendar window supplies both: up to 28 full days, at least 7,
including rest days. `initial_history_days=21` on the speed-model API requests
an explicit 21-day window. Missing experience is not treated as zero.

The speed page exposes the result as an initial threshold estimate only.
Available individual tests or a valid paired profile supersede it. Missing
HR does not invalidate complete activity metadata. Estimates never become
test records, observed indices, calibration targets or validation truth.

## Zone and load behavior

- Valid observed zonal boundaries retain priority, followed by valid GENERAL
  paired boundaries. Conflicting paired boundaries can fall back to a curve
  rebuilt exclusively from accepted real maximal tests, without an index prior.
- Existing V50/V6/HR rules determine the test-derived zone edges. The existing
  30/70 capacity curve remains distinct from the provenance of zone boundaries.
- A real maximal test can inform the preceding 14 calendar days. Its recorded
  date is unchanged. HR/index evidence still ends before the activity date.
  No test later than the evaluation date is admitted. This is retrospective
  accounting, not a causal backtest of what was known on the activity date.
- The speed-only comparison panel consistently re-evaluates all its activities
  against the current sport profile. Direct historical load retains its
  applicable per-day calibration. The distinction is shown in the interface.
- Incomplete Recovery history withholds readiness percentages and deadlines.
  Recorded load and baseline diagnostics remain visible as partial information.

## Unresolved zone policy

The approved threshold formula provides neither a duration anchor nor V6/V10.
No equivalence to V50 has been implemented. Scaling the reference curve at
50 minutes would require a separate explicit modeling decision, along with a
raw-speed versus Vflat policy. Until then, the threshold is informative and
does not create a complete speed prescription. The existing HR-only fallback
remains available when HR data and an HRmax profile exist; dual-missing
intensity stays unknown.

## Release verification

The staging speed report's existing unavailable state was reproduced in the
browser before deployment. A regression reproduces and fixes mixed-parameter
comparison aggregation. Live verification of the changed version is pending
staging deployment; local test results do not establish live behavior.
