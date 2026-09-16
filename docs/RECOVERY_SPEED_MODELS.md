# Recovery v2 and speed–duration integration

## Recovery

Staging enables `ONFLOWS_RECOVERY_V2_ENABLED=true` on the API. All dashboard,
standalone recovery, training-status and planning-context reads use the same
`model_service.project_recovery` projection and one profile settings revision.
Immutable canonical load snapshots remain the source; no Intervals re-import is
needed to change recovery settings. Existing Tref, cascade, spillover and 7/40
calculations remain unchanged. The worker continues producing these inputs.
Older snapshots are readable; the v2 output has its own `recovery-history-v2`
schema and parameter fingerprint. Turning the flag off restores the v1 view.

For a zone and a completed-day history of at most 40 calendar days:

```
baseline_E_per_calendar_day = permanent_base + prior_40_day_mean_E
ratio = E_today / baseline_E_per_calendar_day
D = duration_coefficient * ratio                         # days
A = min(100, 100 * sensitivity * ratio)                   # one daily impulse
F(t) = A/2 * (exp(-rate*t) + exp(-shape*rate*t))
```

E includes the existing cascade and received spillover exactly once. Daily
resolution is explicit: same-day sessions are summed, rather than inventing
within-day timestamps. Only prior days enter the baseline, including recorded
rest days; absent rows are unknown days. The mean is not multiplied by seven
and is not clamped to expert Tref limits.

Recovery algorithm `recovery-daily-e-biexponential-v2.2` solves the positive
`rate` so `F(D)=10` when `A>=20`. With sensitivity 1,
E=60 and total baseline=20, D=3 days and readiness is 53.584%, 78.456%, 90% at days
1, 2, 3 for shape 1. Shape 1 is one exponential; shape 1–10 mixes fast and slow
recovery while preserving this isolated-dose deadline. If A≤10 the isolated
dose is already above the readiness threshold, but its residual still persists.
For small doses (A<20), the target fraction is 0.5: at least half of the
impulse decays within D, and absolute readiness may be reached earlier. In all
cases `target_fraction=min(0.5,10/A)`. This continuous decay floor avoids an
almost permanent tail when A is only infinitesimally above 10. The displayed
isolated deadline is solved against absolute F<=10, including these small doses.

All prior impulses are retained with their individual rates. Total fatigue is
their sum, without a total-fatigue ceiling. Readiness is `max(0,100-total_F)`;
time until readiness ≥90 is solved against total_F≤10, assuming no new loads.
The per-dose amplitude ceiling therefore never discards previous fatigue.
Changing configuration re-evaluates the historical projection deterministically.

The recovery section is keyed by athlete identity, not configuration revision.
Saving settings therefore preserves chart filters, the selected diagnostic zone
and expanded details. The editor refreshes its inputs on a new server revision,
keeps edits on a failed write, and only confirms applied settings when the returned
revision is present in the displayed projection. Unsaved edits are explicitly
labelled; no Intervals re-import is necessary. The common fixed chart time axis
also prevents a changed duration from being hidden by automatic rescaling.

Permanent expert addition (E minutes/day): Z1=40, Z2=20, Z3=8, Z4=4, Z5=2, STR=8.
In v2.2 this addition remains in the denominator at all history lengths. The
stored/editable `initial_daily_min` key is retained for existing profiles, but
the UI now labels it "Базова добавка, мин/ден". No settings migration or reset
is required. This uses a fixed profile addition, not the adaptive 7/40 base;
the 7/40 calculation itself remains unchanged.

With no history or a zero mean, the denominator is the addition alone; it is
never doubled. Otherwise it is exactly addition + unmodified prior mean. There
is no cold-start blending weight. Short/sparse source flags remain coverage
diagnostics only (fewer than 7 days or less than 7 base daily doses of E).
The addition never creates E, an impulse, or initial fatigue. Zero actual load
still produces zero fatigue. Historical impulses are reconstructed with the
new denominator; residual accumulation, decay shape and the 90% threshold are
unchanged, and there is no hard maximum recovery duration.

