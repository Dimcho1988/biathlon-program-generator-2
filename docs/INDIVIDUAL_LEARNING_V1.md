# Individual learning v1 — staging pilot

Version: `individual-response-bayes-v2`. This is an observational individual
response model with a bounded planning policy. Its coefficients, thresholds and
utility weights are explicit pilot settings, not validated physiological limits
or proof that a recommended intervention causes improvement.

## Architecture and activation

| Module | Responsibility |
|---|---|
| `apps/api/learning_evidence.py` | Completed exposure/response episodes, comparable outcomes, provenance and exclusions |
| `biathlon/individual_learning.py` | Bayesian fitting, chronological validation and one component decision |
| `apps/api/learning_service.py` | Pinned input generation, local support, bounded replay archive and current safeguards |
| `apps/api/learning_methods.py` | Confirmed method completion/tolerability, independently of training efficacy |
| `biathlon/load_progression.py`, `apps/api/training_plan_engine.py` | Apply permitted changes inside the existing dose, calendar and Recovery constraints |
| `apps/api/management_projection.py` | Public summaries without private replay evidence |

The existing management worker and approval lifecycle persist immutable plans;
there is no additional worker, queue, external AI call or pooled athlete model.
The outlook computes a report without publishing; the weekly page may request
an automatic refresh through the existing approval lifecycle. Historical evidence
initializes associations; later executed loads and independent observations
update them. Forecast loads, Recovery predictions, planned completion and prior
model recommendations are never outcome labels.

`individual_learning` in the management profile:

| Setting | Default | Allowed range / meaning |
|---|---:|---|
| `mode` | `SHADOW` | `OFF`, `SHADOW`, `CONTROL` |
| `exploration_enabled` | `true` | Permit explicitly labeled small volume probes |
| `max_volume_step_percent` | `5` | 0–10%; cap for a learned component change |
| `max_intensity_step` | `0.02` | 0–0.05 of within-zone target position, not a speed percentage |

Learning also requires enabled load progression, feedback and planning controls.
Old profiles default to SHADOW. SHADOW computes proposals without changing
sessions or readiness; OFF supplies no learner. CONTROL replaces the legacy
block-outcome dose corrections while retaining its symptom context. Automatic
publication still requires the existing approved AUTO management plan; REVIEW
plans remain proposals. Changing approved rules requires renewed approval.

## Evidence and missingness

- Non-overlapping 14-day episodes use seven exposure days and seven follow-up
  days, with the preceding seven days as dose/response baseline. The incomplete
  current day is never a training label. The calendar grid starts 2020-01-06.
- Direct Q and measured minutes describe exposure separately from canonical E.
  Let `dQ = log((Q_exposure+1)/(Q_baseline+1))` and
  `dT = log((minutes_exposure+1)/(minutes_baseline+1))`.
  Features are `dQ/log(1.05)` for Z1–Z5/STR and `(dQ-dT)/log(1.02)`
  for Z1–Z5. The offsets are fixed numerical scales of one equivalent minute
  and one clock minute, not added training or an inferred effort at zero.
  At substantial exposure the second coordinate approaches a log Q/time ratio.
  Known starts/stops and near-zero doses are finite and retain both Q and time
  as covariates, preventing their effects being silently attributed elsewhere.
  Candidate contrasts use these identical coordinates: volume scales Q/time
  together; within-zone intensity holds Q and recomputes time. E remains context.
  Missing days are not rest days. All component Q/time/E windows must be known;
  missing exposure and inconsistent Q/time remain excluded. Whole-week total Q
  ratios outside 0.25–4, or a wholly inactive baseline/exposure week, are excluded
  as nonlocal transitions. A sparse component ratio alone no longer excludes
  every other component. The model/evidence versions invalidate v1 archives.
- Response uses the same available **nonfunctional** channels before and after
  exposure: at least 20% nominal stress weight, three complete baseline days and
  four complete follow-up days. Functional TI/pace/RPE/test channels cannot
  manufacture an independent recovery label. RHR, HRV and sleep references
  are frozen for the episode. Missing channels are not filled with zero.
- Burden is the observed episode mean minus the baseline median, in stress
  points. Recovery requires two consecutive observed follow-up days no more
  than five points above that baseline; later deterioration cancels the return.
  No observed return produces `recovery_days=null`, not zero.
- Performance requires later observations **after** the observed return:
  accepted, same-sport/comparison-key TI with at least two distinct days per
  side, or explicitly comparable tests with matching protocol/version/unit/
  direction/conditions. One-component tests retain that scope; multi-component
  tests are GLOBAL. Conflicting meaningful outcome directions are excluded.
