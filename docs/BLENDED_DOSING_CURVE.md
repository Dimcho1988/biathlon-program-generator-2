# Separate 30/70 dosing curve

The coach-requested policy adds a third curve. The paired-index/history model
and exact maximal-test curve remain unchanged, including test entries, test
forecasts, standardized/direct CS and race-performance estimates.

For every common duration in seconds:

`v_dose(t) = 0.3 * v_index(t) + 0.7 * v_test(t)`.

Speeds are averaged in the same units, at the same duration, for the same sport
and currently admitted analysis inputs. We do not average pace, independently
average the two inverse times, refit plotting samples, or treat generated
points as measured tests. Both component curves must be available. Missing,
conflicting, incompatible or out-of-window index evidence cannot be assigned
a share of a fabricated speed. With one source absent, existing fallback policy
continues and the separate dosing model reports unavailable.

The mean is evaluated directly on the intersection of the component domains.
Its speed decreases and its distance increases because both components do;
its log slope is their speed-weighted log-slope mean, preserving the existing
physical bounds and smoothness. Speed and distance use numerical inverses of
this exact mean. The test curve's 5% continuation rule stays on that component;
the mean has no new claim of 5% prediction accuracy.

## HR and method prescription

HR uses the index model's existing duration coordinates and expert/history
anchors, composed with the blended speed. Reinverting raw index speed against
the mean would undo the blend, so it is deliberately not done. HR remains an
estimate; short efforts outside the existing HR-duration domain have no HR
prediction. Z5 intervals retain speed/effort control and the existing recent
maximal-test gate.

Continuous and automatic interval methods use the shared speed and capacity.
For a fixed coach-entered speed, capacity is obtained from the blended inverse
within the existing supported test window; the explicit target is preserved.
Race methods evaluate the blend at the original below/race/above duration
references, exposing their new working speeds separately as `dosing_bands`.
Original race predictions and reference bands remain intact. Personal lactate
protocols, when present, are evaluated at the prescribed speed, never inferred
from the blend itself.

Extrapolated/low-sample dosing remains a coaching estimate and requires the
existing expert-fallback permission. The 14-day freshness policy for continuous
HR prescription remains in force. Missing-HR reconstruction and historical
speed exposure still use their original models, avoiding feedback from the
dosing policy into measured training history. Q/E, 7/40, Recovery, availability,
method limits, reserve and manual ceilings still constrain every prescription.

The 30/70 weights are an explicit coaching policy, not error-variance estimates or
scientifically established optimal weights. Counts/seconds describe support;
they are not statistical blend weights. The three curves and zone boundaries
are visible together, and dose evidence names the shared model. Planner v25
invalidates old input fingerprints; saved drafts require regeneration.

Regression checks cover arithmetic equality, monotonicity, speed/distance/HR
round trips, original anchors/CS, missing sources, stale data, continuous and
short interval targets, fixed-speed capacity, race references and UI separation.
