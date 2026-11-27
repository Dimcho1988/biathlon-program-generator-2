# Recovery v2.4: capacity and direct Q

Z1–Z5 initial fatigue is `100 * min(1, sum(dt / Tmax(intensity)))`.
The existing unified recording integrator chooses HRmod/raw HR or speed once
per supported interval, retaining the existing pause, missing-data and sport
coordinate policies. HR uses its relative position between the same common
speed boundaries. Z5 HR mapping remains an accounting estimate. This is an
expert model, not a measurement of physiological reserves.

A 512-interval logarithmic inverse lookup, including the curve's own knots,
is prepared once from the selected continuous dosing curve. It does not refit
the curve. Segment lookup uses binary search and interpolation; it does not
run an inverse solver for every second. Out-of-domain or unavailable curves
use explicitly marked reference-capacity estimates. This version ignores
within-session reconstitution. Missing recordings remain covered by the
existing availability/estimation policy; missing days are not rest days.

The causal nominal recovery duration is:

`D = daily_Q / mean_prior_daily_Q * k(u) * duration_coefficient`

`k(u) = 0.8 + 0.6 * clamp(u - 0.5, 0, 0.5)`

The mean uses the preceding up to 40 covered calendar days, including actual
zero days. Positive personal Q means have no additive expert floor. The saved
initial daily setting is used only with no Q history or an exactly zero mean.
Legacy E history is not mixed into a Q denominator. The complete Q is retained
above u=1, while one daily impulse is capped at 100 percentage points.
Separate days' residual fatigue adds without an upper cap; readiness is
clamped at zero for display. Sessions within a calendar day share one dose.

The existing duration-dependent biexponential shape is frozen per impulse.
For amplitudes >=20, D is the isolated time to absolute 90% readiness; smaller
impulses retain the existing half-life floor and can already be above 90%.
A subsequent workout adds to the residual of prior doses. It does not reset
those doses or change their original decay rates. Q20/u=2/3 with meanQ5 gives
D3.6 days; Q30/u=1 gives D6.6 days; Q40/u=4/3 gives D8.8 days.

STR and legacy imports with no direct-Q activity evidence retain the E-based
model and sensitivity setting. Aerobic capacity impulses do not use the old
sensitivity multiplier. Q, E, E transfers and 7/40 accounting are unchanged.
Recovery does not count E cascade again as capacity exhaustion.

The dashboard and planner use the same daily projection and simulator.
Candidate E vectors have an internal Q/exhaustion sidecar, preserved explicitly
through copies, session addition and frozen reservation-cache keys. Serialized
public E vectors remain numeric accounting vectors. Existing saved plans are
not rewritten. Versioned calculation identities invalidate old derived caches;
normalized recordings are reused without provider downloads.

Daily provenance includes direct Q, raw exhaustion fraction, multiplier,
reference-estimate flag, baseline and recovery basis. The UI caps displayed
exhaustion at 100%; the unbounded fraction remains available in provenance.

Validation includes the approved numerical examples, initial/zero baselines,
residual accumulation, inverse-table accuracy, actual/planned segment equality,
unchanged Q, multiple sessions, cache isolation and frontend contract validation.