For example, a synthetic Z5 dose of 10 E minutes after a prior mean of 0.4 now
has denominator 2.4 and an isolated 90% deadline of 4.167 days at coefficient 1,
rather than a denominator of 0.4 and a deadline of 25 days. The cards show the
addition, raw personal mean and total baseline separately; covered days and
source flags remain visible. The algorithm version changes the model fingerprint
even when the profile settings revision stays the same.
Sensitivity defaults (.55,.70,.88,1,1.12,.95) are retained as expert amplitude
settings, with the new denominator explicitly versioned. Duration and shape
start at 1. The permanent addition, sensitivity, duration and shape are editable.

Fatigue before the available history is unknown. Stale source dates are shown
explicitly; projected rest after the last source day is an assumption. Wellness
diagnostics remain separate. These are expert modeling choices requiring athlete
calibration, not independently validated recovery measurements.

## Speed, distance and heart rate

The original table is preserved in `docs/speed_reference_input.json`.
The reference is a six-knot fit to the user-supplied running table (10.8–43516 s).
Distance and seconds are authoritative when the original speed column disagrees.
The reference speeds are retained from the local piecewise-power prototype and
made C1 continuous by integrating bounded log-speed derivatives. Every derivative
lies in (-1,0), ensuring decreasing speed and increasing distance on every
segment. Domain extrapolation is disabled. The same reference shape in another
sport is explicitly an expert prior, not a cross-sport validation.

One test rescales the reference; multiple tests determine the intervening shape
and preserve their measured duration/speed pairs. Inconsistent tests are rejected
or disable individual predictions; measured values are never silently altered.

Coaches/owners select a continuous maximal activity segment, attest comparability,
and supply conditions. Its time-weighted Vflat is computed on full 1 Hz derived
samples for that same segment, never chart aggregates or summed HR-zone time.
A separate losslessly stored 1 Hz Vflat series preserves its own active intervals,
grade and exclusions independently of HR. Shadow configuration v4 invalidates old
caches; refresh analyses to obtain this series. Older original-time rows are
accepted only if they independently satisfy the conservative coverage gate.
At least 98% eligible coverage is required; excluded samples and descents below
−3% are omitted and reduce coverage. Measured tests store source run, input hash,
configuration fingerprint and model versions in append-only revisions. Tests can
be disabled without deleting the activity. Active tests use a 90-day window and
are separated by sport; different Vflat model/configuration versions cannot share a curve.

Zone-volume correction uses direct equivalent time Q, separately from Recovery E:
7 × the preceding up-to-40-calendar-day mean across **all sports** for the athlete,
separately for Z1–Z5. Only completed calendar days enter this mean; the current
day and older/out-of-window activities are excluded. Rest days remain in the
calendar denominator. The direct Q values are summed once, without cascade,
received spillover, or conversion of the separate STR component into zone minutes.
Weekly ranges
(minutes) are Z1 240–840, Z2 60–300, Z3 30–120, Z4 10–40, Z5 5–30. Lower/mid/upper
give −10%/0/+10% predicted-duration adjustment; raw history is never clamped.
Only the correction is capped, and missing history is neutral. Smoothstep blends
adjacent zone corrections, including +8% to −4%. Corrections fade to zero at
measured tests and domain endpoints. The final bounded C1 curve is reconstructed;
correction strength is reduced and disclosed if required for monotonicity.

The response identifies this basis with `volume_scope=ALL_SPORTS`. The weekly
volumes and requested zone corrections therefore stay the same when selecting
another sport. Measured tests, Vflat, HR-speed summaries, and the calibrated curve
remain sport-specific; the final correction strength can differ by curve. This
shared volume basis is an explicit expert modeling assumption about cross-sport
training exposure, not a measured equal transfer of performance between sports.
It changes neither canonical Tref nor Recovery and requires no snapshot rebuild.
The UI supports older responses without `volume_scope` as same-sport volumes
during deployment, then labels the shared basis explicitly once available.