- Illness/pain, laboratory reference flags, altered execution context, camps,
  races, unavailability, taper, re-entry, transition and competition phases exclude affected
  episodes. Revisions rebuild recent evidence; changed evidence receives its
  new availability date. Identical re-imports preserve original availability.

A single episode contributes at most one weighted performance label per scope;
multiple protocols do not create independent repetitions. TI has weight 0.65,
manual tests 1.0, combined with coverage/completeness quality. Observational
confounding, delayed effects beyond the window and imperfect measurements remain
limitations; the model does not estimate a physiological glycogen quantity.

## Posterior and validation

There are 12 coefficients: an intercept, six Q-change features and five
within-zone intensity-change features. Independent zero-mean Gaussian priors
initialize a full-covariance linear posterior. Observation precision is
`quality × 2^(-age_days/180) / noise²`.

| Outcome | Noise SD | Slope prior SD | Intercept prior SD |
|---|---:|---:|---:|
| Performance change, % | 2 | 2 | 5 |
| Burden change, stress points | 10 | 12 | 20 |
| Observed recovery, days | 2 | 2 | 4 |

Intervals use mean ±1.96 SD. Outcome prediction includes observation noise;
dose contrasts use coefficient uncertainty only. Correlated changes retain
covariance uncertainty. The recovery regression describes observed returns,
not a survival model: unresolved returns are guarded separately.

Validation uses expanding chronological folds with at least four earlier
samples. Earlier labels must have ended **before the held-out episode starts**
and been available by that start. Backfilled outcomes cannot validate decisions
predating their availability. PASSED requires at least four evaluated folds,
MAE no greater than 95% of the better of zero-change and prior weighted-mean
baselines, and at least 75% empirical coverage of the nominal 95% intervals.
This is an outcome-prediction check, not prospective policy/causal validation or
a calibrated probability of benefit. WARMUP and FAILED are distinct states.

## Support and decision safeguards

Fitting uses locally comparable episodes: total current 14-day weekly Q must be
0.67–1.5 times the prior baseline total. Components larger than 5% of the smaller
week's total use the same 0.67–1.5 band on `(current_Q+1)/(baseline_Q+1)`.
For two small component doses, known zero is admitted without a ratio gate;
their observed differences still enter the joint model. This 5% is a local
support setting, independent of the 5% maximum proposed volume step. Missing
component Q is never interpreted as zero;
non-strength Q/min must be within 0.85–1.15 when defined. Where a matching current
GLOBAL TI is observed, its baseline ratio must also be 0.85–1.15. Missing current
TI does not invent a fitness match. Unsupported episodes remain archived but do
not train the current fit.

Current data must be no older than two days, cover at least 40% nominal stress
weight and two families, and have complete matching actual load history through
the required cutoff. Illness/pain, unresolved laboratory findings, altered
execution, stress ≥75 or a supported unrecovered episode within 42 days suppress
learner actions. Laboratory review is a separate dated flag: an exact newer
comparable result within provided limits can resolve its own finding; mere
absence or age never converts it into recovery.

Positive changes additionally require two consecutive fresh days on the same
response panel within baseline +5; the latest day must be today or yesterday,
with a sufficiently recent completed reference episode. This observed check is
separate from the existing per-session Recovery ≥90% gate.

New decisions are restricted to automatic BUILD/MAINTAIN accents during general
or special preparation. Manual targets, explicit cycle overrides, taper,
missing exposure and other periods prevent a new experiment. A learned change
needs PASSED performance validation, at least eight observations and two
materially isolated feature changes. The preferred validated scope is the
component, otherwise GLOBAL. Candidate utility is:

`performance lower bound − 0.04 × max(0, burden upper bound) − 0.15 × max(0, recovery-delay upper bound)`.

It must exceed 0.05. Positive learned candidates also limit predicted baseline
burden plus contrast to 10 points and recovery to seven days. Both increases and
decreases must show support; less load is not assumed better. These utility
constants and screens are pilot settings.

If no learned candidate qualifies, two non-confounded recovered episodes with
independent outcomes, current recovery, enabled exploration and no failed GLOBAL
validation permit a **volume-only probe up to +2.5%**. It has LOW confidence and
is not presented as an optimum. One component/dimension is retained for a
14-day observation window. Refresh does not compound its multiplier or change
its identity; current safeguards can cancel it. Retained learned choices
recheck validation/uncertainty, and CONTROL accent/config changes do not
immediately open another experiment during the retained window.

