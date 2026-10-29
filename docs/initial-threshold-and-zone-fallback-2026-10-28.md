# Initial threshold and fallback implementation

The approved initial estimate is implemented as `heuristic_v0.1_unvalidated`:

`p = 90 * (1 + H)^(-0.04) * (v / 10)^(0.05) * (1 + S)^(-0.02)`

`v_threshold_estimated = v / (p / 100)`

H includes completed training duration from every sport, including strength.
v uses matching raw distance and moving time for the selected sport. The same
end-exclusive calendar window supplies both: up to 28 full days, at least 7,
including rest days. `initial_history_days=21` on the speed-model API requests
an explicit 21-day window. Missing experience is not treated as zero.

The speed page exposes the result as an initial model estimate.
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

## Approved initial zone policy (2026-10-29)

The user separately approved the conditional equivalence threshold≈V50.
`initial-heuristic-v50-reference-scale-v1` uniformly scales the existing
normative curve at 3000 seconds, including its existing 10-second continuation.
V6/V10 and the common zone policy remain model estimates. No synthetic tests
are created, and there is no index/test blend in this mode.

The entire initial path uses `RAW_MOVING_SPEED`: history scoring, the speed
report, component capacities, basic method prescriptions and their displayed
targets use the same coordinate. Vflat/descent completion cannot fill a missing
raw observation. Explicit flat-speed prescriptions cannot be interpreted using
a raw-speed curve. Methods requiring real maximal tests retain that requirement.

Full and compact speed views carry `INITIAL_HEURISTIC`, the threshold inputs,
the zone-policy version and `calibration_eligible=false`. Training experience
and history coverage enter context/reconstruction cache identities. Historical
estimation uses full days strictly before the activity; refresh proves date
coverage from the enumerated activities and available metadata.

Heuristic-derived Q remains usable for planning/accounting but is excluded
from measured learning/adaptation windows. Original sensor observations and
subjective reports are retained. The existing HR-only fallback remains when
there is insufficient metadata for the heuristic; dual-missing intensity
stays unknown.

## Release verification

The staging speed report's existing unavailable state was reproduced in the
browser before deployment. A regression reproduces and fixes mixed-parameter
comparison aggregation. Live verification of the changed version is pending
staging deployment; local test results do not establish live behavior.