HR–speed mapping now uses admitted paired-raw-HR v3 indices from the selected
sport and current HR profile (see `TRAINABILITY_INDEX.md`). At each upper HR
boundary of Z1–Z4, candidate Vflat = (100 × HR / HRmax) / zonal TI. Invert the
already volume-adjusted personal curve to obtain candidate Tmax:

| Zone upper boundary | Allowed Tmax | Fallback midpoint |
| --- | --- | --- |
| Z1 | 2–5 hours | 3 hours 30 minutes |
| Z2 | 90–180 minutes | 135 minutes |
| Z3 | 30–80 minutes | 55 minutes |
| Z4 | 10–30 minutes | 20 minutes |

Missing/out-of-range candidates use the midpoint, then Vflat from the curve.
If independently chosen anchors violate decreasing duration, all four revert to
ordered expert midpoints and expose `CONFLICTING_ZONE_ANCHORS`. Z5 starts at the
shared upper-Z4 boundary; no independent Z5 top-duration constraint is invented.

Within a zone T(HR) = Tupper / (1 − .03 × (HRupper − HR)), using the existing
3 percentage points/bpm equivalence. For example, a Z3 upper time of 55 minutes
at 160 bpm gives 64.706 minutes at 155 bpm. Short log-linear joins at lower zone
boundaries ensure continuity between different anchor times; joins expand when
needed for monotonicity. Z5 continues T4 / (1 + .03 × (HR − HR4)). The Z1 domain
is truncated at the speed curve's maximum supported duration. Both directions
use this same map, not separately clamped answers. Metadata distinguishes expert
fallbacks from index-supported anchors. These are modeled effort guides, not
validated individual HR measurements. Without a selected calibration test the
model remains reference-only.

Volume correction positions now use the four independent expert-duration
midpoints above; Z5 continues from the shared Z4 boundary at its middle HR.
Corrections interpolate over negative log duration and still vanish at measured
tests. No predicted HR or TI is used to place volume corrections, avoiding a
circular dependency. `volume_position_basis=EXPERT_DURATION` identifies this path.

Critical speed fits `S=CS*t+D′` only to explicitly selected real tests of 2–20 min
with at least twofold duration spread. Two tests are labeled preliminary; three
or more expose distance RMSE. Nonpositive/unphysical fits are withheld. Distances
are Vflat equivalents, so CS is correspondingly an estimate for flat conditions.
Predictions support duration, equivalent distance, speed or HR as the single input.

### Test selection and preview

`/speed` explains the reference-only state and links directly to test selection.
The activity picker uses the selected source sport, newest first, with name/date
search. Opening the model from an activity preserves that activity selection.
Elapsed start/end can be entered as `minutes:seconds` or `hours:minutes:seconds`,
set with sliders, or placed on a speed chart. They include pauses in the source
record; moving time and HR-zone time are not used as interval boundaries.

The authenticated, athlete-scoped `GET /api/v2/athlete/models/speed-preview`
does not write a test or assert a maximal effort. It reports duration, eligible
coverage and (only at >=98%) Vflat speed/equivalent distance, along with excluded
descent, invalid-data and missing/paused seconds. Its display is bounded to 360
buckets; measurement and save share the full-resolution interval integrator,
including fractional samples overlapping either boundary. Missing analyses and
out-of-window activities are explicit. An arbitrary activity is never selected
as maximal automatically.

After a successful preview, saving still requires maximal/comparability
attestations and conditions. Changing the bounds invalidates the preview and
clears attestations. Save checks the preview's source run against the current
analysis and the existing test revision. The UI waits for the saved revision in
the refreshed model before reporting that the curve is updated. Prediction
range errors remain inside the page, with the submitted unit/value preserved.
The speed-duration equations, strict 98% gate and 90-day window are unchanged.