Q corrections apply once against the existing reference. They do not enlarge
the independent E/7–40 gate. Strength retains its own E budget. Intensity changes
are limited to supported pulse-based Z1–Z3 methods; position is changed before
recomputing HR and full capacity. Composite, paired and effort-led structures
remain intact. Session limits, available time, periodization, manual decisions,
minimum doses, expert ceilings and Recovery still decide the actual prescription.
The UI therefore says **permitted in the plan**, not necessarily executed.

## Method tolerability

Only athlete-confirmed method identity and comparable execution can teach this
separate model. The server freezes the catalog definition; exact activity
identity, sport, duration, RPE/timing and execution reason must agree. Success
requires AS_PLANNED with at least 95% planned duration and, when known, speed;
failure requires FATIGUE with duration below 90% or speed below 95%. High RPE
alone is not failure.

Beta(2,2) priors compare method and alternative cohorts at the same sport, zone,
purpose, catalog version and RPE timing: planned duration ±10%, target position
within 0.10, and known target speed ±5%. Preferences require at least four
observations in each cohort, 95% interval widths ≤0.45 and separated intervals.
The bounded ranking adjustment is at most ±0.25. It estimates dose completion,
not training efficacy. A probe cannot simultaneously introduce a learned method
preference. Final candidate matching still checks definition, sport, purpose,
duration and speed.

## Persistence, privacy and rollout

The replay window keeps up to 78 episodes, further capped at 64 KiB, and method
observations up to 1,000 / 730 days, further capped at 64 KiB. Newest complete
records are retained deterministically; model fitting uses that same saved
window. Dropped records remain in older immutable revisions, not in the active
fit. Full evidence appears once per internal generated plan; duplicate outlook
and progression views contain summaries. One-year plan size is regression-tested
against the existing 1 MiB draft and 2 MiB active-revision limits.

Management HTTP responses and nested export/diagnostic copies use an allowlist
projection. Replay memory, observations, current laboratory/recovery details,
exclusion records, internal source identities and response revision dates are
not transported to plan viewers. Existing recovery permissions govern the
separate stress page. Server-only storage grants and RLS remain unchanged.

Apply `20260927134122_management_response_checkpoint.sql` **before** deploying
the API/worker. The checkpoint adds sorted latest `{kind, entry_key, revision}`
response identities. Response writes and management publication share the
per-athlete advisory lock; a new response invalidates a pending plan. Draft
saves pass optional `p_expected_responses` and return INPUTS_CHANGED on mismatch.
Legacy calls remain valid; the old overload is removed to avoid ambiguous RPC
resolution. Generation runs outside database locks.

The new rule version/default profile can make existing active plans stale and
require review of changed rules on their next refresh. Deployment does not
silently opt anyone into CONTROL. Rollout verification should use staging;
application rollback can retain this backward-compatible database migration.

Measured scratch CPU checks (five runs, no database/network): 78 synthetic
all-scope episodes fitted in a median 184.56 ms cold / 4.85 ms cached. A sparse
planner fixture measured OFF 2,019.92 ms, SHADOW 2,209.81 ms and CONTROL 2,148.39 ms.
These are local regression observations, not production latency or capacity
claims. Release verification before staging deployment passed 848 API tests,
304 frontend tests and the frontend production build.

## Reproducible checks

From repository root:

```bash
.venv/bin/python -m scripts.generate_learning_examples
.venv/bin/python -m pytest tests/api/test_individual_learning.py tests/api/test_learning_evidence.py tests/api/test_learning_service.py tests/api/test_learning_methods.py tests/api/test_individual_learning_integration.py tests/api/test_management_projection.py -q
```

The generator creates six read-only scenarios from the actual estimator in
`apps/web/lib/learning-examples.json`: missing data, small probe, supported
increase, supported decrease, new caution signal and SHADOW. They are synthetic
and are not inserted into athlete history. Example: a CONTROL Z1 +5% proposal
means a factor of 1.05 against the current baseline, once; SHADOW shows the same
proposal with applied factor 1.0.

Frontend (from `apps/web`):

```bash
npm test -- test/individual-learning.test.tsx test/individual-learning-examples.test.tsx test/learning-method-interactions.test.tsx test/management-interactions.test.tsx
npm run type-check
npm run lint
```

Database CI resets all migrations and runs
`tests/database/management_response_checkpoint.sql` with `psql` and
`ON_ERROR_STOP=1`. The SQL fixture rolls back and checks grants, RLS, actual held
publication lock, current/stale checkpoints, legacy signatures and idempotency.
No synthetic fixture should be used as evidence that real athletes benefit.
