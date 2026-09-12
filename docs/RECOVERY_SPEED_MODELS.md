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

The positive `rate` is solved so `F(D)=10` when `A>10`. With sensitivity 1,
E=60 and baseline=20, D=3 days and readiness is 53.584%, 78.456%, 90% at days
1, 2, 3 for shape 1. Shape 1 is one exponential; shape 1–10 mixes fast and slow
recovery while preserving this isolated-dose deadline. If A≤10 the isolated
dose is already above the readiness threshold, but its residual still persists.
For these small doses D is the 90%-reduction time of that impulse.

All prior impulses are retained with their individual rates. Total fatigue is
their sum, without a total-fatigue ceiling. Readiness is `max(0,100-total_F)`;
time until readiness ≥90 is solved against total_F≤10, assuming no new loads.
The per-dose amplitude ceiling therefore never discards previous fatigue.
Changing configuration re-evaluates the historical projection deterministically.

Cold-start expert prior (E minutes/day): Z1=40, Z2=20, Z3=8, Z4=4, Z5=2, STR=8.
The blending weight is `min(1, covered_days/7, sum_E/(7*initial_daily))`.
Zero history retains the prior; sparse positive history approaches it continuously.
The raw personal mean, used baseline, covered days and source flag remain visible.
Sensitivity defaults (.55,.70,.88,1,1.12,.95) are retained as expert amplitude
settings, with the new denominator explicitly versioned. Duration and shape
start at 1. The initial prior, sensitivity, duration and shape are editable.

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
7 × the preceding up-to-40-calendar-day mean for the same sport. Weekly ranges
(minutes) are Z1 240–840, Z2 60–300, Z3 30–120, Z4 10–40, Z5 5–30. Lower/mid/upper
give −10%/0/+10% predicted-duration adjustment; raw history is never clamped.
Only the correction is capped, and missing history is neutral. Smoothstep blends
adjacent zone corrections, including +8% to −4%. Corrections fade to zero at
measured tests and domain endpoints. The final bounded C1 curve is reconstructed;
correction strength is reduced and disclosed if required for monotonicity.

HR-speed mapping uses weighted valid zone summaries from the existing rank-based
trainability index, with current HRmax/zones and same sport. It is an inferred
mapping, not a validated paired HR–speed regression. HR predictions are returned
only within the observed speed range, and not prescribed for short maximal work.
The mapping permits zone-based volume adjustment; absent data leaves it neutral.

Critical speed fits `S=CS*t+D′` only to explicitly selected real tests of 2–20 min
with at least twofold duration spread. Two tests are labeled preliminary; three
or more expose distance RMSE. Nonpositive/unphysical fits are withheld. Distances
are Vflat equivalents, so CS is correspondingly an estimate for flat conditions.
Predictions support duration, equivalent distance or speed as the single input.

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

Validation covers isolated and accumulated recovery, tiny doses, missing/sparse
history, future-data exclusion, C1/inverse curves, incompatible tests, corrections,
CS eligibility, source immutability, source dates, segment gaps, input/auth guards,
web rendering and the existing API/web suites. A real 90-day staging source was
also projected and validated against the complete snapshot response schema.