### Exploratory complex-session calibration

An explicit `test_mode=EXPLORATORY` admits >=70% eligible Vflat coverage for
provisional calibration from complex workouts with descents and stops, including
biathlon recorded under the source sport `Walk`. `STRICT` remains the default
at >=98%, with its maximal continuous-effort attestation. Source sport labels and
athlete access permissions are unchanged; this is a measurement-quality option.

Both modes keep the selected **elapsed duration** (including pauses) and use the
time-weighted mean of eligible Vflat samples. Excluded speeds never enter that
mean. `measured_duration_s`, `measured_distance_m` and the exclusion breakdown
are returned and persisted. The full-window equivalent distance projects that
eligible mean over the entire elapsed window; the exploratory UI calls it an
estimate and also shows distance integrated only over the included samples.
The 70% threshold is an experimental engineering setting, not a validated
physiological criterion. Interrupted work is not equivalent to a continuous
maximal test even when its recording coverage is high.

Exploratory saving requires a separate explicit confirmation, conditions and
comparability; it stores `maximal=false` and cannot set `use_for_cs=true`.
Any active exploratory entry labels the whole curve and its predictions as
provisional and adds `EXPLORATORY_CALIBRATION` plus `exploratory_test_count`.
Critical speed always excludes exploratory entries, even if stored data were
incorrectly marked CS-eligible. Switching mode invalidates the old preview and
attestations and requests a new measurement. The existing revision/source-run
checks apply. Entries can be disabled to restore a curve using only strict tests.

## Storage and rollout

`onflows_model_entries` contains profile-scoped RECOVERY and SPEED_TEST records.
The table has RLS, no anon/authenticated grants, and service-role-only SELECT/INSERT.
The invoker RPC verifies the actor's ownership/planning rights and uses an advisory
lock plus expected revision. Web writes require same origin, authenticated actor,
planning and recovery access. Service credentials never enter client components.

Apply migration `20260912130629_recovery_speed_models_v2.sql` before deploying the
API. The migration has been tested in staging with rollback-only writes, conflict,
idempotence and unauthorized actor checks; no verification records persisted.
Enable the API flag and deploy API, worker and web from the integration branch.
The new page is `/speed`, linked from the dashboard and each activity detail.
Recovery settings save and refresh the projection immediately.

For the v2.2 update, deploy the web first: its parser and labels support both
v2/v2.1 and v2.2 responses. Then deploy the API, which reprojects existing
canonical snapshots on read. No database migration, worker deploy or activity
re-import is needed for this update.

### Recovery chart and traceability

The overview displays all five zones and STR on the same calendar axis, from
five days before the projection date to two days after it. Solid lines join
consecutive recorded daily readiness values; missing days stay disconnected.
Dashed lines forecast no new training. Today uses the same current value as the
zone card. Individual zones can be hidden and values inspected by day/hour.
Every zone's API forecast covers at least two days, includes hourly samples and
its exact 90% crossing; a crossing beyond two days is outside the overview.

Daily rows also expose `residual_fatigue_now`, calculated from each original
impulse at the projection date. Their sum is current residual fatigue. The UI
lists the largest contributors with the effective dose, baseline at that time,
and isolated deadline counted from the dose date. In v2.2 the displayed baseline
includes the permanent addition, preventing a near-zero personal mean from
becoming a near-zero denominator. Cascade, spillover and stored loads remain
unchanged.

Validation covers isolated and accumulated recovery, tiny doses, missing/sparse
history, future-data exclusion, C1/inverse curves, incompatible tests, corrections,
CS eligibility, source immutability, source dates, segment gaps, input/auth guards,
web rendering and the existing API/web suites. A real 90-day staging source was
also projected and validated against the complete snapshot response schema.
