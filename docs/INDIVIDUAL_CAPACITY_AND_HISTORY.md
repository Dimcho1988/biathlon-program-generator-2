# Individual capacity and incomplete-HR planning

This change connects the speed–duration model, expert duration estimates,
planning history and athlete profile. The canonical normative curve is unchanged.
The numeric two-test contract is in `SPEED_DURATION_5PCT.md`.

## Evidence and order of operations

1. Keep recorded activity duration, measured HR-zone Q and model estimates distinct.
2. Position each zone within its existing expert **maximum-duration** bounds using
   complete measured zone Q. If zonal evidence is incomplete, use actual total
   recorded duration with the explicit existing 4–20-hour positioning rule.
   This is a coaching estimate, not a measured zonal distribution.
3. Only compatible, admitted paired raw-HR/Vflat indices provide absolute speed
   for preliminary zone anchors. Missing absolute-speed evidence leaves a
   duration-only estimate; normative speeds are not shown as athlete abilities.
4. Explicit maximal, comparable tests calibrate the curve. Ordinary/exploratory
   activities and generated points are never hard anchors or CS measurements.
5. Use the same serialized preliminary model and calibration in the chart and
   planner. No subsequent volume warp is applied to calibrated results.

Weekly Q is not a maximum duration. Maximum duration is not session dose. Dose
still passes method-specific minimum/maximum, Q, 7/40, Recovery, actual weekly
time availability and user settings. Recovery methods use their own absolute
minimum and retain the existing 30-minute ceiling.

## Curve modes

| Accepted maximal tests | Mode |
| --- | --- |
| 0 | Estimated prior when paired speed evidence exists; otherwise no speed prediction |
| 1 | Scale the canonical normative shape through the exact test; preliminary shape remains diagnostic only |
| 2 | Versioned log-quadratic fit with independent outward correction, smooth 5% cap and no reversal |
| More than 2 | C1 interpolation through every accepted test; continue its terminal log-quadratic pieces with independent, smooth 5% caps and no reversal |

Invalid or incompatible accepted tests remain visible with a diagnostic. They
are not silently discarded in favor of a reference curve. Predictions outside
10.8–43,516 seconds remain unsupported. Real tests are included as exact chart
points. The five-percent corridor is a modelling constraint, not an error bar
or a physiological ceiling.

Disagreement between the preliminary curve and real tests is exposed as signed
residuals. Automatic reliability-weighted averaging is **not enabled**: paired
sample counts/seconds are not calibrated uncertainty estimates. The real test
anchors take precedence. An averaging mode needs an explicit weighting contract
and its own numerical validation; it must not silently change the verified
two-test algorithm.

## Missing HR

An isolated, transient planning ledger fills only the uncovered part of an
identifiable recording with known duration. Compatible independent Vflat samples
can contribute speed-zone evidence when the paired-index map is supported;
remaining minutes use existing expert Q proportions. Their HR correspondence
and load remain estimated. Inferred HR never becomes a paired TI observation.

Canonical history remains unchanged. Unknown provider recordings, missing
duration and calendar gaps remain blockers. Estimated E reserves Recovery load;
it is not substituted into measured learning or accepted exposure for advanced
methods. An otherwise complete plan can be activated with explicit estimated
readiness provenance. The ordinary coach review still applies. Old active plans
see changed rule versions and require review under the existing lifecycle.

When load history is estimated and the user has not explicitly chosen a weekly
time target, the initial plan uses recorded historical weekly time as its time
envelope. Expert zone minimum references cannot automatically double total
training time. This provisional planning limit is not a permanent capacity or
performance ceiling; explicit settings and better evidence remain distinct.

## Athlete profile

Overall level is separated from shape against the unchanged normative curve.
Only accepted real tests establish the measured short-versus-long contrast.
The comparison is limited to their duration window; fixed-duration extrapolated
points and capped tails cannot establish a measured strength or weakness.
One test establishes level but not empirical shape. Zone exposure is contextual
evidence, not proof of causation, genotype, fibre composition or a performance
ceiling. Repeated comparable tests are required to assess training response.

## Automatic HRmax zones

`ONFLOWS_HRMAX_ZONE_PERCENTAGES` supplies six expert percentage boundaries, with
the last equal to 100. No percentage preset or HRmax-from-age formula is invented.
When configured, new profiles can generate zones from known HRmax. Existing
manual zones remain manual; automatic profiles retain the exact saved scheme.

Apply `20260930045944_athlete_hr_zone_provenance.sql` before deploying the API.
Without an expert scheme, manual setup remains available and automatic mode is
not advertised as configured.

## Periodization

The existing algorithm already clips preparation from the front when an actual
main race is near. Program end is not silently converted to a race. The interface
explains the missing main race and links to its calendar entry.

## Verification

The tests cover exact two-test controls; positive decreasing speed and increasing
distance between interpolation nodes; C1 joins; both correction directions;
neutral tails; inverse functions; domain rejection; one-test shape preservation;
all multipoint anchors; missing-HR provenance; lifecycle eligibility; no repeated
estimation; no generated observations; settings migration and UI empty/conflict
states. Real athlete replays are kept outside the repository.
