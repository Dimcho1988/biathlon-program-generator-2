# Speed load: Nordic descents, running and known rest

Historical implementation note for PR 186. The fixed 5 km/h rule below was
superseded by PR 187's athlete-relative 20% V6 floor. See
[the current model contract](CURRENT_MODEL_CONTRACT.md) for the active rules,
HR/speed source selection and the implemented 1.10 ski correction.

Implemented load sample version: `speed-load-samples-v1-descent-balance-rest`.
The measured Vflat model versions, terrain coefficients, inertia corrections,
paired TI admission and maximal-test input are unchanged.

## Nordic balance

For supported Nordic skiing/roller skiing identifiers, actual processed grade
below -3% selects moving descent intervals. Exactly -3% remains ordinary Vflat.
The load channel assigns the same residual speed to each selected interval:

`descent_speed = (k * sum(raw_speed * dt) - sum(known_load_speed * dt)) / sum(descent_dt)`

Both sums use the same timestamp-supported interval set, including known rest.
Existing valid Vflat already includes the session terrain factor `k`, so it is
retained without another multiplication. Known raw speed below 5 km/h uses
`k * raw_speed` numerically, even when grade/Vflat are unavailable at a stop.
Such samples are known rest and are not part of the unknown descent duration.
Provider moving averages or elapsed-duration metadata are not substituted for
the time-weighted raw mean. Unsupported active samples, gaps and block boundaries
are outside both sides of the balance and remain separately disclosed.

Example: 45 minutes at known corrected Vflat 17 km/h, 15 minutes descending and
a corrected raw target of 16 km/h gives 13 km/h for the descent. Applying the
same terrain factor to both raw and pre-correction Vflat scales this answer once.

A negative residual produces `NEGATIVE_DESCENT_RESIDUAL`, with no estimated
speed on those descent intervals. A zero residual is known zero-speed rest.
A positive residual is not capped at Z1 or forced into a low zone. Balance alone
does not establish low physiological intensity. Values outside the athlete's
supported speed-to-Q mapping remain outside mapping, not extrapolated Q.

## Rest and running

For running and Nordic load, measured raw speed **below** 5 km/h, or a supported
equivalent load speed **below** 5 km/h, is rest with zero Q. Exactly 5 km/h is
eligible for the normal mapping. Numeric equivalent speeds remain in the balance;
they are not overwritten with zero. Recorded duration includes rest.

Running uses its existing grade correction on supported descents, including
grades below -3%. The -3% restriction remains specific to calibration/test
admission. Unsupported running grades and other quality exclusions stay unknown.
Cycling and the canonical measured-HR ledger keep their existing policies.

## Consumers and accounting

The independent speed ledger feeds Q, component E and 7/40. Missing-HR planning
uses the same load intervals. Known rest is included in covered missing time but
has no zone Q; it cannot be filled again by the expert-duration fallback, even
without a usable paired index. Completely absent HR uses the supported speed
timeline without a lag crop; partial missing-HR masks retain the existing 20-second
paired alignment. Conflicting HR masks or incompatible durations still fail closed.

The running local-equivalence comparison also accounts for explicitly marked
rest separately, without adding zone-equivalent time. Its expert zone coefficients
are unchanged.

Reports distinguish `classified_minutes`, `rest_minutes`, `accounted_minutes`
and `unaccounted_minutes`. Estimated descent time is an overlapping provenance
measure, not an additional duration. Gaps, exclusions, unavailable calibration
and out-of-mapping speeds remain unaccounted for load; none become fake rest.

## Persistence and rollout

Load annotations share the full-resolution stored series but use distinct fields:
`load_speed_kmh`, `load_status`, `load_source`, `load_reason`. Strict test consumers
still read the original Vflat fields. The compact `speed_load_summary` accompanies
both persistent and in-memory projections; compression retains all annotations.

The per-activity configuration fingerprint includes the new sample version,
forcing new shadow runs on the next full refresh. The profile-only comparison key
is unchanged, preserving existing TI history and measured calibration evidence.
Ledger and planning cache versions change. No database migration is required.
Old ski/running analyses show a recomputation notice until a full data refresh.
Historical immutable rows are not edited in place.

Validation covers duration-weighted balance, the 45/15-minute example, terrain
factor application, exact -3% and 5 km/h boundaries, low/zero/negative residuals,
raw rest without grade, invalid samples, recording gaps, unchanged test admission,
compression, cache identity, missing-HR fallback, and propagation through Q/E/7/40.
